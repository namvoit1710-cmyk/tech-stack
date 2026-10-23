import { IEditorNode } from "../components/rete-editor/types";

/**
 * Creates an IEditorNode with default values and port normalization.
 * This is the base factory for all workflow node mappers.
 * 
 * @param params - Node configuration parameters
 * @returns Normalized IEditorNode instance
 */
export function createEditorNode(params: {
  id: string;
  name: string;
  title?: string;
  node_type: string;
  description?: string;
  status?: string;
  version?: number;
  color?: string;
  icon?: string;
  ports: {
    inputs: Array<{ id: string; label?: string; readonly?: boolean;[key: string]: any }>;
    outputs: Array<{ id: string; label?: string; readonly?: boolean;[key: string]: any }>;
  };
  [key: string]: any;
}): IEditorNode {
  return {
    ...params,
    name: params.name,
    title: params.title || params.name,
    node_type: params.node_type,
    color: params.color || "#3b82f6",
    icon: params.icon,
    description: params.description || "",
    status: params.status || "active",
    version: params.version ?? 1,

    ports: {
      inputs: params.ports.inputs.map(p => ({
        ...p,
        id: p.id,
        label: p.label || p.id,
        readonly: p.readonly
      })),
      outputs: params.ports.outputs.map(p => ({
        ...p,
        id: p.id,
        label: p.label || p.id,
        readonly: p.readonly
      })),
    },
  };
}
