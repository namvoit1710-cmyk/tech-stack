// SA-2471: modal to create a new Section (its shared key schema starts empty).
// Inline validation: name required + unique (client). The backend still owns the
// truth — a duplicate name comes back as 409 (SECTION_SCHEMA_ALREADY_EXISTS) and
// is surfaced in the dialog.
import { useCreateEnvSection, envSectionKey } from "@/features/environments/hooks/apis/environments";
import { ENV_SECTION_ALREADY_EXISTS } from "@/features/environments/types/environment";
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
import { useQueryClient } from "@ldc/tanstack-query";
import { useEffect, useState } from "react";

interface IAddSectionDialogProps {
    open: boolean;
    existingNames: string[];
    onOpenChange: (open: boolean) => void;
    onCreated: (id: string) => void;
}

const AddSectionDialog = ({ open, existingNames, onOpenChange, onCreated }: IAddSectionDialogProps) => {
    const { t } = useLanguage();
    const create = useCreateEnvSection();
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
            setError(t("environments.section_name_required"));
            return;
        }
        if (existingNames.some(n => n.toLowerCase() === trimmed.toLowerCase())) {
            setError(t("environments.section_name_exists", { name: trimmed }));
            return;
        }
        create.mutate(
            { section: trimmed, keys: [] },
            {
                onSuccess: res => {
                    void queryClient.invalidateQueries({ queryKey: envSectionKey.lists() });
                    onCreated(res.data.id);
                    onOpenChange(false);
                },
                onError: (e) => {
                    const body = e.response?.data;
                    setError(
                        body?.code === ENV_SECTION_ALREADY_EXISTS
                            ? t("environments.section_name_exists", { name: trimmed })
                            : body?.message ?? t("environments.section_create_error"),
                    );
                },
            },
        );
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="max-w-md"
                data-testid="add-section-dialog"
                onInteractOutside={e => e.preventDefault()}
            >
                <DialogHeader>
                    <DialogTitle>{t("environments.add_section")}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-1.5">
                    <Label>{t("environments.section_name")}</Label>
                    <Input
                        value={name}
                        autoFocus
                        placeholder="MCK"
                        onChange={e => setName(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && onSubmit()}
                        data-testid="add-section-name"
                    />
                    <p className="text-xs text-muted-foreground">{t("environments.section_name_hint")}</p>
                    {error && <p className="text-sm text-red-600">{error}</p>}
                </div>
                <DialogFooter>
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                        {t("environments.cancel")}
                    </Button>
                    <Button
                        type="button"
                        disabled={create.isPending || !name.trim()}
                        onClick={onSubmit}
                        data-testid="add-section-create"
                    >
                        {create.isPending ? t("environments.saving") : t("environments.create_section")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default AddSectionDialog;
