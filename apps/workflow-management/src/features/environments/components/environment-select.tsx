// Run-time environment picker (SA-2365, filtered by SA-2605). Stores the choice in
// the editor store; use-execute-workflow reads it and sends environment_id with the
// run so the engine binds that environment's $env source.
//
// SA-2605: the picker offers the environments of the currently-open workflow's
// BOUND SECTION (via useWorkflowSection -> useSectionMembership). A workflow may use
// ANY environment of its section — there is no per-flow subset any more. If the
// workflow is unbound (or its section has no environments) it shows a hint and no
// selectable environment; it never falls back to all envs.
import {
    useEnvironmentList,
    useSectionMembership,
    useWorkflowSection,
} from "@/features/environments/hooks/apis/environments";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { useLanguage } from "@/hooks/use-language";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@ldc/ui/components/select";
import { useEffect } from "react";

const NONE = "__none__";

const EnvironmentSelect = () => {
    const { t } = useLanguage();
    const { data } = useEnvironmentList();
    const workflowId = useEditorStore(s => s.workflowInfo?.id) ?? "";
    const { data: bindData } = useWorkflowSection(workflowId, { meta: { silent: true } });
    const selected = useEditorStore(s => s.selectedEnvironmentId);
    const setSelected = useEditorStore(s => s.setSelectedEnvironmentId);

    const binding = bindData?.data;
    const boundSection = binding?.assigned ? binding.section : "";
    const { data: membershipData } = useSectionMembership(boundSection, { meta: { silent: true } });

    const allEnvs = data?.data?.items ?? [];
    const memberIds = new Set(membershipData?.data?.environment_ids ?? []);

    // Only the bound section's environments — no fallback to all when unbound.
    const envs = boundSection && memberIds.size > 0 ? allEnvs.filter(e => memberIds.has(e.id)) : [];

    // Drop a stale selection that is no longer in the bound set (e.g. the binding
    // changed under the open workflow).
    useEffect(() => {
        if (selected && !envs.some(e => e.id === selected)) {
            setSelected(null);
        }
    }, [selected, envs, setSelected]);

    // Unbound (or empty) workflow: show a hint, nothing selectable.
    if (envs.length === 0) {
        return (
            <span className="text-xs text-muted-foreground" data-testid="run-environment-unassigned">
                {t("environments.select_unassigned_hint")}
            </span>
        );
    }

    return (
        <Select
            value={selected ?? NONE}
            onValueChange={(v: string) => setSelected(v === NONE ? null : v)}
        >
            <SelectTrigger className="h-8 w-[190px]" data-testid="run-environment-select">
                <SelectValue placeholder={t("environments.select_placeholder")} />
            </SelectTrigger>
            <SelectContent>
                <SelectItem value={NONE}>{t("environments.select_none")}</SelectItem>
                {envs.map(e => (
                    <SelectItem key={e.id} value={e.id}>
                        {e.section ? `${e.section} · ${e.name}` : e.name}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
};

export default EnvironmentSelect;
