import { useQuery } from "@ldc/tanstack-query";

import { apiV2 } from "@/lib/api";
import type { IWorkerInstance } from "../../types/worker-instance";

export const workerTypeReplicaKeys = {
    all: ["worker-type-replicas"] as const,
    type: (workerType: string) => ["worker-type-replicas", workerType] as const,
};

/**
 * SA-2424: every registered instance (replica) of one worker_type, for the
 * debug worker-instance picker. Proxies WES R6 via
 * GET /api/v2/worker-replicas/{worker_type} (bare array, no envelope).
 */
export const useQueryWorkerTypeReplicas = (workerType: string, enabled = true) =>
    useQuery({
        queryKey: workerTypeReplicaKeys.type(workerType),
        enabled: enabled && !!workerType,
        queryFn: () =>
            apiV2.get<IWorkerInstance[]>(
                `/worker-replicas/${encodeURIComponent(workerType)}`,
            ),
    });
