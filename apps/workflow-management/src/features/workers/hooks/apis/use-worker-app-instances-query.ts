import { apiV2 } from "@/lib/api";
import { useQuery } from "@ldc/tanstack-query";
import type { IWorkerAppInstance } from "../../types/worker-app";
import { workerAppKeys } from "./use-worker-apps-query";

/**
 * Lazy: the aggregate feed (`useQueryWorkerApps`) stays light because it
 * never pulls per-instance rows. Instances are only fetched once a row is
 * expanded and the caller flips `enabled` to true.
 */
export const useQueryWorkerAppInstances = (workerApp: string, enabled: boolean) =>
    useQuery({
        queryKey: workerAppKeys.instances(workerApp),
        enabled,
        queryFn: () => apiV2.get<IWorkerAppInstance[]>(`/worker-apps/${encodeURIComponent(workerApp)}/instances`),
    });
