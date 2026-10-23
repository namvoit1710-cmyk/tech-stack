import { env } from "@/env";
import type { IDesignStreamEvent } from "../../types";

const STREAM_PATH = "/api/v1/design/stream";

export interface StreamDesignParams {
    message: string;
    convId?: string | null;
    // SA-1582 identity, forwarded for parity with the /execute path.
    userId?: string;
    tenantId?: string;
    source?: string;
    signal?: AbortSignal;
    /** Called for every event as it arrives (progress + the terminal done/error). */
    onEvent: (event: IDesignStreamEvent) => void;
}

// Same host resolution as the designer-agent execute client (SA-1582): the
// dedicated designer URL when set, otherwise the supervisor URL.
const DESIGNER_BASE_URL =
    env.PUBLIC_WORKFLOW_DESIGNER_AGENT_API_URL ??
    env.PUBLIC_WORKFLOW_SUPERVISOR_AGENT_API_URL;

/**
 * Open the workflow-designer SSE stream and drive `onEvent` for each step.
 *
 * Uses `fetch` + a stream reader (not `EventSource`) so we can thread `conv_id`
 * and abort on unmount. Resolves with the terminal `done` event; throws if the
 * server emits an `error` event or the stream ends without a result.
 */
export const streamWorkflowDesign = async ({
    message,
    convId,
    userId,
    tenantId,
    source,
    signal,
    onEvent,
}: StreamDesignParams): Promise<IDesignStreamEvent> => {
    const url = new URL(DESIGNER_BASE_URL + STREAM_PATH);
    url.searchParams.set("message", message);
    if (convId) url.searchParams.set("conv_id", convId);
    if (userId) url.searchParams.set("user_id", userId);
    if (tenantId) url.searchParams.set("tenant_id", tenantId);
    if (source) url.searchParams.set("source", source);

    const response = await fetch(url.toString(), {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        signal,
    });

    if (!response.ok || !response.body) {
        throw new Error(`Design stream failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done: IDesignStreamEvent | null = null;

    const handleBlock = (block: string) => {
        const dataLine = block
            .split("\n")
            .find((line) => line.startsWith("data:"));
        if (!dataLine) return;

        const payload = dataLine.slice("data:".length).trim();
        if (!payload) return;

        let parsed: IDesignStreamEvent;
        try {
            parsed = JSON.parse(payload);
        } catch {
            return;
        }

        onEvent(parsed);
        if (parsed.event === "done") done = parsed;
        if (parsed.event === "error") {
            throw new Error(parsed.message || "Design failed");
        }
    };

    while (true) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;

        buffer += decoder.decode(value, { stream: true });

        let separator = buffer.indexOf("\n\n");
        while (separator !== -1) {
            const block = buffer.slice(0, separator);
            buffer = buffer.slice(separator + 2);
            handleBlock(block);
            separator = buffer.indexOf("\n\n");
        }
    }

    if (buffer.trim()) handleBlock(buffer);

    if (!done) {
        throw new Error("Design stream ended without a result");
    }
    return done;
};
