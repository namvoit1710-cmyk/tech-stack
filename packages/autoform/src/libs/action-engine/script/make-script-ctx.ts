import type { EngineContext } from "../types";
import type { ActionScriptCtx, ScriptEvent } from "./action-script-ctx";
import { makeGatewayInvoke } from "./gateway-invoke";

const NOOP = (): void => undefined;
const RESOLVED = (): Promise<void> => Promise.resolve();

/** Flatten the nested EngineContext (ctx.methods.* / ctx.ui.*) into the flat ActionScriptCtx. Pure. */
export function makeScriptCtx(engine: EngineContext, event: ScriptEvent): ActionScriptCtx {
    const { methods, ui } = engine;
    return {
        event,
        formValues: engine.formValues,
        rowData: engine.rowData,
        rowId: engine.rowId,
        rowIndex: engine.rowIndex,

        setValue: (field, value, opts) => methods.setValue(field, value, opts),
        getValues: (field) => methods.getValues(field),
        validate: (fields) => methods.trigger(fields),
        setError: (field, message) => methods.setError(field, { message }),
        clearErrors: (field) => methods.clearErrors(field),
        reset: (values) => methods.reset(values),

        updateRow: (partial) => (engine.updateRow ? engine.updateRow(partial) : NOOP()),
        updateRowById: (rowId, partial) => (engine.updateRowById ? engine.updateRowById(rowId, partial) : NOOP()),

        gateway: makeGatewayInvoke(engine.services),

        toast: (message, variant) => ui.toast(message, variant),
        confirm: (message, title) => ui.confirm(message, title),
        redirect: (url) => ui.redirect(url),
        closeDialog: () => (ui.closeDialog ? ui.closeDialog() : NOOP()),
        refresh: () => (ui.refresh ? ui.refresh() : NOOP()),
        emitToHost: (evt, payload) => (ui.emitEvent ? ui.emitEvent(evt, payload) : NOOP()),

        refetch: (key) => (key ? engine.refetchRegistry?.get(key)?.() ?? RESOLVED() : engine.refetchData?.() ?? RESOLVED()),
        refetchOData: () => engine.refetchData?.() ?? RESOLVED(),
    };
}
