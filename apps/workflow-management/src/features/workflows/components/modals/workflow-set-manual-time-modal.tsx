import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { LoadingSpin } from "@ldc/workflow-editor";
import { ChevronDown, ChevronUp, Clock } from "lucide-react";
import type { ComponentProps } from "react";
import { useEffect, useMemo, useState } from "react";
import { useSetManualTime } from "../../hooks/apis/workflows";
import { humanizeSeconds } from "../common/manual-work-chip";

interface IWorkflowSetManualTimeModalProps extends ComponentProps<typeof Dialog> {
    defaultValues?: { id: string; name?: string; manualSeconds?: number | null; successfulRuns?: number };
    onSaved: () => void;
}

// SA-2751: clock-style H:M:S input for the manual time a workflow's task would
// take done by hand, once. Save is disabled at 0 (saving 0 → saved = runs × 0);
// a set value is permanent (adjust up/down, never cleared → no Remove button).
const MAX = { h: 99, m: 59, s: 59 } as const;
const clamp = (v: number, max: number) => Math.max(0, Math.min(max, Number.isFinite(v) ? v : 0));

const WorkflowSetManualTimeModal = ({ defaultValues, onSaved, ...props }: IWorkflowSetManualTimeModalProps) => {
    const { t } = useLanguage();
    const seed = defaultValues?.manualSeconds ?? 0;
    const runs = defaultValues?.successfulRuns ?? 0;

    const [h, setH] = useState(0);
    const [m, setM] = useState(0);
    const [s, setS] = useState(0);

    // Re-seed the fields each time the dialog opens for a row.
    useEffect(() => {
        if (!props.open) return;
        setH(Math.floor(seed / 3600));
        setM(Math.floor((seed % 3600) / 60));
        setS(seed % 60);
    }, [props.open, seed]);

    const total = h * 3600 + m * 60 + s;
    const isEdit = (defaultValues?.manualSeconds ?? 0) > 0;

    const { mutate, isPending } = useSetManualTime({
        onSuccess: () => {
            onSaved();
            toast.success(t("notification.workflow_updated_successfully"));
        },
        onError: () => toast.error(t("notification.workflow_update_failed")),
    });

    const handleSave = () => {
        if (!defaultValues?.id || total <= 0) return;
        mutate({ workflowId: defaultValues.id, manualSeconds: total });
    };

    const units = useMemo(() => ([
        { key: "hours", value: h, set: setH, max: MAX.h },
        { key: "min", value: m, set: setM, max: MAX.m },
        { key: "sec", value: s, set: setS, max: MAX.s },
    ]), [h, m, s]);

    return (
        <Dialog {...props}>
            <DialogContent className="w-[420px] max-w-[calc(100vw-2rem)]">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Clock className="size-4 text-emerald-600" />
                        {isEdit ? "Edit manual time" : "Set manual time"}
                    </DialogTitle>
                    <p className="text-sm text-muted-foreground">
                        How long would this task take a person, done by hand — once? Time saved is that × the number of successful runs.
                    </p>
                </DialogHeader>

                <div className="flex items-start gap-2 py-2">
                    {units.map((u, i) => (
                        <div key={u.key} className="flex items-start gap-2">
                            <div className="flex flex-col items-center gap-1">
                                <Button variant="outline" size="icon" className="h-6 w-[72px]"
                                    onClick={() => u.set(clamp(u.value + 1, u.max))} aria-label={`${u.key} up`}>
                                    <ChevronUp className="size-3.5" />
                                </Button>
                                <input
                                    inputMode="numeric"
                                    value={u.value}
                                    aria-label={u.key}
                                    onChange={(e) => u.set(clamp(parseInt(e.target.value, 10), u.max))}
                                    onKeyDown={(e) => {
                                        if (e.key === "ArrowUp") { e.preventDefault(); u.set(clamp(u.value + 1, u.max)); }
                                        else if (e.key === "ArrowDown") { e.preventDefault(); u.set(clamp(u.value - 1, u.max)); }
                                    }}
                                    className="h-12 w-[72px] rounded-md border border-input bg-background text-center font-mono text-2xl tabular-nums outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/30"
                                />
                                <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{u.key}</span>
                                <Button variant="outline" size="icon" className="h-6 w-[72px]"
                                    onClick={() => u.set(clamp(u.value - 1, u.max))} aria-label={`${u.key} down`}>
                                    <ChevronDown className="size-3.5" />
                                </Button>
                            </div>
                            {i < units.length - 1 ? <span className="pt-2.5 text-2xl font-semibold text-muted-foreground">:</span> : null}
                        </div>
                    ))}
                </div>

                <div className={cn(
                    "rounded-lg border p-3",
                    total > 0 ? "border-emerald-500/30 bg-emerald-500/10" : "border-border bg-muted",
                )}>
                    {total <= 0 ? (
                        <>
                            <div className="text-base font-semibold text-muted-foreground">Enter a time above 0</div>
                            <div className="text-xs text-muted-foreground">Saving = runs × manual time, so 0 saves nothing</div>
                        </>
                    ) : (
                        <>
                            <div className="text-lg font-bold text-emerald-700 dark:text-emerald-400">
                                {runs > 0 ? `${humanizeSeconds(total * runs)} saved` : "no successful runs yet"}
                            </div>
                            <div className="text-xs text-emerald-700/80 dark:text-emerald-400/80">
                                {`${runs} successful runs × ${humanizeSeconds(total)}`}
                            </div>
                        </>
                    )}
                </div>

                <DialogFooter className="pt-2">
                    <DialogClose asChild disabled={isPending}>
                        <Button variant="outline">{t("cancel")}</Button>
                    </DialogClose>
                    <Button disabled={isPending || total <= 0} onClick={handleSave}>
                        {isPending ? <LoadingSpin /> : t("save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default WorkflowSetManualTimeModal;
