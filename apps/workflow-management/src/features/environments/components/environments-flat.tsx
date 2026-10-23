// SA-2605 — flat, section-independent Environments tab. Lists every environment
// (from useEnvironmentList) with a master-detail values editor on the right. An
// environment's variable KEYS are the UNION of the shared key schemas (SA-2471) of
// every section it is a member of — membership is many-to-many, derived from
// useListSectionMemberships. Keys are READ-ONLY here (declared on the Section);
// only per-environment VALUES are editable. Membership chips ("in MCK, S4") are
// shown per environment. A key declared with two different types across an env's
// sections is flagged as a conflict inline (KEY_TYPE_CONFLICT territory).
import ConfirmDialog from "@/features/environments/components/confirm-dialog";
import {
    environmentKey,
    envSectionKey,
    useCreateEnvironment,
    useDeleteEnvironment,
    useEnvironmentList,
    useEnvSectionList,
    useListSectionMemberships,
    useUpdateEnvironment,
} from "@/features/environments/hooks/apis/environments";
import type {
    IEnvironment,
    IEnvSection,
    IEnvSectionKey,
    SectionVariableType,
} from "@/features/environments/types/environment";
import { NONE_CRED } from "@/features/environments/utils/section-schema";
import { useCredentials } from "@/features/workflows/hooks/apis/credentials";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@ldc/ui/components/dialog";
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
import { useQueryClient } from "@ldc/tanstack-query";
import { PlusIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

// A resolved key on an environment: which sections declare it + a type-conflict flag.
interface IResolvedKey {
    key: string;
    type: SectionVariableType;
    from: string[]; // sections that declare it
    conflict: boolean; // declared with >1 type across the env's sections
}

const EnvironmentsFlat = () => {
    const { t } = useLanguage();
    const { data: envData, isLoading } = useEnvironmentList();
    const { data: sectionData } = useEnvSectionList();
    const { data: membershipData } = useListSectionMemberships();

    const environments = envData?.data?.items ?? [];
    const sections = sectionData?.data?.items ?? [];
    const memberships = membershipData?.data?.memberships ?? [];

    // section name -> its shared key schema.
    const schemaBySection = useMemo(() => {
        const map = new Map<string, IEnvSectionKey[]>();
        for (const s of sections) map.set(s.section, s.keys);
        return map;
    }, [sections]);

    // environment id -> the section names it belongs to (many-to-many).
    const sectionsByEnvId = useMemo(() => {
        const map = new Map<string, string[]>();
        for (const m of memberships) {
            for (const id of m.environment_ids) {
                const list = map.get(id) ?? [];
                list.push(m.section);
                map.set(id, list);
            }
        }
        return map;
    }, [memberships]);

    const [search, setSearch] = useState("");
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [addOpen, setAddOpen] = useState(false);

    const filtered = useMemo(
        () => environments.filter(e => e.name.toLowerCase().includes(search.toLowerCase())),
        [environments, search],
    );

    useEffect(() => {
        if (environments.length === 0) {
            setSelectedId(null);
        } else if (!environments.some(e => e.id === selectedId)) {
            const first = environments[0];
            if (first) setSelectedId(first.id);
        }
    }, [environments, selectedId]);

    const selected = environments.find(e => e.id === selectedId) ?? null;

    return (
        <section className="flex h-full min-h-0 flex-col gap-4 px-4 pb-6" data-testid="environments-flat">
            {/* SA-2898: section fills the tab height, so the env list + values panel scroll inside the card */}
            {/* header row (mockup .cfghead): intro on the left, primary Add on the right */}
            <div className="flex shrink-0 items-center gap-3">
                <p className="max-w-160 text-sm text-muted-foreground">{t("environments.flat_intro")}</p>
                <span className="flex-1" />
                <Button onClick={() => setAddOpen(true)} data-testid="flat-add-environment-open">
                    <PlusIcon className="size-4" /> {t("environments.flat_add_environment")}
                </Button>
            </div>

            <div className="min-h-90 flex-1 overflow-hidden rounded-xl border border-border bg-background" data-testid="flat-env-card">
                <div className="grid h-full grid-cols-[288px_1fr] grid-rows-[minmax(0,1fr)]">
                    {/* Left: searchable env list */}
                    <div className="flex min-h-0 flex-col border-r border-border">
                        <div className="flex items-center gap-2 p-3">
                            <Input
                                value={search}
                                placeholder={t("environments.search_environments")}
                                onChange={e => setSearch(e.target.value)}
                                data-testid="flat-env-search"
                            />
                        </div>
                        <p className="px-3 pb-1 text-xs text-muted-foreground">
                            {t("environments.env_count_of", { shown: filtered.length, total: environments.length })}
                        </p>
                        <ScrollArea className="min-h-0 flex-1" data-testid="flat-env-list-scroll">
                            {isLoading && <div className="loader-bar w-full" />}
                            {!isLoading && environments.length === 0 && (
                                <p className="px-3 py-4 text-sm text-muted-foreground" data-testid="flat-env-empty">
                                    {t("environments.no_environments")}
                                </p>
                            )}
                            <ul className="flex flex-col gap-0.5 px-2 pb-2">
                                {filtered.map(env => {
                                    const secs = sectionsByEnvId.get(env.id) ?? [];
                                    return (
                                        <li key={env.id}>
                                            <button
                                                type="button"
                                                onClick={() => setSelectedId(env.id)}
                                                className={cn(
                                                    "flex w-full items-center gap-2 rounded-md border-l-2 border-transparent px-2.5 py-2 text-left hover:bg-muted/50",
                                                    selectedId === env.id && "border-l-primary bg-muted",
                                                )}
                                                data-testid={`flat-env-item-${env.name}`}
                                            >
                                                <span className="flex-1 truncate text-sm font-medium">{env.name}</span>
                                                <Badge variant={secs.length ? "secondary" : "gray"}>
                                                    {t("environments.flat_n_sections", { count: secs.length })}
                                                </Badge>
                                            </button>
                                        </li>
                                    );
                                })}
                            </ul>
                        </ScrollArea>
                    </div>

                    {/* Right: the selected env's values form */}
                    <div className="min-h-0 overflow-y-auto px-4 pb-4" data-testid="flat-env-values-scroll">
                        {selected ? (
                            <FlatEnvValuesForm
                                key={selected.id}
                                environment={selected}
                                sectionNames={sectionsByEnvId.get(selected.id) ?? []}
                                schemaBySection={schemaBySection}
                            />
                        ) : (
                            <p className="py-10 text-center text-sm text-muted-foreground">
                                {t("environments.select_environment")}
                            </p>
                        )}
                    </div>
                </div>
            </div>

            <p className="shrink-0 text-xs text-muted-foreground">{t("environments.flat_hint")}</p>

            <FlatAddEnvironmentDialog
                open={addOpen}
                existingNames={environments.map(e => e.name)}
                onOpenChange={setAddOpen}
                onCreated={id => setSelectedId(id)}
            />
        </section>
    );
};

// Section-independent add-environment dialog for the flat tab: creates an
// environment with NO section and NO variables (keys are inherited later when it
// joins a section). Reuses useCreateEnvironment (POST /environments).
const FlatAddEnvironmentDialog = ({
    open,
    existingNames,
    onOpenChange,
    onCreated,
}: {
    open: boolean;
    existingNames: string[];
    onOpenChange: (open: boolean) => void;
    onCreated: (id: string) => void;
}) => {
    const { t } = useLanguage();
    const create = useCreateEnvironment();
    const queryClient = useQueryClient();
    const [name, setName] = useState("");
    const [error, setError] = useState("");

    useEffect(() => {
        if (open) {
            setName("");
            setError("");
        }
    }, [open]);

    const onSubmit = () => {
        const trimmed = name.trim();
        setError("");
        if (!trimmed) {
            setError(t("environments.environment_name_required"));
            return;
        }
        if (existingNames.some(n => n.toLowerCase() === trimmed.toLowerCase())) {
            setError(t("environments.environment_name_exists", { name: trimmed }));
            return;
        }
        create.mutate(
            { section: "", name: trimmed, variables: [] },
            {
                onSuccess: res => {
                    void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
                    void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
                    onCreated(res.data.id);
                    onOpenChange(false);
                },
                onError: (e) => {
                    setError(e.response?.data?.message ?? t("environments.environment_create_error"));
                },
            },
        );
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="max-w-md"
                data-testid="flat-add-environment-dialog"
                onInteractOutside={e => e.preventDefault()}
            >
                <DialogHeader>
                    <DialogTitle>{t("environments.flat_add_environment_title")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-1.5">
                    <Label>{t("environments.environment_name")}</Label>
                    <Input
                        value={name}
                        autoFocus
                        placeholder="prod"
                        onChange={e => setName(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && onSubmit()}
                        data-testid="flat-add-environment-name"
                    />
                    <p className="text-xs text-muted-foreground">
                        {t("environments.flat_add_environment_hint")}
                    </p>
                    {error && <p className="text-sm text-destructive">{error}</p>}
                </div>
                <DialogFooter>
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                        {t("environments.cancel")}
                    </Button>
                    <Button
                        type="button"
                        disabled={create.isPending || !name.trim()}
                        onClick={onSubmit}
                        data-testid="flat-add-environment-create"
                    >
                        {create.isPending ? t("environments.saving") : t("environments.flat_add_environment")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

// Resolve the KEY set of an environment = union of the schemas of the sections it
// belongs to, tracking origin section(s) + a same-key-different-type conflict.
const resolveEnvKeys = (
    sectionNames: string[],
    schemaBySection: Map<string, IEnvSectionKey[]>,
): IResolvedKey[] => {
    const map = new Map<string, IResolvedKey>();
    for (const sn of sectionNames) {
        for (const k of schemaBySection.get(sn) ?? []) {
            const cur = map.get(k.key);
            if (!cur) {
                map.set(k.key, { key: k.key, type: k.type, from: [sn], conflict: false });
            } else {
                cur.from.push(sn);
                if (cur.type !== k.type) cur.conflict = true;
            }
        }
    }
    return Array.from(map.values());
};

interface IFlatEnvValuesFormProps {
    environment: IEnvironment;
    sectionNames: string[];
    schemaBySection: Map<string, IEnvSectionKey[]>;
}

const FlatEnvValuesForm = ({ environment, sectionNames, schemaBySection }: IFlatEnvValuesFormProps) => {
    const { t } = useLanguage();
    const update = useUpdateEnvironment();
    const del = useDeleteEnvironment();
    const queryClient = useQueryClient();
    const { data: credData } = useCredentials();
    const credentials = credData?.credentials ?? [];

    const keys = useMemo(
        () => resolveEnvKeys(sectionNames, schemaBySection),
        [sectionNames, schemaBySection],
    );

    const [values, setValues] = useState<Record<string, string>>({});
    const [confirmDel, setConfirmDel] = useState(false);
    const [renameOpen, setRenameOpen] = useState(false);

    useEffect(() => {
        const initial: Record<string, string> = {};
        keys.forEach(k => {
            initial[k.key] = environment.variables.find(v => v.key === k.key)?.value ?? "";
        });
        setValues(initial);
    }, [keys, environment]);

    const isDirty = useMemo(
        () =>
            keys.some(k => {
                const stored = environment.variables.find(v => v.key === k.key)?.value ?? "";
                const cur = values[k.key] === NONE_CRED ? "" : (values[k.key] ?? "");
                return cur !== stored;
            }),
        [values, keys, environment],
    );

    const hasConflict = keys.some(k => k.conflict);

    const invalidate = () => {
        void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
        void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
    };

    const onSave = () => {
        // Persist the union of shared keys with their (possibly edited) values.
        const variables = keys.map(k => ({
            key: k.key,
            type: k.type,
            value: values[k.key] === NONE_CRED ? "" : (values[k.key] ?? ""),
        }));
        update.mutate(
            {
                id: environment.id,
                version: environment.version,
                // section left unchanged — env membership is edited on the Sections tab.
                payload: { name: environment.name, variables },
            },
            {
                onSuccess: () => {
                    invalidate();
                    toast.success(t("environments.values_saved", { name: environment.name }));
                },
                onError: (e) => {
                    toast.error(e.response?.data?.message ?? t("environments.values_save_error"));
                },
            },
        );
    };

    const onDelete = () => {
        del.mutate(environment.id, {
            onSuccess: () => {
                setConfirmDel(false);
                invalidate();
                toast.success(t("environments.environment_deleted", { name: environment.name }));
            },
            onError: (e) => {
                toast.error(e.response?.data?.message ?? t("environments.environment_delete_error"));
            },
        });
    };

    return (
        <div className="flex flex-col" data-testid={`flat-env-values-${environment.name}`}>
            {/* header row — mirrors SectionCard: name + count on the left, ghost
                "Rename" / danger "Delete" text buttons in the top-right. */}
            <div className="sticky top-0 z-10 mb-1 flex items-center gap-2 bg-background pt-4 pb-1">
                <span className="text-base font-bold">{environment.name}</span>
                <span className="text-xs text-muted-foreground">
                    {t("environments.flat_variable_count", { count: keys.length })}
                </span>
                <span className="flex-1" />
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setRenameOpen(true)}
                    data-testid={`flat-rename-environment-${environment.name}`}
                >
                    {t("environments.flat_rename_environment")}
                </Button>
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => setConfirmDel(true)}
                    data-testid={`flat-delete-environment-${environment.name}`}
                >
                    {t("environments.delete_environment")}
                </Button>
            </div>

            {/* section membership chips (derived, read-only here) */}
            <div className="mb-3 flex flex-wrap gap-1.5">
                {sectionNames.length ? (
                    sectionNames.map(s => (
                        <Badge key={s} variant="secondary" data-testid={`flat-env-section-${s}`}>
                            {s}
                        </Badge>
                    ))
                ) : (
                    <Badge variant="gray">{t("environments.flat_no_section")}</Badge>
                )}
            </div>

            <p className="mb-4 text-xs text-muted-foreground">{t("environments.flat_keys_readonly")}</p>

            {hasConflict && (
                <div className="mb-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs font-medium text-destructive">
                    {t("environments.flat_type_conflict")}
                </div>
            )}

            {keys.length === 0 ? (
                <p className="py-4 text-sm text-muted-foreground">{t("environments.flat_no_keys")}</p>
            ) : (
                <div className="flex flex-col">
                    {keys.map(k => {
                        const v = values[k.key] ?? "";
                        const empty = v === "" || v === NONE_CRED;
                        return (
                            <div
                                key={k.key}
                                className="grid grid-cols-[88px_minmax(120px,220px)_1fr] items-center gap-3 border-t border-border py-2.5 first:border-t-0"
                            >
                                <Badge variant={k.conflict ? "destructive" : k.type === "credential" ? "secondary" : "outline"}>
                                    {k.conflict ? "?" : k.type}
                                </Badge>
                                <div className="flex min-w-0 flex-col">
                                    <div className="flex min-w-0 items-center gap-1.5">
                                        <span className="truncate font-mono text-sm font-medium" title={k.key}>
                                            {k.key}
                                        </span>
                                        {k.from.length > 1 && (
                                            <Badge
                                                variant="success"
                                                className="shrink-0 px-1.5 py-0 text-[9px] uppercase tracking-wide"
                                                data-testid={`flat-shared-${k.key}`}
                                            >
                                                {t("environments.flat_shared_chip")}
                                            </Badge>
                                        )}
                                    </div>
                                    <span className="truncate text-[10px] text-muted-foreground">
                                        {k.from.length > 1
                                            ? t("environments.flat_key_shared", { sections: k.from.join(", ") })
                                            : t("environments.flat_key_from", { section: k.from[0] ?? "" })}
                                    </span>
                                </div>
                                <div className="min-w-0">
                                    {k.type === "credential" ? (
                                        <Select
                                            value={v || NONE_CRED}
                                            onValueChange={val =>
                                                setValues(prev => ({ ...prev, [k.key]: val === NONE_CRED ? "" : val }))
                                            }
                                        >
                                            <SelectTrigger
                                                className={cn(
                                                    "w-full overflow-hidden [&>span]:block [&>span]:min-w-0 [&>span]:truncate",
                                                    empty && "border-amber-300",
                                                )}
                                                data-testid={`flat-value-cred-${environment.name}-${k.key}`}
                                            >
                                                <SelectValue placeholder={t("environments.select_credential")} />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value={NONE_CRED}>{t("environments.select_none")}</SelectItem>
                                                {credentials.map(c => (
                                                    <SelectItem key={c.id} value={c.id}>
                                                        {c.displayName} — {c.credentialKind}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    ) : (
                                        <Input
                                            value={v}
                                            placeholder={t("environments.value_for", { name: environment.name })}
                                            onChange={e => setValues(prev => ({ ...prev, [k.key]: e.target.value }))}
                                            data-testid={`flat-value-text-${environment.name}-${k.key}`}
                                            className={cn(empty && "border-amber-300 focus-visible:border-amber-300")}
                                        />
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            <div className="mt-4 flex items-center gap-3 border-t border-border pt-4">
                <Button
                    type="button"
                    disabled={update.isPending || keys.length === 0 || !isDirty}
                    onClick={onSave}
                    data-testid={`flat-save-values-${environment.name}`}
                >
                    {update.isPending ? t("environments.saving") : t("environments.save_values")}
                </Button>
            </div>

            <ConfirmDialog
                open={confirmDel}
                title={t("environments.delete_environment")}
                description={t("environments.delete_environment_confirm", {
                    name: environment.name,
                    section: sectionNames.join(", ") || t("environments.flat_no_section"),
                })}
                confirmLabel={t("environments.delete_environment")}
                pending={del.isPending}
                onConfirm={onDelete}
                onOpenChange={setConfirmDel}
            />

            <FlatRenameEnvironmentDialog
                open={renameOpen}
                environment={environment}
                onOpenChange={setRenameOpen}
            />
        </div>
    );
};

// Rename an environment (name only) via useUpdateEnvironment. Section membership
// and values are untouched — only the display name changes.
const FlatRenameEnvironmentDialog = ({
    open,
    environment,
    onOpenChange,
}: {
    open: boolean;
    environment: IEnvironment;
    onOpenChange: (open: boolean) => void;
}) => {
    const { t } = useLanguage();
    const update = useUpdateEnvironment();
    const queryClient = useQueryClient();
    const [name, setName] = useState(environment.name);

    useEffect(() => {
        if (open) setName(environment.name);
    }, [open, environment.name]);

    const save = () => {
        const target = name.trim();
        if (!target || target === environment.name) {
            onOpenChange(false);
            return;
        }
        update.mutate(
            { id: environment.id, version: environment.version, payload: { name: target } },
            {
                onSuccess: () => {
                    onOpenChange(false);
                    void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
                    void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
                    toast.success(t("environments.values_saved", { name: target }));
                },
                onError: (e) => {
                    toast.error(e.response?.data?.message ?? t("environments.values_save_error"));
                },
            },
        );
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-md" data-testid="flat-rename-environment-dialog">
                <DialogHeader>
                    <DialogTitle>{t("environments.flat_rename_environment_title")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-1.5">
                    <Label>{t("environments.environment_name")}</Label>
                    <Input
                        autoFocus
                        value={name}
                        onChange={e => setName(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && save()}
                        data-testid="flat-rename-environment-input"
                    />
                </div>
                <DialogFooter>
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={update.isPending}>
                        {t("environments.cancel")}
                    </Button>
                    <Button type="button" onClick={save} disabled={update.isPending} data-testid="flat-rename-environment-save">
                        {update.isPending ? t("environments.saving") : t("environments.save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default EnvironmentsFlat;
