// ─── Branch focus — canvas application (SA-2452) ─────────────────────────────
//
// Bridges the pure `resolveBranchFocus` logic (see branch-focus.ts) to the live
// Rete editor: it reads the current nodes/edges, computes the focus result, then
//   • edges  → set `connection.branchFocus` and re-render via `area.update`
//              (LineConnection reads `data.branchFocus` and styles itself),
//   • nodes  → set the dim/focus look directly on each node view's DOM element,
//              so we don't have to thread a prop through every node component.
//
// Clicking a trunk arrow, a linear-flow arrow, or empty canvas => full view.

import type { NodeEditor } from "rete";
import type { AreaPlugin } from "rete-area-plugin";

import type { AreaExtra, BranchFocusState, Schemes } from "../types";
import type { BranchFocusResult } from "./branch-focus";
import { resolveBranchFocus } from "./branch-focus";

// A node view exposes its root DOM element; we style that element directly.
interface NodeViewLike {
    element?: HTMLElement;
}

function nodeElement(area: AreaPlugin<Schemes, AreaExtra>, nodeId: string): HTMLElement | undefined {
    const views = (area as unknown as { nodeViews?: Map<string, NodeViewLike> }).nodeViews;
    return views?.get(nodeId)?.element;
}

const NODE_DIM = { opacity: "0.13", filter: "blur(2px)" };
const NODE_FOCUS_SHADOW = "0 0 0 2px #d9ddfb, 0 6px 18px rgba(79, 70, 229, 0.18)";

function styleNode(el: HTMLElement, state: BranchFocusState | undefined): void {
    // A CSS transition matches the ticket's ~200ms smooth dim/blur.
    el.style.transition = "opacity 0.2s, filter 0.2s, box-shadow 0.2s";
    if (state === "dim") {
        el.style.opacity = NODE_DIM.opacity;
        el.style.filter = NODE_DIM.filter;
        el.style.boxShadow = "";
    } else if (state === "focus") {
        el.style.opacity = "";
        el.style.filter = "";
        el.style.boxShadow = NODE_FOCUS_SHADOW;
    } else {
        el.style.opacity = "";
        el.style.filter = "";
        el.style.boxShadow = "";
    }
}

const FULL_VIEW_RESULT: BranchFocusResult = {
    isFullView: true,
    focusNodeIds: [],
    focusEdgeIds: [],
    dimNodeIds: [],
    dimEdgeIds: [],
};

function applyResult(
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
    r: BranchFocusResult,
): void {
    const focusE = new Set(r.focusEdgeIds);
    const dimE = new Set(r.dimEdgeIds);
    const focusN = new Set(r.focusNodeIds);
    const dimN = new Set(r.dimNodeIds);

    for (const conn of editor.getConnections()) {
        const next: BranchFocusState | undefined = r.isFullView
            ? undefined
            : focusE.has(conn.id)
              ? "focus"
              : dimE.has(conn.id)
                ? "dim"
                : undefined;
        if (conn.branchFocus !== next) {
            conn.branchFocus = next;
            void area.update("connection", conn.id);
        }
    }

    for (const node of editor.getNodes()) {
        const next: BranchFocusState | undefined = r.isFullView
            ? undefined
            : focusN.has(node.id)
              ? "focus"
              : dimN.has(node.id)
                ? "dim"
                : undefined;
        const el = nodeElement(area, node.id);
        if (el) styleNode(el, next);
    }
}

/** Focus (or switch to) the branch owning `clickedEdgeId`; dims the rest. Returns the result. */
export function applyBranchFocus(
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
    clickedEdgeId: string,
): BranchFocusResult {
    const nodes = editor.getNodes().map((n) => ({ id: n.id }));
    const edges = editor.getConnections().map((c) => ({ id: c.id, source: c.source, target: c.target }));
    const result = resolveBranchFocus(nodes, edges, clickedEdgeId);
    applyResult(editor, area, result);
    return result;
}

/** Clear any branch focus — restores the full view (empty-canvas click). */
export function clearBranchFocus(
    editor: NodeEditor<Schemes>,
    area: AreaPlugin<Schemes, AreaExtra>,
): void {
    applyResult(editor, area, FULL_VIEW_RESULT);
}
