// SA-2471 / SA-2605: modal to add a shared variable key to a Section (applies to
// EVERY environment in the section). Two modes:
//   • "Choose existing" — pick a key already declared in ANOTHER section; it is
//     reused WITH its existing type (keeps the shared-key model consistent).
//   • "Create new" — type a fresh KEY + pick a type.
// A first-guard client check on the key format + uniqueness; the backend Save
// check (422 duplicate/empty) remains authoritative.
import { useEnvSectionList } from "@/features/environments/hooks/apis/environments";
import type {
    IEnvSectionKey,
    SectionVariableType,
} from "@/features/environments/types/environment";
import { useLanguage } from "@/hooks/use-language";
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
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@ldc/ui/components/select";
import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";
import { useEffect, useMemo, useState } from "react";

const TYPES: SectionVariableType[] = ["text", "credential"];
const KEY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

type Mode = "existing" | "new";

interface IAddVariableDialogProps {
    open: boolean;
    sectionName: string;
    existingKeys: string[];
    onOpenChange: (open: boolean) => void;
    onAdd: (key: IEnvSectionKey) => void;
}

const AddVariableDialog = ({
    open,
    sectionName,
    existingKeys,
    onOpenChange,
    onAdd,
}: IAddVariableDialogProps) => {
    const { t } = useLanguage();
    const [mode, setMode] = useState<Mode>("existing");
    const [key, setKey] = useState("");
    const [type, setType] = useState<SectionVariableType>("text");
    const [chosen, setChosen] = useState(""); // selected existing key
    const [error, setError] = useState("");

    // Catalog of keys declared in OTHER sections (distinct {key,type}), excluding
    // keys already in THIS section. First-declared type wins on a name clash.
    const { data: sectionData } = useEnvSectionList();
    const catalog = useMemo(() => {
        const taken = new Set(existingKeys.map(k => k.trim().toLowerCase()));
        const byName = new Map<string, IEnvSectionKey>();
        for (const s of sectionData?.data?.items ?? []) {
            if (s.section === sectionName) continue;
            for (const k of s.keys) {
                const lc = k.key.trim().toLowerCase();
                if (!lc || taken.has(lc) || byName.has(lc)) continue;
                byName.set(lc, { key: k.key, type: k.type });
            }
        }
        return Array.from(byName.values()).sort((a, b) => a.key.localeCompare(b.key));
    }, [sectionData, sectionName, existingKeys]);

    // Default to "Create new" when there's nothing to reuse.
    useEffect(() => {
        if (open) {
            setMode(catalog.length > 0 ? "existing" : "new");
            setKey("");
            setType("text");
            setChosen("");
            setError("");
        }
        // catalog identity is stable per open; intentionally not a dep to avoid
        // resetting the form as the list query resolves.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    const submitExisting = () => {
        setError("");
        const picked = catalog.find(k => k.key === chosen);
        if (!picked) {
            setError(t("environments.variable_existing_required"));
            return;
        }
        onAdd({ key: picked.key, type: picked.type });
        onOpenChange(false);
    };

    const submitNew = () => {
        const trimmed = key.trim();
        setError("");
        if (!trimmed) {
            setError(t("environments.variable_key_required"));
            return;
        }
        if (!KEY_RE.test(trimmed)) {
            setError(t("environments.variable_key_invalid"));
            return;
        }
        if (existingKeys.some(k => k.toLowerCase() === trimmed.toLowerCase())) {
            setError(t("environments.variable_key_exists", { key: trimmed }));
            return;
        }
        onAdd({ key: trimmed, type });
        onOpenChange(false);
    };

    const onSubmit = () => (mode === "existing" ? submitExisting() : submitNew());

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="max-w-md"
                data-testid="add-variable-dialog"
                onInteractOutside={e => e.preventDefault()}
            >
                <DialogHeader>
                    <DialogTitle>{t("environments.add_variable_to", { section: sectionName })}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-4">
                    {/* mode toggle — "Choose existing" vs "Create new" */}
                    <ToggleGroup
                        type="single"
                        value={mode}
                        onValueChange={v => {
                            if (v === "existing" || v === "new") {
                                setMode(v);
                                setError("");
                            }
                        }}
                        variant="outline"
                        size="sm"
                        className="w-full"
                        data-testid="add-variable-mode"
                    >
                        <ToggleGroupItem
                            value="existing"
                            disabled={catalog.length === 0}
                            className="flex-1"
                            data-testid="add-variable-mode-existing"
                        >
                            {t("environments.variable_mode_existing")}
                        </ToggleGroupItem>
                        <ToggleGroupItem
                            value="new"
                            className="flex-1"
                            data-testid="add-variable-mode-new"
                        >
                            {t("environments.variable_mode_new")}
                        </ToggleGroupItem>
                    </ToggleGroup>

                    {mode === "existing" ? (
                        <div className="flex flex-col gap-1.5">
                            <Label>{t("environments.variable_existing_label")}</Label>
                            {catalog.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    {t("environments.variable_existing_empty")}
                                </p>
                            ) : (
                                <>
                                    <Select value={chosen} onValueChange={setChosen}>
                                        <SelectTrigger data-testid="add-variable-existing-select">
                                            <SelectValue placeholder={t("environments.variable_existing_placeholder")} />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {catalog.map(k => (
                                                <SelectItem key={k.key} value={k.key}>
                                                    <span className="font-mono">{k.key}</span>
                                                    <span className="ml-2 text-xs text-muted-foreground">{k.type}</span>
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">
                                        {t("environments.variable_existing_hint")}
                                    </p>
                                </>
                            )}
                        </div>
                    ) : (
                        <>
                            <div className="flex flex-col gap-1.5">
                                <Label>{t("environments.variable_key")}</Label>
                                <Input
                                    value={key}
                                    autoFocus
                                    placeholder="OAUTH"
                                    className="font-mono"
                                    onChange={e => setKey(e.target.value)}
                                    onKeyDown={e => e.key === "Enter" && onSubmit()}
                                    data-testid="add-variable-key"
                                />
                                <p className="text-xs text-muted-foreground">{t("environments.variable_key_hint")}</p>
                            </div>
                            <div className="flex flex-col gap-1.5">
                                <Label>{t("environments.variable_type")}</Label>
                                <Select value={type} onValueChange={(v: string) => setType(v as SectionVariableType)}>
                                    <SelectTrigger data-testid="add-variable-type">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {TYPES.map(tp => (
                                            <SelectItem key={tp} value={tp}>
                                                {tp}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </>
                    )}
                    {error && <p className="text-sm text-red-600">{error}</p>}
                </div>
                <DialogFooter>
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                        {t("environments.cancel")}
                    </Button>
                    <Button
                        type="button"
                        onClick={onSubmit}
                        disabled={mode === "existing" && !chosen}
                        data-testid="add-variable-add"
                    >
                        {t("environments.add_variable")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default AddVariableDialog;
