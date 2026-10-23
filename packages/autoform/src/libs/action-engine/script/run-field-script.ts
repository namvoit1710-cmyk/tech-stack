import type { IField } from "../../../types/schema";
import type { EngineContext } from "../types";
import type { ActionScriptCtx, ScriptEvent } from "./action-script-ctx";
import { makeScriptCtx } from "./make-script-ctx";

type CompiledScript = (ctx: ActionScriptCtx) => unknown;
const scriptCache = new Map<string, CompiledScript>();

/** Compile a full named async function source. Cached by source. Trust model === evalExpr. */
export function compileScript(source: string): CompiledScript {
    let fn = scriptCache.get(source);
    if (!fn) {
        // Indirect eval (global scope); parenthesised so it returns the function expression.
        fn = (0, eval)(`(${source})`) as CompiledScript;
        scriptCache.set(source, fn);
    }
    return fn;
}

/** Test-only: reset the compile cache. */
export function clearScriptCache(): void {
    scriptCache.clear();
}

/** Resolve field.fieldConfig.script[eventName], build ctx, run it. Errors → ctx.toast + console. */
export async function runFieldScript(
    field: IField,
    eventName: string,
    eventData: Partial<ScriptEvent> | undefined,
    engine: EngineContext,
): Promise<void> {
    const source = field.fieldConfig?.script?.[eventName];
    if (!source) return;
    const ctx = makeScriptCtx(engine, { type: eventName, ...(eventData ?? {}) });
    try {
        await compileScript(source)(ctx);
    } catch (error) {
        console.error(`[runFieldScript] ${field.key}.${eventName} threw`, error);
        ctx.toast(error instanceof Error ? error.message : String(error), "error");
    }
}
