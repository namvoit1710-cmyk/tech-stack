import type { NodeEditor } from "rete";
import { ClassicPreset } from "rete";
import type { AreaPlugin } from "rete-area-plugin";
import type { HistoryPlugin } from "rete-history-plugin";
import { getNodeSize } from "../config/node-config";
import { BaseNode } from "../nodes/base-node";
import type { AreaExtra, IEditorValue, Schemes } from "../types";
import { zoomToFit } from "./view-operation";

export const serializeNodes = (
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>
): IEditorValue => {
    const nodes = editor.getNodes();
    const connections = editor.getConnections();

    return {
        nodes: nodes.map((node) => {
            const position = area.nodeViews.get(node.id)?.position;
            return {
                id: node.id,
                parent: node.parent,
                position: { x: position?.x ?? 0, y: position?.y ?? 0 },
                data: node.original,
            };
        }),
        connections: connections.map((conn) => ({
            id: conn.id,
            source: conn.source,
            sourceOutput: conn.sourceOutput,
            target: conn.target,
            targetInput: conn.targetInput,
        })),
    };
};

export const initialLoadNodes = async ({
    editor,
    area,
    history,
    initialData,
    layout
}: {
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
    history: HistoryPlugin<Schemes>,
    initialData: IEditorValue,
    layout: (() => Promise<void>) | null,
}): Promise<void> => {
    for (const nodeData of initialData.nodes) {
        const worker = nodeData.data;

        const node = new BaseNode(
            nodeData.id,
            worker?.name,
            { inputs: worker?.ports?.inputs, outputs: worker?.ports?.outputs },
            getNodeSize(worker?.name),
            worker
        );

        if (editor.getNode(nodeData.id)) {
            continue;
        }
        await editor.addNode(node);
        await area.translate(node.id, nodeData.position);
    }

    for (const conn of initialData.connections) {
        const source = editor.getNode(conn.source);
        const target = editor.getNode(conn.target);

        if (!source || !target) continue;

        const connection = new ClassicPreset.Connection(
            source,
            conn.sourceOutput,
            target,
            conn.targetInput
        )
        connection.id = conn.id;

        await editor.addConnection(connection);
    }

    history.clear()

    await layout?.()

    await zoomToFit(area, editor)
};

export const removeAllEditorNodes = async (editor: NodeEditor<Schemes>) => {
    const nodes = editor.getNodes();
    for (const node of nodes) {
        await editor.removeNode(node.id);
    }

    const connections = editor.getConnections();
    for (const conn of connections) {
        await editor.removeConnection(conn.id);
    }
};

export const resetAllNodeStatus = (editor: NodeEditor<Schemes>, area: AreaPlugin<Schemes, AreaExtra>) => {
    editor.getNodes().forEach((node) => {
        node.executionStatus = "idle";
        area.update("node", node.id);
    });

    editor.getConnections().forEach((conn) => {
        conn.executionStatus = "idle";
        area.update("connection", conn.id);
    });
};
