import { useLanguage } from "@/hooks/use-language";
import type { BuilderRef, FieldValues, IField } from "@ldc/autoform";
import { Builder } from "@ldc/autoform";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "@ldc/ui/components/dialog";
import { useMemo, useRef } from "react";
import { useEditorStore } from "../../stores/editor-stores";
import { useUIPanelStore } from "../../stores/ui-panel-stores";

interface NodeInfoType {
    label: string;
    description?: string;
    instruction?: string;
}

const NodeEditInfoModal = () => {

    const { t } = useLanguage();

    const isOpenNodesPopup = useUIPanelStore(s => s.isOpenNodesPopup);
    const isOpenNodeInfoEditorModal = useUIPanelStore(s => s.isOpenNodeInfoEditorModal);
    const setIsOpenNodeInfoEditorModal = useUIPanelStore(s => s.setIsOpenNodeInfoEditorModal);

    const selectedNode = useEditorStore(s => s.selectedNode);
    const setSelectedNode = useEditorStore(s => s.setSelectedNode);

    const builderRef = useRef<BuilderRef>(null);

    const schemaForm = useMemo<IField[]>(() => {
        return [
            {
                "key": "label",
                "outputType": "string",
                "fieldConfig": {
                    "fieldWrapper": "FormItemWrapper",
                    "fieldControl": "InputControl",
                    "wrapperProps": {
                        "label": t("nodes.label")
                    },
                    "controlProps": {
                        "placeholder": t("nodes.label_placeholder")
                    },
                    "rules": [
                        {
                            "method": "required",
                            "message": t("nodes.label_required")
                        }
                    ]
                }
            },
            {
                "key": "description",
                "outputType": "string",
                "fieldConfig": {
                    "fieldWrapper": "FormItemWrapper",
                    "fieldControl": "TextareaControl",
                    "wrapperProps": {
                        "label": t("nodes.description")
                    },
                    "controlProps": {
                        "placeholder": t("nodes.description_placeholder"),
                        "rowsLength": 5
                    }
                }
            },
            {
                "key": "instruction",
                "outputType": "string",
                "fieldConfig": {
                    "fieldWrapper": "FormItemWrapper",
                    "fieldControl": "TextareaControl",
                    "wrapperProps": {
                        "label": t("nodes.instruction")
                    },
                    "controlProps": {
                        "placeholder": t("nodes.instruction_placeholder"),
                        "rowsLength": 5
                    }
                }
            }
        ]
    }, []);

    const defaultValues = useMemo(() => {
        return {
            label: selectedNode?.original?.label ?? selectedNode?.original?.name ?? "",
            description: selectedNode?.original?.description || "",
            instruction: selectedNode?.original?.instruction || ""
        }
    }, [selectedNode]);

    const handleUpdateNodeInfo = (data: { label?: string; description?: string; instruction?: string }) => {
        if (!selectedNode) return;

        selectedNode.updateNodeInfo(
            data.label ?? selectedNode.original.title,
            data.description ?? selectedNode.original.description,
            data.instruction ?? selectedNode.original.instruction
        );
    }

    const handleSubmit = (data: FieldValues) => {
        handleUpdateNodeInfo({
            label: data.label,
            description: data.description,
            instruction: data.instruction
        });

        if (!isOpenNodesPopup) {
            setSelectedNode(null);
        }
        setIsOpenNodeInfoEditorModal(false);
    };

    return (
        <Dialog open={isOpenNodeInfoEditorModal} onOpenChange={setIsOpenNodeInfoEditorModal}>
            <DialogContent className="min-w-95vw sm:min-w-150">
                <DialogHeader>
                    <span className="font-semibold">{t("nodes.edit_info")}</span>
                </DialogHeader>

                <div className="flex flex-col flex-2 overflow-y-auto px-1">
                    {selectedNode && (
                        <Builder
                            ref={builderRef}
                            schema={{ fields: schemaForm }}
                            defaultValues={defaultValues}
                            onSubmit={handleSubmit}
                        />
                    )}

                    {!selectedNode && (
                        <div className="p-4 text-center text-sm text-muted-foreground">
                            {t("nodes.no_node_selected")}
                        </div>
                    )}
                </div>

                <DialogFooter className="p-1 flex items-center justify-end gap-2">
                    <Button variant="outline"
                        onClick={() => setIsOpenNodeInfoEditorModal(false)}
                    >
                        {t("cancel")}
                    </Button>

                    <Button
                        onClick={() => builderRef.current?.onSubmit()}
                    >
                        {t("save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default NodeEditInfoModal;