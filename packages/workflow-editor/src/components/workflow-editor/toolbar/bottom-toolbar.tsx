import { useTranslation } from "@ldc/i18n";
import { Button } from "@ldc/ui/components/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { Minimize, RedoIcon, UndoIcon, Workflow, ZoomIn, ZoomOut } from "lucide-react";
import { useMemo } from "react";
import useEditorHistory from "../../../hooks/use-editor-history";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../i18n";
import type { IEditorInstance } from "../../rete-editor";

interface IBottomToolbarProps {
    readOnly?: boolean;
    editorInstance: IEditorInstance | undefined;
}

const BottomToolbar = (props: IBottomToolbarProps) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    const { editorInstance, readOnly } = props;

    const { canRedo, canUndo } = useEditorHistory(editorInstance);

    const menuItems = useMemo(() => {
        return [
            ...(!readOnly ? [
                {
                    icon: <UndoIcon />,
                    label: t("toolbar.undo"),
                    onClick: editorInstance?.undo,
                    disabled: !canUndo,
                },
                {
                    icon: <RedoIcon />,
                    label: t("toolbar.redo"),
                    onClick: editorInstance?.redo,
                    disabled: !canRedo,
                },
            ] : []),
            {
                icon: <Minimize />,
                label: t("toolbar.zoom_fit"),
                onClick: editorInstance?.zoomToFit,
            },
            {
                icon: <ZoomIn />,
                label: t("toolbar.zoom_in"),
                onClick: editorInstance?.zoomIn,
            },
            {
                icon: <ZoomOut />,
                label: t("toolbar.zoom_out"),
                onClick: editorInstance?.zoomOut,
            },
            {
                icon: <Workflow />,
                label: t("toolbar.auto_layout"),
                onClick: editorInstance?.layout,
            },
        ]
    }, [editorInstance, canRedo, canUndo, t])

    return (
        <TooltipProvider>
            <div className="absolute bottom-4 left-4 z-20">
                <div className="flex items-center gap-2">
                    {menuItems.map((item) => (
                        <Tooltip key={item.label}>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon-lg"
                                    variant="outline"
                                    disabled={item.disabled}
                                    className="shadow-lg cursor-pointer"
                                    onClick={item.onClick}
                                    aria-label={item.label}
                                >
                                    {item.icon}
                                </Button>
                            </TooltipTrigger>

                            <TooltipContent side="top">
                                {item.label}
                            </TooltipContent>
                        </Tooltip>
                    ))}
                </div>
            </div>
        </TooltipProvider>
    )
}

export default BottomToolbar;