import type { SocketDataChunkPayload, SocketMessageEnvelope } from "../types/socket-event";

const isRecord = (value: unknown): value is Record<string, unknown> => {
    return typeof value === "object" && value !== null;
};

const parseSocketRecord = (value: unknown): Record<string, unknown> | null => {
    if (typeof value === "string") {
        try {
            const parsed: unknown = JSON.parse(value);
            return isRecord(parsed) ? parsed : null;
        } catch {
            return null;
        }
    }

    return isRecord(value) ? value : null;
};

export const normalizeSocketPayload = (socketMessage: unknown): SocketDataChunkPayload | null => {
    const envelope: SocketMessageEnvelope | null = parseSocketRecord(socketMessage);
    if (!envelope) {
        return null;
    }

    const nestedPayload = parseSocketRecord(envelope.data);
    return nestedPayload ?? envelope;
};

export const getSocketEventName = (payload: SocketDataChunkPayload): string => {
    const nestedPayload = parseSocketRecord(payload.data);

    const rawEvent = [
        nestedPayload?.event_type,
        payload.event_type,
        nestedPayload?.eventType,
        payload.eventType,
        payload._event,
        payload.event,
    ].find((value): value is string => typeof value === "string" && value.length > 0) ?? "";

    return rawEvent.replace(/^workflow:/, "");
};

export const getSocketEventPayload = (payload: SocketDataChunkPayload): Record<string, unknown> => {
    return parseSocketRecord(payload.data) ?? payload;
};