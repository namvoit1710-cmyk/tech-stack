// React-query hooks for the Environments / Config Variables API in the workflow
// control plane. `api` baseURL already includes /api/v1 and unwraps the {status,
// data} envelope, so these return the inner payload directly (mirrors gateway-apps).
import api from "@/lib/api";
import { AxiosError } from "@ldc/api-sdk";
import {
    QueryKey,
    queryKeyFactory,
    useMutation,
    UseMutationOptions,
    useQuery,
    useQueryClient,
    UseQueryOptions,
} from "@ldc/tanstack-query";
import {
    ICreateEnvironmentPayload,
    ICreateEnvSectionPayload,
    IEnvDeleteResponse,
    IEnvErrorBody,
    IEnvMutateResponse,
    IEnvSectionDeleteResponse,
    IEnvSectionMutateResponse,
    IGetEnvironmentResponse,
    IGetEnvSectionResponse,
    IListEnvironmentsResponse,
    IListEnvSectionsResponse,
    IListSectionMembershipsResponse,
    ISectionMembershipResponse,
    ISectionWorkflowsResponse,
    ISetSectionMembershipPayload,
    ISetSectionMembershipResponse,
    ISetWorkflowEnvironmentAssignmentPayload,
    ISetWorkflowEnvironmentAssignmentResponse,
    ISetWorkflowSectionPayload,
    ISetWorkflowSectionResponse,
    IUpdateEnvironmentPayload,
    IUpdateEnvSectionPayload,
    IWorkflowEnvironmentAssignmentResponse,
    IWorkflowSectionResponse,
    IWorkflowsForEnvironmentResponse,
} from "../../types/environment";

export interface IEnvironmentListParams {
    section?: string;
    name_contains?: string;
    include_inactive?: boolean;
    skip?: number;
    top?: number;
}

const enc = encodeURIComponent;

const _environmentKey = queryKeyFactory("environments");

export const environmentKey = {
    ..._environmentKey,
    list: (params?: IEnvironmentListParams) => [..._environmentKey.lists(), params] as const,
    detail: (id: string) => [..._environmentKey.details(), id] as const,
    // SA-2450: a workflow's environment assignment (section + subset of env ids).
    workflowAssignment: (workflowId: string) =>
        [..._environmentKey.all, "workflow-assignment", workflowId] as const,
    // SA-2450: the workflows bound to a given environment.
    environmentWorkflows: (environmentId: string) =>
        [..._environmentKey.all, "environment-workflows", environmentId] as const,
    // SA-2605: a workflow's single-section binding (no per-flow env subset).
    workflowSection: (workflowId: string) =>
        [..._environmentKey.all, "workflow-section", workflowId] as const,
};

type Err = AxiosError<IEnvErrorBody>;

// SA-2471: the section-scoped variable KEY schema (shared by every environment
// in the section). Separate query key namespace from environments so the two
// caches invalidate independently.
const _envSectionKey = queryKeyFactory("env-sections");

export const envSectionKey = {
    ..._envSectionKey,
    detail: (id: string) => [..._envSectionKey.details(), id] as const,
    // SA-2605: one section's many-to-many environment membership.
    membership: (section: string) => [..._envSectionKey.all, "membership", section] as const,
    // SA-2605: every section's membership at once.
    memberships: () => [..._envSectionKey.all, "memberships"] as const,
    // SA-2904: the workflows bound to a section.
    workflows: (section?: string) =>
        (section ? [..._envSectionKey.all, "workflows", section] : [..._envSectionKey.all, "workflows"]) as readonly string[],
};

// Query: list environments (tenant-scoped by the API)
export const useEnvironmentList = (
    params?: IEnvironmentListParams,
    options?: Omit<UseQueryOptions<IListEnvironmentsResponse, Err, IListEnvironmentsResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: environmentKey.list(params),
        queryFn: (): Promise<IListEnvironmentsResponse> => api.get("/environments", { params }),
        ...options,
    });

// Query: get one environment by id
export const useEnvironment = (
    id: string,
    options?: Omit<UseQueryOptions<IGetEnvironmentResponse, Err, IGetEnvironmentResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: environmentKey.detail(id),
        queryFn: (): Promise<IGetEnvironmentResponse> => api.get(`/environments/${enc(id)}`),
        enabled: !!id,
        ...options,
    });

// Mutation: create environment
export const useCreateEnvironment = (
    options?: Omit<UseMutationOptions<IEnvMutateResponse, Err, ICreateEnvironmentPayload>, "mutationFn">
) =>
    useMutation({
        mutationFn: (payload: ICreateEnvironmentPayload): Promise<IEnvMutateResponse> =>
            api.post("/environments", payload),
        ...options,
    });

// Mutation: update environment — optimistic concurrency via If-Match: <version>
export const useUpdateEnvironment = (
    options?: Omit<
        UseMutationOptions<IEnvMutateResponse, Err, { id: string; version: number; payload: IUpdateEnvironmentPayload }>,
        "mutationFn"
    >
) =>
    useMutation({
        mutationFn: ({ id, version, payload }): Promise<IEnvMutateResponse> =>
            api.patch(`/environments/${enc(id)}`, payload, { headers: { "If-Match": String(version) } }),
        ...options,
    });

// Mutation: delete (soft) environment
export const useDeleteEnvironment = (
    options?: Omit<UseMutationOptions<IEnvDeleteResponse, Err, string>, "mutationFn">
) =>
    useMutation({
        mutationFn: (id: string): Promise<IEnvDeleteResponse> => api.delete(`/environments/${enc(id)}`),
        ...options,
    });

// --- SA-2450: workflow <-> environment binding ------------------------------

// Query: a workflow's current environment assignment (section + subset of env
// ids). An unassigned workflow returns section:"", environment_ids:[],
// assigned:false — used both by the Assign editor (prefill) and the run-time
// environment selector (which offers only the assigned subset).
export const useWorkflowEnvironmentAssignment = (
    workflowId: string,
    options?: Omit<
        UseQueryOptions<IWorkflowEnvironmentAssignmentResponse, Err, IWorkflowEnvironmentAssignmentResponse, QueryKey>,
        "queryKey" | "queryFn"
    >
) =>
    useQuery({
        queryKey: environmentKey.workflowAssignment(workflowId),
        queryFn: (): Promise<IWorkflowEnvironmentAssignmentResponse> =>
            api.get(`/workflows/${enc(workflowId)}/environment-assignment`),
        enabled: !!workflowId,
        ...options,
    });

// Mutation: set (bind) a workflow's environment assignment. On success we
// invalidate the workflow's assignment query and the environment list so both
// the Assign editor and the run-time selector re-fetch.
export const useSetWorkflowEnvironmentAssignment = (
    options?: Omit<
        UseMutationOptions<
            ISetWorkflowEnvironmentAssignmentResponse,
            Err,
            { workflowId: string; payload: ISetWorkflowEnvironmentAssignmentPayload }
        >,
        "mutationFn"
    >
) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ workflowId, payload }): Promise<ISetWorkflowEnvironmentAssignmentResponse> =>
            api.put(`/workflows/${enc(workflowId)}/environment-assignment`, payload),
        ...options,
        onSuccess: (...args) => {
            const variables = args[1];
            queryClient.invalidateQueries({
                queryKey: environmentKey.workflowAssignment(variables.workflowId),
            });
            queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
            options?.onSuccess?.(...args);
        },
    });
};

// --- SA-2471: section-scoped shared variable key schema ---------------------
// The KEY set (+ type per key) lives on the Section; environments only supply
// per-key VALUES. The backend enforces unique + non-empty keys on Save and, on a
// 422 (SECTION_SCHEMA_INVALID_KEYS), returns error.details so the FE can mark the
// offending fields. `api` baseURL already includes /api/v1 and unwraps the
// {status,data} envelope, so these return the inner payload directly.

// Query: list every section's shared schema (+ environment counts) for the
// collapsible cards.
export const useEnvSectionList = (
    options?: Omit<UseQueryOptions<IListEnvSectionsResponse, Err, IListEnvSectionsResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: envSectionKey.lists(),
        queryFn: (): Promise<IListEnvSectionsResponse> => api.get("/env-sections"),
        ...options,
    });

// Query: one section's shared schema by id.
export const useEnvSection = (
    id: string,
    options?: Omit<UseQueryOptions<IGetEnvSectionResponse, Err, IGetEnvSectionResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: envSectionKey.detail(id),
        queryFn: (): Promise<IGetEnvSectionResponse> => api.get(`/env-sections/${enc(id)}`),
        enabled: !!id,
        ...options,
    });

// Mutation: create a section (name + initial key schema). 409 =
// SECTION_SCHEMA_ALREADY_EXISTS, 422 = SECTION_SCHEMA_INVALID_KEYS.
export const useCreateEnvSection = (
    options?: Omit<UseMutationOptions<IEnvSectionMutateResponse, Err, ICreateEnvSectionPayload>, "mutationFn">
) =>
    useMutation({
        mutationFn: (payload: ICreateEnvSectionPayload): Promise<IEnvSectionMutateResponse> =>
            api.post("/env-sections", payload),
        ...options,
    });

// Mutation: save a section's shared key schema — optimistic concurrency via
// If-Match: <version>. 422 (SECTION_SCHEMA_INVALID_KEYS) returns error.details
// {duplicate_keys, empty_key_indexes, invalid_keys}; 409 is a CAS conflict.
export const useUpdateEnvSection = (
    options?: Omit<
        UseMutationOptions<IEnvSectionMutateResponse, Err, { id: string; version: number; payload: IUpdateEnvSectionPayload }>,
        "mutationFn"
    >
) =>
    useMutation({
        mutationFn: ({ id, version, payload }): Promise<IEnvSectionMutateResponse> =>
            api.patch(`/env-sections/${enc(id)}`, payload, { headers: { "If-Match": String(version) } }),
        ...options,
    });

// Mutation: delete a section (cascades its environments + values). The response
// reports how many environments were removed.
export const useDeleteEnvSection = (
    options?: Omit<UseMutationOptions<IEnvSectionDeleteResponse, Err, string>, "mutationFn">
) =>
    useMutation({
        mutationFn: (id: string): Promise<IEnvSectionDeleteResponse> => api.delete(`/env-sections/${enc(id)}`),
        ...options,
    });

// Query: the workflows bound to a given environment (for the reverse view).
export const useWorkflowsForEnvironment = (
    environmentId: string,
    options?: Omit<
        UseQueryOptions<IWorkflowsForEnvironmentResponse, Err, IWorkflowsForEnvironmentResponse, QueryKey>,
        "queryKey" | "queryFn"
    >
) =>
    useQuery({
        queryKey: environmentKey.environmentWorkflows(environmentId),
        queryFn: (): Promise<IWorkflowsForEnvironmentResponse> =>
            api.get(`/environments/${enc(environmentId)}/workflows`),
        enabled: !!environmentId,
        ...options,
    });

// --- SA-2605: workflow <-> single-section binding ---------------------------
// A workflow binds to EXACTLY ONE section (no per-flow environment subset). The
// run-time env picker offers every environment of the bound section. An unbound
// workflow returns section:"", assigned:false. Mirrors the SA-2450
// useWorkflowEnvironmentAssignment/useSetWorkflowEnvironmentAssignment pair.

// Query: a workflow's current section binding.
export const useWorkflowSection = (
    workflowId: string,
    options?: Omit<
        UseQueryOptions<IWorkflowSectionResponse, Err, IWorkflowSectionResponse, QueryKey>,
        "queryKey" | "queryFn"
    >
) =>
    useQuery({
        queryKey: environmentKey.workflowSection(workflowId),
        queryFn: (): Promise<IWorkflowSectionResponse> => api.get(`/workflows/${enc(workflowId)}/section`),
        enabled: !!workflowId,
        ...options,
    });

// Mutation: set (bind) a workflow's section. On success we invalidate the
// workflow's section query and the environment list so both the binding editor
// and the run-time selector re-fetch.
export const useSetWorkflowSection = (
    options?: Omit<
        UseMutationOptions<ISetWorkflowSectionResponse, Err, { workflowId: string; payload: ISetWorkflowSectionPayload }>,
        "mutationFn"
    >
) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ workflowId, payload }): Promise<ISetWorkflowSectionResponse> =>
            api.put(`/workflows/${enc(workflowId)}/section`, payload),
        ...options,
        onSuccess: (...args) => {
            const variables = args[1];
            queryClient.invalidateQueries({ queryKey: environmentKey.workflowSection(variables.workflowId) });
            queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
            queryClient.invalidateQueries({ queryKey: envSectionKey.workflows() });
            options?.onSuccess?.(...args);
        },
    });
};

// --- SA-2605: Section <-> Environment many-to-many membership ---------------

// Query: one section's environment membership (the env ids that belong to it).
export const useSectionMembership = (
    section: string,
    options?: Omit<
        UseQueryOptions<ISectionMembershipResponse, Err, ISectionMembershipResponse, QueryKey>,
        "queryKey" | "queryFn"
    >
) =>
    useQuery({
        queryKey: envSectionKey.membership(section),
        queryFn: (): Promise<ISectionMembershipResponse> => api.get(`/sections/${enc(section)}/environments`),
        enabled: !!section,
        ...options,
    });

// Mutation: set (replace) a section's environment membership. On success we
// invalidate this section's membership, the memberships list, the environment
// list and the section-schema list so every derived "also in X,Y" chip and env
// key-union re-fetches. A 422 KEY_TYPE_CONFLICT is surfaced by the caller.
export const useSetSectionMembership = (
    options?: Omit<
        UseMutationOptions<ISetSectionMembershipResponse, Err, { section: string; version: number; payload: ISetSectionMembershipPayload }>,
        "mutationFn"
    >
) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ section, version, payload }): Promise<ISetSectionMembershipResponse> =>
            api.put(`/sections/${enc(section)}/environments`, payload, { headers: { "If-Match": String(version) } }),
        ...options,
        onSuccess: (...args) => {
            const variables = args[1];
            queryClient.invalidateQueries({ queryKey: envSectionKey.membership(variables.section) });
            queryClient.invalidateQueries({ queryKey: envSectionKey.memberships() });
            queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
            queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
            options?.onSuccess?.(...args);
        },
    });
};

// Query: every workflow bound to a section (SA-2904) — one call instead of
// probing each workflow's /section binding.
export const useSectionWorkflows = (section: string) =>
    useQuery({
        queryKey: envSectionKey.workflows(section),
        queryFn: (): Promise<ISectionWorkflowsResponse> => api.get(`/sections/${enc(section)}/workflows`),
        enabled: !!section,
    });

// Query: every section's membership at once (for the "also in X,Y" chips and the
// flat Environments tab's per-env section derivation).
export const useListSectionMemberships = (
    options?: Omit<
        UseQueryOptions<IListSectionMembershipsResponse, Err, IListSectionMembershipsResponse, QueryKey>,
        "queryKey" | "queryFn"
    >
) =>
    useQuery({
        queryKey: envSectionKey.memberships(),
        queryFn: (): Promise<IListSectionMembershipsResponse> => api.get("/sections/memberships"),
        ...options,
    });
