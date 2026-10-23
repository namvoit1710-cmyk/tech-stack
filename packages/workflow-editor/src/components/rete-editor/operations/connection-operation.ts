import { NodeEditor } from "rete";
import { AreaPlugin } from "rete-area-plugin";
import { Transform } from "rete-area-plugin/_types/area";
import { BaseNode } from "../nodes/base-node";
import { AreaExtra, Connection, NodeExecutionStatus, Schemes } from "../types";

export const getConnections = (
  editor: NodeEditor<Schemes>
): Connection<BaseNode>[] => {
  return editor.getConnections() as Connection<BaseNode>[];
};

export const addConnection = (
  editor: NodeEditor<Schemes>,
  connection: Connection<BaseNode>
): void => {
    editor.addConnection(connection);
};

export const removeConnection = (
  editor: NodeEditor<Schemes>,
  connectionId: string
): void => {
    editor.removeConnection(connectionId);
};

export const getTransform = (area: AreaPlugin<Schemes, AreaExtra>): Transform => {
    return area.area.transform;
};

export const setConnectionStatusBySource = (
        editor: NodeEditor<Schemes>,
        area: AreaPlugin<Schemes, AreaExtra>,
        sourceNodeId: string,
        status: NodeExecutionStatus
    ): void => {
        const targetStatus = status === "completed" ? "completed" : "idle";

        editor
            .getConnections()
            .filter((c) => c.source === sourceNodeId)
            .forEach((conn) => {
                conn.executionStatus = targetStatus;
                area.update("connection", conn.id);
            });
};

export const setConnectionStatusBySourcePort = (
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
    sourceNodeId: string,
    sourcePortId: string,
    status: NodeExecutionStatus
) => {
    const targetConnection = editor.getConnections().filter((c) => c.source === sourceNodeId && c.sourceOutput === sourcePortId);
    if (targetConnection.length === 0) return;

    targetConnection.forEach((conn) => {
        conn.executionStatus = status;
        area.update("connection", conn.id);
    });
}

export const setConnectionStatusByTargetPort = (
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
    targetNodeId: string,
    targetPortId: string,
    status: NodeExecutionStatus
) => {
    const targetConnection = editor.getConnections().filter((c) => c.target === targetNodeId && c.targetInput === targetPortId);
    if (targetConnection.length === 0) return;

    targetConnection.forEach((conn) => {
        conn.executionStatus = status;
        area.update("connection", conn.id);
    });
}

export const removeConnectionBySource = (
    editor: NodeEditor<Schemes>,
    sourceNodeId: string
): void => {
    editor
        .getConnections()
        .filter((c) => c.source === sourceNodeId)
        .forEach((conn) => {
            editor.removeConnection(conn.id);
        });
};

export const removeConnectionByTarget = (
    editor: NodeEditor<Schemes>,
    targetNodeId: string
): void => {
    editor
        .getConnections()
        .filter((c) => c.target === targetNodeId)
        .forEach((conn) => {
            editor.removeConnection(conn.id);
        });
};

export const checkDuplicateConnection = (
    editor: NodeEditor<Schemes>,
    newConnection: Connection<BaseNode>
): boolean => {
    return editor
        .getConnections()
        .some((c) => c.source === newConnection.source && c.sourceOutput === newConnection.sourceOutput && c.target === newConnection.target && c.targetInput === newConnection.targetInput);
};
