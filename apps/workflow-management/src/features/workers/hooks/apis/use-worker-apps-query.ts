import { apiV2 } from "@/lib/api";
import { queryKeyFactory, useQuery } from "@ldc/tanstack-query";
import type { IWorkerAppAggregate } from "../../types/worker-app";

const _k = queryKeyFactory("worker-apps");

// Deliberately distinct from node-pallete.ts's queryKeyFactory("workers") —
// a collision there would cross-wire two unrelated caches.
export const workerAppKeys = {
    ..._k,
    instances: (workerApp: string) => [..._k.details(), workerApp, "instances"] as const,
};

export const useQueryWorkerApps = () =>
    useQuery({
        queryKey: workerAppKeys.all,
        queryFn: () => apiV2.get<IWorkerAppAggregate[]>("/worker-apps"),
    });
