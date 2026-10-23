/**
 * @description Sync workflow logs to the server. 
 * Will be update in the next version. 
 * Currently, we are not using this API.
 */

import api from "@/lib/api";
import type { IApiErrorBody } from "../../types/api";
import type { IWorkflowLogs } from "../../types/workflow-log";
import { useMutation, UseMutationOptions } from "@ldc/tanstack-query";
import { AxiosError } from "@ldc/api-sdk";

interface ISyncLogsPayload {
  workflowId: string;
  logs: IWorkflowLogs[];
}

interface ISyncLogsResponse {
  ok: boolean;
  syncedIds: string[];
}

export const useSyncWorkflowLogs = (
  options?: Omit<
    UseMutationOptions<ISyncLogsResponse, AxiosError<IApiErrorBody>, ISyncLogsPayload>,
    "mutationFn"
  >
) => {
  return useMutation({
    mutationFn: ({ workflowId, logs }: ISyncLogsPayload): Promise<ISyncLogsResponse> =>
      api.post(`/workflows/${workflowId}/logs/sync`, { logs }),
    ...options,
  });
};
