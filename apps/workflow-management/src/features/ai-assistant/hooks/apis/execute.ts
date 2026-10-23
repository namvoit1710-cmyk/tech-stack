import { workflowDesignerAgentApi } from "@/lib/api";
import { AxiosError } from "@ldc/api-sdk";
import { useMutation, UseMutationOptions } from "@ldc/tanstack-query";
import type {
    IApiErrorBody,
    IWorkflowDesignerExecutePayload,
    IWorkflowDesignerExecuteResponse,
} from "../../types";

// SA-1582: call the Workflow Designer Agent (SA-1437) directly.
// The agent continues a conversation by conv_id on the checkpointer, so a
// clarifying answer is just another /execute with the SAME conv_id — there is
// no HITL interrupt, hence no /resume call for this agent.
const DESIGNER_EXECUTE_PATH = "/api/v1/execute";

export const useCreateWorkflowWithAI = (
    options?: Omit<
        UseMutationOptions<
            IWorkflowDesignerExecuteResponse,
            AxiosError<IApiErrorBody>,
            IWorkflowDesignerExecutePayload
        >,
        "mutationFn"
    >,
) => {
    return useMutation({
        mutationFn: (payload: IWorkflowDesignerExecutePayload) =>
            workflowDesignerAgentApi.post<IWorkflowDesignerExecuteResponse>(
                DESIGNER_EXECUTE_PATH,
                payload,
            ),
        ...options,
    });
};
