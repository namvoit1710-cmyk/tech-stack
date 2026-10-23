/**
 * ActionScriptCtx — v1 contract for inline control-event scripts (SA-2177).
 * Single source of truth for runtime injection, Monaco extraLib (later), AI codegen.
 * FLAT surface; make-script-ctx.ts adapts the nested EngineContext into it.
 */

export interface ScriptEvent {
    type: string;          // "onChange" | "onClick" | …
    value?: unknown;       // new control value for onChange/onInput
    name?: string;         // field/control name when present
}

export interface GatewayInvoke {
    /** Invoke a registered Gateway app function. Throws on transport error or status_code >= 400. */
    invoke<T = unknown>(app: string, fn: string, args?: Record<string, unknown>): Promise<T>;
}

export interface ActionScriptCtx {
    readonly event: ScriptEvent;

    readonly formValues: Record<string, unknown>;
    readonly rowData?: Record<string, unknown>;
    readonly rowId?: string;
    readonly rowIndex?: number;

    setValue(field: string, value: unknown, opts?: { shouldValidate?: boolean; shouldDirty?: boolean }): void;
    getValues(field?: string): unknown;
    validate(fields?: string | string[]): Promise<boolean>;
    setError(field: string, message: string): void;
    clearErrors(field?: string | string[]): void;
    reset(values?: Record<string, unknown>): void;

    updateRow(partial: Record<string, unknown>): void;
    updateRowById(rowId: string, partial: Record<string, unknown>): void;

    gateway: GatewayInvoke;

    toast(message: string, variant?: "success" | "error" | "info" | "warning"): void;
    confirm(message: string, title?: string): Promise<boolean>;
    redirect(url: string): void;
    closeDialog(): void;
    refresh(): void;
    emitToHost(event: string, payload?: unknown): void;

    refetch(key?: string): Promise<void>;
    refetchOData(): Promise<void>;
}
