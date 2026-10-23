/**
 * The ActionScriptCtx contract as an AMBIENT .d.ts string for Monaco's
 * `addExtraLib` — no `import`/`export`, so its declarations are global in the
 * editor's TS/JS worker. Keep in sync with `action-script-ctx.d.ts` (the
 * type-only source consumed by the runtime). This is the Monaco-facing copy.
 */
export const ACTION_SCRIPT_CTX_DTS = `
interface ScriptEvent { type: string; value?: unknown; name?: string; }
interface GatewayInvoke {
  invoke<T = unknown>(app: string, fn: string, args?: Record<string, unknown>): Promise<T>;
}
interface ActionScriptCtx {
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
declare const ctx: ActionScriptCtx;
`;

export interface CtxHint {
    label: string;
    /** Text inserted at the cursor when the chip is clicked. */
    snippet: string;
}

export const CTX_HINTS: CtxHint[] = [
    { label: "ctx.event", snippet: "ctx.event" },
    { label: "ctx.value", snippet: "ctx.event.value" },
    { label: "ctx.setValue(k,v)", snippet: "ctx.setValue('field', value)" },
    { label: "ctx.formValues", snippet: "ctx.formValues" },
    { label: "await ctx.gateway.invoke(app,fn,args)", snippet: "await ctx.gateway.invoke('app', 'fn', {})" },
    { label: "ctx.refetch(key)", snippet: "await ctx.refetch('key')" },
    { label: "ctx.toast()", snippet: "ctx.toast('message', 'info')" },
    { label: "ctx.emitToHost()", snippet: "ctx.emitToHost('event', {})" },
];
