import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { memo, useEffect, useRef } from "react";
import type { ClassicScheme, RenderEmit } from "rete-react-plugin";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../../i18n";
import type { EditorDirection, IEditorNode } from "../../types";
import DynamicNodeIcon from "./dynamic-node-icon";
import { SocketColumn } from "./socket-column";
import { SocketRow } from "./socket-row";

import "./base-node-shell.css";

interface NodeExtraData { width?: number; height?: number }

export interface ApprovalNodeProps<S extends ClassicScheme> {
    readOnly?: boolean;
    styles?: () => any;
    emit: RenderEmit<S>;
    direction: EditorDirection;
    data: S["Node"] & NodeExtraData & { original: IEditorNode };
    onDoubleClick?: (nodeId: string) => void;
    onContextMenu?: (ref: HTMLDivElement, nodeId: string) => void;
}

function sortByIndex<T extends [string, undefined | { index?: number }][]>(entries: T) {
    entries.sort((a, b) => {
        const ai = a[1]?.index ?? 0;
        const bi = b[1]?.index ?? 0;
        return ai - bi;
    });
}

// Status badge color mapping
const STATUS_BADGE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
    "rework": "destructive",      // Orange/Red
    "rejected": "destructive",    // Red
    "completed": "default",        // Green
    "awaiting": "secondary",       // Blue
    "processing": "outline",       // Purple
    "none": "outline",             // Gray
};

const STATUS_BADGE_CLASS: Record<string, string> = {
    "rework": "bg-orange-100 text-orange-800 border-orange-300 dark:bg-orange-900/30 dark:text-orange-400",
    "rejected": "bg-red-100 text-red-800 border-red-300 dark:bg-red-900/30 dark:text-red-400",
    "completed": "bg-green-100 text-green-800 border-green-300 dark:bg-green-900/30 dark:text-green-400",
    "awaiting": "bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-900/30 dark:text-blue-400",
    "processing": "bg-purple-100 text-purple-800 border-purple-300 dark:bg-purple-900/30 dark:text-purple-400",
    "none": "bg-muted text-muted-foreground border-border dark:bg-gray-800/30 dark:text-gray-400",
};

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

interface StartEndNodeContentProps {
    icon: string;
    role: string;
    color?: string;
    isHorizontal: boolean;
}

const StartEndNodeContent = memo<StartEndNodeContentProps>(({ icon, role, color, isHorizontal }) => (
    <div className={cn("flex items-center gap-2 px-3 py-2", !isHorizontal && "w-full! justify-center")}>
        <DynamicNodeIcon
            size={20}
            name={icon}
            strokeWidth={1.5}
            className="text-muted-foreground"
            style={{ color: color ?? "#6a7282" }}
        />
        <span className="text-xs font-semibold whitespace-nowrap">
            {role}
        </span>
    </div>
));
StartEndNodeContent.displayName = "StartEndNodeContent";

interface ApprovalNodeContentProps {
    role: string;
    assignee: string | null;
    approvalStatus: string;
}

const HorizontalApprovalContent = memo<ApprovalNodeContentProps>(({ role, assignee, approvalStatus }) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    return (
        <div className="flex items-center flex-col gap-2 p-3">
            <span className="text-sm font-bold text-center line-clamp-2 max-w-[200px]">
                {role}
            </span>

            {assignee && (
                <div className="text-xs text-muted-foreground text-center line-clamp-1 max-w-[200px]">
                    {assignee}
                </div>
            )}

            <Badge
                variant={STATUS_BADGE_VARIANT[approvalStatus] || "outline"}
                className={cn(
                    "text-xs px-2 py-0.5 font-medium",
                    STATUS_BADGE_CLASS[approvalStatus] || "bg-muted text-muted-foreground border-border"
                )}
            >
                {t(`approval_status.${approvalStatus}`)}
            </Badge>
        </div>
    );
});
HorizontalApprovalContent.displayName = "HorizontalApprovalContent";

const VerticalApprovalContent = memo<ApprovalNodeContentProps>(({ role, assignee, approvalStatus }) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    return (
        <div className="flex flex-col w-full">
            {/* Header section with role */}
            <div className="bg-muted px-3 py-2 rounded-t-lg">
                <span className="text-sm font-bold line-clamp-2">
                    {role}
                </span>
            </div>

            {/* Assignee section with muted background */}
            {assignee && (
                <div className="bg-muted px-3 py-2">
                    <div className="text-xs text-muted-foreground line-clamp-1 max-w-[200px]">
                        {assignee}
                    </div>
                </div>
            )}

            {/* Status badge section */}
            <div className="flex flex-col gap-2 p-3 items-start">
                <Badge
                    variant={STATUS_BADGE_VARIANT[approvalStatus] || "outline"}
                    className={cn(
                        "text-xs px-2 py-0.5 font-medium",
                        STATUS_BADGE_CLASS[approvalStatus] || "bg-muted text-muted-foreground border-border"
                    )}
                >
                    {t(`approval_status.${approvalStatus}`)}
                </Badge>
            </div>
        </div>
    );
});
VerticalApprovalContent.displayName = "VerticalApprovalContent";

// ─────────────────────────────────────────────────────────────────────────────
// Main Component
// ─────────────────────────────────────────────────────────────────────────────

const ApprovalNode = <S extends ClassicScheme>({
    data,
    emit,
    readOnly,
    direction,
    onDoubleClick,
    onContextMenu,
}: ApprovalNodeProps<S>) => {
    const inputs = Object.entries(data.inputs);
    const outputs = Object.entries(data.outputs);

    sortByIndex(inputs);
    sortByIndex(outputs);

    const nodeRef = useRef<HTMLDivElement>(null);

    const { id, label, width, height, original } = data;
    const selected = data.selected ?? false;
    const icon = original.icon;

    const role = (original as any).role || original.title || "Unknown Role";
    const assignee = (original as any).assignee || (original as any).name || null;
    const approvalStatus = ((original as any).approvalStatus || original.status || "none").toLowerCase();

    const isHorizontal = direction === "horizontal";
    const hasInput = inputs.length > 0;
    const hasOutput = outputs.length > 0;

    const isStartOrEndNode = role === "Start" || role === "End";

    useEffect(() => {
        const el = nodeRef.current;
        if (!el) return;

        let lastPointerDownTime = Date.now();

        const handler = (e: PointerEvent) => {
            if (e.button === 2) {
                e.stopPropagation();
                return;
            }
            if (e.button !== 0) return;

            const now = Date.now();
            const timeSinceLast = now - lastPointerDownTime;

            if (timeSinceLast < 200 && timeSinceLast > 0) {
                e.preventDefault();
                e.stopPropagation();
                onDoubleClick?.(id);
            }

            lastPointerDownTime = now;
        };

        const preventAutoZoom = (e: Event) => {
            e.preventDefault();
            e.stopPropagation();
        };

        el.addEventListener("dblclick", preventAutoZoom, { capture: true });
        el.addEventListener("pointerdown", handler, { capture: true });

        return () => {
            el.removeEventListener("dblclick", preventAutoZoom, { capture: true });
            el.removeEventListener("pointerdown", handler, { capture: true });
        };
    }, [id, onDoubleClick]);

    return (
        <div
            data-testid="approval-node"
            data-node-id={id}
            ref={nodeRef}
            className={cn(
                "base-node-shell approval-node",
                `base-node-shell--${direction}`,
                selected && "base-node-shell--selected",
                isHorizontal && !hasInput && "rounded-l-4xl!",
                isHorizontal && !hasOutput && "rounded-r-4xl!",
                isStartOrEndNode && "approval-node--start-end"
            )}
            style={{
                minWidth: isStartOrEndNode ? "auto" : width,
                minHeight: isStartOrEndNode ? "auto" : height
            }}
            onContextMenu={(e) => {
                e.preventDefault();
                onContextMenu?.(nodeRef.current!, id);
            }}
        >
            <div
                className={cn(
                    "base-node-shell__content min-w-full! min-h-full! relative",
                    isHorizontal && !hasInput && "rounded-l-4xl!",
                    isHorizontal && !hasOutput && "rounded-r-4xl!",
                    "border-2 rounded-xl shadow-sm",
                    isHorizontal ? "bg-card" : "bg-muted",
                    !isStartOrEndNode && approvalStatus === "completed" && "border-green-400",
                    !isStartOrEndNode && approvalStatus === "processing" && "border-purple-400",
                    !isStartOrEndNode && approvalStatus === "awaiting" && "border-blue-400",
                    !isStartOrEndNode && approvalStatus === "rework" && "border-orange-400",
                    !isStartOrEndNode && approvalStatus === "rejected" && "border-red-400",
                )}
            >
                {role !== "Start" && isHorizontal && (
                    <SocketRow
                        side="input"
                        readOnly={readOnly}
                        entries={inputs.map(([key, socket]) => [
                            key,
                            { ...socket, label: "" }
                        ]) as any}
                        nodeId={id}
                        emit={emit}
                    />
                )}

                {role !== "Start" && !isHorizontal && (
                    <SocketColumn
                        side="input"
                        readOnly={readOnly}
                        entries={inputs.map(([key, socket]) => [
                            key,
                            { ...socket, label: "" }
                        ]) as any}
                        nodeId={id}
                        emit={emit}
                    />
                )}

                {isStartOrEndNode ? (
                    <StartEndNodeContent
                        icon={icon!}
                        role={role}
                        color={original.color}
                        isHorizontal={isHorizontal}
                    />
                ) : isHorizontal ? (
                    <HorizontalApprovalContent
                        role={role}
                        assignee={assignee}
                        approvalStatus={approvalStatus}
                    />
                ) : (
                    <VerticalApprovalContent
                        role={role}
                        assignee={assignee}
                        approvalStatus={approvalStatus}
                    />
                )}

                {role !== "End" && isHorizontal && (
                    <SocketRow
                        side="output"
                        readOnly={readOnly}
                        entries={outputs.map(([key, socket]) => [
                            key,
                            { ...socket, label: "" }
                        ]) as any}
                        nodeId={id}
                        emit={emit}
                    />
                )}

                {role !== "End" && !isHorizontal && (
                    <SocketColumn
                        side="output"
                        readOnly={readOnly}
                        entries={outputs.map(([key, socket]) => [
                            key,
                            { ...socket, label: "" }
                        ]) as any}
                        nodeId={id}
                        emit={emit}
                    />
                )}
            </div>
        </div>
    );
};

export default memo(ApprovalNode);
