import type { IEditorValue } from "../components/rete-editor";

export function validateEditorValue(value: unknown): value is IEditorValue {
  if (!value || typeof value !== "object") return false;

  const v = value as Record<string, unknown>;

  if (!Array.isArray(v.nodes) || !Array.isArray(v.connections)) return false;

  const hasValidNodes = v.nodes.every(
    (node) =>
      node &&
      typeof node === "object" &&
      typeof (node as Record<string, unknown>).id === "string" &&
      typeof (node as Record<string, unknown>).data === "object"
  );

  const hasValidConnections = v.connections.every(
    (conn) =>
      conn &&
      typeof conn === "object" &&
      typeof (conn as Record<string, unknown>).id === "string" &&
      typeof (conn as Record<string, unknown>).source === "string" &&
      typeof (conn as Record<string, unknown>).target === "string"
  );

  return hasValidNodes && hasValidConnections;
}