import type { EngineContext } from "../types";
import { evalExpr, getByPath, interpolate, syncFormValues } from "../utils";

export async function executeSetValue(
  step: { type: "set_value"; field: string; value?: unknown; from?: string; expr?: string },
  ctx: EngineContext
): Promise<boolean> {
  let value: unknown;

  if (step.expr) {
    value = evalExpr(step.expr, ctx);
  } else if (step.from) {
    value = ctx.methods.getValues(step.from);
  } else {
    value = step.value;
  }

  ctx.methods.setValue(step.field, value);
  syncFormValues(ctx);
  return true;
}

export async function executeClearField(
  step: { type: "clear_field"; field?: string; fields?: string[] },
  ctx: EngineContext
): Promise<boolean> {
  const fields = step.fields ?? (step.field ? [step.field] : []);
  for (const f of fields) {
    ctx.methods.setValue(f, undefined);
  }
  syncFormValues(ctx);
  return true;
}

// export async function executeValidate(
//     step: { type: "validate"; fields?: string[] },
//     ctx: EngineContext
// ): Promise<boolean> {
//     if (step.fields?.length) {
//         console.log("Validating fields", step.fields);
//         const results = await Promise.all(step.fields.map((f) => ctx.methods.trigger(f)));
//         console.log("results", results);
//         return results.every(Boolean);
//     }
//     return ctx.methods.trigger();
// }

export async function executeValidate(
  step: { type: "validate"; fields?: string[]; row?: boolean; source?: string },
  ctx: EngineContext
): Promise<boolean> {
  if (step.source) {
    const source = step.source;
    let data: any;

    if (source.startsWith("payload.")) {
      const path = source.split(".").slice(1).join(".");
      data = getByPath(ctx.payload, path);
    } else if (source === "formValues") {
      data = ctx.formValues;
    } else if (source.startsWith("formValues.")) {
      const path = source.split(".").slice(1).join(".");
      data = getByPath(ctx.formValues, path);
    } else {
      data = getByPath(ctx.formValues, source);
    }

    if (!data) {
      console.warn(`[ActionEngine] validate: no data at ${source}`);
      return false;
    }

    // Array validation
    if (Array.isArray(data)) {
      if (step.fields) {
        const hasErrors = data.some(row =>
          step.fields!.some(field => !row[field])
        );
        return !hasErrors;
      }
      return true;
    }

    console.warn(`[ActionEngine] validate: source ${source} is not an array`);
    return false;
  }

  if (step.row) {
    const tableName = Object.keys(ctx.formValues).find(
      key => Array.isArray(ctx.formValues[key])
    );

    if (!tableName) {
      console.warn("[ActionEngine] validate row: no array field found in formValues");
      return false;
    }

    const tableData = ctx.formValues[tableName] as any[];

    if (ctx.rowIndex !== undefined) {
      if (step.fields?.length) {
        const paths = step.fields.map(f => `${tableName}.${ctx.rowIndex}.${f}`);
        const results = await Promise.all(paths.map(p => ctx.methods.trigger(p)));
        return results.every(Boolean);
      }
      return ctx.methods.trigger(`${tableName}.${ctx.rowIndex}`);
    }

    if (step.fields?.length) {
      const paths = tableData.flatMap((_: any, i: number) =>
        step.fields!.map(f => `${tableName}.${i}.${f}`)
      );
      const results = await Promise.all(paths.map(p => ctx.methods.trigger(p)));
      return results.every(Boolean);
    }

    return ctx.methods.trigger(tableName);
  }

  if (step.fields?.length) {
    const resolvedFields = step.fields.map(f => interpolate(f, ctx));
    const results = await Promise.all(resolvedFields.map(f => ctx.methods.trigger(f)));
    return results.every(Boolean);
  }

  return ctx.methods.trigger();
}

export async function executeResetForm(
  step: { type: "reset_form"; keepFields?: string[] },
  ctx: EngineContext
): Promise<boolean> {
  const keep: Record<string, unknown> = {};
  if (step.keepFields) {
    for (const f of step.keepFields) {
      keep[f] = ctx.methods.getValues(f);
    }
  }

  ctx.methods.reset();

  if (step.keepFields) {
    for (const [k, v] of Object.entries(keep)) {
      ctx.methods.setValue(k, v);
    }
  }

  syncFormValues(ctx);
  return true;
}

export async function executeSetFieldError(
  step: { type: "set_field_error"; field: string; message: string },
  ctx: EngineContext
): Promise<boolean> {
  const message = interpolate(step.message, ctx);
  const field = interpolate(step.field, ctx);
  ctx.methods.setError(field, { message });
  return true;
}

export async function executeClearFieldError(
  step: { type: "clear_field_error"; field?: string; fields?: string[] },
  ctx: EngineContext
): Promise<boolean> {
  const fields = step.fields ?? (step.field ? [step.field] : []);
  if (fields.length) {
    ctx.methods.clearErrors(fields);
  } else {
    ctx.methods.clearErrors();
  }
  return true;
}