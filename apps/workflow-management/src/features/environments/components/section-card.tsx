// SA-2471: one collapsible Section card on the Config tab.
//
// A Section owns ONE shared variable key schema (keys + types); every environment
// in the section carries the SAME keys, and only the per-environment VALUE differs.
// Two sub-tabs:
//   Variables — the section's shared key schema (key input + type select + delete).
//               Save writes the schema; the backend rejects duplicate/empty keys
//               (422 SECTION_SCHEMA_INVALID_KEYS with details) and the offending
//               key fields are highlighted red inline.
//   Values    — a searchable env list (left, with a completeness badge) + the
//               selected env's values as a vertical form (right, one row per key).
import AddVariableDialog from "@/features/environments/components/add-variable-dialog";
import ConfirmDialog from "@/features/environments/components/confirm-dialog";
import {
    environmentKey,
    envSectionKey,
    useDeleteEnvSection,
    useUpdateEnvSection,
} from "@/features/environments/hooks/apis/environments";
import type {
    IEnvSection,
    IEnvSectionInvalidKeysDetails,
    IEnvSectionKey,
    SectionVariableType,
} from "@/features/environments/types/environment";
import { ENV_SECTION_INVALID_KEYS } from "@/features/environments/types/environment";
import { optimisticBadKeyIndexes } from "@/features/environments/utils/section-schema";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@ldc/ui/components/collapsible";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@ldc/ui/components/dialog";
import { Input } from "@ldc/ui/components/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@ldc/ui/components/select";
import { useQueryClient } from "@ldc/tanstack-query";
import { ChevronRightIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const SECTION_TYPES: SectionVariableType[] = ["text", "credential"];

interface ISectionCardProps {
    section: IEnvSection;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

const SectionCard = ({ section, open, onOpenChange }: ISectionCardProps) => {
    const { t } = useLanguage();
    return (
        <Collapsible
            open={open}
            onOpenChange={onOpenChange}
            className="rounded-xl border border-border bg-background"
            data-testid={`env-section-${section.section}`}
        >
            <CollapsibleTrigger asChild>
                <div
                    className="flex cursor-pointer select-none items-center gap-3 px-4 py-3 hover:bg-muted/50"
                    data-testid={`env-section-header-${section.section}`}
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
                {open && <SectionBody section={section} />}
            </CollapsibleContent>
        </Collapsible>
    );
};

// --- Rename section (SA-2450 W18) -------------------------------------------
// A button next to "Delete section" (consistent placement); opens a small dialog
// with the new name. Renaming re-tags every environment in the section
// server-side (update section-schema use case), so workflow assignments follow
// via the SA-2481 read-time section derive.
export const RenameSectionButton = ({ section }: { section: IEnvSection }) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);
    const [value, setValue] = useState(section.section);
    const update = useUpdateEnvSection();
    const queryClient = useQueryClient();

    const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

    const save = () => {
        const target = value.trim();
        if (!target || target === section.section) { setOpen(false); return; }
        update.mutate(
            { id: section.id, version: section.version, payload: { section: target } },
            {
                onSuccess: () => {
                    setOpen(false);
                    void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
                    void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
                    // Also refresh workflow-assignment queries so the Assign tab
                    // reflects the new section name (SA-2450 W16/W18).
                    void queryClient.invalidateQueries({ queryKey: environmentKey.all });
                    toast.success(t("environments.section_rename_ok"));
                },
                onError: (e) => {
                    const code = e.response?.data?.code;
                    toast.error(
                        code === "SECTION_SCHEMA_ALREADY_EXISTS"
                            ? t("environments.section_rename_exists")
                            : e.response?.data?.message ?? t("environments.section_rename_error"),
                    );
                },
            },
        );
    };

    return (
        <>
            <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={e => { stop(e); setValue(section.section); setOpen(true); }}
                data-testid={`rename-section-${section.section}`}
            >
                {t("environments.section_rename")}
            </Button>
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent onClick={stop}>
                    <DialogHeader>
                        <DialogTitle>{t("environments.section_rename")}</DialogTitle>
                    </DialogHeader>
                    <Input
                        autoFocus
                        value={value}
                        onChange={e => setValue(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter") save(); }}
                        data-testid="section-rename-input"
                    />
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={update.isPending}>
                            {t("environments.cancel")}
                        </Button>
                        <Button type="button" onClick={save} disabled={update.isPending} data-testid="section-rename-save">
                            {update.isPending ? t("environments.saving") : t("environments.save")}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
};

// --- Delete section (confirm modal, cascade) --------------------------------
export const DeleteSectionButton = ({ section }: { section: IEnvSection }) => {
    const { t } = useLanguage();
    const [confirm, setConfirm] = useState(false);
    const del = useDeleteEnvSection();
    const queryClient = useQueryClient();

    const onConfirm = () => {
        del.mutate(section.id, {
            onSuccess: () => {
                setConfirm(false);
                void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
                void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
                toast.success(t("environments.section_deleted", { name: section.section }));
            },
            onError: (e) => {
                toast.error(e.response?.data?.message ?? t("environments.section_delete_error"));
            },
        });
    };

    return (
        <>
            <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={e => {
                    e.stopPropagation();
                    setConfirm(true);
                }}
                data-testid={`delete-section-${section.section}`}
            >
                {t("environments.delete_section")}
            </Button>
            <ConfirmDialog
                open={confirm}
                title={t("environments.delete_section")}
                description={t("environments.delete_section_confirm", {
                    name: section.section,
                    variables: section.keys.length,
                    environments: section.environment_count,
                })}
                confirmLabel={t("environments.delete_section")}
                pending={del.isPending}
                onConfirm={onConfirm}
                onOpenChange={setConfirm}
            />
        </>
    );
};

// --- Expanded body: the shared-key schema editor ----------------------------
// SA-2605: values are edited only on the Environments tab, so the section body
// is just the Variables (shared-key schema) editor. Exported so
// SectionsManagement can compose it inside its single collapsible (header +
// membership + bound flows).
export const SectionBody = ({ section }: { section: IEnvSection }) => {
    return <VariablesTab section={section} />;
};

// --- Variables sub-tab: the shared key schema -------------------------------
interface IKeyRow extends IEnvSectionKey {
    _uid: number;
}
let ROW_UID = 0;

const VariablesTab = ({ section }: { section: IEnvSection }) => {
    const { t } = useLanguage();
    const update = useUpdateEnvSection();
    const queryClient = useQueryClient();

    const [rows, setRows] = useState<IKeyRow[]>([]);
    const [addOpen, setAddOpen] = useState(false);
    // Offending key row indexes (from the backend 422, or an optimistic check).
    const [badIndexes, setBadIndexes] = useState<Set<number>>(new Set());
    const [errorMessage, setErrorMessage] = useState("");

    // (Re)load rows from the section schema whenever it changes.
    useEffect(() => {
        setRows(section.keys.map(k => ({ ...k, _uid: ROW_UID++ })));
        setBadIndexes(new Set());
        setErrorMessage("");
    }, [section.keys, section.version]);

    const setRow = (i: number, patch: Partial<IEnvSectionKey>) =>
        setRows(rs => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
    const removeRow = (i: number) => setRows(rs => rs.filter((_, idx) => idx !== i));

    // Save is enabled only when the edited schema differs from what's stored
    // (no-op saves are disabled so an unchanged section can't be re-submitted).
    const isDirty = useMemo(
        () =>
            JSON.stringify(rows.map(r => ({ key: r.key, type: r.type }))) !==
            JSON.stringify(section.keys.map(k => ({ key: k.key, type: k.type }))),
        [rows, section.keys],
    );

    // Persist a keys list to the section schema. Used by both the "Save" button
    // (persist current inline edits) and "Add variable" (persist immediately, so
    // adding a variable does not need a separate Save click).
    const persist = (nextRows: IKeyRow[]) => {
        setErrorMessage("");
        // Optimistic guard for instant feedback (backend is authoritative).
        const optimistic = optimisticBadKeyIndexes(nextRows);
        if (optimistic.size > 0) {
            setBadIndexes(optimistic);
            setErrorMessage(t("environments.keys_invalid_client"));
            return;
        }
        setBadIndexes(new Set());
        const keys: IEnvSectionKey[] = nextRows.map(r => ({ key: r.key.trim(), type: r.type }));
        update.mutate(
            { id: section.id, version: section.version, payload: { keys } },
            {
                onSuccess: () => {
                    void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
                    void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
                    toast.success(t("environments.schema_saved", { keys: keys.length }));
                },
                onError: (e) => {
                    const body = e.response?.data;
                    if (body?.code === ENV_SECTION_INVALID_KEYS) {
                        // Backend is authoritative — highlight exactly its offending fields.
                        const details = body.details as IEnvSectionInvalidKeysDetails | undefined;
                        const bad = new Set<number>();
                        (details?.empty_key_indexes ?? []).forEach(i => bad.add(i));
                        const flagByName = (names?: string[]) =>
                            (names ?? []).forEach(name => {
                                nextRows.forEach((r, i) => {
                                    if (r.key.trim().toLowerCase() === name.toLowerCase()) bad.add(i);
                                });
                            });
                        flagByName(details?.duplicate_keys);
                        flagByName(details?.invalid_keys);
                        setBadIndexes(bad);
                        setErrorMessage(body.message ?? t("environments.keys_invalid_server"));
                    } else {
                        toast.error(body?.message ?? t("environments.schema_save_error"));
                    }
                },
            },
        );
    };

    const onSave = () => persist(rows);

    // Add variable = append the new key and persist immediately (add == save).
    const onAddVariable = (key: IEnvSectionKey) => {
        const nextRows = [...rows, { ...key, _uid: ROW_UID++ }];
        setRows(nextRows);
        persist(nextRows);
    };

    return (
        <div className="flex flex-col">
            <div className="flex items-center gap-2 border-b border-border px-4 py-2">
                <span className="flex-1" />
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setAddOpen(true)}
                    data-testid={`add-variable-open-${section.section}`}
                >
                    <PlusIcon className="size-4" /> {t("environments.add_variable")}
                </Button>
                <Button
                    type="button"
                    size="sm"
                    disabled={update.isPending || !isDirty}
                    onClick={onSave}
                    data-testid={`save-schema-${section.section}`}
                >
                    {update.isPending ? t("environments.saving") : t("environments.save")}
                </Button>
            </div>

            {rows.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                    {t("environments.no_shared_variables")}
                </p>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                            <tr>
                                <th className="px-4 py-2.5 font-medium">{t("environments.col_key")}</th>
                                <th className="w-44 px-4 py-2.5 font-medium">{t("environments.col_type")}</th>
                                <th className="px-4 py-2.5 text-right font-medium">{t("environments.col_actions")}</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                            {rows.map((row, i) => (
                                <tr key={row._uid}>
                                    <td className="px-4 py-2.5 align-top">
                                        <Input
                                            value={row.key}
                                            placeholder="VARIABLE_KEY"
                                            className={cn(
                                                "font-mono",
                                                badIndexes.has(i) && "border-destructive bg-destructive/5 focus-visible:ring-destructive/30",
                                            )}
                                            onChange={e => setRow(i, { key: e.target.value })}
                                            data-testid={`schema-key-${section.section}-${i}`}
                                        />
                                        {badIndexes.has(i) && (
                                            <p className="mt-1 text-xs font-medium text-destructive">
                                                {t("environments.key_duplicate_hint")}
                                            </p>
                                        )}
                                    </td>
                                    <td className="px-4 py-2.5 align-top">
                                        <Select
                                            value={row.type}
                                            onValueChange={(v: string) => setRow(i, { type: v as SectionVariableType })}
                                        >
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {SECTION_TYPES.map(tp => (
                                                    <SelectItem key={tp} value={tp}>
                                                        {tp}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </td>
                                    <td className="px-4 py-2.5 text-right align-top">
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="text-muted-foreground hover:text-destructive"
                                            onClick={() => removeRow(i)}
                                            data-testid={`schema-del-${section.section}-${i}`}
                                        >
                                            <Trash2Icon className="size-4" />
                                        </Button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {errorMessage && (
                <p className="px-4 py-2 text-sm font-medium text-destructive">{errorMessage}</p>
            )}

            <AddVariableDialog
                open={addOpen}
                sectionName={section.section}
                existingKeys={rows.map(r => r.key.trim()).filter(Boolean)}
                onOpenChange={setAddOpen}
                onAdd={onAddVariable}
            />
        </div>
    );
};


export default SectionCard;
