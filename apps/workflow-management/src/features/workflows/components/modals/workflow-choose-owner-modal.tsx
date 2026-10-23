import { useLanguage } from "@/hooks/use-language";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { LoadingSpin } from "@ldc/workflow-editor";
import { cn } from "@ldc/ui";
import type { ComponentProps } from "react";
import { useEffect, useMemo, useState } from "react";
import { useSetWorkflowOwner, useWorkflowUsers, workflowKey } from "../../hooks/apis/workflows";
import { useQueryClient } from "@ldc/tanstack-query";
import type { IWorkflowUser } from "../../types/workflows";

interface IWorkflowChooseOwnerModalProps extends ComponentProps<typeof Dialog> {
    // SA-2570: reassign the owner of this workflow. currentOwner is the owner's
    // id (Keycloak sub) — pre-selected + flagged in the picker.
    defaultValues?: { id: string, name: string, currentOwner: string | null };
    onSaved: () => void;
}

function initials(user: IWorkflowUser): string {
    return (user.name || user.email || "?")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((s) => s[0]?.toUpperCase() ?? "")
        .join("") || "?";
}

const WorkflowChooseOwnerModal = ({ defaultValues, onSaved, ...props }: IWorkflowChooseOwnerModalProps) => {
    const { t } = useLanguage();
    const queryClient = useQueryClient();
    const workflowId = defaultValues?.id ?? "";
    const currentOwner = defaultValues?.currentOwner ?? null;

    const { data, isLoading } = useWorkflowUsers({ enabled: !!props.open });
    const [search, setSearch] = useState("");
    const [selectedId, setSelectedId] = useState<string | null>(currentOwner);

    // Reset the selection + search each time the modal opens.
    useEffect(() => {
        if (props.open) {
            setSelectedId(currentOwner);
            setSearch("");
        }
    }, [props.open, currentOwner]);

    const users = useMemo(() => {
        const items = data?.data?.items ?? [];
        const needle = search.trim().toLowerCase();
        if (!needle) return items;
        return items.filter(
            (u) => u.name.toLowerCase().includes(needle) || (u.email ?? "").toLowerCase().includes(needle),
        );
    }, [data, search]);

    const { mutate, isPending } = useSetWorkflowOwner({
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: workflowKey.all });
            toast.success(t("owner_updated_successfully"));
            onSaved();
        },
        onError: (error) => {
            toast.error(error.message || t("owner_update_failed"));
        },
    });

    const handleSave = () => {
        if (!workflowId || !selectedId) return;
        mutate({ workflowId, owner: selectedId });
    };

    const unchanged = selectedId === currentOwner;

    return (
        <Dialog {...props}>
            <DialogContent className="w-1/3">
                <DialogHeader>
                    <DialogTitle>
                        {t("choose_owner")}
                        {defaultValues?.name ? ` — ${defaultValues.name}` : ""}
                    </DialogTitle>
                </DialogHeader>

                <input
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
                    placeholder={t("search_users_placeholder")}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                />

                <div className="mt-2 flex max-h-64 flex-col gap-1 overflow-y-auto">
                    {isLoading ? (
                        <div className="flex justify-center py-6"><LoadingSpin /></div>
                    ) : users.length === 0 ? (
                        <div className="py-6 text-center text-sm text-muted-foreground">{t("no_users_found")}</div>
                    ) : (
                        users.map((u) => {
                            const isSelected = u.id === selectedId;
                            const isCurrent = u.id === currentOwner;
                            return (
                                <button
                                    type="button"
                                    key={u.id}
                                    onClick={() => setSelectedId(u.id)}
                                    className={cn(
                                        "flex items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                                        isSelected ? "border-primary bg-primary/10" : "border-transparent hover:bg-muted/60",
                                    )}
                                >
                                    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                                        {initials(u)}
                                    </span>
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-sm font-medium">{u.name}</span>
                                        {u.email ? <span className="block truncate text-xs text-muted-foreground">{u.email}</span> : null}
                                    </span>
                                    {isCurrent ? (
                                        <span className="shrink-0 text-xs text-muted-foreground">{t("current_owner")}</span>
                                    ) : null}
                                    <span
                                        className={cn(
                                            "size-3.5 shrink-0 rounded-full border-2",
                                            isSelected ? "border-primary bg-primary" : "border-muted-foreground/40",
                                        )}
                                    />
                                </button>
                            );
                        })
                    )}
                </div>

                <DialogFooter className="p-2">
                    <DialogClose asChild disabled={isPending}>
                        <Button variant="outline">{t("cancel")}</Button>
                    </DialogClose>
                    <Button disabled={isPending || unchanged || !selectedId} onClick={handleSave}>
                        {isPending ? <LoadingSpin /> : t("save_owner")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default WorkflowChooseOwnerModal;
