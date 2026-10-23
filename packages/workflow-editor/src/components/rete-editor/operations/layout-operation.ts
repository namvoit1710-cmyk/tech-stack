import type { NodeEditor } from "rete";
import type { AreaPlugin } from "rete-area-plugin";
import { AreaExtensions } from "rete-area-plugin";
import type { AutoArrangePlugin } from "rete-auto-arrange-plugin";
import type { AreaExtra, Schemes } from "../types";

const SOCKET_OFFSET = 12;
const PATH_ROUTER_R = 12;
const EDGE_CORRIDOR_PADDING = 20;

interface LayoutOptions {
  direction: "vertical" | "horizontal";
}

interface DummyNode {
  id: string;
  isDummy: true;
  edgeId: string;
  sourceId: string;
  targetId: string;
  sourceOutput: string;
  width: number;
  height: number;
}

interface RealNode {
  id: string;
  isDummy: false;
  width: number;
  height: number;
}

type LayoutNode = DummyNode | RealNode;

export function createLayoutOperations(
  editor: NodeEditor<Schemes>,
  area: AreaPlugin<Schemes, any>,
  arrange: AutoArrangePlugin<Schemes, AreaExtra> | null,
  syncNodeSizesFromDOM: () => void
) {
  const layoutNodes = async (options: LayoutOptions) => {
    if (!arrange) return;

    const { direction } = options;
    const isVertical = direction === "vertical";

    syncNodeSizesFromDOM();

    const realNodes = editor.getNodes();
    const connections = editor.getConnections();
    if (realNodes.length === 0) return;

    const getCrossSize = (sz: { w: number; h: number }) =>
      isVertical ? sz.w : sz.h;
    const getMainSize = (sz: { w: number; h: number }) =>
      isVertical ? sz.h : sz.w;
    const nodeSize = (id: string): { w: number; h: number } => {
      const n = editor.getNode(id);
      return { w: n?.width ?? 200, h: n?.height ?? 100 };
    };


    const visited = new Set<string>();
    const recursionStack = new Set<string>();
    const backEdges = new Set<string>();

    function detectCycles(nodeId: string) {
      visited.add(nodeId);
      recursionStack.add(nodeId);
      for (const conn of connections) {
        if (conn.source !== nodeId) continue;
        const ek = `${conn.source}->${conn.target}`;
        if (!visited.has(conn.target)) {
          detectCycles(conn.target);
        } else if (recursionStack.has(conn.target)) {
          backEdges.add(ek);
        }
      }
      recursionStack.delete(nodeId);
    }
    for (const n of realNodes) {
      if (!visited.has(n.id)) detectCycles(n.id);
    }

    const dagEdges = connections.filter(
      (c) => !backEdges.has(`${c.source}->${c.target}`)
    );

    const inDegree = new Map<string, number>();
    const adjOut = new Map<string, typeof dagEdges>();
    for (const n of realNodes) {
      inDegree.set(n.id, 0);
      adjOut.set(n.id, []);
    }
    for (const e of dagEdges) {
      inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
      adjOut.get(e.source)!.push(e);
    }
    const topoOrder: string[] = [];
    const topoQueue: string[] = [];
    for (const [id, deg] of inDegree) {
      if (deg === 0) topoQueue.push(id);
    }
    while (topoQueue.length > 0) {
      const cur = topoQueue.shift()!;
      topoOrder.push(cur);
      for (const e of adjOut.get(cur) ?? []) {
        const d = (inDegree.get(e.target) ?? 1) - 1;
        inDegree.set(e.target, d);
        if (d === 0) topoQueue.push(e.target);
      }
    }
    for (const n of realNodes) {
      if (!topoOrder.includes(n.id)) topoOrder.push(n.id);
    }

    const depthMap = new Map<string, number>();
    for (const id of topoOrder) {
      const predDepths = dagEdges
        .filter((e) => e.target === id)
        .map((e) => depthMap.get(e.source) ?? 0);
      depthMap.set(
        id,
        predDepths.length === 0 ? 0 : Math.max(...predDepths) + 1
      );
    }

    const DUMMY_CROSS_SIZE = isVertical
      ? SOCKET_OFFSET * 2 + EDGE_CORRIDOR_PADDING 
      : SOCKET_OFFSET * 2 + EDGE_CORRIDOR_PADDING;

    const allLayoutNodes = new Map<string, LayoutNode>();
    const allLayoutEdges: Array<{
      source: string;
      target: string;
      sourceOutput: string;
      originalEdgeId: string;
    }> = [];

    for (const n of realNodes) {
      allLayoutNodes.set(n.id, {
        id: n.id,
        isDummy: false,
        width: n.width ?? 200,
        height: n.height ?? 100,
      });
    }

    let dummyCounter = 0;

    for (const edge of dagEdges) {
      const srcDepth = depthMap.get(edge.source) ?? 0;
      const tgtDepth = depthMap.get(edge.target) ?? 0;
      const span = tgtDepth - srcDepth;

      if (span <= 1) {
        allLayoutEdges.push({
          source: edge.source,
          target: edge.target,
          sourceOutput: edge.sourceOutput,
          originalEdgeId: edge.id,
        });
        continue;
      }

      let prevId = edge.source;
      for (let d = srcDepth + 1; d < tgtDepth; d++) {
        const dummyId = `__dummy_${dummyCounter++}`;
        allLayoutNodes.set(dummyId, {
          id: dummyId,
          isDummy: true,
          edgeId: edge.id,
          sourceId: edge.source,
          targetId: edge.target,
          sourceOutput: edge.sourceOutput,
          width: DUMMY_CROSS_SIZE,
          height: isVertical ? 10 : DUMMY_CROSS_SIZE,
        });
        depthMap.set(dummyId, d);
        allLayoutEdges.push({
          source: prevId,
          target: dummyId,
          sourceOutput: edge.sourceOutput,
          originalEdgeId: edge.id,
        });
        prevId = dummyId;
      }
      allLayoutEdges.push({
        source: prevId,
        target: edge.target,
        sourceOutput: edge.sourceOutput,
        originalEdgeId: edge.id,
      });
    }

    type BranchTag = {
      branchSourceId: string;
      portIndex: number;
      totalPorts: number;
    };

    const branchMap = new Map<string, BranchTag | null>();
    const depthSorted = [...depthMap.entries()].sort((a, b) => a[1] - b[1]);

    for (const [id] of depthSorted) {
      if (branchMap.has(id)) continue;

      const inEdges = allLayoutEdges.filter((e) => e.target === id);
      const isMerge =
        inEdges.length > 1 &&
        new Set(inEdges.map((e) => e.originalEdgeId)).size > 1;

      if (inEdges.length === 0) {
        branchMap.set(id, null);
      } else if (isMerge) {
        branchMap.set(id, null);
      } else {
        const firstEdge = inEdges[0];
        if (!firstEdge) {
          branchMap.set(id, null);
          continue;
        }
        const parentId = firstEdge.source;
        const parentNode = allLayoutNodes.get(parentId);

        if (parentNode && !parentNode.isDummy) {
          const realParent = editor.getNode(parentId);
          const outputKeys = Object.keys(realParent?.outputs ?? {});

          if (outputKeys.length > 1) {
            const portIndex = outputKeys.indexOf(firstEdge.sourceOutput);
            branchMap.set(id, {
              branchSourceId: parentId,
              portIndex: Math.max(portIndex, 0),
              totalPorts: outputKeys.length,
            });
          } else {
            branchMap.set(id, branchMap.get(parentId) ?? null);
          }
        } else {
          branchMap.set(id, branchMap.get(parentId) ?? null);
        }
      }
    }


    const layerByDepth = new Map<number, string[]>();
    for (const [id, depth] of depthMap) {
      if (!layerByDepth.has(depth)) layerByDepth.set(depth, []);
      layerByDepth.get(depth)!.push(id);
    }

    const sortedLayers = [...layerByDepth.entries()].sort(
      (a, b) => a[0] - b[0]
    );

    const crossOrder = new Map<string, number>();

    for (const [, nodeIds] of sortedLayers) {
      for (const id of nodeIds) {
        const branch = branchMap.get(id);
        const parentEdges = allLayoutEdges.filter((e) => e.target === id);
        const parentPositions = parentEdges
          .map((e) => crossOrder.get(e.source))
          .filter((v): v is number => v !== undefined);

        let order: number;
        if (parentPositions.length > 0) {
          parentPositions.sort((a, b) => a - b);
          const mid = Math.floor(parentPositions.length / 2);
          const midLow = parentPositions[mid - 1] ?? 0;
          const midVal = parentPositions[mid] ?? 0;
          const median: number =
            parentPositions.length % 2 === 0
              ? (midLow + midVal) / 2
              : midVal;

          if (branch) {
            const offset =
              (branch.portIndex - (branch.totalPorts - 1) / 2) * 2;
            order = median + offset;
          } else {
            order = median;
          }
        } else {
          order = 0;
        }
        crossOrder.set(id, order);
      }

      nodeIds.sort(
        (a, b) => (crossOrder.get(a) ?? 0) - (crossOrder.get(b) ?? 0)
      );
      nodeIds.forEach((id, i) => crossOrder.set(id, i));
    }

    function countCrossings(layer1: string[], layer2: string[]): number {
      const posIn2 = new Map<string, number>();
      layer2.forEach((id, i) => posIn2.set(id, i));

      const eps: [number, number][] = [];
      for (let i = 0; i < layer1.length; i++) {
        for (const e of allLayoutEdges) {
          if (e.source !== layer1[i]) continue;
          const tp = posIn2.get(e.target);
          if (tp !== undefined) eps.push([i, tp]);
        }
      }

      let crossings = 0;
      for (let i = 0; i < eps.length; i++) {
        for (let j = i + 1; j < eps.length; j++) {
          const ei = eps[i];
          const ej = eps[j];
          if (ei && ej && (ei[0] - ej[0]) * (ei[1] - ej[1]) < 0)
            crossings++;
        }
      }
      return crossings;
    }

    for (let iter = 0; iter < 4; iter++) {
      let improved = false;
      for (let li = 0; li < sortedLayers.length - 1; li++) {
        const layerCur = sortedLayers[li];
        const layerNxt = sortedLayers[li + 1];
        if (!layerCur || !layerNxt) continue;
        const [, cur] = layerCur;
        const [, nxt] = layerNxt;
        for (let i = 0; i < nxt.length - 1; i++) {
          const before = countCrossings(cur, nxt);
          const tmp1 = nxt[i]; const tmp2 = nxt[i + 1];
          if (tmp1 !== undefined && tmp2 !== undefined) { nxt[i] = tmp2; nxt[i + 1] = tmp1; }
          if (countCrossings(cur, nxt) < before) {
            improved = true;
          } else {
            const r1 = nxt[i]; const r2 = nxt[i + 1];
            if (r1 !== undefined && r2 !== undefined) { nxt[i] = r2; nxt[i + 1] = r1; }
          }
        }
      }
      for (let li = sortedLayers.length - 1; li > 0; li--) {
        const layerCur2 = sortedLayers[li];
        const layerPrv = sortedLayers[li - 1];
        if (!layerCur2 || !layerPrv) continue;
        const [, cur] = layerCur2;
        const [, prv] = layerPrv;
        for (let i = 0; i < prv.length - 1; i++) {
          const before = countCrossings(prv, cur);
          const tp1 = prv[i]; const tp2 = prv[i + 1];
          if (tp1 !== undefined && tp2 !== undefined) { prv[i] = tp2; prv[i + 1] = tp1; }
          if (countCrossings(prv, cur) < before) {
            improved = true;
          } else {
            const rp1 = prv[i]; const rp2 = prv[i + 1];
            if (rp1 !== undefined && rp2 !== undefined) { prv[i] = rp2; prv[i + 1] = rp1; }
          }
        }
      }
      if (!improved) break;
    }

    const LAYER_SPACING = 120;
    const NODE_SPACING = 60;
    const DUMMY_SPACING = 30;

    const layerMainPos = new Map<number, number>();
    let mainCursor = 0;
    for (const [depth, nodeIds] of sortedLayers) {
      layerMainPos.set(depth, mainCursor);
      const maxMainSz = Math.max(
        ...nodeIds.map((id) => {
          const ln = allLayoutNodes.get(id);
          if (!ln) return 100;
          return getMainSize({ w: ln.width, h: ln.height });
        })
      );
      mainCursor += maxMainSz + LAYER_SPACING;
    }

    const finalPositions = new Map<string, { x: number; y: number }>();

    for (const [depth, nodeIds] of sortedLayers) {
      const items = nodeIds.map((id) => {
        const ln = allLayoutNodes.get(id)!;
        return {
          id,
          crossSize: getCrossSize({ w: ln.width, h: ln.height }),
          isDummy: ln.isDummy,
        };
      });

      let totalCross = 0;
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (!item) continue;
        totalCross += item.crossSize;
        const nextItem = items[i + 1];
        if (i < items.length - 1 && nextItem) {
          totalCross +=
            item.isDummy || nextItem.isDummy
              ? DUMMY_SPACING
              : NODE_SPACING;
        }
      }

      let crossCursor = -totalCross / 2;
      const mainPos = layerMainPos.get(depth)!;

      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (!item) continue;
        const pos = isVertical
          ? { x: crossCursor, y: mainPos }
          : { x: mainPos, y: crossCursor };
        finalPositions.set(item.id, pos);
        crossCursor += item.crossSize;
        const nextItem = items[i + 1];
        if (i < items.length - 1 && nextItem) {
          crossCursor +=
            item.isDummy || nextItem.isDummy
              ? DUMMY_SPACING
              : NODE_SPACING;
        }
      }
    }

    const realNodeIds = new Set(realNodes.map((n) => n.id));

    function computeEdgeCorridorSegments(
      srcPos: { x: number; y: number },
      srcSize: { w: number; h: number },
      tgtPos: { x: number; y: number },
      tgtSize: { w: number; h: number }
    ): Array<{ x: number; y: number; w: number; h: number }> {
      const pad = EDGE_CORRIDOR_PADDING;
      const corridorThickness = pad * 2; 

      if (isVertical) {
        const sx = srcPos.x + srcSize.w / 2 - SOCKET_OFFSET;
        const sy = srcPos.y + srcSize.h; // bottom of source
        const tx = tgtPos.x + tgtSize.w / 2 + SOCKET_OFFSET;
        const ty = tgtPos.y; // top of target

        const dy = ty - sy;
        const dx = tx - sx;

        if (dy > 40 && Math.abs(dx) > 5) {

          const yMid = (sy + ty) / 2;

          return [
  
            {
              x: sx - pad,
              y: sy,
              w: corridorThickness,
              h: yMid - sy,
            },

            {
              x: Math.min(sx, tx) - pad,
              y: yMid - pad,
              w: Math.abs(dx) + corridorThickness,
              h: corridorThickness,
            },

            {
              x: tx - pad,
              y: yMid,
              w: corridorThickness,
              h: ty - yMid,
            },
          ];
        }

        if (dy > 40 && Math.abs(dx) <= 5) {
          const midX = (sx + tx) / 2;
          return [
            {
              x: midX - pad,
              y: sy,
              w: corridorThickness,
              h: ty - sy,
            },
          ];
        }

        if (dy <= 0) {
          const loopOffset = 100;
          if (dx >= 0) {
            const loopX = Math.max(sx, tx) + loopOffset;
            return [
       
              { x: sx - pad, y: sy, w: corridorThickness, h: 50 },
           
              {
                x: sx,
                y: sy + 50 - pad,
                w: loopX - sx + pad,
                h: corridorThickness,
              },

              {
                x: loopX - pad,
                y: ty - 50,
                w: corridorThickness,
                h: sy + 50 - (ty - 50),
              },
     
              {
                x: tx - pad,
                y: ty - 50 - pad,
                w: loopX - tx + corridorThickness,
                h: corridorThickness,
              },
      
              { x: tx - pad, y: ty - 50, w: corridorThickness, h: 50 },
            ];
          } else {
            const loopX = Math.min(sx, tx) - loopOffset;
            return [
              { x: sx - pad, y: sy, w: corridorThickness, h: 50 },
              {
                x: loopX - pad,
                y: sy + 50 - pad,
                w: sx - loopX + corridorThickness,
                h: corridorThickness,
              },
              {
                x: loopX - pad,
                y: ty - 50,
                w: corridorThickness,
                h: sy + 50 - (ty - 50),
              },
              {
                x: loopX - pad,
                y: ty - 50 - pad,
                w: tx - loopX + corridorThickness,
                h: corridorThickness,
              },
              { x: tx - pad, y: ty - 50, w: corridorThickness, h: 50 },
            ];
          }
        }

        return [
          {
            x: Math.min(sx, tx) - pad,
            y: Math.min(sy, ty),
            w: Math.abs(dx) + corridorThickness,
            h: Math.abs(dy),
          },
        ];
      } else {
        const sx = srcPos.x + srcSize.w;
        const sy = srcPos.y + srcSize.h / 2;
        const tx = tgtPos.x;
        const ty = tgtPos.y + tgtSize.h / 2;

        const dx = tx - sx;
        const dy = ty - sy;

        if (dx > 40 && Math.abs(dy) > 5) {
          const xMid = (sx + tx) / 2;
          return [
            { x: sx, y: sy - pad, w: xMid - sx, h: corridorThickness },
            {
              x: xMid - pad,
              y: Math.min(sy, ty) - pad,
              w: corridorThickness,
              h: Math.abs(dy) + corridorThickness,
            },
            { x: xMid, y: ty - pad, w: tx - xMid, h: corridorThickness },
          ];
        }

        return [
          {
            x: Math.min(sx, tx),
            y: Math.min(sy, ty) - pad,
            w: Math.abs(dx),
            h: Math.abs(dy) + corridorThickness,
          },
        ];
      }
    }

    function rectsOverlap(
      a: { x: number; y: number; w: number; h: number },
      b: { x: number; y: number; w: number; h: number }
    ): boolean {
      return (
        a.x < b.x + b.w &&
        a.x + a.w > b.x &&
        a.y < b.y + b.h &&
        a.y + a.h > b.y
      );
    }

    const MAX_COLLISION_ITERATIONS = 3;

    for (let iteration = 0; iteration < MAX_COLLISION_ITERATIONS; iteration++) {
      let anyNudged = false;

      for (const edge of dagEdges) {
        const srcPos = finalPositions.get(edge.source);
        const tgtPos = finalPositions.get(edge.target);
        if (!srcPos || !tgtPos) continue;

        const srcSz = nodeSize(edge.source);
        const tgtSz = nodeSize(edge.target);

        const corridorSegments = computeEdgeCorridorSegments(
          srcPos,
          srcSz,
          tgtPos,
          tgtSz
        );

        for (const nodeId of realNodeIds) {
          if (nodeId === edge.source || nodeId === edge.target) continue;

          const nPos = finalPositions.get(nodeId);
          if (!nPos) continue;
          const nSz = nodeSize(nodeId);

          const nodeRect = {
            x: nPos.x,
            y: nPos.y,
            w: nSz.w,
            h: nSz.h,
          };

          for (const seg of corridorSegments) {
            if (rectsOverlap(nodeRect, seg)) {

              const segCenterCross = isVertical
                ? seg.x + seg.w / 2
                : seg.y + seg.h / 2;
              const nodeCenterCross = isVertical
                ? nPos.x + nSz.w / 2
                : nPos.y + nSz.h / 2;

              const nudgeDir = nodeCenterCross >= segCenterCross ? 1 : -1;

              let overlapAmount: number;
              if (isVertical) {
                if (nudgeDir > 0) {
                  overlapAmount = seg.x + seg.w - nPos.x;
                } else {
                  overlapAmount = nPos.x + nSz.w - seg.x;
                }
                nPos.x += nudgeDir * (overlapAmount + NODE_SPACING / 2);
              } else {
                if (nudgeDir > 0) {
                  overlapAmount = seg.y + seg.h - nPos.y;
                } else {
                  overlapAmount = nPos.y + nSz.h - seg.y;
                }
                nPos.y += nudgeDir * (overlapAmount + NODE_SPACING / 2);
              }

              finalPositions.set(nodeId, nPos);
              anyNudged = true;
              break;
            }
          }
        }
      }

      for (const edgeKey of backEdges) {
        const parts = edgeKey.split("->");
        const sourceId = parts[0];
        const targetId = parts[1];
        if (!sourceId || !targetId) continue;
        const srcPos = finalPositions.get(sourceId);
        const tgtPos = finalPositions.get(targetId);
        if (!srcPos || !tgtPos) continue;

        const srcSz = nodeSize(sourceId);
        const tgtSz = nodeSize(targetId);

        const corridorSegments = computeEdgeCorridorSegments(
          srcPos,
          srcSz,
          tgtPos,
          tgtSz
        );

        for (const nodeId of realNodeIds) {
          if (nodeId === sourceId || nodeId === targetId) continue;
          const nPos = finalPositions.get(nodeId);
          if (!nPos) continue;
          const nSz = nodeSize(nodeId);
          const nodeRect = { x: nPos.x, y: nPos.y, w: nSz.w, h: nSz.h };

          for (const seg of corridorSegments) {
            if (rectsOverlap(nodeRect, seg)) {
              const segCenter = isVertical
                ? seg.x + seg.w / 2
                : seg.y + seg.h / 2;
              const nodeCenter = isVertical
                ? nPos.x + nSz.w / 2
                : nPos.y + nSz.h / 2;
              const dir = nodeCenter >= segCenter ? 1 : -1;

              if (isVertical) {
                const overlap =
                  dir > 0
                    ? seg.x + seg.w - nPos.x
                    : nPos.x + nSz.w - seg.x;
                nPos.x += dir * (overlap + NODE_SPACING / 2);
              } else {
                const overlap =
                  dir > 0
                    ? seg.y + seg.h - nPos.y
                    : nPos.y + nSz.h - seg.y;
                nPos.y += dir * (overlap + NODE_SPACING / 2);
              }
              finalPositions.set(nodeId, nPos);
              anyNudged = true;
              break;
            }
          }
        }
      }

      if (!anyNudged) break;
    }

    for (const node of realNodes) {
      const pos = finalPositions.get(node.id);
      if (pos) {
        await area.translate(node.id, pos);
      }
    }

    AreaExtensions.zoomAt(area, editor.getNodes());
  };

  return { layoutNodes };
}