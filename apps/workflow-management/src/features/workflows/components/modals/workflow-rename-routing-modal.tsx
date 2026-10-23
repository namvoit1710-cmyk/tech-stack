import { useLanguage } from "@/hooks/use-language";
import type { BuilderRef, FieldValues } from "@ldc/autoform";
import { Builder } from "@ldc/autoform";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { LoadingSpin } from "@ldc/workflow-editor";
import type { ComponentProps } from "react";
import { useRef } from "react";
import { useUpdateWorkflowPatch } from "../../hooks/apis/workflows";

interface IWorkflowRenameRoutingPathModalProps extends ComponentProps<typeof Dialog> {
    defaultValues?: { routing_path: string, id: string }
    onSaved: () => void;
}

const WorkflowRenameRoutingPathModal = ({ defaultValues, onSaved, ...props }: IWorkflowRenameRoutingPathModalProps) => {
    const { t } = useLanguage();

    const builderRef = useRef<BuilderRef>(null);

    const { mutate, isPending } = useUpdateWorkflowPatch({
        onSuccess: () => {
            onSaved();
            toast.success(t("notification.workflow_updated_successfully"));
        },
        onError: (error) => {
            toast.error(error.message || t("notification.workflow_update_failed"));
        }
    })

    const handleSubmitReplaceName = (data: FieldValues) => {
        if (!defaultValues?.id) return;

        mutate({
            workflowId: defaultValues.id,
            payload: {
                routing_path: data.routing_path
            }
        })
    }

    return (
        <Dialog
            {...props}
        >
            <DialogContent className="w-1/3">
                <DialogHeader>
                    <DialogTitle>{t("setting_routing_path")}</DialogTitle>
                </DialogHeader>

                {props.open &&
                    <Builder
                        defaultValues={defaultValues}
                        schema={{
                            fields: [
                                {
                                    outputType: "string",
                                    key: "routing_path",
                                    fieldConfig: {
                                        fieldControl: "InputControl",
                                        controlProps: {
                                            placeholder: t("enter_routing_path"),
                                            className: "w-full"
                                        },
                                        rules: [
                                            {
                                                method: "required",
                                                message: t("enter_routing_path")
                                            }
                                        ]
                                    },
                                }
                            ]
                        }}
                        onSubmit={handleSubmitReplaceName}
                        ref={builderRef}
                    />
                }

                <DialogFooter className="p-2">
                    <DialogClose asChild>
                        <Button variant="outline" disabled={isPending}>{t("cancel")}</Button>
                    </DialogClose>
                    <Button
                        disabled={isPending}
                        onClick={() => {
                            builderRef.current?.onSubmit();
                        }}
                    >
                        {isPending ? <LoadingSpin /> : t("nodes.replace")}
                    </Button>
                </DialogFooter>
            </DialogContent>

        </Dialog>
    )
}

export default WorkflowRenameRoutingPathModal