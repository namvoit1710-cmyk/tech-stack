import api, { workflowAgentApi } from "@/lib/api";
import type { AxiosError } from "@ldc/api-sdk";
import type { QueryKey, UseInfiniteQueryOptions, UseMutationOptions, UseQueryOptions } from "@ldc/tanstack-query";
import { queryKeyFactory, useInfiniteQuery, useMutation, useQuery } from "@ldc/tanstack-query";
import type { IApiErrorBody } from "../../types/api";
import type { IExecutionLogDetailResponse } from "../../types/execution-log";
import type { IReplayOpenResponse, IReplayStateResponse, IReplayCloseResponse, IReplayStepPayload, IReplayTimelinePageResponse } from "../../types/replay";
import type { IGetNodeOutputResponse, IResumeTaskPayload, IRunChildrenResponse, IWorkflowExecutionHistoryResponse, IWorkflowExecutionParams } from "../../types/execution";
import type { IWorkflowRatingsResponse, IRunRatingsResponse, IRunRatingView, IRatersResponse, IRateRunResponse } from "../../types/rating";

import type { PatchDataResponse, PatchNodeDataParams } from "../../types/run-workflow";
import type { IWorkflowSessionResponse } from "../../types/workflow-session";
import type { ICreateWorkflowPayload, ICreateWorkflowResponse, ICreateWorkflowWithAIResponse, IDesignTimeSuggestionResponse, IExecuteAIPayload, IExecuteAIResponse, IPublishWorkflowPayload, IPublishWorkflowResponse, IVariableSuggestionResponse, IWorkflowDetailResponse, IWorkflowListResponse, IWorkflowParams, IWorkflowSavePayload, IWorkflowUsersResponse, IWorkflowVersionListResponse } from "../../types/workflows";

const _workflowKey = queryKeyFactory("workflows");

export const workflowKey = {
    ..._workflowKey,
    getAllWorkflows: (queryParams: IWorkflowParams) => [..._workflowKey.lists(), queryParams],
    getAllWorkflowsInfinite: (queryParams: Omit<IWorkflowParams, "$skip">) => [..._workflowKey.lists(), "infinite", queryParams],
    getWorkflowSession: (workflowId: string) => [..._workflowKey.details(), ...["workflow", workflowId, "session"]],
    getAllWorkflowExecutions: (queryParams: IWorkflowExecutionParams) => [..._workflowKey.lists(), "executions", queryParams],
    getWorkflowById: (workflowId: string) => [..._workflowKey.details(), workflowId],
    getWorkflowPublished: (workflowId: string) => [..._workflowKey.details(), workflowId, "published"],
    getWorkflowVersions: (workflowId: string) => [..._workflowKey.details(), workflowId, "versions"],
    getWorkflowVersionsInfinite: (workflowId: string, search: string) => [..._workflowKey.details(), workflowId, "versions", "infinite", { search }],
    getWorkflowByVersion: (workflowId: string, versionId: string) => [..._workflowKey.details(), workflowId, "versions", versionId],
    getNodeDataInfo: (runId: string, nodeId: string) => [..._workflowKey.details(), ...["workflow", runId, "nodes", nodeId, "data-info"]],
    getVariableSuggestions: (workflowId: string, nodeId: string, environmentId?: string) => [..._workflowKey.details(), ...["workflow", workflowId, "nodes", nodeId, "variable-suggestions", environmentId ?? ""]],
    getDesignTimeVariableSuggestions: (workflowId: string, nodeId: string, q?: string, environmentId?: string) => [..._workflowKey.details(), ...["workflow", workflowId, "nodes", nodeId, "design-variable-suggestions", q ?? "", environmentId ?? ""]],
    getNodeOutput: (runId: string, nodeId: string) => [..._workflowKey.details(), ...["workflow", runId, "nodes", nodeId, "output"]],
    users: () => [..._workflowKey.all, "users"] as const,
    executionLog: (runId: string) => [..._workflowKey.all, "execution-log", runId] as const,
    runChildren: (runId: string) => [..._workflowKey.all, "run-children", runId] as const,
    // SA-2741: per-run rating. `ratings()` is the prefix the realtime hook
    // invalidates on a `rating.updated` socket event.
    ratings: () => [..._workflowKey.all, "ratings"] as const,
    workflowRatings: (ids: string[]) => [..._workflowKey.all, "ratings", "workflows", [...ids].sort().join(",")] as const,
    runRatings: (runIds: string[]) => [..._workflowKey.all, "ratings", "runs", [...runIds].sort().join(",")] as const,
    runRaters: (workflowId: string) => [..._workflowKey.all, "ratings", "raters", workflowId] as const,
    runRatersByRun: (runId: string) => [..._workflowKey.all, "ratings", "run-raters", runId] as const,
    // SA-2942: the caller's favorite workflow ids (♥ state + Favorites tab count).
    favorites: () => [..._workflowKey.all, "favorites"] as const,
    replayTimelinePage: (runId: string, sessionId: string, offset: number, limit: number) =>
        [..._workflowKey.all, "replay-timeline", runId, sessionId, { offset, limit }] as const,
}


// Replay debugger (SA-1372) — non-mutating time-travel over a run's event log.
export const useOpenReplay = (
    options?: Omit<UseMutationOptions<IReplayOpenResponse, AxiosError<IApiErrorBody>, string>, "mutationFn">,
) => {
    return useMutation({
        mutationFn: (runId: string): Promise<IReplayOpenResponse> => api.post(`/runs/${runId}/replay`, {}),
        ...options,
    });
};

export const useReplayStep = (
    runId: string,
    sessionId: string,
    options?: Omit<UseMutationOptions<IReplayStateResponse, AxiosError<IApiErrorBody>, IReplayStepPayload>, "mutationFn">,
) => {
    return useMutation({
        mutationFn: (payload: IReplayStepPayload): Promise<IReplayStateResponse> =>
            api.post(`/runs/${runId}/replay/${sessionId}/step`, payload),
        ...options,
    });
};

export const useCloseReplay = (
    options?: Omit<UseMutationOptions<IReplayCloseResponse, AxiosError<IApiErrorBody>, { runId: string; sessionId: string }>, "mutationFn">,
) => {
    return useMutation({
        mutationFn: ({ runId, sessionId }: { runId: string; sessionId: string }): Promise<IReplayCloseResponse> =>
            api.delete(`/runs/${runId}/replay/${sessionId}`),
        ...options,
    });
};

// One page of the replay timeline — the open response ships only the first
// page, so long runs (1M+ events) never travel as one payload. The recorded
// log is immutable per session, so pages cache forever (staleTime: Infinity).
export const useReplayTimelinePage = (
    runId: string,
    sessionId: string,
    offset: number,
    limit: number,
    options?: Omit<UseQueryOptions<IReplayTimelinePageResponse, AxiosError<IApiErrorBody>, IReplayTimelinePageResponse, QueryKey>, "queryKey" | "queryFn">,
) => {
    return useQuery({
        queryKey: workflowKey.replayTimelinePage(runId, sessionId, offset, limit),
        queryFn: (): Promise<IReplayTimelinePageResponse> =>
            api.get(`/runs/${runId}/replay/${sessionId}/timeline`, { params: { offset, limit } }),
        enabled: !!runId && !!sessionId,
        staleTime: Infinity,
        ...options,
    });
};


// Query workflows ---------------------

export const useWorkflowList = (queryParams: IWorkflowParams, options?: Omit<UseQueryOptions<IWorkflowListResponse, AxiosError<IApiErrorBody>, IWorkflowListResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getAllWorkflows(queryParams),
        queryFn: (): Promise<IWorkflowListResponse> => api.get("/workflows", { params: queryParams }),
        ...options
    })
}

const WORKFLOW_INFINITE_PAGE_SIZE = 20;

export const useWorkflowListInfinite = (
    queryParams: Omit<IWorkflowParams, "$skip">,
    options?: { enabled?: boolean }
) => {
    return useInfiniteQuery({
        queryKey: workflowKey.getAllWorkflowsInfinite(queryParams),
        queryFn: ({ pageParam }): Promise<IWorkflowListResponse> =>
            api.get("/workflows", {
                params: {
                    ...queryParams,
                    $skip: pageParam,
                    $top: WORKFLOW_INFINITE_PAGE_SIZE,
                },
            }),
        initialPageParam: 0,
        getNextPageParam: (lastPage, allPages) => {
            const fetched = allPages.length * WORKFLOW_INFINITE_PAGE_SIZE;
            return fetched < (lastPage.data.total ?? 0) ? fetched : undefined;
        },
        ...options,
    })
}

const WORKFLOW_VERSION_PAGE_SIZE = 10;

export const useWorkflowVersionListInfinite = (
    workflowId: string,
    { search }: { search?: string },
    options?: Omit<UseInfiniteQueryOptions<IWorkflowListResponse, AxiosError<IApiErrorBody>>, "queryKey" | "queryFn" | "initialPageParam" | "getNextPageParam">
) => {
    return useInfiniteQuery({
        queryKey: workflowKey.getWorkflowVersionsInfinite(workflowId, search ?? ""),
        queryFn: ({ pageParam }): Promise<IWorkflowListResponse> => // OData version list reuses the workflow list envelope
            api.get("/workflows", {
                params: {
                    $id: workflowId,
                    $skip: pageParam,
                    $top: WORKFLOW_VERSION_PAGE_SIZE,
                    $orderby: "updated_at desc",
                    ...(search ? { $filter: `version contains '${search}'` } : {}),
                },
            }),
        initialPageParam: 0,
        getNextPageParam: (lastPage, allPages) => {
            const fetched = allPages.length * WORKFLOW_VERSION_PAGE_SIZE;
            return fetched < (lastPage.data.total ?? 0) ? fetched : undefined;
        },
        ...options,
        enabled: (options?.enabled ?? true) && !!workflowId,
    });
};

// SA-2670: no interval polling — the Execution History list is kept live by
// realtime socket events (see useExecutionHistoryRealtime), which invalidate
// this query when a run's status/progress changes.
export const useWorkflowExecutionList = (queryParams: IWorkflowExecutionParams, options?: Omit<UseQueryOptions<IWorkflowExecutionHistoryResponse, AxiosError<IApiErrorBody>, IWorkflowExecutionHistoryResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getAllWorkflowExecutions(queryParams),
        queryFn: (): Promise<IWorkflowExecutionHistoryResponse> => api.get("/runs", { params: queryParams }),
        ...options
    })
}

export const useWorkflowById = (workflowId: string, options?: Omit<UseQueryOptions<IWorkflowDetailResponse, AxiosError<IApiErrorBody>, IWorkflowDetailResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getWorkflowById(workflowId),
        queryFn: (): Promise<IWorkflowDetailResponse> => api.get(`/workflows/${workflowId}`),
        enabled: !!workflowId,
        ...options
    })
}

// SA-2252 — published-preferred single-get. Returns the workflow's latest PUBLISHED
// version, falling back to the latest-edited version when none is published (the
// backend `GET /workflows/publish/{id}` route). Used as the detail page's default
// load so opening a workflow shows what's published, not an in-progress draft.
export const useWorkflowPublished = (workflowId: string, options?: Omit<UseQueryOptions<IWorkflowDetailResponse, AxiosError<IApiErrorBody>, IWorkflowDetailResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getWorkflowPublished(workflowId),
        queryFn: (): Promise<IWorkflowDetailResponse> => api.get(`/workflows/publish/${workflowId}`),
        enabled: !!workflowId,
        ...options
    })
}

export const useWorkflowVersionList = (workflowId: string, options?: Omit<UseQueryOptions<IWorkflowVersionListResponse, AxiosError<IApiErrorBody>, IWorkflowVersionListResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getWorkflowVersions(workflowId),
        queryFn: (): Promise<IWorkflowVersionListResponse> => api.get(`/workflows/${workflowId}/versions`),
        enabled: !!workflowId,
        ...options,
    })
}

export const useWorkflowByVersion = (workflowId: string, versionId: string, options?: Omit<UseQueryOptions<IWorkflowDetailResponse, AxiosError<IApiErrorBody>, IWorkflowDetailResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getWorkflowByVersion(workflowId, versionId),
        queryFn: (): Promise<IWorkflowDetailResponse> => api.get(`/workflows/${workflowId}/versions/${versionId}`),
        enabled: !!workflowId && !!versionId,
        ...options,
    })
}

// Query workflow session ---------------------

export const useQueryWorkflowSession = (workflowId: string, options?: Omit<UseQueryOptions<IWorkflowSessionResponse, AxiosError<IApiErrorBody>, IWorkflowSessionResponse, QueryKey>, "queryKey" | "queryFn">) => {
    return useQuery({
        queryKey: workflowKey.getWorkflowSession(workflowId),
        queryFn: (): Promise<IWorkflowSessionResponse> => api.get(`/dev-sessions/workflow/${workflowId}`),
        ...options
    })
}

// Mutation workflow session ------------------------

export const useCreateWorkflowSession = (options?: Omit<UseMutationOptions<{ ok: boolean }, AxiosError<IApiErrorBody>, { workflowId: string, draftGraphVersion: string }>, "mutationFn">) => {
    return useMutation({
        mutationFn: (
            { workflowId, draftGraphVersion }: { workflowId: string, draftGraphVersion: string }): Promise<{ ok: boolean }> => {
            return api.post("/dev-sessions", { workflow_id: workflowId, draft_graph_version: draftGraphVersion })
        },
        ...options
    })
}


// Mutation ---------------------------

export const useCreateWorkflow = (
    options?: Omit<
        UseMutationOptions<
            ICreateWorkflowResponse,
            AxiosError<IApiErrorBody>, ICreateWorkflowPayload
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (payload: ICreateWorkflowPayload): Promise<ICreateWorkflowResponse> =>
            api.post("/workflows", payload),
        ...options
    });
};

// export const useCreateWorkflowWithAI = (
//     options?: Omit<
//         UseMutationOptions<
//             ICreateWorkflowWithAIResponse,
//             AxiosError<IApiErrorBody>, ICreateWorkflowWithAIPayload
//         >,
//         "mutationFn"
//     >
// ) => {
//     return useMutation({
//         mutationFn: async (payload: ICreateWorkflowWithAIPayload): Promise<ICreateWorkflowWithAIResponse> => {
//             const agentUrl = import.meta.env.VITE_WORKFLOW_AGENT_URL || 
//                 "https://smdg-ai-dev-ai-workflow-workflow-supervisor-agent-qas.cfapps.br10.hana.ondemand.com";

//             const response = await fetch(`${agentUrl}/api/v1/execute`, {
//                 method: "POST",
//                 headers: {
//                     "accept": "application/json",
//                     "Content-Type": "application/json",
//                 },
//                 body: JSON.stringify({
//                     message: payload.message,
//                     conv_id: payload.conv_id || `conv_${Date.now()}`,
//                     user_id: payload.user_id || "anonymous",
//                     tenant_id: payload.tenant_id || "default",
//                     source: payload.source || "api",
//                 }),
//             });

//             if (!response.ok) {
//                 throw new Error(`AI workflow creation failed: ${response.statusText}`);
//             }

//             return response.json();
//         },
//         ...options
//     });
// };

export const useCreateWorkflowWithAI = (
    options?: Omit<
        UseMutationOptions<
            ICreateWorkflowWithAIResponse,
            AxiosError<IApiErrorBody>,
            string
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (message: string) =>
            workflowAgentApi.post<ICreateWorkflowWithAIResponse>(
                "/api/v1/execute",
                { message }
            ),
        ...options,
    });
};

// export const useCreateWorkflowWithAI = (
//     options?: Omit<UseMutationOptions<ICreateWorkflowWithAIResponse, AxiosError<IApiErrorBody>, ICreateWorkflowWithAIPayload>, "mutationFn">
// ) => {
//     return useMutation({
//         mutationFn: async (payload: ICreateWorkflowWithAIPayload): Promise<ICreateWorkflowWithAIResponse> => api.post("/execute", payload),
//         ...options
//     })
// }

export const useUpdateWorkflow = (
    options?: Omit<
        UseMutationOptions<
            IWorkflowDetailResponse,
            AxiosError<IApiErrorBody>, { workflowId: string, payload: IWorkflowSavePayload }
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (
            { workflowId, payload }: {
                workflowId: string,
                payload: IWorkflowSavePayload
            }): Promise<IWorkflowDetailResponse> => api.put(`/workflows/${workflowId}`, payload),
        ...options
    })
}

export const usePublishWorkflow = (
    options?: Omit<
        UseMutationOptions<
            IPublishWorkflowResponse,
            AxiosError<IApiErrorBody>, { workflowId: string, payload?: IPublishWorkflowPayload }
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ workflowId, payload }: { workflowId: string, payload?: IPublishWorkflowPayload }): Promise<IPublishWorkflowResponse> => {
            return api.post(`/workflows/publish/${workflowId}`, payload ?? {})
        },
        ...options,
    })
}

export const useUpdateWorkflowPatch = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>, { workflowId: string, payload: Partial<IWorkflowSavePayload> }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ workflowId, payload }: { workflowId: string, payload: Partial<IWorkflowSavePayload> }): Promise<{ ok: boolean }> => {
            return api.patch(`/workflows/${workflowId}`, payload)
        },
        ...options
    })
}

// SA-2751: dedicated lightweight endpoint for manual time — never runs the node
// pipeline on the backend (unlike the general PATCH /workflows/{id}).
export const useSetManualTime = (
    options?: Omit<
        UseMutationOptions<
            { id: string; manual_seconds: number },
            AxiosError<IApiErrorBody>, { workflowId: string, manualSeconds: number }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ workflowId, manualSeconds }: { workflowId: string, manualSeconds: number }): Promise<{ id: string; manual_seconds: number }> => {
            return api.patch(`/workflows/${workflowId}/manual-time`, { manual_seconds: manualSeconds })
        },
        ...options
    })
}

// SA-2576: known users of the workflow app — the owner-picker options.
export const useWorkflowUsers = (
    options?: Omit<
        UseQueryOptions<IWorkflowUsersResponse, AxiosError<IApiErrorBody>, IWorkflowUsersResponse, QueryKey>,
        "queryKey" | "queryFn"
    >
) => {
    return useQuery({
        queryKey: workflowKey.users(),
        queryFn: (): Promise<IWorkflowUsersResponse> => api.get("/users"),
        ...options,
    })
}

// SA-2573: reassign a workflow's owner.
export const useSetWorkflowOwner = (
    options?: Omit<
        UseMutationOptions<
            { id: string, owner: string },
            AxiosError<IApiErrorBody>, { workflowId: string, owner: string }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ workflowId, owner }: { workflowId: string, owner: string }): Promise<{ id: string, owner: string }> => {
            return api.put(`/workflows/${workflowId}/owner`, { owner })
        },
        ...options
    })
}

// SA-2942: my favorite workflow ids, newest first.
export const useFavoriteWorkflowIds = (options?: { enabled?: boolean }) => {
    return useQuery({
        queryKey: workflowKey.favorites(),
        queryFn: (): Promise<{ data: { ids: string[] } }> => api.get("/workflows/favorites"),
        select: (r) => r.data?.ids ?? [],
        ...options,
    })
}

// SA-2942: add (PUT) / remove (DELETE) a workflow from my favorites — idempotent, 204.
export type FavoriteIdsEnvelope = { data: { ids: string[] } };
export type SetFavoriteVars = { workflowId: string, favorite: boolean, name?: string };
export const useSetFavoriteWorkflow = (
    options?: Omit<UseMutationOptions<unknown, AxiosError<IApiErrorBody>, SetFavoriteVars, { previous?: FavoriteIdsEnvelope }>, "mutationFn">
) => {
    return useMutation({
        mutationFn: ({ workflowId, favorite }: SetFavoriteVars) =>
            favorite ? api.put(`/workflows/${workflowId}/favorite`, {}) : api.delete(`/workflows/${workflowId}/favorite`),
        ...options,
    })
}

export const useModifyMainFlowWorkflow = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>, { workflowId: string, main_flow: boolean }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ workflowId, main_flow }: { workflowId: string, main_flow: boolean }): Promise<{ ok: boolean }> => {
            return api.patch(`/workflows/${workflowId}/main-flow`, { main_flow })
        },
        ...options
    })
}

export const useDeleteWorkflow = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>, string
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (workflowId: string): Promise<{ ok: boolean }> => api.delete(`/workflows/${workflowId}`),
        ...options
    })
}

// Execute workflow ---------------------------

export const useRunNode = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>, { workflowId: string, nodeId: string }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ workflowId, nodeId }: { workflowId: string, nodeId: string }): Promise<{ ok: boolean }> => {
            return api.post(`/workflows/${workflowId}/nodes/${nodeId}/test`, {
                input_data: {},
                mock_variables: {}
            })
        },
        ...options
    })
}

// SA-2424: the run config the FE sends to /test/full. `instance_selection` is
// the optional per-node worker-instance pin (debug routing); omitted => no pin.
export interface IRunWorkflowConfig {
    input_data: Record<string, unknown>;
    instance_selection?: {
        mode: "WHOLE_FLOW" | "PER_NODE";
        by_worker_type: Record<string, string>;
        by_node: Record<string, string>;
    };
}

export const useRunWorkflow = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>,
            {
                workflowId: string,
                // SA-2365: environment_id selects the environment whose $env
                // variables are bound to this run.
                config?: { input_data?: Record<string, unknown>; environment_id?: string }
            }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (
            { workflowId, config }: {
                workflowId: string,
                // SA-2365: environment_id selects the environment whose $env
                // variables are bound to this run.
                config?: { input_data?: Record<string, unknown>; environment_id?: string }
            }
        ): Promise<{ ok: boolean }> => {
            return api.post(`/workflows/${workflowId}/test/full`, { ...config })
        },
        ...options
    })
}

export const useStopWorkflow = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>,
            string
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (runId: string): Promise<{ ok: boolean }> => {
            return api.post(`/runs/${runId}/cancel`)
        },
        ...options
    })
}

export const useResumeTask = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>,
            { runId: string, taskId: string, payload?: IResumeTaskPayload }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ runId, taskId, payload }: { runId: string, taskId: string, payload?: IResumeTaskPayload }): Promise<{ ok: boolean }> => {
            return api.post(`/runs/${runId}/tasks/${taskId}/resume`, payload ?? { new_input: {} })
        },
        ...options
    })
}

// Get node detail ---------------------------

export const useGetNodeVariableSuggestions = (
    { runId, nodeId, environmentId }: { runId: string, nodeId: string, environmentId?: string },
    options?: Omit<
        UseQueryOptions<
            IVariableSuggestionResponse,
            AxiosError<IApiErrorBody>, IVariableSuggestionResponse
        >, "queryKey" | "queryFn"
    >
) => {
    return useQuery({
        queryKey: workflowKey.getVariableSuggestions(runId, nodeId, environmentId),
        queryFn: (): Promise<IVariableSuggestionResponse> => api.get(`/runs/${runId}/nodes/${nodeId}/variable-suggestions`, { params: { environment_id: environmentId || undefined } }),
        enabled: !!runId && !!nodeId,
        ...options
    })
}

// Design-time (no-run) variable suggestions. Used on the canvas before a run exists so authors
// get {{$...}} hints while configuring a node. `q` filters server-side beyond the result limit.
export const useGetDesignTimeVariableSuggestions = (
    { workflowId, nodeId, q, environmentId }: { workflowId: string, nodeId: string, q?: string, environmentId?: string },
    options?: Omit<
        UseQueryOptions<
            IDesignTimeSuggestionResponse,
            AxiosError<IApiErrorBody>, IDesignTimeSuggestionResponse
        >, "queryKey" | "queryFn"
    >
) => {
    return useQuery({
        queryKey: workflowKey.getDesignTimeVariableSuggestions(workflowId, nodeId, q, environmentId),
        queryFn: (): Promise<IDesignTimeSuggestionResponse> =>
            api.get(`/workflows/${workflowId}/nodes/${nodeId}/variable-suggestions`, { params: { q: q || undefined, limit: 200, environment_id: environmentId || undefined } }),
        enabled: !!workflowId && !!nodeId,
        ...options
    })
}

export const useGetNodeDataInfo = (
    { runId, nodeId }: { runId: string, nodeId: string },
    options?: Omit<
        UseQueryOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>, string
        >, "queryKey" | "queryFn"
    >
) => {
    return useQuery({
        queryKey: workflowKey.getNodeDataInfo(runId, nodeId),
        queryFn: (): Promise<IWorkflowDetailResponse> => api.get(`/runs/${runId}/nodes/${nodeId}/data-info`),
        enabled: !!runId && !!nodeId,
        ...options
    })
}

export const useGetNodeOutput = (
    { runId, nodeId }: { runId: string, nodeId: string },
    options?: Omit<
        UseQueryOptions<
            IGetNodeOutputResponse,
            AxiosError<IApiErrorBody>, IGetNodeOutputResponse
        >, "queryKey" | "queryFn"
    >
) => {
    return useQuery({
        queryKey: workflowKey.getNodeOutput(runId, nodeId),
        queryFn: (): Promise<IGetNodeOutputResponse> => api.get(`/runs/${runId}/nodes/${nodeId}/output`),
        enabled: !!runId && !!nodeId,
        ...options
    })
}

export const useExecuteAI = (
    options?: Omit<
        UseMutationOptions<
            IExecuteAIResponse,
            AxiosError<IApiErrorBody>,
            IExecuteAIPayload
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (payload: IExecuteAIPayload): Promise<IExecuteAIResponse> => {
            return api.post("/api/v1/execute", {
                message: payload.message,
                conv_id: payload.conv_id ?? "2",
                user_id: payload.user_id ?? "anonymous",
                tenant_id: payload.tenant_id ?? "default",
                source: payload.source ?? "api",
            })
        },
        ...options
    })
}

export const usePatchNodeData = (options?: Omit<
    UseMutationOptions<
        PatchDataResponse,
        AxiosError<IApiErrorBody>,
        PatchNodeDataParams
    >, "mutationFn"
>) => {
    return useMutation({
        mutationFn: async ({ run_id, node_id, task_id, etag, data }) => {
            return api.patch<PatchDataResponse>(
                `/runs/${run_id}/nodes/${node_id}/data${task_id ? `?taskId=${task_id}` : ""}`,
                data,
                {
                    headers: { "If-Match": `${etag}` },
                }
            );
        },
        ...options
    });
};

export const useSubmitNodeData = (
    options?: Omit<
        UseMutationOptions<
            { ok: boolean },
            AxiosError<IApiErrorBody>,
            { runId: string, nodeId: string, taskId?: string }
        >, "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ runId, nodeId, taskId }: { runId: string, nodeId: string, taskId?: string }): Promise<{ ok: boolean }> => {
            return api.post(`/runs/${runId}/nodes/${nodeId}/submit${taskId ? `?taskId=${taskId}` : ""}`)
        },
        ...options
    })
}

export const useRunChildren = (
    runId: string,
    options?: Omit<UseQueryOptions<IRunChildrenResponse, AxiosError<IApiErrorBody>, IRunChildrenResponse, QueryKey>, "queryKey" | "queryFn">
) => {
    return useQuery({
        queryKey: workflowKey.runChildren(runId),
        queryFn: (): Promise<IRunChildrenResponse> => api.get(`/runs/${runId}/children`),
        enabled: !!runId,
        // SA-2670: kept live by realtime socket events (see useExecutionHistoryRealtime).
        ...options,
    });
};

export const useExecutionLogDetail = (
    runId: string,
    options?: Omit<UseQueryOptions<IExecutionLogDetailResponse, AxiosError<IApiErrorBody>, IExecutionLogDetailResponse, QueryKey>, "queryKey" | "queryFn">
) => {
    return useQuery({
        queryKey: workflowKey.executionLog(runId),
        queryFn: (): Promise<IExecutionLogDetailResponse> => api.get(
            `/runs/${runId}/execution-log`,
            {
                params: {
                    offset: 0,
                    include_children: true,
                },
            }
        ),
        enabled: !!runId,
        ...options,
    });
};


// ── SA-2741: per-run star rating ─────────────────────────────────────
// `api` returns the response envelope; `select` unwraps `.data` so callers
// get the payload directly.
type RatingEnvelope<T> = { data: T };

// Aggregate {avg,count} per workflow — Workflows-tab chips + Execution-History
// row chips (batch by the visible workflow ids).
export const useWorkflowRatings = (
    ids: string[],
    options?: Omit<UseQueryOptions<RatingEnvelope<IWorkflowRatingsResponse>, AxiosError<IApiErrorBody>, IWorkflowRatingsResponse, QueryKey>, "queryKey" | "queryFn" | "select">,
) => {
    return useQuery({
        queryKey: workflowKey.workflowRatings(ids),
        queryFn: (): Promise<RatingEnvelope<IWorkflowRatingsResponse>> =>
            api.get("/runs/rating/summary", { params: { ids: ids.join(",") } }),
        select: (r) => r.data ?? {},
        enabled: ids.length > 0,
        ...options,
    });
};

// One run's star view {workflow_id, count, workflow_total, my_starred} — the
// Execution-History Star button + workflow-total chip.
export const useRunRating = (
    runId: string,
    options?: Omit<UseQueryOptions<RatingEnvelope<IRunRatingsResponse>, AxiosError<IApiErrorBody>, IRunRatingView | null, QueryKey>, "queryKey" | "queryFn" | "select">,
) => {
    return useQuery({
        queryKey: workflowKey.runRatings([runId]),
        queryFn: (): Promise<RatingEnvelope<IRunRatingsResponse>> =>
            api.get("/runs/rating/by-run", { params: { ids: runId } }),
        select: (r) => r.data?.[runId] ?? null,
        enabled: !!runId,
        ...options,
    });
};

// SA-2825: BATCH of run rating views — one call for all visible Execution-History
// rows. The endpoint accepts a comma list of ids and returns a dict keyed by run
// id (same shape as useWorkflowRatings), so the per-row N+1 collapses into a single
// request. Shares the `ratings()` key prefix, so a rate/socket invalidation refetches
// just this one query.
export const useRunRatings = (
    runIds: string[],
    options?: Omit<UseQueryOptions<RatingEnvelope<IRunRatingsResponse>, AxiosError<IApiErrorBody>, IRunRatingsResponse, QueryKey>, "queryKey" | "queryFn" | "select">,
) => {
    return useQuery({
        queryKey: workflowKey.runRatings(runIds),
        queryFn: (): Promise<RatingEnvelope<IRunRatingsResponse>> =>
            api.get("/runs/rating/by-run", { params: { ids: runIds.join(",") } }),
        select: (r) => r.data ?? {},
        enabled: runIds.length > 0,
        ...options,
    });
};

// Names of a workflow's raters (NAMES ONLY, privacy) — the hover popover.
export const useRunRaters = (
    workflowId: string,
    options?: Omit<UseQueryOptions<RatingEnvelope<IRatersResponse>, AxiosError<IApiErrorBody>, IRatersResponse, QueryKey>, "queryKey" | "queryFn" | "select">,
) => {
    return useQuery({
        queryKey: workflowKey.runRaters(workflowId),
        queryFn: (): Promise<RatingEnvelope<IRatersResponse>> =>
            api.get("/runs/rating/raters", { params: { workflow_id: workflowId } }),
        select: (r) => r.data ?? { total: 0, names: [] },
        enabled: !!workflowId,
        ...options,
    });
};

// Names of raters of a single RUN (Execution-History hover) — NAMES ONLY.
export const useRunRatersByRun = (
    runId: string,
    options?: Omit<UseQueryOptions<RatingEnvelope<IRatersResponse>, AxiosError<IApiErrorBody>, IRatersResponse, QueryKey>, "queryKey" | "queryFn" | "select">,
) => {
    return useQuery({
        queryKey: workflowKey.runRatersByRun(runId),
        queryFn: (): Promise<RatingEnvelope<IRatersResponse>> =>
            api.get("/runs/rating/run-raters", { params: { run_id: runId } }),
        select: (r) => r.data ?? { total: 0, names: [] },
        enabled: !!runId,
        ...options,
    });
};

// SA-2876: star a run (top-level only; writes to the whole run tree). Permanent —
// there is no unstar. Returns the affected workflow totals.
export const useRateRun = (
    options?: Omit<UseMutationOptions<RatingEnvelope<IRateRunResponse>, AxiosError<IApiErrorBody>, { runId: string }>, "mutationFn">,
) => {
    return useMutation({
        mutationFn: ({ runId }: { runId: string }): Promise<RatingEnvelope<IRateRunResponse>> =>
            api.post(`/runs/${runId}/star`),
        ...options,
    });
};
