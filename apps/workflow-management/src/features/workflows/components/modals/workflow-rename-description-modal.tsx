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

interface IWorkflowRenameDescriptionModalProps extends ComponentProps<typeof Dialog> {
    defaultValues?: { description: string, id: string }
    onSaved: () => void;
}

const WorkflowRenameDescriptionModal = ({ defaultValues, onSaved, ...props }: IWorkflowRenameDescriptionModalProps) => {
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
                description: data.description
            }
        })
    }

    return (
        <Dialog
            {...props}
        >
            <DialogContent className="w-1/3">
                <DialogHeader>
                    <DialogTitle>{t("replace_workflow_description")}</DialogTitle>
                </DialogHeader>

                {props.open &&
                    <Builder
                        defaultValues={defaultValues}
                        schema={{
                            fields: [
                                {
                                    outputType: "string",
                                    key: "description",
                                    fieldConfig: {
                                        fieldControl: "TextareaControl",
                                        controlProps: {
                                            placeholder: t("enter_workflow_description"),
                                            className: "w-full"
                                        },
                                        rules: [
                                            {
                                                method: "required",
                                                message: t("enter_workflow_description")
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

export default WorkflowRenameDescriptionModal