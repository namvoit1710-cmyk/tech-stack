import type { SocketClient } from "@ldc/api-sdk/socket";
import type { BuilderServices } from "../../../hooks/use-builder-services";
export interface RefetchParams {
  endpoint?: string;
  filter?: string;
  orderBy?: string;
  pageSize?: number;
}

export interface SetValueStep {
  type: "set_value";
  field: string;
  value?: unknown;
  from?: string;
  expr?: string;
}

export interface ClearFieldStep {
  type: "clear_field";
  field?: string;
  fields?: string[];
}

export interface ValidateStep {
  type: "validate";
  fields?: string[];
  row?: boolean;
  source?: string;  // "formValues" (default) | "payload.tableData" | "formValues.tblProcess"
}

export interface ResetFormStep {
  type: "reset_form";
  keepFields?: string[];
}

export interface SetFieldErrorStep {
  type: "set_field_error";
  field: string;
  message: string;
}

export interface ClearFieldErrorStep {
  type: "clear_field_error";
  field?: string;
  fields?: string[];
}

export interface ApiCallStep {
  type: "api_call";
  service?: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  endpoint: string;
  body?: "formValues" | "rowData" | Record<string, unknown>;
  headers?: Record<string, string>;
  resultMapping?: Record<string, string>;
  errorMapping?: Record<string, string>;
}

export interface UpdateRowStep {
  type: "update_row";
  data?: Record<string, unknown>;
  fromResult?: Record<string, string>;
}

export interface UpdateRowByIdStep {
  type: "update_row_by_id";
  field?: string;
  rowId: string;
  data: Record<string, unknown>;
}

export interface RefetchODataStep {
  type: "refetch_odata";
}

export interface RefetchDataStep {
  type: "refetch_data";
  service?: string;
  endpoint?: string;
  filter?: string;
  orderBy?: string;
  pageSize?: number;
}

export interface PollStep {
  type: "poll";
  service?: string;
  endpoint: string;
  interval?: number;
  maxAttempts?: number;
  until: string;
}

export interface ToastStep {
  type: "toast";
  message: string;
  variant?: "success" | "error" | "info" | "warning";
}

export interface ConfirmStep {
  type: "confirm";
  message: string;
  title?: string;
}

export interface RedirectStep {
  type: "redirect";
  url: string;
}

export interface CloseDialogStep {
  type: "close_dialog";
}

export interface RefreshStep {
  type: "refresh";
}

export interface EmitEventStep {
  type: "emit_event";
  event: string;
  payload?: Record<string, unknown>;
}

export interface ConditionStep {
  type: "condition";
  expr: string;
  then: ActionStep[];
  else?: ActionStep[];
}

export interface CustomStep {
  type: "custom";
  handler: string;
}

export interface TriggerWorkflowStep {
  type: "trigger_workflow";
  service?: string;
  endpoint: string;
  body?: "formValues" | "rowData" | Record<string, unknown>;
  runIdPath?: string;
  socketNamespace?: string;
  socketChannel?: string;
  socketEventKeyField?: string;
  socketEventKey: string;
  socketErrorKey?: string;
  timeout?: number;
  resultMapping?: Record<string, string>;
}

export interface TransformStep {
  type: "transform";
  expr?: string;
  outputKey?: string;
  outputs?: Record<string, string>;
}

export type ActionStep =
  | SetValueStep
  | ClearFieldStep
  | ValidateStep
  | ResetFormStep
  | SetFieldErrorStep
  | ClearFieldErrorStep
  | ApiCallStep
  | UpdateRowStep
  | UpdateRowByIdStep
  | RefetchODataStep
  | RefetchDataStep
  | PollStep
  | ToastStep
  | ConfirmStep
  | RedirectStep
  | CloseDialogStep
  | RefreshStep
  | EmitEventStep
  | ConditionStep
  | CustomStep
  | TriggerWorkflowStep
  | TransformStep;

export interface ActionConfig {
  action: string;
  label?: string;
  steps: ActionStep[];
  onError?: ActionStep[];
}

export interface EngineContext {
  formValues: Record<string, unknown>;

  methods: {
    setValue: (name: string, value: unknown, options?: Record<string, boolean>) => void;
    getValues: (name?: string) => unknown;
    trigger: (name?: string | string[]) => Promise<boolean>;
    setError: (name: string, error: { message: string }) => void;
    reset: (values?: Record<string, unknown>) => void;
    clearErrors: (name?: string | string[]) => void;
  };

  lastResult?: unknown;

  rowId?: string;
  rowIndex?: number;
  rowData?: Record<string, unknown>;
  updateRow?: (partial: Record<string, unknown>) => void;
  updateRowById?: (rowId: string, partial: Record<string, unknown>) => void;

  refetchRegistry?: {
    get: (key: string) => (() => Promise<void>) | undefined;
    getAll: () => Map<string, () => Promise<void>>;
  };

  customHandlers?: Record<string, (ctx: EngineContext) => Promise<void>>;

  refetchData?: (params?: RefetchParams) => Promise<void>;

  actionSocket?: SocketClient;

  services?: BuilderServices;

  payload: Record<string, unknown>;

  ui: {
    toast: (message: string, variant?: string) => void;
    confirm: (message: string, title?: string) => Promise<boolean>;
    redirect: (url: string) => void;
    closeDialog?: () => void;
    refresh?: () => void;
    emitEvent?: (event: string, payload?: unknown) => void;
  };
}

export interface ActionResult {
  success: boolean;
  message?: string;
  data?: unknown;
}