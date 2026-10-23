import type { SocketClient } from "@ldc/api-sdk/socket";
import { useCallback, useMemo, useRef, useState } from "react";
import type { BuilderRef } from "../../../components/builder/builder";
import type { BuilderServices } from "../../../contexts/builder.context";
import { runAction } from "../core/runner";
import type { ActionConfig, EngineContext, RefetchParams } from "../types";

export interface UseActionEngineOptions {
  actions: ActionConfig[];

  builderRef: React.RefObject<BuilderRef | null>;

  actionSocket?: SocketClient;

  services?: BuilderServices;

  customHandlers?: Record<string, (ctx: EngineContext) => Promise<void>>;

  refetchData?: (params?: RefetchParams) => Promise<void>;

  toast?: (message: string, variant?: string) => void;
  confirm?: (message: string, title?: string) => Promise<boolean>;
  redirect?: (url: string) => void;
  closeDialog?: () => void;
  refresh?: () => void;
  onEvent?: (event: string, payload?: unknown) => void;
}

export interface UseActionEngineReturn {
  handleFormActions: (
    actionName: string,
    payload?: Record<string, unknown>
  ) => Promise<void>;

  hasAction: (actionName: string) => boolean;

  isWaitingWorkflow: boolean;
}

function defaultToast(message: string, variant?: string) {
  console.log(`[Toast][${variant ?? "info"}] ${message}`);
}

function defaultConfirm(message: string, _title?: string): Promise<boolean> {
  return Promise.resolve(window.confirm(message));
}

function defaultRedirect(url: string) {
  window.location.href = url;
}

export function useActionEngine(
  options: UseActionEngineOptions
): UseActionEngineReturn {
  const {
    actions,
    builderRef,
    actionSocket,
    services,
    customHandlers,
    refetchData,
    toast = defaultToast,
    confirm = defaultConfirm,
    redirect = defaultRedirect,
    closeDialog,
    refresh,
    onEvent,
  } = options;

  const [isWaitingWorkflow, setIsWaitingWorkflow] = useState(false);
  const isWaitingWorkflowRef = useRef(false);

  const actionMap = useMemo(() => {
    const map = new Map<string, ActionConfig>();
    for (const action of actions) {
      map.set(action.action, action);
    }
    return map;
  }, [actions]);

  const hasAction = useCallback(
    (actionName: string) => actionMap.has(actionName),
    [actionMap]
  );

  const handleFormActions = useCallback(
    async (actionName: string, payload?: Record<string, unknown>) => {
      const config = actionMap.get(actionName);
      if (!config) {
        console.warn(
          `[useActionEngine] No action config for: "${actionName}"`
        );
        return;
      }

      const methods = builderRef.current?.getMethods();
      if (!methods) {
        console.warn("[useActionEngine] Builder ref not available");
        return;
      }

      const hasTriggerWorkflow = config.steps.some(
        (s) => s.type === "trigger_workflow"
      );
      if (hasTriggerWorkflow && isWaitingWorkflowRef.current) {
        console.warn(
          `[useActionEngine] Action "${actionName}" blocked: trigger_workflow already running`
        );
        return;
      }
      if (hasTriggerWorkflow) {
        isWaitingWorkflowRef.current = true;
        setIsWaitingWorkflow(true);
      }

      const ctx: EngineContext = {
        formValues:
          (payload?.formValues as Record<string, unknown>) ??
          methods.getValues(),
        methods: {
          setValue: methods.setValue,
          getValues: methods.getValues,
          trigger: methods.trigger,
          setError: methods.setError,
          reset: methods.reset,
          clearErrors: methods.clearErrors,
        },

        refetchRegistry: builderRef.current?.getRefetchRegistry?.(),

        rowId: payload?.rowId as string | undefined,
        rowIndex: payload?.rowIndex as number | undefined,
        rowData: payload?.rowData as Record<string, unknown> | undefined,
        updateRow: payload?.updateRow as
          | ((partial: Record<string, unknown>) => void)
          | undefined,
        updateRowById: payload?.updateRowById as
          | ((rowId: string, partial: Record<string, unknown>) => void)
          | undefined,

        customHandlers: {
          ...customHandlers,
          ...(payload?.customHandlers as Record<string, (ctx: EngineContext) => Promise<void>> | undefined),
        },
        refetchData: refetchData ?? undefined,
        actionSocket: actionSocket ?? undefined,
        services: services ?? undefined,

        payload: payload ?? {},

        ui: {
          toast,
          confirm,
          redirect,
          closeDialog,
          refresh: refresh ?? (() => builderRef.current?.setRefresh()),
          emitEvent: onEvent,
        },
      };

      try {
        await runAction(config, ctx);
      } finally {
        if (hasTriggerWorkflow) {
          isWaitingWorkflowRef.current = false;
          setIsWaitingWorkflow(false);
        }
      }
    },
    [
      actionMap,
      builderRef,
      actionSocket,
      services,
      customHandlers,
      refetchData,
      toast,
      confirm,
      redirect,
      closeDialog,
      refresh,
      onEvent,
    ]
  );

  return { handleFormActions, hasAction, isWaitingWorkflow };
}