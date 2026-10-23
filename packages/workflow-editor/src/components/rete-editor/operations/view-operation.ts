import { NodeEditor } from "rete";
import { AreaExtensions, AreaPlugin } from "rete-area-plugin";
import { AreaExtra, HistoryType, Schemes } from "../types";

const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);

export const getZoomLevel = (area: AreaPlugin<Schemes, AreaExtra>): number => {
    return area.area.transform.k;
}

export const zoomIn = async (area: AreaPlugin<Schemes, AreaExtra>): Promise<void> => {
    const { k: oldK, x: oldTx, y: oldTy } = area.area.transform;
    const newK = oldK * 1.1;

    const ox = area.container.clientWidth / 2;
    const oy = area.container.clientHeight / 2;

    const newTx = ox - (ox - oldTx) * (newK / oldK);
    const newTy = oy - (oy - oldTy) * (newK / oldK);

    await area.area.zoom(newK);
    await area.area.translate(newTx, newTy);
};

export const zoomOut = async (area: AreaPlugin<Schemes, AreaExtra>): Promise<void> => {
    const { k: oldK, x: oldTx, y: oldTy } = area.area.transform;
    const newK = oldK * 0.9;

    const ox = area.container.clientWidth / 2;
    const oy = area.container.clientHeight / 2;

    const newTx = ox - (ox - oldTx) * (newK / oldK);
    const newTy = oy - (oy - oldTy) * (newK / oldK);

    await area.area.zoom(newK);
    await area.area.translate(newTx, newTy);
};

export const zoomToFit = async (
    area: AreaPlugin<Schemes, AreaExtra>,
    editor: NodeEditor<Schemes>
): Promise<void> => {
    await AreaExtensions.zoomAt(area, editor.getNodes());
};

export const getNodePositionInViewport = (area: AreaPlugin<Schemes, AreaExtra>, editor: NodeEditor<Schemes>, nodeId: string) => {
    try {
        const node = editor.getNode(nodeId);
        if (!node) throw new Error(`Node not found for nodeId: ${nodeId}`);

        const nodeWidth = node.width ?? 0;
        const nodeHeight = node.height ?? 0;

        const nodeView = area.nodeViews.get(nodeId);
        if (!nodeView) throw new Error(`Node view not found for nodeId: ${nodeId}`);

        const { x: nodeX, y: nodeY } = nodeView.position;
        const { k: zoom, x: fromTx, y: fromTy } = area.area.transform;

        const container = area.container;
        const viewportWidth = container.clientWidth;
        const viewportHeight = container.clientHeight;

        const tx = viewportWidth / 2 - (nodeX + nodeWidth / 2) * zoom;
        const ty = viewportHeight / 2 - (nodeY + nodeHeight / 2) * zoom;

        return {
            tx,
            ty,
            fromTx,
            fromTy,
            zoom,
        }

    } catch {
        return null;
    }
}

export const zoomByLevel = async (
    area: AreaPlugin<Schemes, AreaExtra>,
    zoomLevel: number,
): Promise<void> => {
    const { k } = area.area.transform;

    if (zoomLevel === k) return;
    
    await area.area.zoom(zoomLevel);
};

export const centerOnNode = async (
    area: AreaPlugin<Schemes, AreaExtra>,
    editor: NodeEditor<Schemes>,
    nodeId: string,
    duration: number = 300
): Promise<void> => {
    const node = editor.getNode(nodeId);
    if (!node) return;

    const nodeView = area.nodeViews.get(nodeId);
    if (!nodeView) return;

    // Wait for browser layout/paint after container resize
    await new Promise<void>(resolve => requestAnimationFrame(() => 
        requestAnimationFrame(() => resolve())
    ));

    const position = getNodePositionInViewport(area, editor, nodeId);
    if (!position) return;
    
    const { tx, ty, fromTx, fromTy } = position;

    return new Promise<void>((resolve) => {
        const startTime = performance.now();

        const animate = (currentTime: number) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = easeOutCubic(progress);

            const currentTx = fromTx + (tx - fromTx) * eased;
            const currentTy = fromTy + (ty - fromTy) * eased;

            area.area.translate(currentTx, currentTy);

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                resolve();
            }
        };

        requestAnimationFrame(animate);
    });
};

export const isNodeInViewport = (
  area: AreaPlugin<Schemes, AreaExtra>,
  editor: NodeEditor<Schemes>,
  nodeId: string
): boolean => {
  const node = editor.getNode(nodeId);
  if (!node) return false;

  const nodeView = area.nodeViews.get(nodeId);
  if (!nodeView) return false;

  const { x: nodeX, y: nodeY } = nodeView.position;
  const nodeWidth = node.width ?? 0;
  const nodeHeight = node.height ?? 0;

  const { k: zoom, x: tx, y: ty } = area.area.transform;
  const viewportWidth = area.container.clientWidth;
  const viewportHeight = area.container.clientHeight;

  const screenX = nodeX * zoom + tx;
  const screenY = nodeY * zoom + ty;
  const screenW = nodeWidth * zoom;
  const screenH = nodeHeight * zoom;

  return (
    screenX >= 0 &&
    screenX + screenW <= viewportWidth &&
    screenY >= 0 &&
    screenY + screenH <= viewportHeight
  );
};

export const undo = (history: HistoryType): void => {
    history.undo();
};

export const redo = (history: HistoryType): void => {
    history.redo();
};