import type { ActionStep, EngineContext } from "../types";
import { evalExpr } from "../utils";

let executePipelineFn: (steps: ActionStep[], ctx: EngineContext) => Promise<boolean>;

export function injectPipelineRunner(
  fn: (steps: ActionStep[], ctx: EngineContext) => Promise<boolean>
): void {
  executePipelineFn = fn;
}

export async function executeCondition(
  step: { type: "condition"; expr: string; then: ActionStep[]; else?: ActionStep[] },
  ctx: EngineContext
): Promise<boolean> {
  const result = evalExpr(step.expr, ctx);
  const branch = result ? step.then : (step.else ?? []);
  return executePipelineFn(branch, ctx);
}

export async function executeCustom(
  step: { type: "custom"; handler: string },
  ctx: EngineContext
): Promise<boolean> {
  const handler = ctx.customHandlers?.[step.handler];
  if (!handler) {
    throw new Error(
      `[ActionEngine] Custom handler "${step.handler}" not registered`
    );
  }
  await handler(ctx);
  return true;
}