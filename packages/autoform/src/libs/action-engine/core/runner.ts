import {
  executeApiCall,
  executeClearField,
  executeClearFieldError,
  executeCloseDialog,
  executeCondition,
  executeConfirm,
  executeCustom,
  executeEmitEvent,
  executePoll,
  executeRedirect,
  executeRefetchData,
  executeRefetchOData,
  executeRefresh,
  executeResetForm,
  executeSetFieldError,
  executeSetValue,
  executeToast,
  executeTransform,
  executeTriggerWorkflow,
  executeUpdateByRowId,
  executeUpdateRow,
  executeValidate,
  injectPipelineRunner,
} from "../executors";
import type { ActionConfig, ActionResult, ActionStep, EngineContext } from "../types";

async function executeStep(step: ActionStep, ctx: EngineContext): Promise<boolean> {
  switch (step.type) {
    case "set_value": return executeSetValue(step, ctx);
    case "clear_field": return executeClearField(step, ctx);
    case "validate": return executeValidate(step, ctx);
    case "reset_form": return executeResetForm(step, ctx);
    case "set_field_error": return executeSetFieldError(step, ctx);
    case "clear_field_error": return executeClearFieldError(step, ctx);

    case "api_call": return executeApiCall(step, ctx);
    case "update_row": return executeUpdateRow(step, ctx);
    case "update_row_by_id": return executeUpdateByRowId(step, ctx);
    case "refetch_odata": return executeRefetchOData(step, ctx);
    case "refetch_data": return executeRefetchData(step, ctx);
    case "poll": return executePoll(step, ctx);

    case "toast": return executeToast(step, ctx);
    case "confirm": return executeConfirm(step, ctx);
    case "redirect": return executeRedirect(step, ctx);
    case "close_dialog": return executeCloseDialog(step, ctx);
    case "refresh": return executeRefresh(step, ctx);
    case "emit_event": return executeEmitEvent(step, ctx);

    case "condition": return executeCondition(step, ctx);
    case "custom": return executeCustom(step, ctx);
    case "transform": return executeTransform(step, ctx);

    case "trigger_workflow": return executeTriggerWorkflow(step, ctx);

    default:
      console.warn(`[ActionEngine] Unknown step type: ${(step as any).type}`);
      return true;
  }
}

async function executePipeline(
  steps: ActionStep[],
  ctx: EngineContext
): Promise<boolean> {
  for (const step of steps) {
    const ok = await executeStep(step, ctx);
    if (!ok) return false;
  }
  return true;
}

injectPipelineRunner(executePipeline);

export async function runAction(
  config: ActionConfig,
  ctx: EngineContext
): Promise<ActionResult> {
  try {
    const ok = await executePipeline(config.steps, ctx);
    return { success: ok };
  } catch (error) {
    if (config.onError?.length) {
      const errorCtx: EngineContext = {
        ...ctx,
        lastResult: {
          error: error instanceof Error ? error.message : String(error),
        },
      };
      try {
        await executePipeline(config.onError, errorCtx);
      } catch (onErrorError) {
        console.error("[ActionEngine] onError pipeline also failed:", onErrorError);
      }
    }

    return {
      success: false,
      message: error instanceof Error ? error.message : String(error),
    };
  }
}