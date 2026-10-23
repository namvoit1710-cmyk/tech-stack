import { useMutation, useQuery, type UseMutationOptions } from "@ldc/tanstack-query";
import type { AxiosError } from "@ldc/api-sdk";

import { api } from "@/lib/api";
import type { IApiErrorBody } from "../../types/api";

// SA-2437: the per-workflow worker-instance pin, persisted server-side
// (AIW_WORKFLOW_WORKER_CONFIG) via /api/v1/workflows/{id}/worker-config.
export interface IWorkerConfigPin {
    instance_id: string;
    host?: string;
}

export interface IWorkerConfigSelection {
    by_worker_type: Record<string, IWorkerConfigPin>;
    by_node: Record<string, IWorkerConfigPin>;
}

export interface IWorkerConfig {
    workflow_id: string;
    mode: "WHOLE_FLOW" | "PER_NODE";
    selection: IWorkerConfigSelection;
    updated_at?: string;
    updated_by?: string;
}

export const workerConfigKeys = {
    one: (workflowId: string) => ["worker-config", workflowId] as const,
};

export const useWorkerConfigQuery = (workflowId: string, enabled = true) =>
    useQuery({
        queryKey: workerConfigKeys.one(workflowId),
        enabled: enabled && !!workflowId,
        // The config only changes when WE save it (then we invalidate). Don't let a
        // window-focus/stale refetch fire while the modal is open — a refetch reset
        // the user's in-progress pick back to the saved value ("can't change it").
        refetchOnWindowFocus: false,
        staleTime: Infinity,
        // The control-plane wraps responses as {status, data}. api.get returns that
        // body verbatim (no auto-unwrap — see workflows.ts using `.data.total`), so
        // unwrap `.data` here to hand callers a bare IWorkerConfig. Without this the
        // modal read `saved.mode`/`saved.selection` off the envelope (undefined) and
        // never re-showed a saved pin on reopen.
        queryFn: async (): Promise<IWorkerConfig> => {
            const res = await api.get<{ data: IWorkerConfig }>(
                `/workflows/${workflowId}/worker-config`,
            );
            return res.data;
        },
    });

export const useSetWorkerConfig = (
    options?: Omit<
        UseMutationOptions<
            unknown,
            AxiosError<IApiErrorBody>,
            { workflowId: string; mode: string; selection: IWorkerConfigSelection }
        >,
        "mutationFn"
    >,
) =>
    useMutation({
        mutationFn: ({ workflowId, mode, selection }: {
            workflowId: string;
            mode: string;
            selection: IWorkerConfigSelection;
        }) => api.put(`/workflows/${workflowId}/worker-config`, { mode, selection }),
        ...options,
    });
