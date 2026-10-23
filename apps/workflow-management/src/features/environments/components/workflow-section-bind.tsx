// SA-2605 — Workflow binding tab. Replaces the SA-2450 Assign UI: a workflow binds
// to EXACTLY ONE section (no per-env checkboxes). At run time it may use ANY
// environment of the bound section. The right pane is a single Section <select>
// plus a READ-ONLY preview of that section's environments (from the section's
// many-to-many membership).
//
// Left: a searchable workflow picker showing each workflow's current section.
// Right: a Section dropdown + the bound section's environments as chips.
import {
    useEnvironmentList,
    useEnvSectionList,
    useSectionMembership,
    useSetWorkflowSection,
    useWorkflowSection,
} from "@/features/environments/hooks/apis/environments";
import { calledSubflowIds } from "@/features/environments/utils/subflow-roles";
import { useWorkflowById, useWorkflowListInfinite } from "@/features/workflows/hooks/apis/workflows";
import type { IWorkflowListItem } from "@/features/workflows/types/workflows";
import { useLanguage } from "@/hooks/use-language";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { ScrollArea } from "@ldc/ui/components/scroll-area";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@ldc/ui/components/select";
import { cn } from "@ldc/ui";
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import { useEffect, useMemo, useRef, useState } from "react";

const NONE = "__none__";

const WorkflowSectionBind = () => {
    const { t } = useLanguage();

    const [search, setSearch] = useState("");
    const debouncedSetSearch = useDebounceCallback(setSearch, 300);
    const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
    const sentinelRef = useRef<HTMLDivElement>(null);

    const queryParams = useMemo(() => {
        const escaped = search.replace(/'/g, "''");
        return {
            $orderby: "updated_at desc",
            ...(search ? { $filter: `contains(name, '${escaped}')` } : {}),
        };
    }, [search]);

    const { data: wfData, isLoading: wfLoading, hasNextPage, isFetchingNextPage, fetchNextPage } =
        useWorkflowListInfinite(queryParams);

    const workflows: IWorkflowListItem[] = wfData?.pages?.flatMap(p => p.data.items) ?? [];

    useEffect(() => {
        const sentinel = sentinelRef.current;
        if (!sentinel) return;
        const observer = new IntersectionObserver(entries => {
            if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
                void fetchNextPage();
            }
        });
        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

    return (
        <div className="grid grid-cols-[minmax(240px,1fr)_1.4fr] gap-6 px-4 pb-6" data-testid="workflow-section-bind">
            {/* Left: workflow list */}
            <div className="flex flex-col gap-2 min-w-0">
                <Label>{t("environments.assign_workflow_label")}</Label>
                <Input
                    placeholder={t("environments.assign_search_workflows")}
                    onChange={e => debouncedSetSearch(e.target.value)}
                    data-testid="bind-workflow-search"
                />
                <ScrollArea className="h-[420px] rounded-md border border-border">
                    {wfLoading && <div className="loader-bar w-full" />}
                    {!wfLoading && workflows.length === 0 && (
                        <p className="text-sm text-muted-foreground py-8 text-center">
                            {t("environments.assign_no_workflows")}
                        </p>
                    )}
                    <ul className="divide-y divide-border">
                        {workflows.map(wf => (
                            <li key={wf.id}>
                                <button
                                    type="button"
                                    onClick={() => setSelectedWorkflowId(wf.id)}
                                    className={cn(
                                        "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors hover:bg-muted/50",
                                        selectedWorkflowId === wf.id && "bg-muted",
                                    )}
                                    data-testid={`bind-workflow-${wf.id}`}
                                >
                                    <span className="text-sm font-medium truncate w-full">{wf.name}</span>
                                    <WorkflowSectionHint workflowId={wf.id} />
                                </button>
                            </li>
                        ))}
                    </ul>
                    {isFetchingNextPage && <div className="loader-bar w-full" />}
                    <div ref={sentinelRef} className="h-4" />
                </ScrollArea>
            </div>

            {/* Right: single section select + read-only env preview */}
            <div className="min-w-0">
                {selectedWorkflowId ? (
                    <BindEditor key={selectedWorkflowId} workflowId={selectedWorkflowId} />
                ) : (
                    <p className="text-sm text-muted-foreground py-10 text-center">
                        {t("environments.assign_pick_workflow")}
                    </p>
                )}
            </div>
        </div>
    );
};

const WorkflowSectionHint = ({ workflowId }: { workflowId: string }) => {
    const { t } = useLanguage();
    const { data } = useWorkflowSection(workflowId, { meta: { silent: true } });
    const b = data?.data;
    if (!b || !b.assigned || !b.section) {
        return <span className="text-xs text-muted-foreground">{t("environments.assign_no_binding")}</span>;
    }
    return (
        <span className="text-xs text-muted-foreground">
            {t("environments.bind_current", { section: b.section })}
        </span>
    );
};

const BindEditor = ({ workflowId }: { workflowId: string }) => {
    const { t } = useLanguage();
    const { data: bindData, isLoading: bindLoading } = useWorkflowSection(workflowId);
    const setSection = useSetWorkflowSection();

    // Sections come from the canonical schema list (SA-2471), one entry per section.
    const { data: sectionData } = useEnvSectionList();
    const sections = useMemo(() => {
        const set = new Set<string>();
        for (const s of sectionData?.data?.items ?? []) if (s.section) set.add(s.section);
        return Array.from(set).sort();
    }, [sectionData]);

    const [section, setSectionState] = useState<string>("");
    const [saved, setSaved] = useState<string>("");
    const prefilledRef = useRef(false);

    useEffect(() => {
        if (prefilledRef.current) return;
        if (bindLoading) return;
        prefilledRef.current = true;
        const b = bindData?.data;
        const s = b?.assigned ? b.section : "";
        setSectionState(s);
        setSaved(s);
    }, [bindLoading, bindData]);

    const isDirty = section !== saved;
    const canSave = isDirty && !setSection.isPending;

    const onSave = () => {
        if (!canSave) return;
        setSection.mutate(
            { workflowId, payload: { section } },
            {
                onSuccess: () => {
                    setSaved(section);
                    toast.success(section ? t("environments.bind_saved", { section }) : t("environments.bind_cleared"));
                },
                onError: (e) => {
                    const body = e.response?.data;
                    toast.error(body?.message ?? t("environments.bind_save_error"));
                },
            },
        );
    };

    return (
        <div className="flex flex-col gap-4" data-testid="bind-editor">
            {bindLoading && <div className="loader-bar w-full" />}

            <p className="text-sm text-muted-foreground">{t("environments.bind_intro")}</p>

            <div className="flex flex-col gap-1.5">
                <Label>{t("environments.assign_section_label")}</Label>
                {sections.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("environments.assign_no_sections")}</p>
                ) : (
                    <Select
                        value={section || NONE}
                        onValueChange={(v: string) => setSectionState(v === NONE ? "" : v)}
                    >
                        <SelectTrigger data-testid="bind-section-select">
                            <SelectValue placeholder={t("environments.assign_section_placeholder")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value={NONE}>{t("environments.bind_none")}</SelectItem>
                            {sections.map(s => (
                                <SelectItem key={s} value={s}>
                                    {s}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                )}
            </div>

            {/* read-only preview of the bound section's environments */}
            <div className="flex flex-col gap-1.5">
                <Label>{t("environments.bind_preview_label")}</Label>
                {section ? (
                    <SectionEnvPreview section={section} />
                ) : (
                    <p className="text-sm text-muted-foreground">{t("environments.bind_preview_empty")}</p>
                )}
            </div>

            {/* when this workflow is a parent (calls sub-flows), list them with a
                same/different-section indicator against this parent's section. */}
            <CalledSubflows workflowId={workflowId} parentSection={section} />

            <div className="flex items-center justify-end">
                <Button type="button" disabled={!canSave} onClick={onSave} data-testid="bind-save">
                    {setSection.isPending
                        ? t("environments.assign_saving")
                        : !isDirty && saved
                            ? t("environments.assign_saved_state")
                            : t("environments.bind_save")}
                </Button>
            </div>
        </div>
    );
};

// Parent branch: if the selected workflow's graph calls sub-flows, list each one
// with a "same section" (green) / "different section" (red) indicator comparing
// the sub-flow's bound section to this parent's. A parent + sub-flow in the same
// section always share the same environments, so the call can never fail.
const CalledSubflows = ({ workflowId, parentSection }: { workflowId: string; parentSection: string }) => {
    const { t } = useLanguage();
    const { data } = useWorkflowById(workflowId, { meta: { silent: true } });
    const subflowIds = calledSubflowIds(data?.data);

    if (subflowIds.length === 0) return null;

    return (
        <div className="flex flex-col gap-1.5">
            <Label>{t("environments.bind_calls_subflows")}</Label>
            <div className="rounded-md border border-border">
                <ul className="divide-y divide-border">
                    {subflowIds.map(id => (
                        <CalledSubflowRow key={id} subflowId={id} parentSection={parentSection} />
                    ))}
                </ul>
            </div>
            <p className="text-xs text-muted-foreground">{t("environments.bind_calls_subflows_hint")}</p>
        </div>
    );
};

// One called sub-flow: its name (from the graph) + its bound section vs. parent.
const CalledSubflowRow = ({ subflowId, parentSection }: { subflowId: string; parentSection: string }) => {
    const { t } = useLanguage();
    const { data: detail } = useWorkflowById(subflowId, { meta: { silent: true } });
    const { data: bindData } = useWorkflowSection(subflowId, { meta: { silent: true } });

    const name = detail?.data?.name ?? subflowId;
    const binding = bindData?.data;
    const subSection = binding?.assigned ? binding.section : "";
    const same = !!subSection && subSection === parentSection;

    return (
        <li className="flex items-center gap-2 px-3 py-2 text-sm">
            <span className="truncate font-medium">{name}</span>
            <Badge variant="gray" data-testid={`subflow-role-${subflowId}`}>
                {t("environments.role_subflow")}
            </Badge>
            <span className="flex-1" />
            {!subSection ? (
                <Badge variant="destructive" data-testid={`subflow-section-cmp-${subflowId}`}>
                    ✗ {t("environments.bind_subflow_unbound")}
                </Badge>
            ) : same ? (
                <Badge variant="success" data-testid={`subflow-section-cmp-${subflowId}`}>
                    ✓ {t("environments.bind_same_section")}
                </Badge>
            ) : (
                <Badge variant="destructive" data-testid={`subflow-section-cmp-${subflowId}`}>
                    ✗ {t("environments.bind_different_section")}
                </Badge>
            )}
        </li>
    );
};

// Read-only chips of the environments in a section (from its m2m membership).
const SectionEnvPreview = ({ section }: { section: string }) => {
    const { t } = useLanguage();
    const { data: membershipData } = useSectionMembership(section);
    const { data: envData } = useEnvironmentList();

    const ids = new Set(membershipData?.data?.environment_ids ?? []);
    const envs = (envData?.data?.items ?? []).filter(e => ids.has(e.id));

    if (envs.length === 0) {
        return (
            <div className="rounded-md border border-border bg-muted/40 px-3 py-2.5">
                <p className="text-sm text-muted-foreground">{t("environments.bind_preview_section_empty")}</p>
            </div>
        );
    }
    return (
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2.5">
            <p className="mb-1.5 text-xs text-muted-foreground">
                {t("environments.bind_preview_count", { count: envs.length })}
            </p>
            <div className="flex flex-wrap gap-1.5">
                {envs.map(e => (
                    <Badge key={e.id} variant="secondary" data-testid={`bind-preview-env-${e.name}`}>
                        {e.name}
                    </Badge>
                ))}
            </div>
        </div>
    );
};

export default WorkflowSectionBind;
