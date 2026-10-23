// SA-2471: modal to add an environment to a Section. The new environment INHERITS
// the full section key schema (one entry per shared key, empty value). Reuses the
// existing SA-2365 create-environment endpoint (POST /environments {section, name,
// variables:[{key,type,value:""}...]}).
import { useCreateEnvironment, environmentKey } from "@/features/environments/hooks/apis/environments";
import type { IEnvSectionKey } from "@/features/environments/types/environment";
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

interface IAddEnvironmentDialogProps {
    open: boolean;
    sectionName: string;
    schemaKeys: IEnvSectionKey[];
    existingNames: string[];
    onOpenChange: (open: boolean) => void;
    onCreated: (id: string) => void;
}

const AddEnvironmentDialog = ({
    open,
    sectionName,
    schemaKeys,
    existingNames,
    onOpenChange,
    onCreated,
}: IAddEnvironmentDialogProps) => {
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
        // Inherit the full section schema with empty values.
        const variables = schemaKeys.map(k => ({ key: k.key, type: k.type, value: "" }));
        create.mutate(
            { section: sectionName, name: trimmed, variables },
            {
                onSuccess: res => {
                    void queryClient.invalidateQueries({ queryKey: environmentKey.lists() });
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
                data-testid="add-environment-dialog"
                onInteractOutside={e => e.preventDefault()}
            >
                <DialogHeader>
                    <DialogTitle>{t("environments.add_environment_to", { section: sectionName })}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-1.5">
                    <Label>{t("environments.environment_name")}</Label>
                    <Input
                        value={name}
                        autoFocus
                        placeholder="prod"
                        onChange={e => setName(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && onSubmit()}
                        data-testid="add-environment-name"
                    />
                    <p className="text-xs text-muted-foreground">
                        {t("environments.environment_inherit_hint", { count: schemaKeys.length })}
                    </p>
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
                        data-testid="add-environment-create"
                    >
                        {create.isPending ? t("environments.saving") : t("environments.add_environment")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default AddEnvironmentDialog;
