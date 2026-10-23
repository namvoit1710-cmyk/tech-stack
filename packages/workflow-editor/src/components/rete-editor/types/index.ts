import type { GetSchemes, NodeEditor } from "rete";
import { ClassicPreset } from "rete";
import type { AreaPlugin } from "rete-area-plugin";
import type { Transform } from "rete-area-plugin/_types/area";
import type { ContextMenuExtra } from "rete-context-menu-plugin";
import type { HistoryPlugin } from "rete-history-plugin";
import type { ReactArea2D } from "rete-react-plugin";
import type { BaseNode } from "../nodes/base-node";
import type { GroupNode } from "../nodes/group-node";


// ─── Connection ───────────────────────────────────────────────────────────────

export class Connection<N extends BaseNode> extends ClassicPreset.Connection<N, N> {
    isMagnetic?: boolean;
    executionStatus?: NodeExecutionStatus;
    /** SA-2452: transient branch-focus state driven by clicking an arrow. */
    branchFocus?: BranchFocusState;
}

// ─── Branch focus (SA-2452) ───────────────────────────────────────────────────

/** How a node/edge renders while a branch is focused: highlighted, dimmed, or normal (undefined). */
export type BranchFocusState = "focus" | "dim";

// ─── Rete Schemes ─────────────────────────────────────────────────────────────

export type Schemes = GetSchemes<BaseNode, Connection<BaseNode>>;
export type AreaExtra = ReactArea2D<Schemes> | ContextMenuExtra;
export type HistoryType = HistoryPlugin<Schemes>;
export interface HistoryDataType {
    history: {
        produced: Record<string, unknown>[];
        reserved: Record<string, unknown>[];
    }
}

// ─── Editor Config ────────────────────────────────────────────────────────────

export interface EditorConfig {
    readOnly?: boolean;
    direction?: EditorDirection;
    layout?: { duration?: number; animated?: boolean };
    contextMenu?: boolean;
    autoArrange?: boolean;
    additionalConfig?: Record<string, any>;
}

// ─── Node Types ───────────────────────────────────────────────────────────────

export interface NodePort {
    id: string;
    label: string;
    [key: string]: any;
}

export interface NodePortConfig {
    inputs?: NodePort[];
    outputs?: NodePort[];
}

export interface NodeSizeConfig {
    width?: number;
    height?: number;
}

export interface INodePorts {
    inputs: NodePort[];
    outputs: NodePort[];
}

export interface IEditorNode {
    id: string;
    name: string;
    title: string;
    icon?: string;
    node_type: string;
    color: string;
    description: string;
    ports: INodePorts;
    status: string;
    [key: string]: any;
}

// ─── Workflow Value ───────────────────────────────────────────────────────────

export interface IEditorNodeValue {
    id: string;
    position: { x: number; y: number };
    parent?: string;
    data: IEditorNode;
}

export interface IEditorConnectionValue {
    id: string;
    source: string;
    sourceOutput: string;
    target: string;
    targetInput: string;
}

export interface IEditorValue {
    nodes: IEditorNodeValue[];
    connections: IEditorConnectionValue[];
}

// ─── Node Execution Status ──────────────────────────────────────────────────────────

export type NodeExecutionStatus =
    | "idle"
    | "executing"
    | "completed"
    | "failed";

export type EditorDirection = "horizontal" | "vertical";

// ─── Editor Instance ──────────────────────────────────────────────────────────

export interface IEditorInstance {
    editor: NodeEditor<Schemes>;
    area: AreaPlugin<Schemes, AreaExtra>;
    history: HistoryType;

    resetAllNodeStatus: () => void;
    removeAllNodes: () => Promise<void>;
    updateNodeConnectionStatus: (nodeId: string, status: NodeExecutionStatus) => void;
    addNode: (node: BaseNode) => Promise<void>;
    updateNodeView: (nodeId: string) => Promise<void>;
    getNodes: () => BaseNode[];
    setNodeStatus: (nodeId: string, status: NodeExecutionStatus) => void;
    getNodeById: (nodeId: string) => BaseNode | undefined;
    removeNode: (nodeId: string) => Promise<void>;
    copyNode: (nodeId: string) => Promise<void>;
    getTransform: () => Transform;

    getConnections: () => Connection<BaseNode>[];
    addConnection: (connection: Connection<BaseNode>) => void;
    setConnectionStatusBySourcePort: (sourceNodeId: string, sourcePortId: string, status: NodeExecutionStatus) => void;
    setConnectionStatusByTargetPort: (targetNodeId: string, targetPortId: string, status: NodeExecutionStatus) => void;
    removeConnection: (connectionId: string) => void;
    removeConnectionBySource: (sourceNodeId: string) => void;
    removeConnectionByTarget: (targetNodeId: string) => void;
    checkDuplicateConnection: (newConn: Connection<BaseNode>) => boolean;

    getPredecessorNodes: (nodeId: string) => BaseNode[];
    getSuccessorNodes: (nodeId: string) => BaseNode[];
    getOutGoerNodes: (nodeId: string) => BaseNode[];
    getIncomerNodes: (nodeId: string) => BaseNode[];

    addInputSocket: (nodeId: string, key: string, label: string) => Promise<void>;
    addOutputSocket: (nodeId: string, key: string, label: string) => Promise<void>;
    removeInputSocket: (nodeId: string, key: string) => Promise<void>;
    removeOutputSocket: (nodeId: string, key: string) => Promise<void>;
    updateInputSocketLabel: (nodeId: string, key: string, label: string) => Promise<void>;
    updateOutputSocketLabel: (nodeId: string, key: string, label: string) => Promise<void>;

    serializeNodes: () => IEditorValue;
    initialLoadNodes: (initialData: IEditorValue) => Promise<void>;

    translateNode: (nodeId: string, position: { x: number; y: number }) => Promise<void>;
    getNodePosition: (nodeId: string) => { x: number; y: number } | undefined;

    getGroupAtPosition: (x: number, y: number) => GroupNode | null;
    joinGroup: (nodeId: string, groupId: string) => Promise<void>;
    leaveGroup: (nodeId: string) => Promise<void>;

    getZoomLevel: () => number;
    zoomByLevel: (zoomLevel: number) => Promise<void>;
    centerOnNode: (nodeId: string) => Promise<void>;
    isNodeInViewport: (nodeId: string) => boolean;
    zoomToFit: () => void;
    zoomIn: () => void;
    zoomOut: () => void;
    layout: () => Promise<void>;
    undo: () => void;
    redo: () => void;
    destroy: () => void;
}