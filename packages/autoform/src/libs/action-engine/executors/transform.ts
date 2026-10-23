import type { EngineContext, TransformStep } from "../types";
import { evalExpr } from "../utils";

export async function executeTransform(
  step: TransformStep,
  ctx: EngineContext
): Promise<boolean> {
  if (step.outputs) {
    const result: Record<string, unknown> = {};
    for (const [key, expr] of Object.entries(step.outputs)) {
      result[key] = evalExpr(expr, ctx);
    }
    ctx.lastResult = result;
  } else if (step.expr) {
    const value = evalExpr(step.expr, ctx);
    const outputKey = step.outputKey ?? "result";
    ctx.lastResult = { [outputKey]: value };
  } else {
    console.warn("[ActionEngine] transform step has no expr or outputs");
  }

  return true;
}
