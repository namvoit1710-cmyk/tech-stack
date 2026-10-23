// SA-2471: a reusable confirm dialog for destructive actions (delete section /
// variable / environment). Replaces native window.confirm — the body states the
// blast radius so the operator sees what is removed before confirming.
import { Button } from "@ldc/ui/components/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@ldc/ui/components/dialog";
import { useLanguage } from "@/hooks/use-language";
import type { ReactNode } from "react";

interface IConfirmDialogProps {
    open: boolean;
    title: string;
    description: ReactNode;
    confirmLabel?: string;
    pending?: boolean;
    onConfirm: () => void;
    onOpenChange: (open: boolean) => void;
}

const ConfirmDialog = ({
    open,
    title,
    description,
    confirmLabel,
    pending,
    onConfirm,
    onOpenChange,
}: IConfirmDialogProps) => {
    const { t } = useLanguage();
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-md" data-testid="env-confirm-dialog">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>
                <DialogFooter>
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                        {t("environments.cancel")}
                    </Button>
                    <Button
                        type="button"
                        variant="destructive"
                        disabled={pending}
                        onClick={onConfirm}
                        data-testid="env-confirm-delete"
                    >
                        {pending ? t("environments.saving") : (confirmLabel ?? t("environments.delete"))}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default ConfirmDialog;
