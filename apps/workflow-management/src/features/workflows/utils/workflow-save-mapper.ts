

import type { IEditorConnectionValue, IEditorNodeValue, IEditorValue, INodePorts, NodePort } from "@ldc/workflow-editor";
import type { IWorkflow, IWorkflowEdge, IWorkflowNodePort, IWorkflowNodePorts, IWorkflowSaveNode, IWorkflowSavePayload, NodeType } from "../types/workflows";

function mapEditorPortsToWorkflowPorts(
    editorPorts: INodePorts
): IWorkflowNodePorts | undefined {
    if (!editorPorts) return undefined;

    const mapPort = (port: NodePort): IWorkflowNodePort => ({
        id: port.id,
        label: port.label,
        data_type: port.data_type ?? "any",
        required: port.required ?? true,
        description: port.description ?? "",
        readonly: port.readonly ?? false,
    });

    return {
        in: editorPorts.inputs?.map(mapPort) ?? [],
        out: editorPorts.outputs?.map(mapPort) ?? [],
    };
}

export function mapEditorNodeToSaveNode(
    editorNode: IEditorNodeValue
): IWorkflowSaveNode {
    const { data, position } = editorNode;

    return {
        id: editorNode.id,
        name: data.title ?? data.name,
        node_type: data.node_type as NodeType,
        worker_type: data.worker_type ?? null,
        node_definition_id: data.node_definition_id ?? null,
        parameters: data.parameters,
        ports: mapEditorPortsToWorkflowPorts(data.ports),
        x: position?.x ?? null,
        y: position?.y ?? null,
        instruction: data.instruction,
        description: data.description,
        icon: data.icon,
        color: data.color,
        tags: data.tags,
    };
}

export function mapEditorConnectionToEdge(
    connection: IEditorConnectionValue
): IWorkflowEdge {
    return {
        id: connection.id,
        source_node_id: connection.source,
        target_node_id: connection.target,
        source_port_id: connection.sourceOutput,
        target_port_id: connection.targetInput,
        condition: null,
        label: "",
        description: "",
    };
}

export function mapEditorValueToSavePayload(editorValue: IEditorValue): {
    nodes: IWorkflowSaveNode[];
    edges: IWorkflowEdge[];
} {
    return {
        nodes: editorValue.nodes.map(mapEditorNodeToSaveNode),
        edges: editorValue.connections.map(mapEditorConnectionToEdge),
    };
}

export function mapWorkflowToSavePayload(
    workflow: IWorkflow, 
    editorValue: IEditorValue, 
    baseChangeVersion: string
): IWorkflowSavePayload {
    const { nodes, edges } = mapEditorValueToSavePayload(editorValue);
    return {
        ...workflow,
        edges: edges,
        nodes: nodes,
        version: baseChangeVersion
    };
}