import { IEditorConnectionValue } from "../components/rete-editor/types";

/**
 * Creates an IEditorConnectionValue for connecting workflow nodes.
 * 
 * @param params - Connection parameters
 * @returns Normalized connection object
 */
export function createEditorConnection(params: {
  id?: string;
  source: string;
  sourceOutput: string;
  target: string;
  targetInput: string;
}): IEditorConnectionValue {
  return {
    id: params.id || `connection_${params.source}_${params.target}`,
    source: params.source,
    sourceOutput: params.sourceOutput,
    target: params.target,
    targetInput: params.targetInput,
  };
}

/**
 * Validates if a connection is valid based on node existence.
 * 
 * @param source - Source node ID
 * @param target - Target node ID
 * @param nodeIds - Set of valid node IDs
 * @returns true if connection is valid
 */
export function isValidConnection(
  source: string,
  target: string,
  nodeIds: Set<string>
): boolean {
  return nodeIds.has(source) && nodeIds.has(target);
}
