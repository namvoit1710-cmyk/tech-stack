// =============================================================================
// AI Assistant Feature — Main Panel Component
// =============================================================================
//
// Entry point duy nhất cho UI. Compose:
// 1. useAIAssistantRuntime  → wires external store runtime
// 2. AIAssistantProvider    → provides internal messages cho child lookup
// 3. AssistantRuntimeProvider → assistant-ui context
// 4. AIAssistantThread      → customized thread UI
//
// Pattern tương tự WorkflowDetailPage: shell component chỉ compose,
// không chứa business logic.
// =============================================================================

import { AIAssistantThread } from "@/features/ai-assistant/components/thread";
import { AIAssistantProvider } from "@/features/ai-assistant/context/assistant-context";
import { useAIAssistantRuntime } from "@/features/ai-assistant/hooks/use-ai-asssistant-runtime";
import { useLanguage } from "@/hooks/use-language";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { Button } from "@ldc/ui/components/button";
import { SparklesIcon, XIcon } from "lucide-react";

interface WorkflowCreateChatbotPanelProps {
    // Client-minted conversation id, fresh per page load (owned by WorkflowsPage).
    convId: string;
    onClose?: () => void;
    onWorkflowCreated?: (workflowId: string) => void;
}

const WorkflowCreateChatbotPanel = ({
    convId,
    onClose,
    onWorkflowCreated,
}: WorkflowCreateChatbotPanelProps) => {
    const { t } = useLanguage();
    const { runtime, messages } = useAIAssistantRuntime({
        convId,
        onWorkflowCreated,
    });

    return (
        <AssistantRuntimeProvider runtime={runtime}>
            <AIAssistantProvider messages={messages}>
                {/* SA-1582: claymorphism shell — soft pastel wash + layered depth. */}
                <div className="flex h-full w-full flex-col overflow-hidden bg-linear-to-b from-primary/5 via-background to-background">
                    <div className="flex items-center justify-between border-b border-border/50 bg-background/70 px-6 py-3 shadow-[0_2px_8px_-4px_rgba(15,23,42,0.12)] backdrop-blur-sm">
                        <div className="flex items-center gap-2.5">
                            <span className="flex size-8 items-center justify-center rounded-2xl bg-primary/10 text-primary shadow-[inset_0_1px_2px_rgba(255,255,255,0.6),0_2px_6px_-2px_rgba(15,23,42,0.25)]">
                                <SparklesIcon className="size-4" />
                            </span>
                            <h3 className="text-lg font-semibold">
                                {t("create_with_ai") || "Create with AI"}
                            </h3>
                        </div>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={onClose}
                            className="size-8 rounded-full"
                        >
                            <XIcon className="size-4" />
                        </Button>
                    </div>

                    <AIAssistantThread />
                </div>
            </AIAssistantProvider>
        </AssistantRuntimeProvider>
    );
};

export default WorkflowCreateChatbotPanel;