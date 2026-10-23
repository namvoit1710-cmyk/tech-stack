import type { EditorConfig, IEditorInstance, NodeExecutionStatus } from "../types";
import { setupPlugins } from "./plugins";

import {
  addConnection,
  checkDuplicateConnection,
  getConnections,
  getTransform,
  removeConnection,
  removeConnectionBySource,
  removeConnectionByTarget,
  setConnectionStatusBySource,
  setConnectionStatusBySourcePort,
  setConnectionStatusByTargetPort,
} from "../operations/connection-operation";
import {
  addInputSocket,
  addNode,
  addOutputSocket,
  copyNode,
  getIncomerNodes,
  getNodeById,
  getNodePosition,
  getNodes,
  getOutGoerNodes,
  getPredecessorNodes,
  getSuccessorNodes,
  removeInputSocket,
  removeNode,
  removeOutputSocket,
  setNodeStatus,
  syncNodeSizesFromDOM,
  translateNode,
  updateInputSocketLabel,
  updateNodeView,
  updateOutputSocketLabel,
} from "../operations/nodes-operation";

import {
  centerOnNode,
  getZoomLevel,
  isNodeInViewport,
  redo,
  undo,
  zoomByLevel,
  zoomIn,
  zoomOut,
  zoomToFit
} from "../operations/view-operation";

import { getGroupAtPosition, joinGroup, leaveGroup } from "../operations/group-operation";
import { createLayoutOperations } from "../operations/layout-operation";
import {
  initialLoadNodes,
  removeAllEditorNodes,
  resetAllNodeStatus,
  serializeNodes,
} from "../operations/serialization";

export async function createEditor(
  container: HTMLElement,
  config: EditorConfig
): Promise<IEditorInstance> {
  const { layout = { duration: 500, animated: true }, direction = "horizontal" } = config;

  const { editor, area, arrange, applier, history } =
    await setupPlugins(container, config);

  const updateNodeConnectionStatus = (nodeId: string, status: NodeExecutionStatus = "idle") => {
    setNodeStatus(editor, area, nodeId, status)
    setConnectionStatusBySource(editor, area, nodeId, status)
  }

  const { layoutNodes } = createLayoutOperations(editor, area, arrange, () => syncNodeSizesFromDOM(editor, area));

  return {
    editor,
    area,
    history,

    addNode: (node) => addNode(editor, node),
    removeAllNodes: () => removeAllEditorNodes(editor),
    updateNodeView: (nodeId) => updateNodeView({ nodeId, area }),
    getNodes: () => getNodes(editor),
    updateNodeConnectionStatus,
    resetAllNodeStatus: () => resetAllNodeStatus(editor, area),
    setNodeStatus: (nodeId, status) => setNodeStatus(editor, area, nodeId, status),
    getNodeById: (nodeId) => getNodeById(editor, nodeId),
    removeNode: (nodeId) => removeNode(editor, nodeId),
    copyNode: (nodeId) => copyNode(editor, area, nodeId),
    getNodePosition: (nodeId) => getNodePosition(area, nodeId),
    translateNode: (nodeId, position) => translateNode(area, nodeId, position),
    getPredecessorNodes: (nodeId) => getPredecessorNodes(nodeId, editor),
    getSuccessorNodes: (nodeId) => getSuccessorNodes(nodeId, editor),
    getOutGoerNodes: (nodeId) => getOutGoerNodes(nodeId, editor),
    getIncomerNodes: (nodeId) => getIncomerNodes(nodeId, editor),

    addInputSocket: (nodeId, key, label) => addInputSocket(nodeId, key, label, editor, area),
    addOutputSocket: (nodeId, key, label) => addOutputSocket(nodeId, key, label, editor, area),
    removeInputSocket: (nodeId, key) => removeInputSocket(nodeId, key, editor, area),
    removeOutputSocket: (nodeId, key) => removeOutputSocket(nodeId, key, editor, area),
    updateInputSocketLabel: (nodeId, key, label) => updateInputSocketLabel({ nodeId, key, newLabel: label, editor, area }),
    updateOutputSocketLabel: (nodeId, key, label) => updateOutputSocketLabel({ nodeId, key, newLabel: label, editor, area }),

    addConnection: (conn) => addConnection(editor, conn),
    getConnections: () => getConnections(editor),
    checkDuplicateConnection: (newConn) => checkDuplicateConnection(editor, newConn),
    setConnectionStatusBySourcePort: (sourceNodeId, sourcePortId, status) => setConnectionStatusBySourcePort(editor, area, sourceNodeId, sourcePortId, status),
    setConnectionStatusByTargetPort: (targetNodeId, targetPortId, status) => setConnectionStatusByTargetPort(editor, area, targetNodeId, targetPortId, status),
    removeConnectionBySource: (sourceNodeId) => removeConnectionBySource(editor, sourceNodeId),
    removeConnectionByTarget: (targetNodeId) => removeConnectionByTarget(editor, targetNodeId),

    removeConnection: (id) => removeConnection(editor, id),
    getTransform: () => getTransform(area),

    getGroupAtPosition: (x, y) => getGroupAtPosition(x, y, editor, area),
    joinGroup: (nodeId, groupId) => {
      const node = editor.getNode(nodeId);
      const group = editor.getNode(groupId);
      return joinGroup(node!, group!, area, editor);
    },
    leaveGroup: (nodeId) => {
      const node = editor.getNode(nodeId);
      return leaveGroup(node!, editor, area);
    },

    zoomIn: () => zoomIn(area),
    zoomOut: () => zoomOut(area),
    isNodeInViewport: (nodeId) => isNodeInViewport(area, editor, nodeId),
    getZoomLevel: () => getZoomLevel(area),
    zoomByLevel: (zoomLevel) => zoomByLevel(area, zoomLevel),
    centerOnNode: (nodeId) => centerOnNode(area, editor, nodeId),
    zoomToFit: () => zoomToFit(area, editor),
    layout: () => layoutNodes({ direction }),
    undo: () => undo(history),
    redo: () => redo(history),

    serializeNodes: () => serializeNodes(editor, area),
    initialLoadNodes: (initialData) =>
      initialLoadNodes({
        editor,
        area,
        history,
        initialData,
        layout: (direction === "vertical" && config.readOnly) ? () => layoutNodes({ direction }) : null
      }),

    destroy: () => {
      area.destroy();
    },
  };
}

export const makeCreateEditor = (config: EditorConfig) => {
  return (container: HTMLElement) => createEditor(container, config);
};