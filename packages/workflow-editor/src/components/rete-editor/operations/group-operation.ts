import { NodeEditor } from "rete";
import { AreaPlugin } from "rete-area-plugin";
import { BaseNode } from "../nodes/base-node";
import { GroupNode } from "../nodes/group-node";
import { AreaExtra, Schemes } from "../types";

export function getGroupAtPosition(
    x: number,
    y: number,
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>
): GroupNode | null {
    const groupNodes = editor
        .getNodes()
        .filter((n): n is GroupNode => n instanceof GroupNode);

    for (const group of groupNodes) {
        const view = area.nodeViews.get(group.id);
        if (!view) continue;

        const { x: gx, y: gy } = view.position;
        const gw = group.width;
        const gh = group.height;

        if (x >= gx && x <= gx + gw && y >= gy && y <= gy + gh) {
            return group;
        }
    }

    return null;
}

// Node join group — set parent + recalculate
export async function joinGroup(
    node: BaseNode,
    groupNode: GroupNode,
    area: AreaPlugin<Schemes, AreaExtra>,
    editor: NodeEditor<Schemes>
): Promise<void> {
    const nodeView = area.nodeViews.get(node.id);
    const groupView = area.nodeViews.get(groupNode.id);
    if (!nodeView || !groupView) return;

    const relativeX = nodeView.position.x - groupView.position.x;
    const relativeY = nodeView.position.y - groupView.position.y;

    node.parent = groupNode.id;

    await area.translate(node.id, { x: relativeX, y: relativeY });

    // await recalculateGroupBounds(groupNode.id, editor, area);
}

// Node leave group — unset parent + recalculate
export async function leaveGroup(
    node: BaseNode,
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>
): Promise<void> {
    const groupId = node.parent;
    if (!groupId) return;

    const nodeView = area.nodeViews.get(node.id);
    const groupView = area.nodeViews.get(groupId);
    if (!nodeView || !groupView) return;

    const absoluteX = groupView.position.x + nodeView.position.x;
    const absoluteY = groupView.position.y + nodeView.position.y;

    node.parent = undefined;

    await area.translate(node.id, { x: absoluteX, y: absoluteY });

    // await recalculateGroupBounds(groupId, editor, area);
}