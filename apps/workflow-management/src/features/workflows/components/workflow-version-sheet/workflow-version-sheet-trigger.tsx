import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { Button } from "@ldc/ui/components/button";
import { HistoryIcon } from "lucide-react";

interface IWorkflowVersionSheetTriggerProps {
    onClick: () => void;
    disabled?: boolean;
}

const WorkflowVersionSheetTrigger = ({
    onClick,
    disabled,
}: IWorkflowVersionSheetTriggerProps) => {
    const unsavedDraft = useEditorStore((s) => s.unsavedDraft);

    const hasUnsavedChanges = !!unsavedDraft;

    return (
        <Button
            variant="ghost"
            size="icon"
            onClick={onClick}
            disabled={disabled}
            className="relative"
        >
            <HistoryIcon className="size-5" />
            {hasUnsavedChanges && (
                <span className="absolute top-1 right-1 size-2 rounded-full bg-amber-500" />
            )}
        </Button>
    );
};

export default WorkflowVersionSheetTrigger;
