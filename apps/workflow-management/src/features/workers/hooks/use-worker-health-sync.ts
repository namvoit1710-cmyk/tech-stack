import { pushGatewaySocket } from "@/lib/socket";
import { useQueryClient } from "@ldc/tanstack-query";
import { useEffect, useState } from "react";
import { SocketEvent } from "@/features/workflows/types/socket-event";
import type { IWorkerAppAggregate, IWorkerAppHealthPayload } from "../types/worker-app";
import { getSocketEventName, getSocketEventPayload, normalizeSocketPayload } from "@/features/workflows/utils/socket-data-chunk";
import { patchAppAggregate } from "../utils/worker-app-view";
import { workerAppKeys } from "./apis/use-worker-apps-query";

const WORKERS_HEALTH = "workers:health";

export const useWorkerHealthSync = () => {
    const queryClient = useQueryClient();
    const [connected, setConnected] = useState(false);

    useEffect(() => {
        const socket = pushGatewaySocket.connect("/v1/pushgateway");

        const join = () => {
            setConnected(true);
            socket.emit("wait", { key: WORKERS_HEALTH });
        };
        const drop = () => setConnected(false);

        if (socket.connected) join();
        socket.on("connect", join);
        socket.on("reconnect", join);
        socket.on("disconnect", drop);

        const handleDataChunk = (message: unknown) => {
            const normalized = normalizeSocketPayload(message);
            if (!normalized) return;
            if (getSocketEventName(normalized) !== SocketEvent.WorkerAppHealth) return;
            const payload = getSocketEventPayload(normalized) as unknown as IWorkerAppHealthPayload;
            queryClient.setQueryData<IWorkerAppAggregate[]>(
                workerAppKeys.all,
                (prev) => patchAppAggregate(prev ?? [], payload)
            );
        };
        socket.on("data_chunk", handleDataChunk);

        return () => {
            socket.emit("leave", { key: WORKERS_HEALTH });
            socket.off("connect", join);
            socket.off("reconnect", join);
            socket.off("disconnect", drop);
            socket.off("data_chunk", handleDataChunk);
            pushGatewaySocket.disconnect();
        };
    }, [queryClient]);

    return { connected };
};
