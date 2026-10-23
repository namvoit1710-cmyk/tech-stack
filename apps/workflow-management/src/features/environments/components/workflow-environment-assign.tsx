// SA-2450 — Assign sub-tab of the Environments page. Binds a workflow to EXACTLY
// ONE section and a subset (>=1) of that section's environments. Picking a section
// preselects ALL its environments; the user may deselect down to one (never zero).
// The run-time environment picker (environment-select) then offers only this subset.
//
// Left: a searchable workflow picker showing each workflow's current binding.
// Right: a single-select Section dropdown + a checklist of that section's envs.
import {
    useEnvironmentList,
    useEnvSectionList,
    useSetWorkflowEnvironmentAssignment,
    useWorkflowEnvironmentAssignment,
} from "@/features/environments/hooks/apis/environments";
import { useWorkflowListInfinite } from "@/features/workflows/hooks/apis/workflows";
import type { IWorkflowListItem } from "@/features/workflows/types/workflows";
import { useLanguage } from "@/hooks/use-language";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Checkbox } from "@ldc/ui/components/checkbox";
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

const WorkflowEnvironmentAssign = () => {
    const { t } = useLanguage();

    // ---- Left: searchable workflow picker -------------------------------
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
        <div className="grid grid-cols-[minmax(240px,1fr)_1.4fr] gap-6 px-4 pb-6" data-testid="workflow-env-assign">
            {/* Left: workflow list */}
            <div className="flex flex-col gap-2 min-w-0">
                <Label>{t("environments.assign_workflow_label")}</Label>
                <Input
                    placeholder={t("environments.assign_search_workflows")}
                    onChange={e => debouncedSetSearch(e.target.value)}
                    data-testid="assign-workflow-search"
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
                                    data-testid={`assign-workflow-${wf.id}`}
                                >
                                    <span className="text-sm font-medium truncate w-full">{wf.name}</span>
                                    <WorkflowBindingHint workflowId={wf.id} />
                                </button>
                            </li>
                        ))}
                    </ul>
                    {isFetchingNextPage && <div className="loader-bar w-full" />}
                    <div ref={sentinelRef} className="h-4" />
                </ScrollArea>
            </div>

            {/* Right: section + environment checklist */}
            <div className="min-w-0">
                {selectedWorkflowId ? (
                    <AssignEditor key={selectedWorkflowId} workflowId={selectedWorkflowId} />
                ) : (
                    <p className="text-sm text-muted-foreground py-10 text-center">
                        {t("environments.assign_pick_workflow")}
                    </p>
                )}
            </div>
        </div>
    );
};

// Small inline summary of a workflow's current binding shown in the left list.
const WorkflowBindingHint = ({ workflowId }: { workflowId: string }) => {
    const { t } = useLanguage();
    const { data } = useWorkflowEnvironmentAssignment(workflowId, { meta: { silent: true } });
    const a = data?.data;
    if (!a || !a.assigned || !a.section) {
        return <span className="text-xs text-muted-foreground">{t("environments.assign_no_binding")}</span>;
    }
    return (
        <span className="text-xs text-muted-foreground">
            {t("environments.assign_current_binding", { section: a.section, count: a.environment_ids.length })}
        </span>
    );
};

interface IAssignEditorProps {
    workflowId: string;
}

const AssignEditor = ({ workflowId }: IAssignEditorProps) => {
    const { t } = useLanguage();
    const { data: envData } = useEnvironmentList();
    const { data: assignData, isLoading: assignLoading } = useWorkflowEnvironmentAssignment(workflowId);
    const setAssignment = useSetWorkflowEnvironmentAssignment();

    const environments = envData?.data?.items ?? [];

    // Sections come SOLELY from the canonical SECTION SCHEMAS (SA-2471) — a
    // section is a first-class object, so every environment's `section` field is
    // just a denormalized pointer to one of these. We deliberately do NOT union
    // in env.section values: if an env's section ever disagrees with the schema
    // list (stale denormalized copy), unioning would surface BOTH the schema name
    // and the stale env name as two separate options. Sourcing only from the
    // schema list guarantees one entry per real section, and a rename in Config
    // (which invalidates this list) reflects here immediately.
    const { data: sectionData } = useEnvSectionList();
    const sections = useMemo(() => {
        const set = new Set<string>();
        for (const s of sectionData?.data?.items ?? []) if (s.section) set.add(s.section);
        return Array.from(set).sort();
    }, [sectionData]);

    const [section, setSection] = useState<string>("");
    const [checked, setChecked] = useState<Set<string>>(new Set());
    // The last-persisted binding (from GET, then refreshed on each successful
    // save). Save is enabled only when the current selection differs from this —
    // no-op saves stay disabled, and the button disables again right after a save
    // until the next change.
    const [saved, setSaved] = useState<{ section: string; ids: string[] }>({
        section: "",
        ids: [],
    });
    // Guard so the GET-prefill effect runs only once per workflow load, not on
    // every environment-list refetch (which would clobber user edits).
    const prefilledRef = useRef(false);
    // When the section Select popover closes on an option click, the pointer-up
    // can leak through to a checkbox rendered underneath it. Switching TO a
    // 1-env section would then spuriously fire the "keep at least one" warning
    // on that lone env. Suppress checkbox toggles for a brief window right after
    // a section change so that stray event is ignored.
    const switchingRef = useRef(false);

    const envsInSection = useMemo(
        () => environments.filter(e => e.section === section),
        [environments, section],
    );

    // Prefill from the workflow's current assignment once it (and the env list)
    // have loaded.
    useEffect(() => {
        if (prefilledRef.current) return;
        if (assignLoading) return;
        if (environments.length === 0) return;
        const a = assignData?.data;
        prefilledRef.current = true;
        if (a?.assigned) {
            // SA-2481: a section rename in Config makes the stored section name
            // stale. Reconcile: the true section is the CURRENT section of the
            // assigned environments (env ids are the stable reference), so a
            // rename shows the new name with no re-click and Save stays disabled.
            const byId = new Map(environments.map(e => [e.id, e]));
            const envSections = a.environment_ids
                .map(id => byId.get(id)?.section)
                .filter((s): s is string => !!s);
            const effectiveSection = envSections[0] || a.section;
            if (effectiveSection) {
                setSection(effectiveSection);
                const validInSection = new Set(
                    environments.filter(e => e.section === effectiveSection).map(e => e.id),
                );
                const keep = a.environment_ids.filter(id => validInSection.has(id));
                const initial = new Set(keep.length > 0 ? keep : Array.from(validInSection));
                setChecked(initial);
                // Baseline the dirty-check against the reconciled binding.
                setSaved({ section: effectiveSection, ids: Array.from(initial) });
            }
        }
    }, [assignLoading, assignData, environments]);

    // SA-2450 W18 self-heal: the one-shot prefill can latch a section name that
    // then becomes stale — e.g. it ran against a not-yet-refetched env list, or
    // the section was renamed in Config while this editor stayed mounted. When
    // the latched `section` is no longer a live section, remap it to the CURRENT
    // section of the already-checked (assigned) envs — env ids are the stable
    // reference. This shows the single new name (no old+new duplicate, never
    // empty) and keeps the saved-baseline in sync so Save doesn't spuriously arm.
    useEffect(() => {
        if (!section) return;
        if (sections.length === 0) return; // sections not loaded yet
        if (sections.includes(section)) return; // still valid — nothing to do
        const byId = new Map(environments.map(e => [e.id, e]));
        const current = Array.from(checked)
            .map(id => byId.get(id)?.section)
            .filter((s): s is string => !!s && sections.includes(s));
        const next = current[0];
        if (!next) return;
        setSection(next);
        setChecked(prev => {
            const validIds = new Set(environments.filter(e => e.section === next).map(e => e.id));
            const keep = Array.from(prev).filter(id => validIds.has(id));
            return new Set(keep.length > 0 ? keep : Array.from(validIds));
        });
        setSaved(prev => (prev.section && !sections.includes(prev.section) ? { ...prev, section: next } : prev));
    }, [section, sections, environments, checked]);

    // Choosing a section (re)selects ALL of its environments.
    const onSectionChange = (next: string) => {
        switchingRef.current = true;
        setSection(next);
        setChecked(new Set(environments.filter(e => e.section === next).map(e => e.id)));
        // Release after the popover-close pointer event has settled.
        window.setTimeout(() => {
            switchingRef.current = false;
        }, 350);
    };

    const toggle = (envId: string, isChecked: boolean) => {
        // Ignore a stray toggle right after a section switch (popover pointer
        // leak) or one for an env not in the current section (leftover from the
        // previous section) — neither is a genuine user uncheck.
        if (switchingRef.current) return;
        if (!envsInSection.some(e => e.id === envId)) return;
        setChecked(prev => {
            if (isChecked) {
                const nextSet = new Set(prev);
                nextSet.add(envId);
                return nextSet;
            }
            // Block unchecking the LAST remaining environment (never 0).
            if (prev.size <= 1 && prev.has(envId)) {
                toast.warning(t("environments.assign_min_one_warning"));
                return prev;
            }
            const nextSet = new Set(prev);
            nextSet.delete(envId);
            return nextSet;
        });
    };

    // Dirty = current selection differs from the last-saved binding.
    const sameIds = (a: Set<string>, b: string[]) =>
        a.size === b.length && b.every(id => a.has(id));
    const isDirty = section !== saved.section || !sameIds(checked, saved.ids);
    const hasSavedBinding = !!saved.section;
    const canSave = !!section && checked.size > 0 && isDirty && !setAssignment.isPending;

    const onSave = () => {
        if (!canSave) return;
        const ids = Array.from(checked);
        setAssignment.mutate(
            {
                workflowId,
                payload: { section, environmentIds: ids },
            },
            {
                onSuccess: () => {
                    // Persisted → this is the new baseline, so Save disables until
                    // the next change.
                    setSaved({ section, ids });
                    toast.success(t("environments.assign_saved"));
                },
                onError: (e: unknown) => {
                    const body = (e as { response?: { data?: { error?: { message?: string }; message?: string } } })
                        ?.response?.data;
                    toast.error(body?.error?.message ?? body?.message ?? t("environments.assign_save_error"));
                },
            },
        );
    };

    return (
        <div className="flex flex-col gap-4" data-testid="assign-editor">
            {assignLoading && <div className="loader-bar w-full" />}

            <div className="flex flex-col gap-1.5">
                <Label>{t("environments.assign_section_label")}</Label>
                {sections.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("environments.assign_no_sections")}</p>
                ) : (
                    <Select value={section || undefined} onValueChange={onSectionChange}>
                        <SelectTrigger data-testid="assign-section-select">
                            <SelectValue placeholder={t("environments.assign_section_placeholder")} />
                        </SelectTrigger>
                        <SelectContent>
                            {sections.map(s => (
                                <SelectItem key={s} value={s}>
                                    {s}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                )}
            </div>

            <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                    <Label>{t("environments.assign_environments_label")}</Label>
                    {section && envsInSection.length > 0 && (
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-auto px-2 py-1 text-xs"
                            disabled={checked.size === envsInSection.length}
                            onClick={() => setChecked(new Set(envsInSection.map(e => e.id)))}
                            data-testid="assign-select-all"
                        >
                            {t("environments.assign_select_all")}
                        </Button>
                    )}
                </div>
                {!section ? (
                    <p className="text-sm text-muted-foreground">{t("environments.assign_pick_section")}</p>
                ) : envsInSection.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("environments.assign_section_empty")}</p>
                ) : (
                    <div className="flex flex-col gap-1 rounded-md border border-border p-2">
                        {envsInSection.map(e => (
                            <label
                                key={e.id}
                                className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/50 cursor-pointer"
                            >
                                <Checkbox
                                    checked={checked.has(e.id)}
                                    onCheckedChange={c => toggle(e.id, c === true)}
                                    data-testid={`assign-env-${e.id}`}
                                />
                                <span className="truncate">{e.name}</span>
                            </label>
                        ))}
                    </div>
                )}
            </div>

            <div className="flex items-center justify-end">
                <Button type="button" disabled={!canSave} onClick={onSave} data-testid="assign-save">
                    {setAssignment.isPending
                        ? t("environments.assign_saving")
                        : !isDirty && hasSavedBinding
                            ? t("environments.assign_saved_state")
                            : t("environments.assign_save")}
                </Button>
            </div>
        </div>
    );
};

export default WorkflowEnvironmentAssign;
