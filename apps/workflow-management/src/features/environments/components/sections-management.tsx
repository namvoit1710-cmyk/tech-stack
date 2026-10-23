// SA-2605 — Sections tab. Each section is ONE collapsible card whose body holds,
// top to bottom:
//   1. the shared variable KEY schema + per-env Values (the reused SA-2471
//      SectionBody), then
//   2. a many-to-many environment MEMBERSHIP picker (checkbox list over ALL
//      environments; save via useSetSectionMembership; "also in X,Y" chips derived
//      from useListSectionMemberships), plus
//   3. the workflows bound to the section — each row tagged with a role chip
//      (parent / sub-flow / both) derived from the workflow graphs.
// Section <-> Environment is many-to-many: an environment may sit in several
// sections. Saving a membership that would clash a key type across sections comes
// back as 422 KEY_TYPE_CONFLICT and is shown inline.
import AddSectionDialog from "@/features/environments/components/add-section-dialog";
import {
    DeleteSectionButton,
    RenameSectionButton,
    SectionBody,
} from "@/features/environments/components/section-card";
import {
    useEnvironmentList,
    useListSectionMemberships,
    useSectionMembership,
    useSectionWorkflows,
    useSetSectionMembership,
    useEnvSectionList,
} from "@/features/environments/hooks/apis/environments";
import type {
    IEnvSection,
    IKeyTypeConflictDetails,
} from "@/features/environments/types/environment";
import { KEY_TYPE_CONFLICT } from "@/features/environments/types/environment";
import { calledSubflowIds } from "@/features/environments/utils/subflow-roles";
import { workflowKey } from "@/features/workflows/hooks/apis/workflows";
import type { IWorkflow, IWorkflowDetailResponse } from "@/features/workflows/types/workflows";
import { useLanguage } from "@/hooks/use-language";
import api from "@/lib/api";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Checkbox } from "@ldc/ui/components/checkbox";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@ldc/ui/components/collapsible";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { ScrollArea } from "@ldc/ui/components/scroll-area";
import { useQueries } from "@ldc/tanstack-query";
import { ChevronRightIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

interface ISectionsManagementProps {
    addOpen: boolean;
    onAddOpenChange: (open: boolean) => void;
}

const SectionsManagement = ({ addOpen, onAddOpenChange }: ISectionsManagementProps) => {
    const { t } = useLanguage();
    const { data, isLoading } = useEnvSectionList();
    const sections = data?.data?.items ?? [];

    return (
        <section className="flex flex-col gap-4 px-4 pb-6" data-testid="sections-management">
            {/* header row (mockup .cfghead): note on the left, primary Add on the right */}
            <div className="flex items-center gap-3">
                <p className="max-w-160 text-sm text-muted-foreground">{t("environments.sections_intro")}</p>
                <span className="flex-1" />
                <Button data-testid="sections-add-inline" onClick={() => onAddOpenChange(true)}>
                    {t("environments.add_section")}
                </Button>
            </div>

            {isLoading && <div className="loader-bar w-full" />}

            {!isLoading && sections.length === 0 && (
                <p className="py-10 text-center text-sm text-muted-foreground" data-testid="sections-empty">
                    {t("environments.no_sections")}
                </p>
            )}

            <div className="flex flex-col gap-4">
                {sections.map(sec => (
                    <SectionBindingCard key={sec.id} section={sec} />
                ))}
            </div>

            <AddSectionDialog
                open={addOpen}
                existingNames={sections.map(s => s.section)}
                onOpenChange={onAddOpenChange}
                onCreated={() => onAddOpenChange(false)}
            />
        </section>
    );
};

// One section = a single collapsible card. Its header toggles the whole body
// (schema + values + membership + bound workflows) so everything collapses as a
// unit, matching the mockup's section cards.
const SectionBindingCard = ({ section }: { section: IEnvSection }) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(true);

    return (
        <Collapsible
            open={open}
            onOpenChange={setOpen}
            className="rounded-xl border border-border bg-background"
            data-testid={`section-binding-${section.section}`}
        >
            <CollapsibleTrigger asChild>
                <div
                    className="flex cursor-pointer select-none items-center gap-3 px-4 py-3 hover:bg-muted/50"
                    data-testid={`section-binding-header-${section.section}`}
                >
                    <ChevronRightIcon
                        className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-90")}
                    />
                    <span className="text-sm font-bold">{section.section}</span>
                    <span className="text-xs text-muted-foreground">
                        {t("environments.section_summary", {
                            variables: section.keys.length,
                            environments: section.environment_count,
                        })}
                    </span>
                    <span className="flex-1" />
                    <RenameSectionButton section={section} />
                    <DeleteSectionButton section={section} />
                </div>
            </CollapsibleTrigger>
            <CollapsibleContent className="border-t border-border">
                {open && (
                    <>
                        {/* 1. schema + per-env values (reused SA-2471 body) */}
                        <SectionBody section={section} />

                        {/* 2. many-to-many membership + 3. bound workflows */}
                        <div className="grid grid-cols-1 gap-6 border-t border-border p-4 md:grid-cols-2">
                            <div className="min-w-0">
                                <p className="mb-2 text-xs font-semibold uppercase tracking-wide">
                                    {t("environments.membership_title", { section: section.section })}
                                </p>
                                <MembershipPicker section={section} />
                            </div>
                            <div className="min-w-0">
                                <p className="mb-2 text-xs font-semibold uppercase tracking-wide">
                                    {t("environments.section_workflows_title", { section: section.section })}
                                </p>
                                <BoundWorkflows section={section.section} />
                                <p className="mt-2 text-xs text-muted-foreground">
                                    {t("environments.section_workflows_hint")}
                                </p>
                            </div>
                        </div>
                    </>
                )}
            </CollapsibleContent>
        </Collapsible>
    );
};

// Many-to-many environment membership: a checkbox over ALL environments, save via
// useSetSectionMembership. "also in X,Y" is derived from the memberships list.
const MembershipPicker = ({ section }: { section: IEnvSection }) => {
    const { t } = useLanguage();
    const { data: envData } = useEnvironmentList();
    const { data: membershipData } = useSectionMembership(section.section);
    const { data: allMembershipData } = useListSectionMemberships();
    const setMembership = useSetSectionMembership();

    const environments = envData?.data?.items ?? [];
    const membership = membershipData?.data;

    // env id -> other sections it belongs to (for the "also in" hint).
    const otherSectionsByEnvId = useMemo(() => {
        const map = new Map<string, string[]>();
        for (const m of allMembershipData?.data?.memberships ?? []) {
            if (m.section === section.section) continue;
            for (const id of m.environment_ids) {
                const list = map.get(id) ?? [];
                list.push(m.section);
                map.set(id, list);
            }
        }
        return map;
    }, [allMembershipData, section.section]);

    const [checked, setChecked] = useState<Set<string>>(new Set());
    const [saved, setSaved] = useState<string[]>([]);
    const [search, setSearch] = useState("");
    const [conflict, setConflict] = useState("");
    const prefilledRef = useRef(false);

    // Prefill once from the section's current membership.
    useEffect(() => {
        if (prefilledRef.current) return;
        if (!membership) return;
        prefilledRef.current = true;
        setChecked(new Set(membership.environment_ids));
        setSaved(membership.environment_ids);
    }, [membership]);

    const filtered = useMemo(
        () => environments.filter(e => e.name.toLowerCase().includes(search.toLowerCase())),
        [environments, search],
    );

    const toggle = (id: string, isChecked: boolean) =>
        setChecked(prev => {
            const next = new Set(prev);
            if (isChecked) next.add(id);
            else next.delete(id);
            return next;
        });

    const sameIds = (a: Set<string>, b: string[]) => a.size === b.length && b.every(id => a.has(id));
    const isDirty = !sameIds(checked, saved);
    const canSave = isDirty && !setMembership.isPending && membership !== undefined;

    const onSave = () => {
        if (!canSave || !membership) return;
        setConflict("");
        const ids = Array.from(checked);
        setMembership.mutate(
            { section: section.section, version: membership.version, payload: { environmentIds: ids } },
            {
                onSuccess: () => {
                    setSaved(ids);
                    toast.success(t("environments.membership_saved", { count: ids.length }));
                },
                onError: (e) => {
                    const body = e.response?.data;
                    if (body?.code === KEY_TYPE_CONFLICT) {
                        const d = body.details as IKeyTypeConflictDetails | undefined;
                        setConflict(
                            t("environments.membership_key_conflict", {
                                key: d?.key ?? "",
                                sectionA: d?.section_a ?? "",
                                typeA: d?.type_a ?? "",
                                sectionB: d?.section_b ?? "",
                                typeB: d?.type_b ?? "",
                            }),
                        );
                    } else {
                        toast.error(body?.message ?? t("environments.membership_save_error"));
                    }
                },
            },
        );
    };

    return (
        <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
                <Label className="text-xs font-normal text-muted-foreground">
                    {t("environments.membership_count", { selected: checked.size, total: environments.length })}
                </Label>
            </div>
            <Input
                value={search}
                placeholder={t("environments.search_environments")}
                onChange={e => setSearch(e.target.value)}
                data-testid={`membership-search-${section.section}`}
            />
            <ScrollArea className="h-60 rounded-md border border-border">
                {environments.length === 0 ? (
                    <p className="px-3 py-4 text-sm text-muted-foreground">{t("environments.no_environments")}</p>
                ) : filtered.length === 0 ? (
                    <p className="px-3 py-4 text-sm text-muted-foreground">{t("environments.no_environments")}</p>
                ) : (
                    <div className="flex flex-col gap-1 p-2">
                        {filtered.map(env => {
                            const others = otherSectionsByEnvId.get(env.id) ?? [];
                            return (
                                <label
                                    key={env.id}
                                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/50"
                                >
                                    <Checkbox
                                        checked={checked.has(env.id)}
                                        onCheckedChange={c => toggle(env.id, c === true)}
                                        data-testid={`membership-env-${section.section}-${env.id}`}
                                    />
                                    <span className="truncate font-medium">{env.name}</span>
                                    {others.length > 0 && (
                                        <span className="truncate text-xs text-muted-foreground">
                                            {t("environments.membership_also_in", { sections: others.join(", ") })}
                                        </span>
                                    )}
                                </label>
                            );
                        })}
                    </div>
                )}
            </ScrollArea>

            {conflict && (
                <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs font-medium text-destructive">
                    {conflict}
                </p>
            )}

            <div className="flex items-center gap-3">
                <Button
                    type="button"
                    disabled={!canSave}
                    onClick={onSave}
                    data-testid={`membership-save-${section.section}`}
                >
                    {setMembership.isPending ? t("environments.saving") : t("environments.membership_save")}
                </Button>
            </div>
        </div>
    );
};

// The workflows bound to a section, each tagged with a role chip (parent /
// sub-flow / both). SA-2904: the bound ids come from ONE call
// (GET /sections/{section}/workflows) — the old version probed /section for the
// first page (20) of /workflows only, so flows further down never showed. Roles
// come from REAL GRAPHS: a bound workflow gets `sub-flow` when its id appears as
// a WORKFLOW-node `workflow_id` in ANY bound workflow's graph, and `parent` when
// its own graph calls one.
export const BoundWorkflows = ({ section }: { section: string }) => {
    const { t } = useLanguage();
    const { data, isLoading: idsLoading } = useSectionWorkflows(section);
    const ids = data?.data?.workflow_ids ?? [];

    // The graph of each bound workflow (name + real nodes → real roles).
    const graphQueries = useQueries({
        queries: ids.map(id => ({
            queryKey: workflowKey.getWorkflowById(id),
            queryFn: (): Promise<IWorkflowDetailResponse> =>
                api.get(`/workflows/${encodeURIComponent(id)}`),
            meta: { silent: true },
        })),
    });
    const isLoading = idsLoading || graphQueries.some(q => q.isLoading);

    // Loaded bound workflows (a deleted flow whose detail 404s is skipped), and
    // the union of every sub-flow id referenced across all bound graphs.
    const { boundWorkflows, referencedIds } = useMemo(() => {
        const boundWorkflows: IWorkflow[] = [];
        const referencedIds = new Set<string>();
        for (const q of graphQueries) {
            const graph = q.data?.data;
            if (!graph) continue;
            boundWorkflows.push(graph);
            for (const id of calledSubflowIds(graph)) referencedIds.add(id);
        }
        return { boundWorkflows, referencedIds };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [graphQueries.map(q => q.data?.data?.id).join("|")]);

    return (
        <div className="rounded-md border border-border">
            {isLoading && <div className="loader-bar w-full" />}
            <ul className="divide-y divide-border">
                {boundWorkflows.map(wf => (
                    <BoundWorkflowRow
                        key={wf.id}
                        workflow={wf}
                        isParent={calledSubflowIds(wf).length > 0}
                        isSubflow={referencedIds.has(wf.id)}
                    />
                ))}
            </ul>
            {!isLoading && boundWorkflows.length === 0 && (
                <p className="px-3 py-4 text-sm text-muted-foreground">{t("environments.section_no_workflows")}</p>
            )}
        </div>
    );
};

// One bound row: its name + the derived role chip(s). A workflow that is both a
// parent and a sub-flow shows BOTH chips.
const BoundWorkflowRow = ({
    workflow,
    isParent,
    isSubflow,
}: {
    workflow: Pick<IWorkflow, "id" | "name">;
    isParent: boolean;
    isSubflow: boolean;
}) => {
    const { t } = useLanguage();
    return (
        <li className="flex items-center gap-2 px-3 py-2 text-sm">
            <span className="truncate font-medium">{workflow.name}</span>
            <span className="flex-1" />
            {isParent && (
                <Badge variant="success" data-testid={`bound-role-parent-${workflow.id}`}>
                    {t("environments.role_parent")}
                </Badge>
            )}
            {isSubflow && (
                <Badge variant="gray" data-testid={`bound-role-subflow-${workflow.id}`}>
                    {t("environments.role_subflow")}
                </Badge>
            )}
        </li>
    );
};

export default SectionsManagement;
