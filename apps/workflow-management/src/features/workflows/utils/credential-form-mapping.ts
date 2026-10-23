// Schema-driven mapping between the @ldc/autoform Builder's nested form values and the
// FLAT {displayName, formData} the vault expects. The mapping is not guessed — it is read
// from the credential type's own form_schema `submit_credential` action, whose api_call
// body declares each target key as `${formValues.<path>}` (e.g. formData.host =
// "${formValues.tabs.server.host}"). Using that same declaration to (a) flatten on save
// and (b) nest stored values back into defaults guarantees prefill round-trips exactly.
import type { ICredentialFormSchema } from "@/features/workflows/types/credentials"

// Remove ButtonControl fields (the schema's "Test Connection" / "Save Credential" buttons)
// before rendering the form. Those buttons drive the schema's declarative actions, which
// only fire when a Builder is given an `onFormActions` handler; the credential add/edit
// forms submit via their own page-level buttons instead, so the schema buttons render dead
// and duplicate the real controls. The submit_credential action (used to build the flatten
// mapping) lives on the root object's wrapperProps, not on these fields, so pruning them
// does not affect save. Recurses because the buttons are nested under the root object.
export const stripActionButtons = (
  schema: ICredentialFormSchema | null,
): ICredentialFormSchema | null => {
  if (!schema) return schema
  const clone = structuredClone(schema) as { fields?: unknown[] }
  const prune = (fields: unknown[] | undefined): unknown[] =>
    (fields ?? [])
      .filter(f => {
        const fc = (f as { fieldConfig?: { fieldControl?: string } })?.fieldConfig
        return fc?.fieldControl !== "ButtonControl"
      })
      .map(f => {
        const node = f as { fields?: unknown[] }
        if (Array.isArray(node.fields)) node.fields = prune(node.fields)
        return f
      })
  clone.fields = prune(clone.fields)
  return clone as ICredentialFormSchema
}

export interface ICredentialSubmitMapping {
  rootKey: string // the schema's single root object field (e.g. "credentialForm")
  formData: Record<string, string[]> // target formData key -> path within the root value
  displayNamePath: string[] | null // path to displayName within the root value, if declared
}

type Json = Record<string, unknown>

const asRecord = (v: unknown): Json | undefined =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : undefined

// "${formValues.tabs.server.host}" -> ["tabs","server","host"] (drops the "formValues" head).
// Anything that isn't a single clean ${formValues.…} reference returns null.
export const parseFormValuesPath = (expr: unknown): string[] | null => {
  if (typeof expr !== "string") return null
  const m = expr.match(/^\$\{\s*formValues\.([^}]+?)\s*\}$/)
  const inner = m?.[1]
  if (!inner) return null
  return inner.split(".").map(s => s.trim()).filter(Boolean)
}

const schemaFields = (schema: ICredentialFormSchema | null): unknown[] | undefined =>
  asRecord(schema as unknown)?.fields as unknown[] | undefined

const findSubmitAction = (schema: ICredentialFormSchema | null): Json | undefined => {
  const rootField = asRecord(schemaFields(schema)?.[0])
  const actions = asRecord(asRecord(rootField?.fieldConfig)?.wrapperProps)?.actions as
    | unknown[]
    | undefined
  return actions?.map(asRecord).find(a => a?.action === "submit_credential")
}

// Build the flatten/nest mapping from a credential type's form_schema. Returns null when
// the schema has no declarative submit_credential api_call (e.g. OAuth-redirect types).
export const buildSubmitMapping = (
  schema: ICredentialFormSchema | null,
): ICredentialSubmitMapping | null => {
  const rootField = asRecord(schemaFields(schema)?.[0])
  const rootKey = typeof rootField?.key === "string" ? rootField.key : undefined
  if (!rootKey) return null

  const submit = findSubmitAction(schema)
  const steps = (submit?.steps ?? []) as unknown[]
  const apiCall = steps.map(asRecord).find(s => s?.type === "api_call")
  const body = asRecord(apiCall?.body)
  if (!body) return null

  const formDataBody = asRecord(body.formData) ?? {}
  const formData: Record<string, string[]> = {}
  for (const [target, expr] of Object.entries(formDataBody)) {
    const path = parseFormValuesPath(expr)
    if (path) formData[target] = path
  }
  const displayNamePath = parseFormValuesPath(body.displayName)

  return { rootKey, formData, displayNamePath }
}

const getPath = (obj: unknown, path: string[]): unknown =>
  path.reduce<unknown>((acc, key) => asRecord(acc)?.[key], obj)

const setPath = (target: Json, path: string[], value: unknown): void => {
  if (path.length === 0) return
  let node = target
  for (const key of path.slice(0, -1)) {
    if (!asRecord(node[key])) node[key] = {}
    node = node[key] as Json
  }
  const last = path[path.length - 1]
  if (last !== undefined) node[last] = value
}

// Flatten the Builder's emitted values into the vault's {displayName, formData}.
// `builderValues` is the full nested tree keyed by the root object field.
export const flattenForSubmit = (
  builderValues: Json,
  mapping: ICredentialSubmitMapping,
): { displayName?: string; formData: Record<string, unknown> } => {
  const inner = asRecord(builderValues[mapping.rootKey]) ?? builderValues
  const formData: Record<string, unknown> = {}
  for (const [target, path] of Object.entries(mapping.formData)) {
    const v = getPath(inner, path)
    if (v !== undefined) formData[target] = v
  }
  const displayName = mapping.displayNamePath
    ? (getPath(inner, mapping.displayNamePath) as string | undefined)
    : undefined
  return { displayName, formData }
}

// Nest stored flat values (from the vault) back into Builder defaultValues, following the
// same paths. `flatValues` may include a synthetic "displayName" key. Only keys present in
// the mapping are placed; secret keys (absent from the vault response) stay unset → blank.
export const buildDefaultValues = (
  mapping: ICredentialSubmitMapping,
  flatValues: Record<string, unknown>,
  displayName?: string,
): Json => {
  const inner: Json = {}
  for (const [target, path] of Object.entries(mapping.formData)) {
    if (target in flatValues && flatValues[target] !== undefined) {
      setPath(inner, path, flatValues[target])
    }
  }
  if (mapping.displayNamePath && displayName !== undefined) {
    setPath(inner, mapping.displayNamePath, displayName)
  }
  return { [mapping.rootKey]: inner }
}
