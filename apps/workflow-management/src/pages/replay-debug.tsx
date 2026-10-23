import Page from "@/components/containers/page";
import LoadingOverlay from "@/features/workflows/components/loading-overlay/loading-overlay";
import ReplayScrubber from "@/features/workflows/components/execution-history/replay-scrubber";
import { useWorkflowDetail } from "@/features/workflows/hooks/use-workflow-detail-page";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { ResizablePanel, ResizablePanelGroup } from "@ldc/ui/components/resizable";
import type { WorkflowEditorHandle } from "@ldc/workflow-editor";
import { WorkflowEditor } from "@ldc/workflow-editor";
import { ArrowLeft } from "lucide-react";
import { useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";

/**
 * SA-1372 Replay Debug view — the n8n-style debug layout: the workflow editor
 * canvas opens READ-ONLY on the left, a docked ReplayScrubber on the right.
 * Scrubbing the recorded event log paints node status + centers the cursor node
 * on the canvas via the editor handle (same mechanism as live execution). The
 * run is never mutated — read-side time-travel only.
 *
 * ponytail: loads the workflow's current/latest definition for the canvas. If a
 * run executed an older version whose topology diverged, node ids may not match
 * — pin the run's version here when that case appears.
 */
const ReplayDebugPage = () => {
    const { workflowId, runId } = useParams();
    const navigate = useNavigate();
    const editorRef = useRef<WorkflowEditorHandle | null>(null);

    const { workflow, isLoading } = useWorkflowDetail(workflowId!);
    const workflowData = useEditorStore((s) => s.workflowData);

    const goBack = () => navigate(-1);

    return (
        <Page className="gap-0">
            <Page.Header
                title={
                    <div className="flex items-center gap-2">
                        <span>{workflow?.name ?? "Workflow"}</span>
                        <Badge variant="outline" className="whitespace-nowrap">Replay · read-only</Badge>
                    </div>
                }
                className="border-b"
                actions={
                    <Button variant="outline" size="sm" onClick={goBack} data-testid="replay-back">
                        <ArrowLeft className="size-4" />
                        Back to history
                    </Button>
                }
            />

            <div className={cn("relative flex flex-col overflow-hidden h-full w-full")} data-testid="replay-debug-view">
                <ResizablePanelGroup direction="horizontal" className="flex-1 overflow-hidden">
                    <ResizablePanel defaultSize={"68%"} minSize={"40%"}>
                        <div className="relative h-full w-full overflow-hidden" data-testid="replay-canvas">
                            <WorkflowEditor value={workflowData} ref={editorRef} readOnly />
                        </div>
                    </ResizablePanel>
                    <ResizablePanel defaultSize={"32%"} minSize={"24%"}>
                        {runId ? (
                            <ReplayScrubber runId={runId} editorRef={editorRef} onClose={goBack} />
                        ) : null}
                    </ResizablePanel>
                </ResizablePanelGroup>

                <LoadingOverlay isLoading={isLoading} />
            </div>
        </Page>
    );
};

export default ReplayDebugPage;
