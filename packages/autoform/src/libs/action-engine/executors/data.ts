import type { EngineContext } from "../types";
import {
  evalExpr,
  getByPath,
  interpolate,
  interpolateDeep,
  resolveBody,
  syncFormValues,
} from "../utils";
import { serviceRequest } from "../utils/service-request";

export async function executeApiCall(
  step: {
    type: "api_call";
    service?: string;
    method: string;
    endpoint: string;
    body?: "formValues" | "rowData" | Record<string, unknown>;
    headers?: Record<string, string>;
    resultMapping?: Record<string, string>;
    errorMapping?: Record<string, string>;
  },
  ctx: EngineContext
): Promise<boolean> {
  const url = interpolate(step.endpoint, ctx);
  const body = resolveBody(step.body, ctx);

  let data: unknown;

  if (step.service && ctx.services?.[step.service]) {
    data = await serviceRequest(
      ctx.services[step.service]!,
      step.method,
      url,
      body,
      step.headers
    );
  } else {
    const res = await fetch(url, {
      method: step.method,
      headers: { "Content-Type": "application/json", ...step.headers },
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({}));

      if (step.errorMapping) {
        for (const [responsePath, fieldName] of Object.entries(step.errorMapping)) {
          const msg = getByPath(error, responsePath);
          if (msg) ctx.methods.setError(fieldName, { message: String(msg) });
        }
      }

      throw new Error(
        (error as any)?.message ?? `API call failed: ${res.status}`
      );
    }

    data = await res.json().catch(() => null);
  }

  ctx.lastResult = data;

  if (step.resultMapping) {
    for (const [responsePath, formField] of Object.entries(step.resultMapping)) {
      const value = getByPath(data, responsePath);
      if (value !== undefined) {
        ctx.methods.setValue(formField, value, { shouldValidate: true });
      }
    }
    syncFormValues(ctx);
  }

  return true;
}

export async function executeUpdateRow(
  step: {
    type: "update_row";
    data?: Record<string, unknown>;
    fromResult?: Record<string, string>;
  },
  ctx: EngineContext
): Promise<boolean> {
  if (!ctx.updateRow) {
    throw new Error(
      "[ActionEngine] update_row step requires row context (updateRow callback)"
    );
  }

  const partial: Record<string, unknown> = {};

  if (step.data) {
    const interpolated = interpolateDeep(step.data, ctx) as Record<string, unknown>;
    Object.assign(partial, interpolated);
  }

  if (step.fromResult && ctx.lastResult) {
    for (const [responsePath, fieldKey] of Object.entries(step.fromResult)) {
      const value = getByPath(ctx.lastResult, responsePath);
      if (value !== undefined) {
        partial[fieldKey] = value;
      }
    }
  }

  ctx.updateRow(partial);
  return true;
}

export async function executeUpdateByRowId(
  step: {
    type: "update_row_by_id";
    field?: string;
    rowId: string;
    data: Record<string, unknown>;
  },
  ctx: EngineContext
): Promise<boolean> {
  const resolvedRowId = interpolate(step.rowId, ctx);
  const resolvedData = interpolateDeep(step.data, ctx) as Record<string, unknown>;

  const updateRowById = ctx.updateRowById;
  if (!updateRowById) {
    throw new Error(
      "[ActionEngine] update_row_by_id requires updateRowById callback in context"
    );
  }

  if (!resolvedRowId) {
    console.warn("[ActionEngine] update_row_by_id: rowId resolved to empty, skipping");
    return true;
  }

  updateRowById(resolvedRowId, resolvedData);
  return true;
}

export async function executeRefetchOData(
  _step: { type: "refetch_odata" },
  ctx: EngineContext
): Promise<boolean> {
  if (!ctx.refetchData) {
    console.warn("[ActionEngine] refetch_odata: no refetchData callback");
    return true;
  }
  await ctx.refetchData();
  return true;
}

export async function executeRefetchData(
  step: { type: "refetch_data"; target?: string },
  ctx: EngineContext
): Promise<boolean> {
  if (step.target) {
    const fn = ctx.refetchRegistry?.get(step.target);
    if (fn) await fn();
  } else {
    const all = ctx.refetchRegistry?.getAll();
    if (all) {
      await Promise.all(Array.from(all.values()).map(fn => fn()));
    }
  }
  return true;
}

export async function executePoll(
  step: {
    type: "poll";
    service?: string;
    endpoint: string;
    interval?: number;
    maxAttempts?: number;
    until: string;
  },
  ctx: EngineContext
): Promise<boolean> {
  const maxAttempts = step.maxAttempts ?? 30;
  const interval = step.interval ?? 2000;

  for (let i = 0; i < maxAttempts; i++) {
    const url = interpolate(step.endpoint, ctx);

    let data: unknown;

    if (step.service && ctx.services?.[step.service]) {
      data = await ctx.services[step.service]?.fetch(url);
    } else {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`[ActionEngine] Poll request failed: ${res.status}`);
      data = await res.json();
    }

    ctx.lastResult = data;

    const done = evalExpr(step.until, { ...ctx, lastResult: data });
    if (done) return true;

    await new Promise((r) => setTimeout(r, interval));
  }

  throw new Error(
    `[ActionEngine] Polling timeout after ${maxAttempts} attempts`
  );
}
