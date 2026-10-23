/**
 * Generates a standardized port ID for workflow nodes.
 * Format: {nodeId}_{type}
 * 
 * @param nodeId - The unique identifier of the node
 * @param type - Port type: "input" or "output"
 * @returns Formatted port ID
 */
export function generatePortId(nodeId: string, type: "input" | "output"): string {
  return `${nodeId}_${type}`;
}

/**
 * Creates a port configuration object with defaults.
 * 
 * @param id - Port identifier
 * @param label - Display label (defaults to id if not provided)
 * @param config - Additional port configuration
 * @returns Port configuration object
 */
export function createPort(
  id: string, 
  label?: string, 
  config?: { readonly?: boolean; required?: boolean; [key: string]: any }
) {
  return {
    id,
    label: label || id,
    readonly: config?.readonly,
    required: config?.required,
    ...config,
  };
}
