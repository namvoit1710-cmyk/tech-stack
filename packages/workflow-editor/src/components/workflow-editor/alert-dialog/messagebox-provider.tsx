import { useTranslation } from "@ldc/i18n";
import {
    AlertDialog,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle
} from "@ldc/ui/components/alert-dialog";
import { Button } from "@ldc/ui/components/button";
import { RETE_EDITOR_I18N_NAMESPACE } from "../../../i18n";

export function Alert({ open, onClose, onConfirm, title, message }: { open?: boolean, onClose?: () => void, onConfirm?: () => void, title?: string, message?: string }) {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    return (
        <AlertDialog open={open} onOpenChange={(o: boolean) => { if (!o) onClose!(); }}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>{title ?? t("confirmation.confirm_deletion")}</AlertDialogTitle>
                    <AlertDialogDescription>{message ?? t("confirmation.delete_node")}</AlertDialogDescription>
                </AlertDialogHeader>

                <AlertDialogFooter className="py-1">
                    <Button onClick={() => onClose?.()}>
                        {t("button.cancel")}
                    </Button>
                    <Button onClick={() => onConfirm?.()}>
                        {t("button.confirm")}
                    </Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );
}