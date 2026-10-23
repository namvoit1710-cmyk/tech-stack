// Types for the Environments / Config Variables feature (SA-2365). The store lives
// in the workflow control plane (/api/v1/environments); values are non-secret — a
// credential-type variable carries only a credential_ref id, never a secret.

export type VariableType = "text" | "credential" | "list";

export interface IEnvVariable {
    key: string;
    type: VariableType;
    value: string; // text / credential(ref)
    values: string[]; // list type only
    is_secret?: boolean; // set by the API for credential-type variables
}

export interface IEnvironment {
    id: string;
    tenant_id: string;
    section: string;
    name: string;
    is_active: boolean;
    version: number;
    created_at: string;
    updated_at: string;
    variables: IEnvVariable[];
}

// The control-plane wraps every /api/v1 response in {status, data} (same as the
// gateway feature). APISdk returns the raw body, so hooks receive the envelope
// and consumers read `.data.<field>`.
export interface IEnvelope<T> {
    status: string;
    data: T;
}

export type IListEnvironmentsResponse = IEnvelope<{ items: IEnvironment[]; total: number }>;

export type IGetEnvironmentResponse = IEnvelope<{ environment: IEnvironment }>;

export interface IEnvVariableInput {
    key: string;
    type: VariableType;
    value?: string;
    values?: string[];
}

export interface ICreateEnvironmentPayload {
    section: string;
    name: string;
    variables: IEnvVariableInput[];
}

export interface IUpdateEnvironmentPayload {
    section?: string | null;
    name?: string | null;
    variables?: IEnvVariableInput[] | null;
}

export type IEnvMutateResponse = IEnvelope<{ id: string; version: number }>;

export type IEnvDeleteResponse = IEnvelope<{ deleted: boolean }>;

export interface IEnvErrorBody {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
}

// SA-2450: a workflow binds to EXACTLY ONE section and a subset (>=1) of that
// section's environments. The engine resolves $env for a run from the picked
// environment, but the run-time picker only offers the workflow's assigned set.

export interface IWorkflowEnvironmentAssignment {
    workflow_id: string;
    section: string; // "" when unassigned
    environment_ids: string[]; // [] when unassigned
    assigned: boolean;
    version: number;
}

export type IWorkflowEnvironmentAssignmentResponse = IEnvelope<IWorkflowEnvironmentAssignment>;

export interface ISetWorkflowEnvironmentAssignmentPayload {
    section: string;
    environmentIds: string[];
}

export type ISetWorkflowEnvironmentAssignmentResponse = IEnvelope<{
    workflow_id: string;
    section: string;
    environment_ids: string[];
    version: number;
}>;

export interface IEnvironmentWorkflowItem {
    workflow_id: string;
    section: string;
}

export type IWorkflowsForEnvironmentResponse = IEnvelope<{
    environment_id: string;
    workflow_ids: string[];
    items: IEnvironmentWorkflowItem[];
    total: number;
}>;

// SA-2471: the variable KEY schema is a property of the Section — every
// environment in a section shares the SAME keys+types; only the value differs.
// A section-scoped key is `text` or `credential` in every environment (a
// credential key still maps a different credential id per environment).

// The schema type is the shared subset of VariableType (no per-section `list`).
export type SectionVariableType = "text" | "credential";

export interface IEnvSectionKey {
    key: string;
    type: SectionVariableType;
}

// One section's shared key schema (+ summary counts for the collapsible card).
export interface IEnvSection {
    id: string;
    section: string;
    keys: IEnvSectionKey[];
    version: number; // CAS token, sent back as If-Match on PATCH
    environment_count: number;
    created_at?: string;
    updated_at?: string;
}

export type IListEnvSectionsResponse = IEnvelope<{ items: IEnvSection[]; total: number }>;

export type IGetEnvSectionResponse = IEnvelope<IEnvSection>;

export interface ICreateEnvSectionPayload {
    section: string;
    keys: IEnvSectionKey[];
}

export interface IUpdateEnvSectionPayload {
    // SA-2450 W18: rename the section (server re-tags its environments).
    section?: string;
    keys?: IEnvSectionKey[];
}

export type IEnvSectionMutateResponse = IEnvelope<{ id: string; version: number }>;

export type IEnvSectionDeleteResponse = IEnvelope<{ deleted: boolean; environments_deleted: number }>;

// Backend error codes for the section schema endpoints.
export const ENV_SECTION_INVALID_KEYS = "SECTION_SCHEMA_INVALID_KEYS";
export const ENV_SECTION_ALREADY_EXISTS = "SECTION_SCHEMA_ALREADY_EXISTS";

// error.details carried by a 422 SECTION_SCHEMA_INVALID_KEYS so the FE can mark
// exactly the offending key field(s).
export interface IEnvSectionInvalidKeysDetails {
    duplicate_keys?: string[];
    empty_key_indexes?: number[];
    invalid_keys?: string[];
}

// SA-2605: a workflow binds to EXACTLY ONE section — there is no per-flow subset of
// environments any more. At run time the flow may use ANY environment that belongs
// to its bound section (Section <-> Environment is many-to-many, see
// ISectionMembership). An unbound workflow returns section:"", assigned:false.
export interface IWorkflowSection {
    workflow_id: string;
    section: string; // "" when unbound
    assigned: boolean;
    version: number;
}

export type IWorkflowSectionResponse = IEnvelope<IWorkflowSection>;

export interface ISetWorkflowSectionPayload {
    section: string; // "" clears the binding
}

export type ISetWorkflowSectionResponse = IEnvelope<{
    workflow_id: string;
    section: string;
    assigned: boolean;
    version: number;
}>;

// SA-2605: the many-to-many membership of a Section — the set of environment ids
// that belong to it. Keys are declared on the Section (SA-2471) and flow down to
// all these environments; an environment may belong to many sections at once.
export interface ISectionMembership {
    section: string;
    environment_ids: string[];
    version: number; // CAS token, sent back as If-Match on PUT
}

export type ISectionMembershipResponse = IEnvelope<ISectionMembership>;

// SA-2904: every workflow bound to a section (AIW_WORKFLOW_SECTION), in one call.
export type ISectionWorkflowsResponse = IEnvelope<{
    section: string;
    workflow_ids: string[];
}>;

export interface ISetSectionMembershipPayload {
    environmentIds: string[];
}

export type ISetSectionMembershipResponse = IEnvelope<{
    section: string;
    environment_ids: string[];
    version: number;
}>;

// SA-2605: every section's membership at once (for the "also in X, Y" chips and
// the flat Environments tab's per-env section derivation).
export type IListSectionMembershipsResponse = IEnvelope<{
    memberships: ISectionMembership[];
}>;

// SA-2605: a key declared with two different types across two sections is rejected
// with 422 KEY_TYPE_CONFLICT; error.details identifies the offending key + the two
// sections/types so the FE can explain the clash inline.
export const KEY_TYPE_CONFLICT = "KEY_TYPE_CONFLICT";
export const ENVIRONMENT_NOT_IN_SECTION = "ENVIRONMENT_NOT_IN_SECTION";
export const WORKFLOW_ENVIRONMENT_INVALID = "WORKFLOW_ENVIRONMENT_INVALID";

export interface IKeyTypeConflictDetails {
    key?: string;
    section_a?: string;
    type_a?: string;
    section_b?: string;
    type_b?: string;
}
