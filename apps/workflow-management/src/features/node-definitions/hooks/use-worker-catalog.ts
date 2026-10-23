// Shared base-worker source for the node-definition builder + create popup.
// Sources from the v1 /node-palette (always available) rather than the v2
// /node-catalog (feature-gated, off by default). Worker items carry worker_id
// (the bare worker_type the backend keys base_worker_id on) plus input_schema /
// output_schema used for locked-defaults + output suggestions.
import { useQueryNodePallete } from "@/features/workflows/hooks/apis/node-pallete";
import { useMemo } from "react";

export interface WorkerOption {
    id: string;
    value: string;
}

export function useWorkerCatalog() {
    const { data, isLoading } = useQueryNodePallete();
    const workerNodes = useMemo<any[]>(() => (data?.data?.categories?.worker ?? []) as any[], [data]);
    const workerOptions = useMemo<WorkerOption[]>(
        () => workerNodes
            .map((w: any) => ({ id: w.worker_id || w.worker_type || w.id, value: w.name }))
            .filter((o: WorkerOption) => !!o.id),
        [workerNodes]
    );
    return { workerNodes, workerOptions, isLoading };
}
