import {
    AlertDialog,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle
} from "@ldc/ui/components/alert-dialog";
import { Button } from "@ldc/ui/components/button";
import { createContext, useCallback, useContext, useRef, useState } from "react";
import { useLanguage } from "../../hooks/use-language";

const Ctx = createContext<(msg: string, title?: string) => Promise<boolean>>(null!);

export function MessageBoxProvider({ children }: { children: React.ReactNode }) {

    const { t } = useLanguage();

    const [open, setOpen] = useState(false);
    const [config, setConfig] = useState<{ message: string; title?: string }>({ message: "" });
    const resolveRef = useRef<((action: boolean) => void) | null>(null);

    const showMessageBox = useCallback((message: string, title?: string): Promise<boolean> => {
        return new Promise((resolve) => {
            resolveRef.current = resolve;
            setConfig({ message, title });
            setOpen(true);
        });
    }, []);

    const handleClose = useCallback((action: boolean) => {
        setOpen(false);
        resolveRef.current?.(action);
    }, []);

    return (
        <Ctx.Provider value={showMessageBox}>
            {children}

            <AlertDialog open={open} onOpenChange={(o) => { if (!o) handleClose(false); }}>
                <AlertDialogContent className="p-4">
                    <AlertDialogHeader>
                        {config.title && <AlertDialogTitle>{config.title}</AlertDialogTitle>}
                        <AlertDialogDescription>{config.message}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <Button variant="destructive" onClick={() => handleClose(false)}>
                            {t("cancel")}
                        </Button>
                        <Button onClick={() => handleClose(true)}>
                            {t("confirm")}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </Ctx.Provider>
    );
}

export const useMessageBox = () => useContext(Ctx);