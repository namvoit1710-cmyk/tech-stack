import { useTranslation } from "@ldc/i18n"
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from "@ldc/ui/components/context-menu"
import { ClipboardIcon, CopyIcon, PlusIcon } from "lucide-react"
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../i18n"

interface IEditorContextMenuProps {
    children: React.ReactNode
    handleAddNode?: () => void
    clipboardHandlers: {
        onCopy: () => void
        onPaste: () => void
    }
    readOnly?: boolean
}

const EditorContextMenu = ({ children, handleAddNode, clipboardHandlers, readOnly }: IEditorContextMenuProps) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE)
    const { onCopy, onPaste } = clipboardHandlers

    return (
        <ContextMenu>
            <ContextMenuTrigger asChild>
                {children}
            </ContextMenuTrigger>

            {!readOnly && (
                <ContextMenuContent>
                    <ContextMenuItem
                        onClick={handleAddNode}
                    >
                        <PlusIcon />
                        {t("toolbar.add_node")}
                    </ContextMenuItem>
                    <ContextMenuItem
                        onClick={onCopy}
                    >
                        <CopyIcon />
                        {t("toolbar.copy")}
                    </ContextMenuItem>
                    <ContextMenuItem
                        onClick={onPaste}
                    >
                        <ClipboardIcon />
                        {t("toolbar.paste")}
                    </ContextMenuItem>
                </ContextMenuContent>
            )}
        </ContextMenu>
    )
}

export default EditorContextMenu