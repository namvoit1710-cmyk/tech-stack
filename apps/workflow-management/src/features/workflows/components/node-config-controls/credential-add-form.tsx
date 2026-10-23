// Inline "+ Add credential" form for the node credential picker + the /credentials manager
// (T7). Reuses the @ldc/autoform Builder to render a credential type's form_schema, then
// POSTs to credentials-management. Submit flattens the Builder's nested values to the flat
// {displayName, formData} the vault validates — using the schema's own submit_credential
// mapping (buildSubmitMapping/flattenForSubmit), the same path the edit page uses. This is
// why tabbed types (SMTP, Basic Auth, OAuth2 app, Database) create correctly. The schema's
// own Display Name field is the name (no separate input), and the dead Test/Save schema
// buttons are stripped. OAuth-redirect types (form_schema == null) render a Connect link.
import {
  useCredentialTypes,
  useIssueCredential,
  useTestInlineCredential,
} from "@/features/workflows/hooks/apis/credentials";
import type {
  IIssueCredentialRequest,
  ICredentialTypeDefinition,
  ITestInlineCredentialRequest,
} from "@/features/workflows/types/credentials";
import { filterCredentials } from "@/features/workflows/utils/credential-filter";
import {
  buildSubmitMapping,
  flattenForSubmit,
  stripActionButtons,
} from "@/features/workflows/utils/credential-form-mapping";
import { SimpleSelect } from "@/components/simple-select/simple-select";
import { encryptFormData, isTransportEncryptionAvailable } from "@/lib/credential-encryption";
import { useLanguage } from "@/hooks/use-language";
import { Builder } from "@ldc/autoform";
import { Button } from "@ldc/ui/components/button";
import { useMemo, useState } from "react";

interface ICredentialAddFormProps {
  provider?: string;
  kinds?: string[];
  onCreated: (credentialId: string) => void;
  onCancel: () => void;
}

export const CredentialAddForm = ({ provider, kinds, onCreated, onCancel }: ICredentialAddFormProps) => {
  const { t } = useLanguage();
  const { data } = useCredentialTypes();
  const issue = useIssueCredential();
  const testInline = useTestInlineCredential();

  const types = filterCredentials<ICredentialTypeDefinition>(data?.types ?? [], { provider, kinds });
  const [typeId, setTypeId] = useState("");
  const [builderValues, setBuilderValues] = useState<Record<string, unknown>>({});
  const [formValid, setFormValid] = useState(false);
  // SA-1577: inline result of a "Test connection" run (null = not tested yet).
  const [testResult, setTestResult] = useState<{ valid: boolean; error?: string | null; latencyMs?: number } | null>(null);

  const selectedType = types.find(tp => tp.typeId === typeId);
  // null formSchema => OAuth-redirect type (render Connect, no inline form).
  const rawSchema = selectedType?.formSchema ?? null;
  // Render without the schema's dead Test/Save buttons; the mapping (for flatten) is read
  // from the original schema's submit action, which stripping preserves.
  const formSchema = useMemo(() => stripActionButtons(rawSchema), [rawSchema]);
  const mapping = useMemo(() => buildSubmitMapping(rawSchema), [rawSchema]);

  // FE-2 secret transport: encrypt formData to the vault's public key so it never leaves
  // the page in clear; fall back to plaintext over TLS only if Web Crypto is unavailable.
  // Shared by Submit and Test so both send the secret the same way.
  const buildSecretPayload = async (
    formData: Record<string, unknown>,
  ): Promise<{ formData: Record<string, unknown> } | { encryptedFormData: Awaited<ReturnType<typeof encryptFormData>> }> => {
    if (isTransportEncryptionAvailable()) {
      try {
        return { encryptedFormData: await encryptFormData(formData) };
      } catch {
        return { formData };
      }
    }
    return { formData };
  };

  const handleSubmit = async () => {
    if (!selectedType || !mapping) return;
    // Flatten the Builder's nested values into the flat {displayName, formData} the vault
    // expects — works for both flat (token/webhookUrl) and tabbed (host/username/…) schemas.
    const { displayName, formData } = flattenForSubmit(builderValues, mapping);
    if (!displayName) return;
    const body: IIssueCredentialRequest = { typeId, displayName, ...(await buildSecretPayload(formData)) };
    const res = await issue.mutateAsync(body);
    if (res?.credentialId) onCreated(res.credentialId);
  };

  // SA-1577: run the adapter's live test against the current form values WITHOUT saving.
  const handleTest = async () => {
    if (!selectedType || !mapping) return;
    setTestResult(null);
    const { formData } = flattenForSubmit(builderValues, mapping);
    const body: ITestInlineCredentialRequest = { typeId, ...(await buildSecretPayload(formData)) };
    try {
      const res = await testInline.mutateAsync(body);
      setTestResult({ valid: !!res?.valid, error: res?.error, latencyMs: res?.latencyMs });
    } catch {
      setTestResult({ valid: false, error: t("nodes.credential.test_failed") });
    }
  };

  // A stale result must not linger once the user EDITS the form. But the autoform Builder
  // also fires onValuesChange on plain re-renders (with unchanged values) — clearing on
  // those would nuke a just-computed result before it ever paints. So only invalidate when
  // the values actually changed. (SA-1577: live test showed the result never appeared until
  // this guard was added.)
  const onValuesChange = (v: unknown) => {
    const next = (v as Record<string, unknown>) ?? {};
    if (testResult && JSON.stringify(next) !== JSON.stringify(builderValues)) setTestResult(null);
    setBuilderValues(next);
  };

  return (
    <div className="flex flex-col gap-2 rounded border p-2">
      <SimpleSelect
        data-testid="credential-add-type"
        value={typeId}
        onValueChange={next => {
          setTypeId(next);
          setTestResult(null);
        }}
        emptyLabel={t("nodes.credential.add_select_type")}
        options={types.map(tp => ({ value: tp.typeId, label: tp.displayName }))}
      />

      {selectedType && formSchema !== null && (
        <>
          <div data-testid="credential-add-form">
            <Builder
              schema={formSchema}
              onValuesChange={onValuesChange}
              onValidChange={setFormValid}
            />
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              data-testid="credential-add-submit"
              disabled={!formValid || issue.isPending}
              onClick={handleSubmit}
            >
              {issue.isPending ? t("nodes.credential.add_creating") : t("nodes.credential.add_create")}
            </Button>
            {/* SA-1577: verify the connection before saving; never persists. */}
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="credential-add-test"
              disabled={!formValid || testInline.isPending}
              onClick={handleTest}
            >
              {testInline.isPending ? t("nodes.credential.testing") : t("nodes.credential.test")}
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={onCancel}>
              {t("nodes.credential.add_cancel")}
            </Button>
          </div>
          {testResult && (
            <p
              data-testid="credential-add-test-result"
              className={`text-sm ${testResult.valid ? "text-green-600" : "text-red-600"}`}
            >
              {testResult.valid
                ? t("nodes.credential.test_ok") +
                  (typeof testResult.latencyMs === "number" ? ` (${testResult.latencyMs} ms)` : "")
                : `${t("nodes.credential.test_fail")}: ${testResult.error ?? ""}`}
            </p>
          )}
        </>
      )}

      {selectedType && formSchema === null && (
        <a
          data-testid="credential-add-connect"
          href={selectedType.connectAction}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-blue-600 underline"
        >
          {t("nodes.credential.connect")}
        </a>
      )}
    </div>
  );
};

CredentialAddForm.displayName = "CredentialAddForm";
