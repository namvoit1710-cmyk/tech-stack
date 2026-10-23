import { useMessageBox } from "@/components/providers/messagebox-provider";
import { useLanguage } from "@/hooks/use-language";
import { formatVersionLabel } from "@/features/workflows/utils/version-utils";
import { keepPreviousData, useQueryClient } from "@ldc/tanstack-query";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Switch } from "@ldc/ui/components/switch";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { HeartIcon, MoreHorizontalIcon, Trash2Icon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ListPager } from "../common/list-pager";
import { ManualWorkChip } from "../common/manual-work-chip";
import { RatingChip } from "../common/rating-chip";
import { RelativeTime } from "../common/relative-time";
import { type FavoriteIdsEnvelope, useDeleteWorkflow, useFavoriteWorkflowIds, useModifyMainFlowWorkflow, useSetFavoriteWorkflow, useWorkflowList, useWorkflowRatings, workflowKey } from "../../hooks/apis/workflows";
import useSearchParamsQuery from "../../hooks/use-search-params-query";
import type { IWorkflowListResponse, IWorkflowParams } from "../../types/workflows";
import WorkflowChooseOwnerModal from "../modals/workflow-choose-owner-modal";
import WorkflowSetManualTimeModal from "../modals/workflow-set-manual-time-modal";
import WorkflowRenameDescriptionModal from "../modals/workflow-rename-description-modal";
import WorkflowRenameModal from "../modals/workflow-rename-modal";
import WorkflowRenameRoutingPathModal from "../modals/workflow-rename-routing-modal";
import MenuConfigSetting, { MenuActionEnum } from "../workflow-header-action/more-config-menu";
import WorkflowFilter from "./workflow-filter";

export interface IWorkflowListProps {
    isActive?: boolean
    // SA-2942: the Favorites tab — only the caller's favorite workflows.
    favoritesOnly?: boolean
}

type WorkflowListItemRow = IWorkflowListResponse["data"]["items"][number];

const WORKFLOW_STATUS_BADGE: Record<string, string> = {
    PUBLISHED: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    DRAFT: "bg-muted text-muted-foreground border-border",
};

const WorkflowList = ({ isActive, favoritesOnly }: IWorkflowListProps) => {

    const { t } = useLanguage();

    const { searchQueryParams, setSearch, setCurrentPage, setMainFlow } = useSearchParamsQuery({ defaultSorting: [{ id: "created_at", desc: true }] })

    const params: IWorkflowParams = useMemo(() => {
        const { currentPage, limit, search, sorting } = searchQueryParams;

        const queryParams: IWorkflowParams = {
            $skip: (currentPage - 1) * limit,
            $top: limit,
            // SA-2252: show the PUBLISHED version per workflow (falls back to latest-edited
            // when a workflow has no published version) so the list reflects what's
            // published rather than an in-progress draft.
            prefer_published: true,
        };

        if (search) {
            queryParams.$filter = `contains(name, '${search}')`;
        }

        if (favoritesOnly) {
            queryParams.$filter = queryParams.$filter ? `${queryParams.$filter} and favorite eq true` : "favorite eq true";
        }

        if (searchQueryParams.mainFlow) {
            queryParams.$filter = queryParams.$filter ? `${queryParams.$filter} and main_flow eq true`
                : `main_flow eq true`;
        }

        if (sorting?.length > 0) {
            queryParams.$orderby = sorting.map((s) => `${s.id} ${s.desc ? "desc" : "asc"}`).join(", ");
        }

        return queryParams;
    }, [searchQueryParams, favoritesOnly])

    const { data: workflowResponse, isLoading, isFetching, refetch } = useWorkflowList(params, {
        enabled: !!isActive,
        placeholderData: keepPreviousData
    })

    const [rowAction, setRowAction] = useState<{
        row: WorkflowListItemRow | null,
        action: MenuActionEnum | ""
    }>({
        row: null,
        action: ""
    })

    const selectedRow = rowAction.row;
    const isReplaceNameOpen = rowAction.action === MenuActionEnum.ReplaceName;
    const isReplaceDescriptionOpen = rowAction.action === MenuActionEnum.ReplaceDescription;
    const isSettingRoutingPathOpen = rowAction.action === MenuActionEnum.SettingRoutingPath;
    const isChooseOwnerOpen = rowAction.action === MenuActionEnum.ChooseOwner;
    const isSetManualTimeOpen = rowAction.action === MenuActionEnum.SetManualTime;

    const tableData = useMemo(() => {
        const search = searchQueryParams.search?.toLowerCase() ?? "";
        let data = workflowResponse?.data?.items ?? []
        if (search) {
            data = data.filter((workflow) => workflow.name.toLowerCase().includes(search))
        }
        return data.map(i => ({ ...i, _id: i.id }))
    }, [workflowResponse, searchQueryParams.search, isActive])

    // SA-2741: batch the aggregate rating for the visible workflows (read-only).
    const ratingIds = useMemo(() => tableData.map((w) => w.id), [tableData]);
    const { data: ratings } = useWorkflowRatings(ratingIds, { enabled: !!isActive && ratingIds.length > 0 });

    const queryClient = useQueryClient()

    // SA-2942: ♥ state comes from my favorite ids; toggling is optimistic and reverts on error.
    const { data: favoriteIds } = useFavoriteWorkflowIds({ enabled: !!isActive });
    const favoriteSet = useMemo(() => new Set(favoriteIds ?? []), [favoriteIds]);
    // Callbacks live on the mutation (not on mutate()): mutate()-level callbacks are dropped
    // when this list unmounts — e.g. ♥ then switch tab before the PUT returns — which left the
    // Favorites list stale and skipped the error revert.
    const { mutate: setFavorite } = useSetFavoriteWorkflow({
        onMutate: async ({ workflowId, favorite }) => {
            const key = workflowKey.favorites();
            await queryClient.cancelQueries({ queryKey: key });
            const previous = queryClient.getQueryData<FavoriteIdsEnvelope>(key);
            const ids = previous?.data?.ids ?? [];
            queryClient.setQueryData(key, { ...previous, data: { ids: favorite ? [workflowId, ...ids] : ids.filter((id) => id !== workflowId) } });
            return { previous };
        },
        onSuccess: (_, { favorite, name }) => {
            toast.success(t(favorite ? "favorite_added" : "favorite_removed", { name }));
            void queryClient.invalidateQueries({ queryKey: workflowKey.favorites() });
            // Mark lists stale WITHOUT refetching the one being clicked on — a refetch flips
            // isFetching (dimmed list + loader bar) and the page jumps on every click.
            void queryClient.invalidateQueries({ queryKey: workflowKey.lists(), refetchType: "none" });
            // ♥ from the Workflows tab while the Favorites tab is already open → refresh it now.
            if (!favoritesOnly) {
                void queryClient.refetchQueries({
                    queryKey: workflowKey.lists(),
                    type: "active",
                    predicate: (q) => JSON.stringify(q.queryKey).includes("favorite eq true"),
                });
            }
        },
        onError: (_, __, ctx) => {
            queryClient.setQueryData(workflowKey.favorites(), ctx?.previous);
            toast.error(t("favorite_update_failed"));
        },
    });
    const handleToggleFavorite = (workflowId: string, name: string) =>
        setFavorite({ workflowId, favorite: !favoriteSet.has(workflowId), name });
    // Favorites tab: an un-♥'d card leaves at once (client-side), no list refetch.
    const visibleRows = favoritesOnly && favoriteIds ? tableData.filter((w) => favoriteSet.has(w.id)) : tableData;

    const { mutate: deleteWorkflow, isPending: isDeleting } = useDeleteWorkflow({
        onSuccess: async () => {
            const { currentPage } = searchQueryParams;
            const currentWorkflowData = queryClient.getQueryData<IWorkflowListResponse>(workflowKey.getAllWorkflows({}))
            if (currentWorkflowData?.data?.items?.length === 1 && currentPage > 1) {
                await setCurrentPage(Math.max(1, currentPage - 1))
                return
            }
            toast.success(t("notification.success"), t("notification.workflow_deleted_successfully"))
            await refetch()
        }
    })

    const showMessageBox = useMessageBox()
    const handleRemoveWorkflow = async (workflowId: string) => {
        const result = await showMessageBox(t("are_you_sure_to_remove_workflow"), t("remove_workflow"))

        if (result) {
            deleteWorkflow(workflowId)
        }
    }

    const onChangeFilter = useCallback(async (filter: { search: string, mainFlow: boolean }) => {
        await setSearch(filter.search)
        await setMainFlow(filter.mainFlow)
    }, [setSearch, setMainFlow])

    const { mutate: updateWorkflow } = useModifyMainFlowWorkflow({
        onError: (_, variables) => {
            queryClient.setQueryData(workflowKey.getAllWorkflows(params), (oldData: IWorkflowListResponse) => {
                const updatedData = oldData?.data?.items?.map((workflow) => {
                    if (workflow.id === variables.workflowId) {
                        return {
                            ...workflow,
                            main_flow: !variables.main_flow
                        }
                    }
                    return workflow
                })
                return {
                    ...oldData,
                    data: {
                        ...oldData.data,
                        items: updatedData
                    }
                }
            })
        }
    })
    const handleMainFlowChange = useCallback(async (workflowId: string, checked: boolean) => {

        if (!checked) {
            const result = await showMessageBox(t("are_you_sure_to_remove_workflow_from_main_flow"), t("modify_main_flow"))
            if (!result) return
        }

        updateWorkflow({ workflowId, main_flow: checked })
        queryClient.setQueryData(workflowKey.getAllWorkflows(params), (oldData: IWorkflowListResponse) => {
            const updatedData = oldData?.data?.items?.map((workflow) => {
                if (workflow.id === workflowId) {
                    return {
                        ...workflow,
                        main_flow: checked
                    }
                }
                return workflow
            })
            return {
                ...oldData,
                data: {
                    ...oldData.data,
                    items: updatedData
                }
            }
        })
    }, [params])

    const total = workflowResponse?.data?.total ?? 0;
    const isBusy = isLoading || isFetching || isDeleting;

    return (
        <>
            <section className="flex flex-col h-full overflow-hidden gap-4 p-4">
                <WorkflowFilter
                    filter={{
                        search: searchQueryParams.search ?? "",
                        mainFlow: searchQueryParams.mainFlow === true
                    }}
                    onChangeFilter={onChangeFilter}
                />

                <div
                    className={cn(
                        "flex-1 flex flex-col gap-3 overflow-hidden",
                        "transition-opacity",
                        isFetching || isDeleting ? "opacity-60 pointer-events-none" : ""
                    )}
                >
                    {isBusy && <div className="loader-bar w-full" />}

                    <div className="flex-1 flex flex-col gap-2 overflow-y-auto pr-1">
                        {visibleRows.length === 0 && !isBusy ? (
                            <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
                                {t(favoritesOnly ? "no_favorite_workflows" : "no_workflows")}
                            </div>
                        ) : (
                            visibleRows.map((w) => (
                                <div
                                    key={w.id}
                                    className="rounded-lg border border-border bg-card p-3 transition-colors hover:bg-muted/50"
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 flex-1">
                                            <div className="flex items-center gap-2">
                                                <Link
                                                    to={`workflow/${w.id}`}
                                                    state={{ listSearch: searchQueryParams }}
                                                    className="truncate font-medium text-primary hover:underline"
                                                >
                                                    {w.name}
                                                </Link>
                                                <Badge
                                                    variant="outline"
                                                    className={cn("shrink-0 text-xs", WORKFLOW_STATUS_BADGE[w.status])}
                                                >
                                                    {t(w.status === "PUBLISHED" ? "status_published" : "status_draft")}
                                                </Badge>
                                            </div>

                                            {w.description ? (
                                                <p className="mt-0.5 truncate text-sm text-muted-foreground">
                                                    {w.description}
                                                </p>
                                            ) : null}

                                            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                                <span className="whitespace-nowrap">v {formatVersionLabel(w.version)}</span>
                                                {w.owner ? (
                                                    <>
                                                        <span>·</span>
                                                        <span className="whitespace-nowrap">
                                                            {t("owner")}: {w.owner_name ?? w.owner}
                                                        </span>
                                                    </>
                                                ) : null}
                                                <span>·</span>
                                                <RelativeTime value={w.updated_at ?? w.created_at} prefix={t("updated")} />
                                                {/* SA-2876: read-only total stars over the workflow's runs. */}
                                                <RatingChip workflowId={w.id} count={ratings?.[w.id]?.count} />
                                                {/* SA-2751: manual-time-saved chip (click to edit via the same dialog). */}
                                                <ManualWorkChip
                                                    manualWork={w.manual_work}
                                                    onClick={() => setRowAction({ row: w, action: MenuActionEnum.SetManualTime })}
                                                />
                                                {/* SA-2942: favorite toggle, last item of the meta line. */}
                                                <TooltipProvider>
                                                    <Tooltip delayDuration={150}>
                                                        <TooltipTrigger asChild>
                                                            <button
                                                                type="button"
                                                                className="group inline-flex items-center rounded p-0.5 hover:bg-rose-500/10 cursor-pointer"
                                                                aria-label={t(favoriteSet.has(w.id) ? "favorite_remove" : "favorite_add")}
                                                                aria-pressed={favoriteSet.has(w.id)}
                                                                onClick={() => handleToggleFavorite(w.id, w.name)}
                                                            >
                                                                <HeartIcon className={cn("size-3.5 transition-transform group-hover:scale-125", favoriteSet.has(w.id) ? "fill-rose-500 text-rose-500" : "text-muted-foreground group-hover:text-rose-500")} />
                                                            </button>
                                                        </TooltipTrigger>
                                                        <TooltipContent className="text-xs">
                                                            {t(favoriteSet.has(w.id) ? "favorite_remove" : "favorite_add")}
                                                        </TooltipContent>
                                                    </Tooltip>
                                                </TooltipProvider>
                                            </div>
                                        </div>

                                        <div className="flex shrink-0 items-center gap-1">
                                            <span title={t("main_flow")} className="px-1">
                                                <Switch
                                                    checked={w.main_flow}
                                                    onCheckedChange={(checked) => handleMainFlowChange(w.id, checked)}
                                                />
                                            </span>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="text-destructive hover:text-destructive hover:bg-destructive/10"
                                                onClick={() => handleRemoveWorkflow(w.id)}
                                                disabled={isDeleting}
                                            >
                                                <Trash2Icon className="size-4" />
                                            </Button>
                                            <MenuConfigSetting onAction={(action) => setRowAction({ row: w, action })}>
                                                <Button variant="ghost" size="icon">
                                                    <MoreHorizontalIcon className="size-4" />
                                                </Button>
                                            </MenuConfigSetting>
                                        </div>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>

                    <ListPager
                        page={searchQueryParams.currentPage}
                        total={total}
                        pageSize={searchQueryParams.limit}
                        onChange={(p) => void setCurrentPage(p)}
                        label={t("pagination_page", {
                            current: searchQueryParams.currentPage,
                            total: Math.max(1, Math.ceil(total / searchQueryParams.limit)),
                        })}
                    />
                </div>
            </section>

            <WorkflowRenameModal
                open={isReplaceNameOpen}
                defaultValues={{ name: selectedRow?.name ?? "", id: selectedRow?.id ?? "" }}
                onOpenChange={(open) => {
                    if (open) return;

                    setRowAction({ row: null, action: "" })
                }
                }
                onSaved={() => {
                    setRowAction({ row: null, action: "" })
                    void queryClient.invalidateQueries({ queryKey: workflowKey.all });
                }}
            />

            <WorkflowRenameDescriptionModal
                open={isReplaceDescriptionOpen}
                defaultValues={{ id: selectedRow?.id ?? "", description: selectedRow?.description ?? "" }}
                onOpenChange={(open) => {
                    if (open) return;

                    setRowAction({ row: null, action: "" })
                }
                }
                onSaved={() => {
                    setRowAction({ row: null, action: "" })
                    void queryClient.invalidateQueries({ queryKey: workflowKey.all });
                }}
            />

            <WorkflowRenameRoutingPathModal
                open={isSettingRoutingPathOpen}
                defaultValues={{ id: selectedRow?.id ?? "", routing_path: selectedRow?.routing_path ?? "" }}
                onOpenChange={(open) => {
                    if (open) return;

                    setRowAction({ row: null, action: "" })
                }
                }
                onSaved={() => {
                    setRowAction({ row: null, action: "" })
                    void queryClient.invalidateQueries({ queryKey: workflowKey.all });
                }}
            />

            <WorkflowChooseOwnerModal
                open={isChooseOwnerOpen}
                defaultValues={{
                    id: selectedRow?.id ?? "",
                    name: selectedRow?.name ?? "",
                    currentOwner: selectedRow?.owner ?? null,
                }}
                onOpenChange={(open) => {
                    if (open) return;

                    setRowAction({ row: null, action: "" })
                }}
                onSaved={() => {
                    setRowAction({ row: null, action: "" })
                    void queryClient.invalidateQueries({ queryKey: workflowKey.all });
                }}
            />

            <WorkflowSetManualTimeModal
                open={isSetManualTimeOpen}
                defaultValues={{
                    id: selectedRow?.id ?? "",
                    name: selectedRow?.name ?? "",
                    manualSeconds: selectedRow?.manual_work?.manual_seconds ?? null,
                    successfulRuns: selectedRow?.manual_work?.successful_runs ?? 0,
                }}
                onOpenChange={(open) => {
                    if (open) return;
                    setRowAction({ row: null, action: "" })
                }}
                onSaved={() => {
                    setRowAction({ row: null, action: "" })
                    void queryClient.invalidateQueries({ queryKey: workflowKey.all });
                }}
            />

        </>
    )
}

export default WorkflowList