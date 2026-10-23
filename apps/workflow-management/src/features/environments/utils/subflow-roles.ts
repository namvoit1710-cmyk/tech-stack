// SA-2605 — derive a workflow's parent / sub-flow role from its graph.
//
// A "call-subflow" node is a WORKFLOW-type node whose `parameters.workflow_id`
// points at the child flow it calls. From that:
//   • PARENT   — a workflow whose own graph contains >=1 WORKFLOW node.
//   • SUB-FLOW — a workflow whose id is referenced by some other workflow's
//                WORKFLOW node (i.e. it is called by another flow).
// A workflow can be BOTH (it calls sub-flows AND is itself called).
import type { IWorkflow, IWorkflowNode } from "@/features/workflows/types/workflows";

const isSubflowCallNode = (node: IWorkflowNode): boolean =>
    (node.node_type ?? node.type) === "WORKFLOW";

// The child (sub-flow) workflow id a WORKFLOW node targets, if any.
export const subflowTargetId = (node: IWorkflowNode): string | undefined => {
    if (!isSubflowCallNode(node)) return undefined;
    const target = (node.parameters as Record<string, unknown> | undefined)?.["workflow_id"];
    return typeof target === "string" && target ? target : undefined;
};

// The sub-flow ids a workflow's graph calls (deduped).
export const calledSubflowIds = (workflow?: Pick<IWorkflow, "nodes">): string[] => {
    if (!workflow?.nodes) return [];
    const ids = new Set<string>();
    for (const node of workflow.nodes) {
        const id = subflowTargetId(node);
        if (id) ids.add(id);
    }
    return Array.from(ids);
};

// Does a workflow's own graph call any sub-flow? (=> it is a parent.)
export const isParentWorkflow = (workflow?: Pick<IWorkflow, "nodes">): boolean =>
    calledSubflowIds(workflow).length > 0;

export interface IWorkflowRole {
    parent: boolean;
    subflow: boolean;
}
