import { useCallback, useEffect } from "react";
import { useWorkflowLogStore } from "../stores/log-store";
import { ExecutionStatus, HeavyLogAction, IWorkflowLogs, LightLogAction } from "../types/workflow-log";

interface AddExecutionLogMeta {
  status: ExecutionStatus;
  timestamp: number;
  nodeId?: string;
  nodeName?: string;
  tokensUsed?: number;
  errorMessage?: string;
}

const SYNC_INTERVAL_MS = 10000;
const SYNC_THRESHOLD = 20;

function generateLogId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useWorkflowLogSync(workflowId: string) {
  const addLog = useWorkflowLogStore((s) => s.addLog);
  const clearLogs = useWorkflowLogStore(s => s.clearLogs)

  const addLightLog = useCallback(
    (actionName: LightLogAction) => {
      const log: IWorkflowLogs = {
        id: generateLogId(),
        workflowId,
        timestamp: Date.now(),
        actionName,
      };
      addLog(log);
    },
    [workflowId, addLog]
  );

  const addExecutionLog = useCallback(
    (actionName: HeavyLogAction, meta: AddExecutionLogMeta) => {
      const log: IWorkflowLogs = {
        id: generateLogId(),
        workflowId,
        actionName,
        ...meta,
      };
      addLog(log);
    },
    [workflowId, addLog]
  );

  useEffect(() => {
    return () => {
        clearLogs()
    }
  }, [])

  return { addLightLog, addExecutionLog };
}
