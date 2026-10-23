import {
    useEnvironmentList,
    useWorkflowEnvironmentAssignment,
} from "@/features/environments/hooks/apis/environments";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import {
    Command,
    CommandEmpty,
    CommandInput,
    CommandItem,
    CommandList,
} from "@ldc/ui/components/command";
import { Input } from "@ldc/ui/components/input";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@ldc/ui/components/popover";
import { Check, ChevronsUpDown, Zap } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

interface IEnvironmentPickerControlProps {
    name: string;
    value?: string;
    onChange?: (event: { target: { name: string; value: string } }) => void;
    placeholder?: string;
    disabled?: boolean;
}

// environment_id sentinels for the child sub-workflow run's $env (mirrors the
// backend ChildWorkflowCoordinator):
//   ""            → No environment (DEFAULT) — child runs with no $env
//   "__inherit__" → Inherit the parent run's $env
//   <env id>      → bind that specific environment
const NO_ENV = "__no_env__";     // UI-only token for the empty (default) row
const INHERIT = "__inherit__";

// SA-2496: "Dynamic environment (only id)". The picker stores an EXPRESSION as
// the environment_id; the backend resolves it at run time and requires the
// result to be an assigned environment id (a name/label is rejected). A value
// carrying "{{" (or a leading "$") IS the dynamic mode.
const looksDynamic = (v?: string): boolean =>
    !!v && (v.includes("{{") || v.trim().startsWith("$"));

/**
 * Custom AutoForm control for the WORKFLOW (sub-workflow) node's
 * ``environment_id`` field (SA-2365 / SA-2458). Renders a searchable environment
 * dropdown plus two sentinel rows: "No environment" (default) and "Inherit
 * parent environment". A specific environment id is what the child sub-workflow
 * run binds as its ``$env``, so one parent flow can drive the same subflow
 * against (e.g.) dev on one branch and qas on another.
 *
 * Wired in via the backend NodeSpec
 * (``field_control="EnvironmentPickerControl"``) and registered on the
 * node-detail Builder's ``fieldControl`` map.
 */
const EnvironmentPickerControl = ({
    name,
    value,
    onChange,
    placeholder,
    disabled,
}: IEnvironmentPickerControlProps) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState("");
    // SA-2496: dynamic (expression) mode. Seeded from an expression value; once
    // on it stays on until the operator picks a fixed option / switches back.
    const [isDynamic, setIsDynamic] = useState(() => looksDynamic(value));
    useEffect(() => {
        if (looksDynamic(value)) setIsDynamic(true);
    }, [value]);

    // SA-2496 / D13: the dynamic field holds exactly ONE variable. The
    // node-detail form has a container-level dropzone (use-form-dropzone) that
    // APPENDS a dragged {{...}} token to any input; for an environment id we
    // want REPLACE (single value). A capture-phase drop handler on this input
    // beats that container handler (stopImmediatePropagation) and replaces.
    const exprRef = useRef<HTMLInputElement>(null);
    useEffect(() => {
        const el = exprRef.current;
        if (!el || !isDynamic) return;
        const onDrop = (e: DragEvent) => {
            const tok = e.dataTransfer?.getData("text/plain");
            if (!tok) return;
            e.preventDefault();
            e.stopImmediatePropagation(); // beat the form-level append dropzone
            onChange?.({ target: { name, value: tok } }); // single value → replace
        };
        const onDragOver = (e: DragEvent) => {
            if (!e.dataTransfer?.types.includes("text/plain")) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            e.dataTransfer.dropEffect = "copy";
        };
        el.addEventListener("drop", onDrop, true);
        el.addEventListener("dragover", onDragOver, true);
        return () => {
            el.removeEventListener("drop", onDrop, true);
            el.removeEventListener("dragover", onDragOver, true);
        };
    }, [isDynamic, name, onChange]);

    const { data, isLoading } = useEnvironmentList();
    const envs = data?.data?.items ?? [];

    // SA-2450: scope the offered environments to the SUBFLOW this node targets.
    // The WORKFLOW node's ``workflow_id`` parameter is the child flow; only that
    // subflow's assigned environments are valid for it (the run guard enforces
    // the same at dispatch). Read it from the node being edited.
    const selectedNode = useEditorStore((s) => s.selectedNode);
    const targetWorkflowId = (
        selectedNode?.original?.parameters as Record<string, unknown> | undefined
    )?.["workflow_id"];
    const subflowId = typeof targetWorkflowId === "string" ? targetWorkflowId : "";
    const { data: assignData } = useWorkflowEnvironmentAssignment(subflowId, {
        meta: { silent: true },
    });
    const assignment = assignData?.data;
    const hasTarget = !!subflowId;
    const isAssigned = !!assignment?.assigned && (assignment?.environment_ids?.length ?? 0) > 0;
    const assignedIds = useMemo(
        () => new Set(isAssigned ? assignment!.environment_ids : []),
        [isAssigned, assignment],
    );

    // With a target subflow: assigned → only its envs; unassigned → none (only
    // the No-environment / Inherit sentinels remain, plus a hint). No target yet
    // → can't scope, so offer all (the run guard still protects the child run).
    const scopedEnvs = useMemo(() => {
        if (!hasTarget) return envs;
        return isAssigned ? envs.filter((e) => assignedIds.has(e.id)) : [];
    }, [envs, hasTarget, isAssigned, assignedIds]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return scopedEnvs;
        return scopedEnvs.filter(
            (e) =>
                e.name.toLowerCase().includes(q) ||
                (e.section ?? "").toLowerCase().includes(q) ||
                e.id.toLowerCase().includes(q),
        );
    }, [scopedEnvs, search]);

    const selected = envs.find((e) => e.id === value);
    const selectedLabel = isDynamic
        ? t("environments.picker_dynamic")
        : value === INHERIT
            ? t("environments.picker_inherit")
            : value && selected
                ? `${selected.section ? `${selected.section} · ` : ""}${selected.name} (${value})`
                : value && !selected
                    ? value
                    : t("environments.picker_none"); // empty → No environment (default)

    const handleSelect = (choice: string) => {
        setSearch("");
        setOpen(false);
        // Picking any fixed option (No env / Inherit / a concrete env) leaves
        // dynamic mode; the dynamic value field is hidden again (SA-2496 / opt A).
        setIsDynamic(false);
        // NO_ENV is the UI token for the empty/default (No environment) row.
        onChange?.({ target: { name, value: choice === NO_ENV ? "" : choice } });
    };

    // SA-2496 (option A): the Environment field always shows the picker; choosing
    // "Dynamic environment (only id)" keeps the picker AND reveals a separate
    // labelled "Dynamic environment value" input (a drop target) below it. The
    // stored ``environment_id`` is the expression typed/dropped there.
    // D10: soft design-time warning when a LITERAL value (not an expression) is
    // not one of the target subflow's assigned env ids. Never blocks saving.
    const trimmedVal = (value ?? "").trim();
    const literalNotAssigned =
        !!trimmedVal && !looksDynamic(value) && isAssigned && !assignedIds.has(trimmedVal);

    return (
        <div className="flex w-full min-w-0 flex-col gap-2">
        <Popover open={open} onOpenChange={setOpen} modal>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="outline"
                    role="combobox"
                    aria-expanded={open}
                    disabled={disabled}
                    className="h-10 w-full min-w-0 justify-between font-normal"
                >
                    <span className="truncate">
                        {selectedLabel || (
                            <span className="text-muted-foreground">
                                {placeholder ??
                                    t("environments.picker_placeholder")}
                            </span>
                        )}
                    </span>
                    <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
                </Button>
            </PopoverTrigger>
            <PopoverContent
                align="start"
                className="w-(--radix-popover-trigger-width) p-0"
            >
                <Command shouldFilter={false}>
                    <CommandInput
                        placeholder={t("environments.picker_search_placeholder")}
                        onValueChange={setSearch}
                    />
                    <CommandList>
                        {isLoading && (
                            <div className="p-2 text-sm text-muted-foreground">
                                {t("environments.picker_loading")}
                            </div>
                        )}
                        {/* No environment — the default: child runs with no $env */}
                        <CommandItem
                            value={NO_ENV}
                            onSelect={() => handleSelect(NO_ENV)}
                            className="flex items-center gap-2"
                        >
                            <span className="flex-1 truncate text-muted-foreground">
                                {t("environments.picker_none")}
                            </span>
                            {!value && (
                                <Check className="size-4 shrink-0 text-green-500" />
                            )}
                        </CommandItem>
                        {/* Inherit — child copies the parent run's $env */}
                        <CommandItem
                            value={INHERIT}
                            onSelect={() => handleSelect(INHERIT)}
                            className="flex items-center gap-2"
                        >
                            <span className="flex-1 truncate text-muted-foreground">
                                {t("environments.picker_inherit")}
                            </span>
                            {value === INHERIT && (
                                <Check className="size-4 shrink-0 text-green-500" />
                            )}
                        </CommandItem>
                        {hasTarget && !isAssigned && (
                            <div className="p-2 text-xs text-muted-foreground">
                                {t("environments.picker_subflow_unassigned")}
                            </div>
                        )}
                        {filtered.map((env) => (
                            <CommandItem
                                key={env.id}
                                value={env.id}
                                onSelect={() => handleSelect(env.id)}
                                className="flex items-center gap-2"
                            >
                                <span className="flex-1 truncate">
                                    {env.section
                                        ? `${env.section} · ${env.name}`
                                        : env.name}
                                </span>
                                <span className="truncate text-xs text-muted-foreground">
                                    {env.id}
                                </span>
                                {value === env.id && (
                                    <Check className="size-4 shrink-0 text-green-500" />
                                )}
                            </CommandItem>
                        ))}
                        {/* SA-2496 — Dynamic environment (only id): switch the
                            field to an expression input resolved at run time. */}
                        <CommandItem
                            value="__dynamic__"
                            onSelect={() => {
                                setSearch("");
                                setOpen(false);
                                setIsDynamic(true);
                                if (!looksDynamic(value)) {
                                    onChange?.({ target: { name, value: "" } });
                                }
                            }}
                            className="mt-1 flex items-center gap-2 border-t"
                        >
                            <Zap className="size-4 shrink-0 text-violet-500" />
                            <span className="flex-1 truncate">
                                {t("environments.picker_dynamic")}
                            </span>
                            {isDynamic && (
                                <Check className="size-4 shrink-0 text-violet-500" />
                            )}
                        </CommandItem>
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>

        {/* SA-2496 (option A): separate labelled value field, shown only when
            Dynamic is selected — the picker above stays visible. */}
        {isDynamic && (
            <div className="flex w-full min-w-0 flex-col gap-1.5">
                <label className="text-xs font-medium text-violet-700">
                    <Zap className="mr-1 inline size-3 align-[-1px]" />
                    {t("environments.picker_dynamic_value_label")}
                    <span className="ml-0.5 text-destructive">*</span>
                </label>
                <Input
                    ref={exprRef}
                    name={name}
                    value={typeof value === "string" ? value : ""}
                    onChange={(e) =>
                        onChange?.({ target: { name, value: e.target.value } })
                    }
                    placeholder={t("environments.picker_dynamic_placeholder")}
                    disabled={disabled}
                    className="h-10 font-mono text-sm"
                />
                {literalNotAssigned && (
                    <span className="text-xs text-amber-600">
                        {t("environments.picker_dynamic_warn_literal")}
                    </span>
                )}
                <span className="text-xs text-muted-foreground">
                    {t("environments.picker_dynamic_hint")}
                </span>
            </div>
        )}
        </div>
    );
};

EnvironmentPickerControl.displayName = "EnvironmentPickerControl";

export default EnvironmentPickerControl;
