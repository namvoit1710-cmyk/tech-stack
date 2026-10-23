import { api, fileApi } from "@/lib/api";
import { pushGatewaySocket } from "@/lib/socket";
import type { BuilderServices } from "@ldc/autoform";
import type { FormToolRef, IApprovalFlowPayload, IInputRequestedPayload, InteractionModalState, InteractionResultType } from "@ldc/form-tool";
import { FormTool, FormToolProvider, InteractionModalEnum } from "@ldc/form-tool";
import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { AlertDialog, AlertDialogContent, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@ldc/ui/components/alert-dialog";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Label } from "@ldc/ui/components/label";
import EnvironmentSelect from "@/features/environments/components/environment-select";
import { RefreshCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";

interface IProps {
    modalState: InteractionModalState | null;
    services: BuilderServices;
}

export function InteractionModalWrapper({ modalState, services }: IProps) {
    const { t } = useTranslation("common");
    const formToolRef = useRef<FormToolRef>(null);
    const [isLargeWrapper, setIsLargeWrapper] = useState(false);
    const [isApprovalFetching, setIsApprovalFetching] = useState(false);

    const payloadType = (modalState?.payload as { type?: string })?.type;
    const isApprovalFlow = payloadType === InteractionModalEnum.APPROVAL_FLOW_VIEWER;
    const isHumanAction = payloadType === InteractionModalEnum.HUMAN_ACTION_REQUESTED;
    const isInputForm = !isApprovalFlow && !isHumanAction;
    // SA-2365: the top-level "run workflow" confirm popup lets the operator pick
    // the environment right before Continue triggers the run. Only this modal —
    // not per-node INPUT prompts mid-run. EnvironmentSelect writes the choice to
    // the editor store; onRunWorkflow reads selectedEnvironmentId on Continue.
    const isWorkflowExecution = payloadType === InteractionModalEnum.WORKFLOW_EXECUTION;

    const instruction = isInputForm
        ? (modalState?.payload as { instruction?: string })?.instruction
        : undefined;

    const approvalPayload = isApprovalFlow ? (modalState?.payload as IApprovalFlowPayload) : null;

    const onResult = (result: InteractionResultType) => modalState?.resolve(result);

    const isOpen = useMemo(() => !!modalState && !isHumanAction, [modalState, isHumanAction]);

    return (
        <FormToolProvider workflowApi={api} fileApi={fileApi}>
            <Dialog open={isOpen}>
                <DialogContent
                    showCloseButton={false}
                    className={cn(
                        "p-4!",
                        "flex flex-col overflow-hidden",
                        isApprovalFlow
                            ? "min-w-[90vw] max-w-[95vw] h-[90vh]"
                            : "min-w-150 max-h-[90vh]",
                        isInputForm && isLargeWrapper ? "sm:max-w-[95vw]!" : ""
                    )}
                >
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            {modalState?.title}
                            {isApprovalFlow && approvalPayload && (
                                <Badge variant={approvalPayload.error ? "destructive" : "default"}>
                                    {approvalPayload.output_port || "In Progress"}
                                </Badge>
                            )}
                        </DialogTitle>
                        {instruction && <DialogDescription>{instruction}</DialogDescription>}
                        {isApprovalFlow && approvalPayload && (
                            <DialogDescription>
                                {approvalPayload.error
                                    ? `Error: ${approvalPayload.error}`
                                    : `Change Request: ${approvalPayload.change_request_id}`}
                            </DialogDescription>
                        )}
                    </DialogHeader>

                    {isOpen && (
                        <FormTool
                            ref={formToolRef}
                            modalState={modalState}
                            services={services}
                            actionSocket={pushGatewaySocket}
                            onResult={onResult}
                            onLargeWrapperChange={setIsLargeWrapper}
                            onFetchingChange={setIsApprovalFetching}
                        />
                    )}

                    {isWorkflowExecution && (
                        <div className="flex flex-col gap-1.5 border-t pt-3">
                            <Label>{t("label.environment", { defaultValue: "Environment" })}</Label>
                            <EnvironmentSelect />
                        </div>
                    )}

                    {isInputForm && (
                        <DialogFooter>
                            <Button variant="outline" onClick={() => formToolRef.current?.onCancel?.()}>
                                {t("button.cancel")}
                            </Button>
                            <Button onClick={() => formToolRef.current?.submit?.()}>
                                {t("button.continue")}
                            </Button>
                        </DialogFooter>
                    )}

                    {isApprovalFlow && approvalPayload && (
                        <DialogFooter>
                            <div className="flex items-center justify-between w-full">
                                <div className="text-sm text-muted-foreground">
                                    {approvalPayload.nodes?.length || 0} nodes,{" "}
                                    {approvalPayload.edges?.length || 0} connections
                                </div>
                                <div className="flex items-center gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => void formToolRef.current?.refresh?.()}
                                        disabled={isApprovalFetching}
                                    >
                                        <RefreshCw className={cn("h-4 w-4 mr-1", isApprovalFetching && "animate-spin")} />
                                        {t("button.refresh")}
                                    </Button>
                                    <Button variant="outline" onClick={() => onResult(null)}>
                                        {t("button.close")}
                                    </Button>
                                </div>
                            </div>
                        </DialogFooter>
                    )}
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!modalState && isHumanAction}>
                <AlertDialogContent className="p-4">
                    <AlertDialogHeader>
                        {modalState?.title && <AlertDialogTitle>{modalState.title}</AlertDialogTitle>}
                    </AlertDialogHeader>

                    <FormTool
                        ref={formToolRef}
                        modalState={modalState}
                        services={services}
                        actionSocket={pushGatewaySocket}
                        onResult={onResult}
                    />

                    <AlertDialogFooter>
                        <Button variant="outline" onClick={() => formToolRef.current?.reject?.()}>
                            {(modalState?.payload as IInputRequestedPayload)?.reject_label || t("button.reject")}
                        </Button>
                        <Button onClick={() => formToolRef.current?.confirm?.()}>
                            {(modalState?.payload as IInputRequestedPayload)?.confirm_label || t("button.confirm")}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </FormToolProvider>
    );
}
