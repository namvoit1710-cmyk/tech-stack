import { useLanguage } from "@/hooks/use-language";
import type { BuilderRef, FieldValues } from "@ldc/autoform";
import { Builder } from "@ldc/autoform";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import type { ComponentProps } from "react";
import { useRef } from "react";
import { useCreateWorkflow } from "../../hooks/apis/workflows";
import type { ICreateWorkflowPayload } from "../../types/workflows";

interface IWorkflowCreateModalProps extends ComponentProps<typeof Dialog> {
    onSave?: (workflowId: string) => void
    onClose?: () => void
}

const WorkflowCreateModal = (props: IWorkflowCreateModalProps) => {
    const { onSave, ...rest } = props
    const { t } = useLanguage()

    const builderRef = useRef<BuilderRef>(null)

    const { mutate, isPending } = useCreateWorkflow({
        onSuccess: (response) => {
            toast.success(t("notification.success"), t("notification.workflow_created_successfully"))
            onSave?.(response.data.id)
        }
    })

    const handleCreateWorkflow = async (formData: FieldValues) => {
        if (!formData.name) {
            return
        }

        const payload: ICreateWorkflowPayload = {
            name: formData.name,
            description: formData.description,
            input_schema: [],
            output_schema: [],
            nodes: [],
            edges: [],
            metadata: {},
            schema_version: "1.0.0"
        }

        mutate(payload)
    }

    const handleSaveButtonClick = () => {
        builderRef.current?.onSubmit()
    }

    return (
        <Dialog
            {...rest}
        // header={(
        //     <div className="flex items-center justify-start w-full py-2">
        //         <Title>
        //             {t("create_workflow")}
        //         </Title>
        //     </div>
        // )}
        // footer={
        //     <div className="flex items-center justify-end w-full gap-2">
        //         <Button design="Transparent" disabled={isPending} onClick={props.onClose}>
        //             {t("cancel")}
        //         </Button>

        //         <Button
        //             design="Emphasized"
        //             disabled={isPending}
        //             onClick={handleSaveButtonClick}
        //         >
        //             <div className="flex items-center gap-2">
        //                 {isPending && <LoadingSpin />}
        //                 <span>{t("save")}</span>
        //             </div>
        //         </Button>
        //     </div>
        // }
        >
            <DialogContent className="min-w-[400px]">
                <DialogHeader>
                    <DialogTitle>{t("create_workflow")}</DialogTitle>
                </DialogHeader>

                <div className="space-y-1">
                    {props.open && (
                        <Builder
                            ref={builderRef}
                            schema={{
                                fields: [
                                    {
                                        outputType: "string",
                                        key: "name",
                                        fieldConfig: {
                                            fieldWrapper: "FormItemWrapper",
                                            wrapperProps: {
                                                label: t("create_workflow_name_field"),
                                                required: true,
                                                labelSpan: "xl-12 lg-12 md-12 sm-12",
                                                fieldSpan: "xl-12 lg-12 md-12 sm-12",
                                            },
                                            fieldControl: "InputControl",
                                            controlProps: {
                                                className: "w-full",
                                                disabled: isPending,
                                                placeholder: t("enter_workflow_name"),
                                            }
                                        }
                                    },
                                    {
                                        outputType: "string",
                                        key: "description",
                                        fieldConfig: {
                                            fieldWrapper: "FormItemWrapper",
                                            wrapperProps: {
                                                label: t("create_workflow_description_field"),
                                                required: true,
                                                labelSpan: "xl-12 lg-12 md-12 sm-12",
                                                fieldSpan: "xl-12 lg-12 md-12 sm-12",
                                            },
                                            fieldControl: "TextareaControl",
                                            controlProps: {
                                                disabled: isPending,
                                                placeholder: t("enter_workflow_description"),
                                            }
                                        }
                                    }
                                ]
                            }}

                            onSubmit={handleCreateWorkflow}
                        />
                    )}
                </div>

                <DialogFooter>
                    <DialogClose asChild>
                        <Button variant="outline">{t("cancel")}</Button>
                    </DialogClose>
                    <Button onClick={handleSaveButtonClick}>{t("save")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}

export default WorkflowCreateModal