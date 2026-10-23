import { createEditorNode, type IEditorNode, type IEditorNodeValue, type IEditorValue } from "@ldc/workflow-editor";
import type { INodePallete } from "../types/node-pallete";
import type { IWorkflow, IWorkflowNode } from "../types/workflows";

const BUSINESS_PREFIX = "business:";

export const mapNodeToEditorNode = (node: INodePallete): IEditorNode => {
  // A business node's catalog id is `business:<def-id>`. Carry the bare
  // definition id on the node so the backend enriches it (injects locked
  // defaults like `code`/`language` and normalizes the worker_type to the
  // runnable base worker); without it the node saves with a null
  // node_definition_id and dispatches to the unroutable `business:<id>` worker.
  const node_definition_id = node.id.startsWith(BUSINESS_PREFIX)
    ? node.id.slice(BUSINESS_PREFIX.length)
    : undefined;
  return createEditorNode({
    ...node,
    worker_type: node.id,
    node_definition_id,
    ports: {
      inputs: (node.ports.inputs || []).map(p => ({
        ...p,
        id: p.id,
        label: p.label,
        readonly: true
      })),
      outputs: (node.ports.outputs || []).map(p => ({
        ...p,
        id: p.id,
        label: p.label,
        readonly: true
      }))
    }
  });
};

export const mapApiNodeToINode = (apiNode: IWorkflowNode): IEditorNode => createEditorNode({
  ...apiNode,
  node_type: apiNode.node_type ?? apiNode.type ?? "",
  worker_type: apiNode.worker_type,
  ports: {
    inputs: (apiNode.ports?.in || []).map(port => ({
      ...port,
      id: port.id,
      label: port.label,
      required: port.required,
      readonly: port.readonly
    })),
    outputs: (apiNode.ports?.out || []).map(port => ({
      ...port,
      id: port.id,
      label: port.label,
      required: port.required,
      readonly: port.readonly
    }))
  }
});


export function mapApiToWorkflowValue(apiWorkflow: IWorkflow): IEditorValue {
  const nodes: IEditorNodeValue[] = apiWorkflow.nodes.map((apiNode) => ({
    id: apiNode.id,
    position: { x: apiNode.x ?? 0, y: apiNode.y ?? 0 },
    data: mapApiNodeToINode(apiNode),
  }));

  const connections = apiWorkflow.edges.map((edge) => ({
    id: edge.id,
    source: edge.source_node_id ?? '',
    sourceOutput: edge.source_port_id ?? '',
    target: edge.target_node_id ?? '',
    targetInput: edge.target_port_id ?? '',
  }));

  return { nodes, connections };
}

