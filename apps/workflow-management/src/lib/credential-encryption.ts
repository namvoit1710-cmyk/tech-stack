// Transport encryption for credential formData (SA-1343 / FE-2).
// Defense-in-depth on top of TLS + the vault's at-rest AES-256-GCM: encrypt the
// secret-bearing formData to the vault's RSA public key before it leaves the page.
// Hybrid scheme, 1:1 with the vault's RsaTransportKeypair:
//   AES-256-GCM the JSON (12-byte IV, tag appended by Web Crypto) under a random
//   key, then RSA-OAEP(SHA-256) wrap that key. Built on the standard Web Crypto
//   API — no dependency.
import { requireCredentialsApi } from "@/lib/credentials-api";

interface IVaultEnvelope<T> {
  status: string;
  data: T;
}

interface ITransportKey {
  publicKey: string;
  keyId: string;
  alg: string;
}

export interface IEncryptedEnvelope {
  key: string;
  iv: string;
  ciphertext: string;
}

const pemToDer = (pem: string): ArrayBuffer => {
  const b64 = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
};

const toB64 = (data: ArrayBuffer | Uint8Array): string => {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  let s = "";
  bytes.forEach(b => {
    s += String.fromCharCode(b);
  });
  return btoa(s);
};

// Cache the key for the session — it is stable per vault process. Reset on error
// so a transient fetch failure does not poison later attempts.
let cachedKey: Promise<ITransportKey> | null = null;
const fetchTransportKey = (): Promise<ITransportKey> => {
  if (!cachedKey) {
    cachedKey = requireCredentialsApi()
      .get<IVaultEnvelope<ITransportKey>>("/credentials/transport-key")
      .then(r => r.data)
      .catch(err => {
        cachedKey = null;
        throw err;
      });
  }
  return cachedKey;
};

// True only when the platform exposes Web Crypto (always in a modern browser;
// false in some test runners, where callers fall back to plaintext over TLS).
export const isTransportEncryptionAvailable = (): boolean =>
  typeof crypto !== "undefined" && !!crypto.subtle;

export async function encryptFormData(
  formData: Record<string, unknown>,
): Promise<IEncryptedEnvelope> {
  const { publicKey } = await fetchTransportKey();
  const rsaKey = await crypto.subtle.importKey(
    "spki",
    pemToDer(publicKey),
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"],
  );
  const aesKey = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = new TextEncoder().encode(JSON.stringify(formData));
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, aesKey, plaintext);
  const rawAes = await crypto.subtle.exportKey("raw", aesKey);
  const wrapped = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, rsaKey, rawAes);
  return { key: toB64(wrapped), iv: toB64(iv), ciphertext: toB64(ciphertext) };
}
