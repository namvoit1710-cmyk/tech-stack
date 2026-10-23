import type { ClassicScheme, RenderEmit } from "rete-react-plugin";

import type { EditorDirection, IEditorNode, NodeExecutionStatus } from "../../types";
import { SocketColumn } from "./socket-column";
import { SocketRow } from "./socket-row";

import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { CheckIcon, InfoIcon, XIcon } from "lucide-react";
import { memo, useEffect, useRef } from "react";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../../i18n";
import "./base-node-shell.css";
import DynamicNodeIcon from "./dynamic-node-icon";
import LoadingSpin from "./loading-spin/loading-spin";

interface NodeExtraData { width?: number; height?: number }

export interface BaseNodeShellProps<S extends ClassicScheme> {
    readOnly?: boolean;
    styles?: () => any;
    emit: RenderEmit<S>;
    direction: EditorDirection;
    data: S["Node"] & NodeExtraData & { original: IEditorNode };
    onDoubleClick?: (nodeId: string) => void;
    onContextMenu?: (ref: HTMLDivElement, nodeId: string) => void;
}

function sortByIndex<T extends [string, undefined | { index?: number }]>(
    entries: T[]
): T[] {
    return [...entries].sort((a, b) => {
        const ai = a[1]?.index ?? 0;
        const bi = b[1]?.index ?? 0;
        return ai - bi;
    });
}

const EXECUTION_BORDER: Record<NodeExecutionStatus, string> = {
    idle: "",
    executing: "node--executing",
    completed: "node--completed",
    failed: "node--failed",
};

const BaseNodeShell = <S extends ClassicScheme>({
    data,
    emit,
    readOnly,
    direction,
    onDoubleClick,
    onContextMenu,
}: BaseNodeShellProps<S>) => {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    const inputs = sortByIndex(Object.entries(data.inputs));
    const outputs = sortByIndex(Object.entries(data.outputs));

    const nodeRef = useRef<HTMLDivElement>(null);

    const { id, label, width, height, original } = data;
    const selected = data.selected ?? false;
    const status: NodeExecutionStatus = (data as any).executionStatus ?? "idle";
    const icon = original.icon;
    const description = original.description ?? "";
    const instruction = original.instruction ?? "";

    const isHorizontal = direction === "horizontal";
    const hasInput = inputs.length > 0;
    const hasOutput = outputs.length > 0;

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
    }, [id]);

    return (
        <div
            data-testid="node"
            data-node-id={id}
            ref={nodeRef}
            className={cn(
                "base-node-shell",
                `base-node-shell--${direction}`,
                selected && "base-node-shell--selected",
                isHorizontal && !hasInput && "rounded-l-4xl!",
                isHorizontal && !hasOutput && "rounded-r-4xl!",
            )}
            style={{ minWidth: width, minHeight: height }}
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
                    EXECUTION_BORDER[status]
                )}
            >
                {isHorizontal && (
                    <>
                        <SocketRow
                            side="input"
                            readOnly={readOnly}
                            entries={inputs as any}
                            nodeId={id}
                            emit={emit}
                        />
                    </>
                )}

                {!isHorizontal && (
                    <>
                        <SocketColumn
                            side="input"
                            readOnly={readOnly}
                            entries={inputs as any}
                            nodeId={id}
                            emit={emit}
                        />
                    </>
                )}

                <div className={cn("base-node-shell__title flex items-center flex-col", !isHorizontal && "p-1 w-full!")} data-testid="title">
                    <div className="flex size-6 items-center justify-center">
                        <DynamicNodeIcon
                            size={24}
                            name={icon!}
                            strokeWidth={1.5}
                            className="text-muted-foreground"
                            style={{ color: original.color ?? "#6a7282" }}
                        />
                    </div>
                    <span className="text-center line-clamp-2 max-w-[200px]">{original?.title ?? label}</span>
                </div>

                {(description || instruction) && (
                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <div className="absolute top-2 left-2">
                                    <InfoIcon className="text-muted-foreground size-4" />
                                </div>
                            </TooltipTrigger>
                            <TooltipContent>
                                <div className="flex flex-col items-start gap-1">
                                    {description && (
                                        <div>
                                            <span className="font-semibold">{t("description")}</span>
                                            <p className="text-muted-foreground">{description}</p>
                                        </div>
                                    )}
                                    {instruction && (
                                        <div>
                                            <span className="font-semibold">{t("instruction")}</span>
                                            <p className="text-muted-foreground">{instruction}</p>
                                        </div>
                                    )}
                                </div>
                            </TooltipContent>
                        </Tooltip>
                    </TooltipProvider>
                )}

                <div className={cn("absolute bottom-2 right-2", !isHorizontal && "top-2 right-2!")}>
                    {status === "executing" && (
                        <LoadingSpin />
                    )}
                    {status === "completed" && (
                        <CheckIcon className="text-[#34d399] size-4.5" />
                    )}
                    {status === "failed" && (
                        <XIcon className="text-[#f87171] size-4.5" />
                    )}
                </div>

                {isHorizontal && (
                    <>
                        <SocketRow
                            side="output"
                            readOnly={readOnly}
                            entries={outputs as any}
                            nodeId={id}
                            emit={emit}
                        />
                    </>
                )}

                {!isHorizontal && (
                    <>
                        <SocketColumn
                            side="output"
                            readOnly={readOnly}
                            entries={outputs as any}
                            nodeId={id}
                            emit={emit}
                        />
                    </>
                )}
            </div>


        </div>
    );
}

export default memo(BaseNodeShell);