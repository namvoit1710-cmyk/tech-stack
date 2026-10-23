import type { EditorDirection } from "../types";

interface Position { x: number; y: number }

const R = 12;

const SOCKET_OFFSET = 12;

export function computeHorizontalStepPath(source: Position, target: Position): { d: string, angle: number } {
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const xMid = (source.x + target.x) / 2;

    if (dx > 40 && Math.abs(dy) > 5) {
        const r = Math.min(R, Math.abs(dx) / 2, Math.abs(dy) / 2);
        const signY = dy > 0 ? 1 : -1;
        return {
            d: [
                `M ${source.x} ${source.y}`,
                `L ${xMid - r} ${source.y}`,
                `Q ${xMid} ${source.y} ${xMid} ${source.y + signY * r}`,
                `L ${xMid} ${target.y - signY * r}`,
                `Q ${xMid} ${target.y} ${xMid + r} ${target.y}`,
                `L ${target.x} ${target.y}`,
            ].join(" "),
            angle: 0,
        };
    }

    if (dx <= 0 && dy > 0) {
        const loopY = Math.min(source.y, target.y) - 100;
        const r = R;
        const upOrDown = loopY > source.y ? 1 : -1;

        return {
            d: [
                `M ${source.x} ${source.y}`,
                `L ${source.x + 50 - r} ${source.y}`,
                `Q ${source.x + 50} ${source.y} ${source.x + 50} ${source.y + upOrDown * r}`,
                `L ${source.x + 50} ${loopY + r}`,
                `Q ${source.x + 50} ${loopY} ${source.x + 50 - r} ${loopY}`,
                `L ${target.x - 50 + r} ${loopY}`,
                `Q ${target.x - 50} ${loopY} ${target.x - 50} ${loopY + r}`,
                `L ${target.x - 50} ${target.y - r}`,
                `Q ${target.x - 50} ${target.y} ${target.x - 50 + r} ${target.y}`,
                `L ${target.x} ${target.y}`,
            ].join(" "),
            angle: 0,
        }
    }

    if (dx <= 0 && dy < 0) {
        const loopY = Math.max(source.y, target.y) + 100;
        const r = R;
        const down = 1;

        return {
            d: [
                `M ${source.x} ${source.y}`,
                `L ${source.x + 50 - r} ${source.y}`,
                `Q ${source.x + 50} ${source.y} ${source.x + 50} ${source.y + down * r}`,
                `L ${source.x + 50} ${loopY - r}`,
                `Q ${source.x + 50} ${loopY} ${source.x + 50 - r} ${loopY}`,
                `L ${target.x - 50 + r} ${loopY}`,
                `Q ${target.x - 50} ${loopY} ${target.x - 50} ${loopY - r}`,
                `L ${target.x - 50} ${target.y + r}`,
                `Q ${target.x - 50} ${target.y} ${target.x - 50 + r} ${target.y}`,
                `L ${target.x} ${target.y}`,
            ].join(" "),
            angle: 0,
        }
    }

    return {
        d: `M ${source.x} ${source.y} L ${target.x} ${target.y}`,
        angle: dx >= 0 ? 0 : 180,
    };
}

function computeVerticalStepPath(
    source: Position,
    target: Position
): { d: string; angle: number } {
    const sx = source.x - SOCKET_OFFSET;
    const tx = target.x + SOCKET_OFFSET;
    const sy = source.y;
    const ty = target.y;

    const dx = tx - sx;
    const dy = ty - sy;
    const yMid = (sy + ty) / 2;

    if (dy > 40 && Math.abs(dx) > 5) {
        const r = Math.min(R, Math.abs(dy) / 2, Math.abs(dx) / 2);
        const signX = dx > 0 ? 1 : -1;
        return {
            d: [
                `M ${sx} ${sy}`,
                `L ${sx} ${yMid - r}`,
                `Q ${sx} ${yMid} ${sx + signX * r} ${yMid}`,
                `L ${tx - signX * r} ${yMid}`,
                `Q ${tx} ${yMid} ${tx} ${yMid + r}`,
                `L ${tx} ${ty}`,
            ].join(" "),
            angle: 90,
        };
    }

    if (dy > 40 && Math.abs(dx) <= 5) {
        return {
            d: `M ${sx} ${sy} L ${tx} ${ty}`,
            angle: 90,
        };
    }

    if (dy <= 0 && dx >= 0) {
        const loopX = Math.max(sx, tx) + 100;
        const r = R;
        return {
            d: [
                `M ${sx} ${sy}`,
                `L ${sx} ${sy + 50 - r}`,
                `Q ${sx} ${sy + 50} ${sx + r} ${sy + 50}`,
                `L ${loopX - r} ${sy + 50}`,
                `Q ${loopX} ${sy + 50} ${loopX} ${sy + 50 - r}`,
                `L ${loopX} ${ty - 50 + r}`,
                `Q ${loopX} ${ty - 50} ${loopX - r} ${ty - 50}`,
                `L ${tx + r} ${ty - 50}`,
                `Q ${tx} ${ty - 50} ${tx} ${ty - 50 + r}`,
                `L ${tx} ${ty}`,
            ].join(" "),
            angle: 90,
        };
    }

    if (dy <= 0 && dx < 0) {
        const loopX = Math.min(sx, tx) - 100;
        const r = R;
        return {
            d: [
                `M ${sx} ${sy}`,
                `L ${sx} ${sy + 50 - r}`,
                `Q ${sx} ${sy + 50} ${sx - r} ${sy + 50}`,
                `L ${loopX + r} ${sy + 50}`,
                `Q ${loopX} ${sy + 50} ${loopX} ${sy + 50 - r}`,
                `L ${loopX} ${ty - 50 + r}`,
                `Q ${loopX} ${ty - 50} ${loopX + r} ${ty - 50}`,
                `L ${tx - r} ${ty - 50}`,
                `Q ${tx} ${ty - 50} ${tx} ${ty - 50 + r}`,
                `L ${tx} ${ty}`,
            ].join(" "),
            angle: 90,
        };
    }

    return {
        d: `M ${sx} ${sy} L ${tx} ${ty}`,
        angle: dy >= 0 ? 90 : 270,
    };
}

export function computeStepPath(
    source: Position,
    target: Position,
    direction: EditorDirection = "horizontal"
): { d: string; angle: number } {
    if (direction === "vertical") {
        return computeVerticalStepPath(source, target);
    }
    return computeHorizontalStepPath(source, target);
}