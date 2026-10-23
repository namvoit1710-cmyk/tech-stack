import { useTranslation } from "@ldc/i18n";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { BookOpenIcon, CopyIcon, DeleteIcon, Edit, PlayIcon, XCircleIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useMemo, useRef } from "react";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../i18n";

export enum NodeContextMenuAction {
    Open = "open",
    Execute = "execute",
    Replace = "replace",
    Copy = "copy",
    Duplicate = "duplicate",
    Delete = "delete",
    Close = "close"
}

export type NodeContextMenuActionType = keyof typeof NodeContextMenuAction;

export interface NodeMenuActionEvent {
    action: NodeContextMenuAction;
    nodeId: string;
    nodeData: Record<string, any>;
}


interface INodeContextMenuProps extends ComponentProps<typeof DropdownMenu> {
    onAction?: (action: NodeContextMenuAction) => void;
    readOnly?: boolean;
    anchorEl?: HTMLElement | null;
}

const getMenuActions = () => {
    const actions = [
        { icon: <BookOpenIcon strokeWidth={1.5} />, labelKey: "context_menu.open", action: NodeContextMenuAction.Open },
        { icon: <Edit strokeWidth={1.5} />, labelKey: "context_menu.update_node_info", action: NodeContextMenuAction.Replace },
        { icon: <PlayIcon strokeWidth={1.5} />, labelKey: "context_menu.execute_step", action: NodeContextMenuAction.Execute },
        { icon: <CopyIcon strokeWidth={1.5} />, labelKey: "context_menu.copy", action: NodeContextMenuAction.Copy },
    ];

    return actions;
};

const NodeContextMenu = ({ onAction, readOnly, anchorEl, ...props }: INodeContextMenuProps) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    const menuActions = useMemo(() => getMenuActions(), []);

    const rectRef = useRef<DOMRect | null>(null);
    const rect = useMemo(() => {
        if (anchorEl) {
            rectRef.current = anchorEl.getBoundingClientRect();
        }
        return rectRef.current;
    }, [anchorEl]);

    if (readOnly) {
        return null;
    }

    return (
        <DropdownMenu
            {...props}
            modal={false}
        >
            <DropdownMenuTrigger asChild>
                <div
                    style={{
                        position: "fixed",
                        left: rect?.x ?? 0,
                        top: rect?.y ?? 0,
                        width: rect?.width ?? 0,
                        height: rect?.height ?? 0,
                        pointerEvents: "none",
                    }}
                />
            </DropdownMenuTrigger>

            <DropdownMenuContent className="min-w-fit!">
                {menuActions.map((item, index) => (
                    <DropdownMenuItem
                        key={`${item.action}-${index}`}
                        className="cursor-pointer py-1 whitespace-nowrap"
                        onClick={() => onAction?.(item.action)}
                    >
                        {item.icon}
                        {t(item.labelKey)}
                    </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                {!readOnly && (<DropdownMenuItem
                    variant="destructive"
                    className="cursor-pointer py-1"
                    onClick={() => onAction?.(NodeContextMenuAction.Delete)}
                >
                    <DeleteIcon />
                    {t("context_menu.delete")}
                </DropdownMenuItem>)}
                <DropdownMenuSeparator />

                <DropdownMenuItem
                    onClick={() => onAction?.(NodeContextMenuAction.Close)}
                >
                    <XCircleIcon />
                    {t("context_menu.close")}
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    )
}

export default NodeContextMenu;