import { useLanguage } from "@/hooks/use-language"
import { useQueryClient } from "@ldc/tanstack-query"
import { Button } from "@ldc/ui/components/button"
import { LoadingSpin } from "@ldc/workflow-editor"
import { CircleStop, MoreHorizontalIcon, PlayIcon, SaveIcon } from "lucide-react"
import { useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"
import { workflowKey } from "../../hooks/apis/workflows"
import { useEditorStore } from "../../stores/editor-stores"
import { useUIPanelStore } from "../../stores/ui-panel-stores"
import RunConfigModal from "../modals/run-config-modal"
import WorkflowRenameDescriptionModal from "../modals/workflow-rename-description-modal"
import WorkflowRenameModal from "../modals/workflow-rename-modal"
import WorkflowRenameRoutingPathModal from "../modals/workflow-rename-routing-modal"
import { WorkflowVersionSheetTrigger } from "../workflow-version-sheet"
import MenuConfigSetting, { MenuActionEnum } from "./more-config-menu"

interface IWorkflowHeaderProps {
    workflowId: string
    workflowTitle: string
    isPrepareWorkflowExecuting?: boolean
    isRunWorkflowExecuting?: boolean
    isSaving?: boolean
    isPublishing?: boolean
    onVersionChange?: (versionId: string) => void
    onVersionSheetOpen?: () => void
    onPublish?: () => void
    onStop: () => void
    onExecute: () => void
    onSave: (name?: string) => void
}

const WorkflowDetailHeaderAction = ({
    workflowId,
    workflowTitle,
    isPrepareWorkflowExecuting,
    isRunWorkflowExecuting,
    isSaving,
    isPublishing,
    onVersionChange,
    onVersionSheetOpen,
    onPublish,
    onSave,
    onExecute,
    onStop
}: IWorkflowHeaderProps) => {
    const { t } = useLanguage()
    const navigate = useNavigate()
    const location = useLocation();
    const routerState = location.state as { listSearch?: string } | undefined;

    const workflowInfo = useEditorStore((state) => state.workflowInfo);
    const selectedVersionId = useEditorStore((state) => state.selectedVersionId);

    const openRunConfigModal = useUIPanelStore((s) => s.openRunConfigModal);

    const [isReplaceWorkflowNameOpen, setIsReplaceWorkflowNameOpen] = useState(false);
    const [isReplaceWorkflowDescriptionOpen, setIsReplaceWorkflowDescriptionOpen] = useState(false);
    const [isSettingRoutingPathOpen, setIsSettingRoutingPathOpen] = useState(false);

    const handleMenuAction = (action: MenuActionEnum) => {
        switch (action) {
            case MenuActionEnum.ReplaceName:
                setIsReplaceWorkflowNameOpen(true);
                break;
            case MenuActionEnum.ReplaceDescription:
                setIsReplaceWorkflowDescriptionOpen(true);
                break;
            case MenuActionEnum.SettingRoutingPath:
                setIsSettingRoutingPathOpen(true);
                break;
            case MenuActionEnum.SelectWorkerInstance:
                openRunConfigModal();
                break;
        }
    }

    const queryClient = useQueryClient();

    const refreshWorkflowMetadata = () => {
        if (!workflowInfo?.id) {
            return;
        }

        void Promise.all([
            queryClient.invalidateQueries({ queryKey: workflowKey.getWorkflowById(workflowInfo.id) }),
            queryClient.invalidateQueries({ queryKey: workflowKey.getWorkflowVersions(workflowInfo.id) }),
        ]);
    };

    return (
        <>
            <div className="flex items-center justify-end gap-2">
                <Button
                    variant="link"
                    disabled={isSaving}
                    onClick={() => {
                        navigate(`/`, { state: routerState });
                    }}
                >
                    {t("back_to_workflow")}
                </Button>

                <WorkflowVersionSheetTrigger
                    onClick={() => onVersionSheetOpen?.()}
                    disabled={isSaving || isPublishing}
                />

                <Button
                    variant="outline"
                    className="border-border"
                    disabled={isSaving || isPublishing}
                    onClick={() => onPublish?.()}
                >
                    {isPublishing ? <LoadingSpin /> : null}
                    {t("publish")}
                </Button>

                <Button
                    variant="outline"
                    className="border-border"
                    disabled={isRunWorkflowExecuting || isPrepareWorkflowExecuting || isSaving}
                    onClick={() => onSave()}
                >
                    {isSaving ? <LoadingSpin /> : <SaveIcon strokeWidth={1.5} />}
                    {t("save")}
                </Button>

                {/* SA-2365: environment selection moved into the "run workflow"
                    confirm popup (InteractionModalWrapper) — picked right before
                    Continue triggers the run. */}
                {/* SA-2484: "Select worker instance" (debug routing) is an advanced
                    config — moved from a top-level button into the ⋯ menu below. */}

                <Button
                    disabled={isPrepareWorkflowExecuting || isSaving}
                    variant={(isRunWorkflowExecuting && !isPrepareWorkflowExecuting) ? "destructive" : "default"}
                    onClick={isRunWorkflowExecuting ? onStop : onExecute}
                >
                    {isRunWorkflowExecuting ? isPrepareWorkflowExecuting ? <LoadingSpin /> : <CircleStop /> : <PlayIcon />}
                    {(isRunWorkflowExecuting || isPrepareWorkflowExecuting) ? t("executing") : t("execute")}
                </Button>

                <MenuConfigSetting
                    onAction={handleMenuAction}
                >
                    <Button
                        id="more-config-btn"
                        variant="ghost"
                        disabled={isRunWorkflowExecuting || isPrepareWorkflowExecuting || isSaving}
                    >
                        <MoreHorizontalIcon />
                    </Button>
                </MenuConfigSetting>
            </div>

            <WorkflowRenameModal
                open={isReplaceWorkflowNameOpen}
                defaultValues={{ name: workflowTitle, id: workflowInfo?.id ?? "" }}
                onOpenChange={(open) => setIsReplaceWorkflowNameOpen(open)}
                onSaved={() => {
                    refreshWorkflowMetadata();
                    setIsReplaceWorkflowNameOpen(false);
                }}
            />

            <WorkflowRenameDescriptionModal
                open={isReplaceWorkflowDescriptionOpen}
                defaultValues={{ id: workflowInfo?.id ?? "", description: workflowInfo?.description ?? "" }}
                onOpenChange={(open) => setIsReplaceWorkflowDescriptionOpen(open)}
                onSaved={() => {
                    refreshWorkflowMetadata();
                    setIsReplaceWorkflowDescriptionOpen(false);
                }}
            />

            <WorkflowRenameRoutingPathModal
                open={isSettingRoutingPathOpen}
                defaultValues={{ id: workflowInfo?.id ?? "", routing_path: workflowInfo?.routing_path ?? "" }}
                onOpenChange={(open) => setIsSettingRoutingPathOpen(open)}
                onSaved={() => {
                    refreshWorkflowMetadata();
                    setIsSettingRoutingPathOpen(false);
                }}
            />

            <RunConfigModal />
        </>
    )
}

export default WorkflowDetailHeaderAction
