import Page from "@/components/containers/page"
import { WORKFLOW_TAB_ENUM } from "@/constants/workflows"
import WorkflowExecutionHistory from "@/features/workflows/components/execution-history/execution-history"
import WorkflowCreateModal from "@/features/workflows/components/modals/workflow-create-modal"
import WorkflowCreateChatbotPanel from "@/features/workflows/components/panels/workflow-create-chatbot-panel"
import WorkflowList from "@/features/workflows/components/workflow-list/workflow-list"
import { useFavoriteWorkflowIds, workflowKey } from "@/features/workflows/hooks/apis/workflows"
import { useLanguage } from "@/hooks/use-language"
import { useQueryClient } from "@ldc/tanstack-query"
import { Button } from "@ldc/ui/components/button"
import { ResizablePanel, ResizablePanelGroup } from "@ldc/ui/components/resizable"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@ldc/ui/components/tabs"
import { PlusIcon, SparklesIcon } from "lucide-react"
import { parseAsString, useQueryState } from "nuqs"
import { useState } from "react"
import { useNavigate } from "react-router-dom"

const WorkflowsPage = () => {
    const { t } = useLanguage()

    const navigate = useNavigate()

    const [activeTab, setActiveTab] = useQueryState("tab", parseAsString.withDefault(WORKFLOW_TAB_ENUM.WORKFLOWS))
    const [isCreateWorkflowPopup, setIsCreateWorkflowPopup] = useState(false);
    const [isCreateWorkflowChatbotOpen, setIsCreateWorkflowChatbotOpen] = useState(false);
    // SA-1582: mint a fresh Create-with-AI conversation id on each page load / refresh.
    // Held at page scope so it survives opening/closing the chat within one visit but
    // never persists across reloads.
    const [createWithAiConvId] = useState(() => crypto.randomUUID());

    const queryClient = useQueryClient()
    // SA-2942: count badge on the Favorites tab.
    const { data: favoriteIds } = useFavoriteWorkflowIds()

    const handleCreateWorkflowSuccess = (workflowId: string) => {
        setIsCreateWorkflowPopup(false)
        setIsCreateWorkflowChatbotOpen(false)

        if (workflowId) {
            navigate(`/workflow/${workflowId}`)
        }

        queryClient.invalidateQueries({ queryKey: workflowKey.all })
    }

    // AI chat: don't auto-open the workflow. Keep the chat panel open so the user
    // can click the "View Workflow" link in the message when ready; just refresh
    // the list behind the panel.
    const handleAiWorkflowCreated = () => {
        queryClient.invalidateQueries({ queryKey: workflowKey.all })
    }

    return (
        <>
            <ResizablePanelGroup className="overflow-hidden">
                <ResizablePanel className="h-full overflow-hidden">
                    <Page>
                        <Page.Header
                            title={t("overview")}
                            description={t("overview_description")}
                            actions={
                                <div className="flex gap-2">
                                    <Button size="sm" variant="outline" onClick={() => setIsCreateWorkflowPopup(true)}>
                                        <PlusIcon />
                                        <span>{t("create_manually")}</span>
                                    </Button>
                                    <Button size="sm" onClick={() => setIsCreateWorkflowChatbotOpen(true)}>
                                        <SparklesIcon className="w-5 h-5" />
                                        <span>{t("create_with_ai")}</span>
                                    </Button>
                                </div>
                            }
                        />

                        <Tabs
                            value={activeTab}
                            onValueChange={(value) => setActiveTab(value)}
                            className="h-full overflow-hidden flex flex-col gap-0"
                        >
                            <TabsList variant="line" className="px-4 border-b border-b-border justify-start w-full">
                                <TabsTrigger value={WORKFLOW_TAB_ENUM.WORKFLOWS} className="flex-0 cursor-pointer data-active:after:bg-primary">
                                    {t("workflows")}
                                </TabsTrigger>

                                <TabsTrigger value={WORKFLOW_TAB_ENUM.EXECUTION_HISTORY} className="flex-0 cursor-pointer data-active:after:bg-primary">
                                    {t("execution_history")}
                                </TabsTrigger>

                                <TabsTrigger value={WORKFLOW_TAB_ENUM.FAVORITES} className="flex-0 cursor-pointer data-active:after:bg-primary">
                                    {t("favorites")}
                                    <span className="ml-1.5 rounded-full bg-muted px-1.5 text-xs font-semibold tabular-nums">{favoriteIds?.length ?? 0}</span>
                                </TabsTrigger>
                            </TabsList>

                            <TabsContent className="flex-2 overflow-hidden" value={WORKFLOW_TAB_ENUM.WORKFLOWS}>
                                <WorkflowList isActive={activeTab === WORKFLOW_TAB_ENUM.WORKFLOWS} />
                            </TabsContent>

                            <TabsContent className="flex-2 overflow-hidden" value={WORKFLOW_TAB_ENUM.EXECUTION_HISTORY}>
                                <WorkflowExecutionHistory isActive={activeTab === WORKFLOW_TAB_ENUM.EXECUTION_HISTORY} />
                            </TabsContent>

                            <TabsContent className="flex-2 overflow-hidden" value={WORKFLOW_TAB_ENUM.FAVORITES}>
                                <WorkflowList favoritesOnly isActive={activeTab === WORKFLOW_TAB_ENUM.FAVORITES} />
                            </TabsContent>
                        </Tabs>
                    </Page>
                </ResizablePanel>

                {isCreateWorkflowChatbotOpen && (
                    <ResizablePanel
                        minSize={400}
                        defaultSize={500}
                        className="border-l h-full overflow-hidden"
                    >
                        <WorkflowCreateChatbotPanel
                            convId={createWithAiConvId}
                            onClose={() =>
                                setIsCreateWorkflowChatbotOpen(false)
                            }
                            onWorkflowCreated={handleAiWorkflowCreated}
                        />
                    </ResizablePanel>
                )}
            </ResizablePanelGroup>

            <WorkflowCreateModal
                open={isCreateWorkflowPopup}
                onOpenChange={(open) => { setIsCreateWorkflowPopup(open) }}
                onSave={handleCreateWorkflowSuccess}
            />
        </>
    )
}

export default WorkflowsPage