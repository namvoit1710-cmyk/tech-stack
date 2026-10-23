import { ClassicPreset, NodeEditor } from "rete";
import { AreaPlugin } from "rete-area-plugin";
import { generateUUID } from "../../../utils/generate-uuid";
import { getNodeSize } from "../config/node-config";
import { BaseNode } from "../nodes/base-node";
import { socket } from "../nodes/socket";
import { AreaExtra, NodeExecutionStatus, Schemes } from "../types";

export const getNodes = (editor: NodeEditor<Schemes>): BaseNode[] => {
  return editor.getNodes();
};

export const getNodeById = (
  editor: NodeEditor<Schemes>,
  nodeId: string
): BaseNode | undefined => {
  return editor.getNode(nodeId);
};

export const addNode = async (
  editor: NodeEditor<Schemes>,
  node: BaseNode
): Promise<void> => {
  await editor.addNode(node);
};

export const removeNode = async (
  editor: NodeEditor<Schemes>,
  nodeId: string
): Promise<void> => {
  for (const item of [...editor.getConnections()]) {
    if (item.source === nodeId || item.target === nodeId) {
      await editor.removeConnection(item.id);
    }
  }
  await editor.removeNode(nodeId);
};

export const copyNode = async (
  editor: NodeEditor<Schemes>,
  area: AreaPlugin<Schemes, AreaExtra>,
  nodeId: string
): Promise<void> => {
  const node = editor.getNode(nodeId);
  if (!node) return;

  const newId = generateUUID();
  const nodeData = structuredClone(node.original);
  nodeData.id = newId;
  const newNode = new BaseNode(
    newId,
    nodeData.name,
    { inputs: nodeData.ports.inputs, outputs: nodeData.ports.outputs },
    getNodeSize(nodeData.name),
    nodeData
  );

  await editor.addNode(newNode);

  const nodeView = area.nodeViews.get(nodeId);
  const pos = nodeView
    ? { x: nodeView.position.x + 200, y: nodeView.position.y }
    : { x: 0, y: 0 };

  await area.translate(newNode.id, pos);
};

export const getNodePosition = (
  area: AreaPlugin<Schemes, AreaExtra>,
  nodeId: string
): { x: number; y: number } | undefined => {
  return area.nodeViews.get(nodeId)?.position;
};

export const translateNode = async (
  area: AreaPlugin<Schemes, AreaExtra>,
  nodeId: string,
  position: { x: number; y: number }
): Promise<void> => {
  await area.translate(nodeId, position);
};

export const setNodeStatus = (
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
    nodeId: string,
    status: NodeExecutionStatus
): void => {
    const node = editor.getNode(nodeId);
    if (!node) return;

    node.executionStatus = status;
    area.update("node", nodeId);
};

export const getPredecessorNodes = (
    nodeId: string,
    editor: NodeEditor<Schemes>
): BaseNode[] => {
    const connections = editor.getConnections();
    const nodes = editor.getNodes();

    const predecessors: Set<string> = new Set();
    const queue: string[] = [nodeId];

    while (queue.length > 0) {
        const currentId = queue.shift()!;

        const incomerIds = connections.filter((connection) => connection.target === currentId).map((connection) => connection.source);
        incomerIds.forEach((incomerId) => {
            if (!predecessors.has(incomerId)) {
                predecessors.add(incomerId);
                queue.push(incomerId);
            }
        });
    }
    
    return nodes.filter((node) => predecessors.has(node.id));
}

export const getSuccessorNodes = (
    nodeId: string,
    editor: NodeEditor<Schemes>
): BaseNode[] => {
    const connections = editor.getConnections();
    const nodes = editor.getNodes();

    const successors: Set<string> = new Set();
    const queue: string[] = [nodeId];

    while (queue.length > 0) {
        const currentId = queue.shift()!;

        const successorIds = connections.filter((connection) => connection.source === currentId).map((connection) => connection.target);
        successorIds.forEach((successorId) => {
            if (!successors.has(successorId)) {
                successors.add(successorId);
                queue.push(successorId);
            }
        });
    }
    
    return nodes.filter((node) => successors.has(node.id));
}

export const getOutGoerNodes = (
    nodeId: string,
    editor: NodeEditor<Schemes>
): BaseNode[] => {
    const connections = editor.getConnections();
    const nodes = editor.getNodes();

    const outGoerIds = connections.filter(connection => connection.source === nodeId).map(connection => connection.target);
    
    return nodes.filter((node) => outGoerIds.includes(node.id));
}

export const getIncomerNodes = (
    nodeId: string,
    editor: NodeEditor<Schemes>
): BaseNode[] => {
    const connections = editor.getConnections();
    const nodes = editor.getNodes();

    const incomerIds = connections.filter(connection => connection.target === nodeId).map(connection => connection.source);
    
    return nodes.filter((node) => incomerIds.includes(node.id));
}

export const syncNodeSizesFromDOM = (editor: NodeEditor<Schemes>, area: AreaPlugin<Schemes, AreaExtra>) => {
    for (const node of editor.getNodes()) {
        const view = area.nodeViews.get(node.id);
        if (!view?.element) continue;

        const { offsetWidth, offsetHeight } = view.element;
        if (offsetWidth > 0) node.width = offsetWidth;
        if (offsetHeight > 0) node.height = offsetHeight;
    }
};

export const addInputSocket = async (
  nodeId: string,
  key: string,
  label: string,
  editor: NodeEditor<Schemes>,
  area: AreaPlugin<Schemes, AreaExtra>
) => {
  const node = editor.getNode(nodeId)
  if (!node) return

  if (node.inputs[key]) return

  node.addInput(key, new ClassicPreset.Input(socket, label))
  node.original = {
    ...node.original,
    ports: {
      ...node.original.ports,
      inputs: [...node.original.ports.inputs, { id: key, label }],
    },
  }

  await area.update("node", nodeId)

  await area.emit({ 
    type: "nodetranslated", 
    data: {
        ...node, 
        position: { x: node.original.x, y: node.original.y },
        previous: { x: node.original.x, y: node.original.y },
    } 
  })
}

export const addOutputSocket = async (
  nodeId: string,
  key: string,
  label: string,
  editor: NodeEditor<Schemes>,
  area: AreaPlugin<Schemes, AreaExtra>
) => {
  const node = editor.getNode(nodeId)
  if (!node) return

  if (node.outputs[key]) return

  node.addOutput(key, new ClassicPreset.Output(socket, label))
  node.original = {
    ...node.original,
    ports: {
      ...node.original.ports,
      outputs: [...node.original.ports.outputs, { id: key, label }],
    },
  }

  await area.update("node", nodeId)

  await area.emit({ 
    type: "nodetranslated", 
    data: {
      ...node, 
      position: { x: node.original.x, y: node.original.y },
      previous: { x: node.original.x, y: node.original.y },
    } 
  })
}

export const removeInputSocket = async (
  nodeId: string,
  key: string,
  editor: NodeEditor<Schemes>,
  area: AreaPlugin<Schemes, AreaExtra>
) => {
  const node = editor.getNode(nodeId)
  if (!node || !node.inputs[key]) return

  const connectionsToRemove = editor
    .getConnections()
    .filter((c) => c.target === nodeId && c.targetInput === key)

  for (const conn of connectionsToRemove) {
    await editor.removeConnection(conn.id)
  }

  node.removeInput(key)
  node.original = {
    ...node.original,
    ports: {
      ...node.original.ports,
      inputs: node.original.ports.inputs.filter((port) => port.id !== key),
    },
  }

    await area.update("node", nodeId)
    
    await area.emit({ 
      type: "nodetranslated", 
      data: {
          ...node, 
          position: { x: node.original.x, y: node.original.y },
          previous: { x: node.original.x, y: node.original.y },
      } 
    })
}

export const removeOutputSocket = async (
    nodeId: string,
    key: string,
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>
) => {
    const node = editor.getNode(nodeId)
    if (!node || !node.outputs[key]) return

    const connectionsToRemove = editor
        .getConnections()
        .filter((c) => c.source === nodeId && c.sourceOutput === key)

    for (const conn of connectionsToRemove) {
        await editor.removeConnection(conn.id)
    }

    node.removeOutput(key)
    node.original = {
        ...node.original,
        ports: {
            ...node.original.ports,
            outputs: node.original.ports.outputs.filter((port) => port.id !== key),
        },
    }

    await area.update("node", nodeId)
    await area.emit({ 
        type: "nodetranslated", 
        data: {
            ...node, 
            position: { x: node.original.x, y: node.original.y },
            previous: { x: node.original.x, y: node.original.y },
        } 
    })
}

export const updateInputSocketLabel = async ({
    nodeId,
    key,
    newLabel,
    editor,
    area
}: {
    nodeId: string,
    key: string,
    newLabel: string,
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>
}) => {
    const node = editor.getNode(nodeId)
    if (!node || !node.inputs[key]) return

    node.inputs[key]!.label = newLabel ?? ""
    node.original = {
        ...node.original,
        ports: {
            ...node.original.ports,
            inputs: node.original.ports.inputs.map((port) => {
                if (port.id === key) {
                    return {
                        ...port,
                        label: newLabel,
                    }
                }
                return port
            })
        }
    }
    await area.update("node", nodeId)

    await area.emit({ 
        type: "nodetranslated", 
        data: {
            ...node, 
            position: { x: node.original.x, y: node.original.y },
            previous: { x: node.original.x, y: node.original.y },
        } 
    })
}

export const updateOutputSocketLabel = async ({
    nodeId,
    key,
    newLabel,
    editor,
    area
}: {
    nodeId: string,
    key: string,
    newLabel: string,
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>
}) => {
    const node = editor.getNode(nodeId)
    if (!node || !node.outputs[key]) return

    node.outputs[key]!.label = newLabel ?? ""
    node.original = {
        ...node.original,
        ports: {
            ...node.original.ports,
            outputs: node.original.ports.outputs.map((port) => {
                if (port.id === key) {
                    return {
                        ...port,
                        label: newLabel,
                    }
                }
                return port
            })
        }
    }
    await area.update("node", nodeId)

    await area.emit({ 
        type: "nodetranslated", 
        data: {
            ...node, 
            position: { x: node.original.x, y: node.original.y },
            previous: { x: node.original.x, y: node.original.y },
        } 
    })
}

export const updateNodeView = async ({
    nodeId,
    area
}: {
    nodeId: string,
    area: AreaPlugin<Schemes, AreaExtra>
}) => {
    await area.update("node", nodeId)
}