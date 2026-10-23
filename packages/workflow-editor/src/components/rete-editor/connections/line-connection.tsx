import { useTranslation } from "@ldc/i18n";
import { Button } from "@ldc/ui/components/button";
import { TrashIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AreaPlugin } from "rete-area-plugin";
import { Presets } from "rete-react-plugin";
import styled from "styled-components";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../i18n";
import type { BaseNode } from "../nodes/base-node";
import type { AreaExtra, Connection, EditorDirection, NodeExecutionStatus, Schemes } from "../types";
import { computeStepPath } from "./path-router";

const { useConnection } = Presets.classic;

const Svg = styled.svg`
  overflow: visible !important;
  position: absolute;
  pointer-events: none;
  width: 9999px;
  height: 9999px;
`;

const Path = styled.path<{
    styles?: (props: any) => any;
    $stroke?: string;
    $dim?: boolean;
    $focus?: boolean;
}>`
  fill: none;
  stroke-width: ${(props) => (props.$focus ? "3.25px" : "2px")};
  stroke: ${(props) => (props.$focus ? "#4f46e5" : (props.$stroke ?? "#6b7280"))};
  pointer-events: auto;
  ${(props) => props.styles?.(props)};
  /* SA-2452: dim + blur unrelated branches; ~200ms transition. */
  opacity: ${(props) => (props.$dim ? 0.14 : 1)};
  filter: ${(props) => (props.$dim ? "blur(1.4px)" : "blur(0.5px)")};
  transition: opacity 0.2s, filter 0.2s, stroke 0.2s, stroke-width 0.2s;
  cursor: pointer;

  &:hover {
    stroke-width: 3px;
    stroke: ${(props) => (props.$focus ? "#4f46e5" : "#2563eb")};
    filter: none;
    opacity: 1;
  }
`;

const HoverPath = styled.path`
  fill: none;
  stroke: transparent;
  stroke-width: 16px;
  pointer-events: auto;
  cursor: pointer;
`;

const BUTTON_SIZE = 32;

function getMidpoint(pathEl: SVGPathElement): { x: number; y: number } {
    const totalLength = pathEl.getTotalLength();
    const pt = pathEl.getPointAtLength(totalLength / 2);
    return { x: pt.x, y: pt.y };
}

export function LineConnection({ data, area, direction, readOnly, ...props }: {
    area: AreaPlugin<Schemes, AreaExtra>
    data: Connection<BaseNode> & { isLoop?: boolean };
    styles?: () => any;
    onRemove?: (connectionId: string) => void;
    onEdgeClick?: (connectionId: string) => void;
    direction: EditorDirection;
    readOnly?: boolean;
}) {
    const { path, start, end } = useConnection();
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);
    const pathRef = useRef<SVGPathElement>(null);
    const [hovered, setHovered] = useState(false);
    const [midpoint, setMidpoint] = useState<{ x: number; y: number } | null>(null);
    // SA-2452: hover hint that follows the cursor — "Focus path" over the arrow,
    // "Delete" over the delete button.
    const [tip, setTip] = useState<{ show: boolean; text: string; x: number; y: number }>(
        { show: false, text: "", x: 0, y: 0 },
    );
    const showTip = useCallback((text: string, e: React.MouseEvent) => {
        setTip({ show: true, text, x: e.clientX, y: e.clientY });
    }, []);
    const hideTip = useCallback(() => setTip((s) => ({ ...s, show: false })), []);

    const executionStatus = data.executionStatus;
    const CONNECTION_STROKE: Partial<Record<NodeExecutionStatus, string>> = {
        idle: "#6b7280",
        completed: "#34d399",
    };
    const stroke = CONNECTION_STROKE[executionStatus ?? "idle"] ?? "#6b7280";

    const arrowTranslate = useMemo(() => {
        if (!end) return "translate(0, 0)";

        return direction === "vertical"
            ? `translate(${end?.x + 12}, ${end?.y - 5})`
            : `translate(${end?.x}, ${end?.y})`;
    }, [direction, end?.x, end?.y])

    const updateMidpoint = useCallback(() => {
        if (pathRef.current) {
            setMidpoint(getMidpoint(pathRef.current));
        }
    }, []);

    useEffect(() => {
        if (hovered) {
            updateMidpoint();
        }
    }, [hovered, path, updateMidpoint]);

    const handleMouseEnter = useCallback(() => {
        setHovered(true);
    }, []);

    const handleMouseLeave = useCallback(() => {
        setHovered(false);
    }, []);

    const handleRemove = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        props.onRemove?.(data.id);
    }, [props.onRemove, data.id]);

    const handleEdgeClick = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        props.onEdgeClick?.(data.id);
    }, [props.onEdgeClick, data.id]);

    if (!path) return null;

    const branchFocus = data.branchFocus;
    const isDim = branchFocus === "dim";
    const isFocus = branchFocus === "focus";

    const { d: customPath, angle: customAngle } = computeStepPath(start!, end!, direction);

    const handleEdgeLeave = () => { handleMouseLeave(); hideTip(); };
    const handleEdgeMove = (e: React.MouseEvent) => showTip(t("connection.focus_path"), e);

    return (
        <>
        <Svg data-id="line-connection">
            <HoverPath
                d={path}
                onMouseEnter={handleMouseEnter}
                onMouseMove={handleEdgeMove}
                onMouseLeave={handleEdgeLeave}
                onClick={handleEdgeClick}
            />

            <Path
                ref={pathRef}
                styles={props.styles}
                d={path ? customPath : path}
                $stroke={stroke}
                $dim={isDim}
                $focus={isFocus}
                onMouseEnter={handleMouseEnter}
                onMouseMove={handleEdgeMove}
                onMouseLeave={handleEdgeLeave}
                onClick={handleEdgeClick}
                className={hovered ? "hovered" : ""}
            />

            <g
                transform={`${arrowTranslate} rotate(${customAngle})`}
                style={{
                    opacity: isDim ? 0.14 : 1,
                    transition: "opacity 0.2s",
                }}
            >
                <polygon
                    points="-7,-4 0,0 -7,4"
                    fill={isFocus ? "#4f46e5" : stroke}
                />
            </g>

            {!readOnly && hovered && midpoint && (
                <foreignObject
                    x={midpoint.x - BUTTON_SIZE / 2}
                    y={midpoint.y - BUTTON_SIZE / 2}
                    width={BUTTON_SIZE}
                    height={BUTTON_SIZE}
                    onMouseEnter={handleMouseEnter}
                    onMouseMove={(e) => { e.stopPropagation(); showTip(t("connection.delete"), e); }}
                    onMouseLeave={handleEdgeLeave}
                    style={{ overflow: "visible" }}
                >
                    <Button
                        variant="outline"
                        size="icon"
                        style={{
                            pointerEvents: "auto",
                        }}
                        className="shadow-lg cursor-pointer"
                        onClick={handleRemove}
                    >
                        <TrashIcon className="text-destructive" />
                    </Button>
                </foreignObject>
            )}
        </Svg>
        {tip.show && createPortal(
            <div
                style={{
                    position: "fixed",
                    left: tip.x,
                    top: tip.y,
                    transform: "translate(-50%, calc(-100% - 12px))",
                    background: "#1f2430",
                    color: "#fff",
                    fontSize: 11.5,
                    fontWeight: 600,
                    padding: "4px 8px",
                    borderRadius: 6,
                    whiteSpace: "nowrap",
                    pointerEvents: "none",
                    zIndex: 2147483647,
                    boxShadow: "0 4px 14px rgba(20,24,40,.22)",
                }}
            >
                {tip.text}
            </div>,
            document.body,
        )}
        </>
    );
}
