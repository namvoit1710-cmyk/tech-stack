import type { ISocket } from "@ldc/api-sdk/socket";
import type { EngineContext } from "../types";
import { getByPath, interpolate, resolveBody, syncFormValues } from "../utils";
import { serviceRequest } from "../utils/service-request";

interface WaitForSocketOptions {
  socket: ISocket;
  channel: string;
  eventKeyField: string;
  successKey: string;
  errorKey?: string;
  runId?: string;
  timeout: number;
}

function waitForSocketEvent(options: WaitForSocketOptions): Promise<unknown> {
  const { socket, channel, eventKeyField, successKey, errorKey, runId, timeout } = options;

  return new Promise((resolve, reject) => {
    let settled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    const cleanup = () => {
      socket.off(channel, handler);
      clearTimeout(timeoutId);
    };

    const handler = (...args: unknown[]) => {
      const payload = args[0] as Record<string, unknown>;
      if (!payload || typeof payload !== "object") return;

      if (runId && payload.run_id !== undefined && payload.run_id !== runId) {
        return;
      }

      const eventKey = payload[eventKeyField];

      if (eventKey === successKey) {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(payload);
        return;
      }

      if (errorKey && eventKey === errorKey) {
        if (settled) return;
        settled = true;
        cleanup();
        const errorMessage =
          (payload.message as string) ??
          (payload.error as string) ??
          `Workflow failed with event: ${String(eventKey)}`;
        reject(new Error(errorMessage));
        return;
      }
    };

    socket.on(channel, handler);

    timeoutId = setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(
        new Error(
          `[ActionEngine] trigger_workflow timeout after ${timeout}ms waiting for "${successKey}"`
        )
      );
    }, timeout);
  });
}

export async function executeTriggerWorkflow(
  step: {
    type: "trigger_workflow";
    service?: string;
    endpoint: string;
    body?: "formValues" | "rowData" | Record<string, unknown>;
    runIdPath?: string;
    socketNamespace?: string;
    socketRoom?: string;
    socketRoomPrefix?: string;
    socketChannel?: string;
    socketEventKeyField?: string;
    socketEventKey: string;
    socketErrorKey?: string;
    timeout?: number;
    resultMapping?: Record<string, string>;
  },
  ctx: EngineContext
): Promise<boolean> {
  const { actionSocket } = ctx;

  if (!actionSocket) {
    throw new Error(
      "[ActionEngine] trigger_workflow requires actionSocket injected into useActionEngine"
    );
  }

  const namespace = step.socketNamespace ?? "/";
  const socket = actionSocket.connect(namespace);

  const url = interpolate(step.endpoint, ctx);
  const body = resolveBody(step.body, ctx);

  let apiResponse: unknown;

  if (step.service && ctx.services?.[step.service]) {
    apiResponse = await serviceRequest(
      ctx.services[step.service]!,
      "POST",
      url,
      body,
    );
  } else {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(
        (error)?.message ?? `trigger_workflow API failed: ${res.status}`
      );
    }

    apiResponse = await res.json().catch(() => null);
  }

  const runId: string | undefined = step.runIdPath
    ? (getByPath(apiResponse, step.runIdPath) as string)
    : undefined;

  const waitKey = step.socketRoom
    ? interpolate(step.socketRoom, { ...ctx, lastResult: apiResponse })
    : step.socketRoomPrefix && runId
      ? `${step.socketRoomPrefix}${runId}`
      : runId
        ? `run:${runId}`
        : undefined;

  const emitWait = () => {
    if (waitKey) socket.emit("wait", { key: waitKey });
  };

  if (socket.connected) {
    emitWait();
  }
  socket.on("connect", emitWait);

  const socketResult = await waitForSocketEvent({
    socket,
    channel: step.socketChannel ?? "data_chunk",
    eventKeyField: step.socketEventKeyField ?? "_event",
    successKey: step.socketEventKey,
    errorKey: step.socketErrorKey,
    runId,
    timeout: step.timeout ?? 30_000,
  });

  socket.off("connect", emitWait);

  ctx.lastResult = {
    apiResponse,
    socketPayload: socketResult,
    run_id: runId,
  };

  if (step.resultMapping) {
    for (const [payloadPath, formField] of Object.entries(step.resultMapping)) {
      const value = getByPath(socketResult, payloadPath);
      if (value !== undefined) {
        ctx.methods.setValue(formField, value, { shouldValidate: true });
      }
    }
    syncFormValues(ctx);
  }

  return true;
}