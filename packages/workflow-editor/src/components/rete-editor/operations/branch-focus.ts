// ─── Branch focus (SA-2452) ─────────────────────────────────────────────────
//
// Clicking an edge (arrow) on the canvas highlights the WHOLE path that runs
// through that arrow — from the start of the flow, through the arrow, to the end
// — and dims/blurs everything not on that path. This module holds the pure,
// framework-agnostic resolution logic (unit-testable without Rete) plus a small
// stateful controller the canvas drives on edge / background clicks.
//
// Model: the focused set of a clicked edge E (source → target) is its cone —
//   ancestors(source) ∪ {source, target} ∪ descendants(target)
// and the edges among them (plus E). Everything else dims. This is robust for
// any DAG shape: fan-out/fan-in, nested switches, and cross-links (a sibling
// input to a merge, or a direct split→merge shortcut, is off-path and dims).
// If nothing can be dimmed (the arrow is on every path — a trunk edge, or a
// linear flow) the result is the full view.

export interface BranchGraphNode {
    id: string;
}

export interface BranchGraphEdge {
    id: string;
    source: string;
    target: string;
}

export interface BranchFocusResult {
    /** true => no isolation possible (arrow on every path); render everything normal. */
    isFullView: boolean;
    /** the whole path through the clicked arrow — highlighted start→end. */
    focusNodeIds: string[];
    focusEdgeIds: string[];
    /** everything off that path — dimmed + blurred. */
    dimNodeIds: string[];
    dimEdgeIds: string[];
}

const FULL_VIEW: BranchFocusResult = {
    isFullView: true,
    focusNodeIds: [],
    focusEdgeIds: [],
    dimNodeIds: [],
    dimEdgeIds: [],
};

interface Graph {
    edgeById: Map<string, BranchGraphEdge>;
    outEdges: Map<string, BranchGraphEdge[]>;
    inEdges: Map<string, BranchGraphEdge[]>;
}

function buildGraph(nodes: BranchGraphNode[], edges: BranchGraphEdge[]): Graph {
    const edgeById = new Map<string, BranchGraphEdge>();
    const outEdges = new Map<string, BranchGraphEdge[]>();
    const inEdges = new Map<string, BranchGraphEdge[]>();
    for (const n of nodes) {
        outEdges.set(n.id, []);
        inEdges.set(n.id, []);
    }
    for (const e of edges) {
        edgeById.set(e.id, e);
        outEdges.get(e.source)?.push(e);
        inEdges.get(e.target)?.push(e);
    }
    return { edgeById, outEdges, inEdges };
}

/**
 * All nodes reachable from `start` by walking `up` (predecessors) or `down`
 * (successors), inclusive of `start`. Cycle-safe.
 */
function reachable(g: Graph, start: string, dir: "up" | "down"): Set<string> {
    const seen = new Set<string>([start]);
    const stack = [start];
    for (;;) {
        const cur = stack.pop();
        if (cur === undefined) break;
        const es = dir === "up" ? (g.inEdges.get(cur) ?? []) : (g.outEdges.get(cur) ?? []);
        for (const e of es) {
            const next = dir === "up" ? e.source : e.target;
            if (!seen.has(next)) {
                seen.add(next);
                stack.push(next);
            }
        }
    }
    return seen;
}

/**
 * Resolve the highlight/dim sets for clicking `clickedEdgeId`.
 * Pure — safe on any {id}/{id,source,target} arrays (e.g. IEditorValue).
 */
export function resolveBranchFocus(
    nodes: BranchGraphNode[],
    edges: BranchGraphEdge[],
    clickedEdgeId: string,
): BranchFocusResult {
    const g = buildGraph(nodes, edges);
    const clicked = g.edgeById.get(clickedEdgeId);
    if (!clicked) return { ...FULL_VIEW };

    // The path through the arrow = upstream cone of source + downstream cone of target.
    const upNodes = reachable(g, clicked.source, "up"); // ancestors + source
    const downNodes = reachable(g, clicked.target, "down"); // descendants + target
    const coneNodes = new Set<string>([...upNodes, ...downNodes]);

    const coneEdges = new Set<string>([clicked.id]);
    for (const e of edges) {
        // an edge is on the path iff it lies wholly within the upstream cone or
        // wholly within the downstream cone (or it is the clicked edge itself).
        if ((upNodes.has(e.source) && upNodes.has(e.target)) ||
            (downNodes.has(e.source) && downNodes.has(e.target))) {
            coneEdges.add(e.id);
        }
    }

    const dimNodeIds = nodes.filter((n) => !coneNodes.has(n.id)).map((n) => n.id);
    const dimEdgeIds = edges.filter((e) => !coneEdges.has(e.id)).map((e) => e.id);

    // Nothing to isolate → full view (trunk arrow / linear flow).
    if (dimNodeIds.length === 0 && dimEdgeIds.length === 0) return { ...FULL_VIEW };

    return {
        isFullView: false,
        focusNodeIds: [...coneNodes],
        focusEdgeIds: [...coneEdges],
        dimNodeIds,
        dimEdgeIds,
    };
}

// ─── Focus controller ────────────────────────────────────────────────────────
//
// Transient view state driven by the canvas: focus a path on edge click, switch
// in one gesture (call focusOnEdge again), clear on empty-canvas click. Nothing
// is persisted — matches the ticket's "focus is transient" decision.

export type BranchFocusMode = "full" | "focused";

export interface BranchFocusView {
    mode: BranchFocusMode;
    result: BranchFocusResult;
}

export class BranchFocusController {
    private nodes: BranchGraphNode[];
    private edges: BranchGraphEdge[];
    private current: BranchFocusResult = { ...FULL_VIEW };

    constructor(nodes: BranchGraphNode[], edges: BranchGraphEdge[]) {
        this.nodes = nodes;
        this.edges = edges;
    }

    /** Update the underlying graph (e.g. after the flow changes). Clears focus. */
    setGraph(nodes: BranchGraphNode[], edges: BranchGraphEdge[]): void {
        this.nodes = nodes;
        this.edges = edges;
        this.current = { ...FULL_VIEW };
    }

    /** Focus (or switch to) the path owning `edgeId`. Trunk/linear edges → full view. */
    focusOnEdge(edgeId: string): BranchFocusView {
        this.current = resolveBranchFocus(this.nodes, this.edges, edgeId);
        return this.getView();
    }

    /** Clear focus — restores the full view (empty-canvas click). */
    clear(): BranchFocusView {
        this.current = { ...FULL_VIEW };
        return this.getView();
    }

    getView(): BranchFocusView {
        return { mode: this.current.isFullView ? "full" : "focused", result: this.current };
    }
}
