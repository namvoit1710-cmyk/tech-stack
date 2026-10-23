import type { EngineContext } from "../types";

export function evalExpr(expr: string, ctx: EngineContext): unknown {
  const { formValues, lastResult, rowData, payload } = ctx;
  try {
    const fn = new Function(
      "formValues",
      "lastResult",
      "rowData",
      "payload",
      "Math",
      `"use strict"; return (${expr});`
    );
    return fn(formValues, lastResult, rowData, payload, Math);
  } catch (error) {
    console.warn(`[ActionEngine] Expression eval failed: "${expr}"`, error);
    return undefined;
  }
}

export function interpolate(template: string, ctx: EngineContext): string {
  return template.replace(/\$\{([^}]+)\}/g, (_, expr) => {
    const value = evalExpr(expr, ctx);
    return value !== undefined && value !== null ? String(value) : "";
  });
}

export function interpolateDeep(value: unknown, ctx: EngineContext): unknown {
  if (typeof value === "string") {
    if (value.startsWith("$ref:")) {
      const path = value.slice(5);
      return evalExpr(path, ctx);
    }
    return interpolate(value, ctx);
  }
  if (Array.isArray(value)) {
    return value.map((item) => interpolateDeep(item, ctx));
  }
  if (value !== null && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      result[k] = interpolateDeep(v, ctx);
    }
    return result;
  }
  return value;
}

export function getByPath(obj: unknown, path: string): unknown {
  return path.split(".").reduce((acc: any, key) => acc?.[key], obj);
}

export function resolveBody(
  body: "formValues" | "rowData" | Record<string, unknown> | undefined,
  ctx: EngineContext
): unknown {
  if (body === undefined) return undefined;
  if (body === "formValues") return ctx.formValues;
  if (body === "rowData") return ctx.rowData;
  return interpolateDeep(body, ctx);
}

export function syncFormValues(ctx: EngineContext): void {
  ctx.formValues = ctx.methods.getValues() as Record<string, unknown>;
}