import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { useLanguage } from "@/hooks/use-language";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@ldc/ui/components/sheet";
import { useUIPanelStore } from "../../stores/ui-panel-stores";
import WorkflowVersionPickerContent from "../workflow-version-picker/workflow-version-picker-content";

interface IWorkflowVersionSheetProps {
    workflowId: string;
    onVersionChange: (versionId: string) => void;
}

const WorkflowVersionSheet = ({
    workflowId,
    onVersionChange,
}: IWorkflowVersionSheetProps) => {
    const { t } = useLanguage();

    const setIsVersionSheetOpen = useUIPanelStore((s) => s.setIsVersionSheetOpen);
    const isVersionSheetOpen = useUIPanelStore((s) => s.isVersionSheetOpen);

    const selectedVersionId = useEditorStore((s) => s.selectedVersionId);

    const handleSelect = (versionId: string) => {
        onVersionChange(versionId);
    };

    return (
        <Sheet open={isVersionSheetOpen} onOpenChange={setIsVersionSheetOpen}>
            <SheetContent side="right" className="w-[400px] gap-0">
                <SheetHeader>
                    <SheetTitle>{t("version_sheet_title")}</SheetTitle>
                    <SheetDescription>{t("version_sheet_description")}</SheetDescription>
                </SheetHeader>

                <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
                    <WorkflowVersionPickerContent
                        workflowId={workflowId}
                        selectedVersionId={selectedVersionId}
                        onSelect={handleSelect}
                    />
                </div>
            </SheetContent>
        </Sheet>
    );
};

export default WorkflowVersionSheet;
