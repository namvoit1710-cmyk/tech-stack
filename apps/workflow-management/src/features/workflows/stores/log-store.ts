import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { IWorkflowLogs } from "../types/workflow-log";

interface IWorkflowLogState {
  logs: IWorkflowLogs[];
  clearLogs: () => void;
  addLog: (log: IWorkflowLogs) => void;
}

export const useWorkflowLogStore = create<IWorkflowLogState>()(
  persist(
    (set) => ({
      logs: [],
      
      addLog: (newLog) => set((state) => {
        const updatedLogs = [newLog, ...state.logs];

        return { logs: updatedLogs.slice(0, 500) };
      }),
      
      clearLogs: () => set({ logs: [] })
    }),
    {
      name: "workflow-logs",
      storage: createJSONStorage(() => localStorage),
    }
  )
);