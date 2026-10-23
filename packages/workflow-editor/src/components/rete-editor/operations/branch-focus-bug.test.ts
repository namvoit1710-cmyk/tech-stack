import { describe, expect, it } from "@rstest/core";
import { resolveBranchFocus, type BranchGraphEdge, type BranchGraphNode } from "./branch-focus";

// Regression: cross-link topology a->b, c->d, AND c->b.
// Embedded in a flow: trigger->a, trigger->c ; b->resp, d->resp.
//   c: out-deg 2 (b,d) => split ;  b: in-deg 2 (a,c) => merge
// Bug was: focusing the a->b path left the sibling input c->b sharp, and clicking
// c->b highlighted a lone edge without dimming its parallel branches.
const nodes: BranchGraphNode[] = [
    { id: "trigger" }, { id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }, { id: "resp" },
];
const edges: BranchGraphEdge[] = [
    { id: "t_a", source: "trigger", target: "a" },
    { id: "t_c", source: "trigger", target: "c" },
    { id: "a_b", source: "a", target: "b" },
    { id: "c_d", source: "c", target: "d" },
    { id: "c_b", source: "c", target: "b" },
    { id: "b_r", source: "b", target: "resp" },
    { id: "d_r", source: "d", target: "resp" },
];

describe("cross-link (a>b, c>d, c>b) — dim is path-through-arrow, not lane-only", () => {
    it("clicking a->b dims the sibling input c->b (was left sharp before the fix)", () => {
        const r = resolveBranchFocus(nodes, edges, "a_b");
        expect(r.isFullView).toBe(false);
        expect(r.focusNodeIds).toContain("a");
        // the parallel input into merge b must dim
        expect(r.dimEdgeIds).toContain("c_b");
        // c's other lane (c->d) and node d are off-path -> dim
        expect(r.dimNodeIds).toEqual(expect.arrayContaining(["c", "d"]));
        // nothing on the a->b path is dimmed
        expect(r.dimEdgeIds).not.toContain("a_b");
        expect(r.dimEdgeIds).not.toContain("t_a");
        expect(r.dimEdgeIds).not.toContain("b_r");
    });

    it("clicking the c->b shortcut focuses that edge and dims both parallel branches", () => {
        const r = resolveBranchFocus(nodes, edges, "c_b");
        expect(r.isFullView).toBe(false);
        expect(r.focusEdgeIds).toContain("c_b");
        // a (other input to b) and d (other output of c) are off-path -> dim
        expect(r.dimNodeIds).toEqual(expect.arrayContaining(["a", "d"]));
        expect(r.dimEdgeIds).toEqual(expect.arrayContaining(["a_b", "c_d"]));
        // the c->b path context stays sharp (not dimmed)
        expect(r.dimNodeIds).not.toContain("c");
        expect(r.dimNodeIds).not.toContain("b");
        expect(r.dimNodeIds).not.toContain("resp");
    });

    it("clicking c->d focuses d's lane and dims a's lane", () => {
        const r = resolveBranchFocus(nodes, edges, "c_d");
        expect(r.isFullView).toBe(false);
        expect(r.focusNodeIds).toContain("d");
        expect(r.dimNodeIds).toContain("a");
    });
});
