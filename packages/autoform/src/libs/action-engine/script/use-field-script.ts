import { useCallback } from "react";
import { useFormContext } from "react-hook-form";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { useOptionalBuilderContext } from "../../../contexts/builder.context";
import type { IField } from "../../../types/schema";
import type { EngineContext } from "../types";
import type { ScriptEvent } from "./action-script-ctx";
import { runFieldScript } from "./run-field-script";

export interface UseFieldScriptOptions {
    row?: {
        rowId?: string; rowIndex?: number; rowData?: Record<string, unknown>;
        updateRow?: (partial: Record<string, unknown>) => void;
        updateRowById?: (rowId: string, partial: Record<string, unknown>) => void;
    };
    toast?: (message: string, variant?: string) => void;
    confirm?: (message: string, title?: string) => Promise<boolean>;
    redirect?: (url: string) => void;
    closeDialog?: () => void;
    refresh?: () => void;
    onEvent?: (event: string, payload?: unknown) => void;
}

const defaultToast = (message: string, variant?: string) => {
    // Shared toast (package/ui) — dispatches to the app's single <ToasterRoot />.
    // The message is the title; the ui toast API is (title, message?, options?).
    if (variant === "error") toast.error(message);
    else if (variant === "success") toast.success(message);
    else if (variant === "warning") toast.warning(message);
    else toast.info(message);
};
const defaultConfirm = (m: string) => Promise.resolve(window.confirm(m));
const defaultRedirect = (url: string) => { window.location.href = url; };

/** Hook: returns a stable runner that executes a control-event script against RHF + builder services. */
export function useFieldScript(options: UseFieldScriptOptions = {}) {
    const methods = useFormContext();
    const builder = useOptionalBuilderContext();
    const services = builder?.services;

    const { row, toast, confirm, redirect, closeDialog, refresh, onEvent } = options;
    const { rowId, rowIndex, rowData, updateRow, updateRowById } = row ?? {};

    return useCallback(
        (field: IField, event: string, eventData?: Partial<ScriptEvent>): Promise<void> => {
            const engine: EngineContext = {
                formValues: methods.getValues(),
                methods: {
                    setValue: methods.setValue,
                    getValues: methods.getValues,
                    trigger: methods.trigger,
                    setError: methods.setError,
                    reset: methods.reset,
                    clearErrors: methods.clearErrors,
                },
                payload: {},
                services,
                rowId,
                rowIndex,
                rowData,
                updateRow,
                updateRowById,
                ui: {
                    toast: toast ?? defaultToast,
                    confirm: confirm ?? defaultConfirm,
                    redirect: redirect ?? defaultRedirect,
                    closeDialog,
                    refresh,
                    emitEvent: onEvent,
                },
            };
            return runFieldScript(field, event, eventData, engine);
        },
        [methods, services, rowId, rowIndex, rowData, updateRow, updateRowById, toast, confirm, redirect, closeDialog, refresh, onEvent],
    );
}
