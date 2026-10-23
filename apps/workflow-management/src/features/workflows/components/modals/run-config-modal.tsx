// SA-2424/SA-2437: debug worker-instance selection modal. Persists the pin
// SERVER-SIDE (per-workflow worker-config), not FE localStorage, so it survives
// reloads/other machines and applies on every run path (incl. triggers/schedules).
// Per-node only: pick the instance for each individual node. Each pin stores
// {instance_id, host}; host is the /workers-page label used as a routing fallback.
// The backend resolves + fails fast if a pin is ineligible.
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@ldc/ui/components/dialog";
import { ScrollArea } from "@ldc/ui/components/scroll-area";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@ldc/ui/components/select";
import { useQueryClient } from "@ldc/tanstack-query";
import { CircleAlert, Loader2 } from "lucide-react";
import type { IEditorNodeValue } from "@ldc/workflow-editor";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { useQueryWorkerTypeReplicas } from "@/features/workers/hooks/apis/use-worker-type-replicas-query";
import type { IWorkerInstance } from "@/features/workers/types/worker-instance";

import {
    type IWorkerConfigPin,
    type IWorkerConfigSelection,
    useSetWorkerConfig,
    useWorkerConfigQuery,
    workerConfigKeys,
} from "../../hooks/apis/use-worker-config";
import { useEditorStore } from "../../stores/editor-stores";
import { useUIPanelStore } from "../../stores/ui-panel-stores";

const AUTO = "__auto__"; // "" is not a valid <SelectItem> value (Radix throws)

// SA-2437: a node that CALLS another (sub-)flow — node_type WORKFLOW or the
// builtin:workflow worker. Selection never covers these (or the sub-flow's
// inner nodes); they run under their own flow's config.
const isSubFlowNode = (node: IEditorNodeValue): boolean => {
    const nt = String((node.data as { node_type?: string }).node_type || "").toUpperCase();
    const wt = String((node.data as { worker_type?: string | null }).worker_type || "");
    return nt === "WORKFLOW" || nt === "SUB_FLOW" || wt.startsWith("builtin:workflow");
};

const nodeWorkerType = (node: IEditorNodeValue): string | null => {
    if (isSubFlowNode(node)) return null; // sub-flow calls are out of scope (SA-2437)
    const raw =
        (node.data as { worker_type?: string | null }).worker_type ??
        (node.data as { base_worker_id?: string | null }).base_worker_id ??
        "";
    const wt = String(raw || "").replace(/^worker:/, "");
    if (!wt || wt.startsWith("builtin:")) return null;
    return wt;
};

// The picker identifies an instance by instance_id @ host (health) · age. The
// worker NAME is intentionally omitted — it's already the row title.
const instanceLabel = (inst: IWorkerInstance): string => {
    const host = inst.host ? ` @ ${inst.host}` : "";
    const age = inst.age_seconds != null ? ` · ${Math.round(inst.age_seconds)}s ago` : "";
    return `${inst.instance_id}${host} (${inst.health_status ?? "?"})${age}`;
};

const InstancePicker = ({
    workerType,
    value,
    onChange,
}: {
    workerType: string;
    value: IWorkerConfigPin | undefined;
    onChange: (pin: IWorkerConfigPin | null) => void;
}) => {
    const { data: instances, isLoading } = useQueryWorkerTypeReplicas(workerType);
    const list = instances ?? [];

    if (isLoading) {
        return (
            <span className="text-muted-foreground flex w-full items-center gap-1.5 text-xs">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading instances…
            </span>
        );
    }

    // No online worker of this type → error state (SA-2439). "Any" still applies
    // (an instance that comes online later will run it) — that's the unpinned default.
    if (list.length === 0) {
        return (
            <span
                className="text-destructive flex w-full items-center gap-1.5 text-xs"
                title="No online worker for this type — it will run on Any instance that comes online"
            >
                <CircleAlert className="h-4 w-4 shrink-0" /> No online worker (runs on Any)
            </span>
        );
    }

    // Radix <SelectValue> can't resolve a PROGRAMMATICALLY-set value's text until
    // its <SelectItem> mounts (dropdown opened) — so on reopen the saved pick showed
    // as the placeholder. Render the selected label explicitly instead.
    const currentId = value?.instance_id || "";
    const selectedInst = list.find((i) => i.instance_id === currentId);
    const selectedText = currentId
        ? selectedInst
            ? instanceLabel(selectedInst)
            : `${currentId}${value?.host ? ` @ ${value.host}` : ""} (offline)`
        : "";

    return (
        <Select
            value={currentId || AUTO}
            onValueChange={(v) => {
                if (v === AUTO) return onChange(null);
                const inst = list.find((i) => i.instance_id === v);
                onChange({ instance_id: v, host: inst?.host || value?.host || "" });
            }}
        >
            <SelectTrigger className="w-full min-w-0">
                {currentId ? (
                    // SA-2439: a pinned instance that's no longer online (not in the
                    // live list) shows a red alert icon so the user notices and can
                    // re-pick — the run would otherwise wait for that exact instance.
                    <span className={`flex items-center gap-1.5 truncate ${selectedInst ? "" : "text-destructive"}`}>
                        {!selectedInst && <CircleAlert className="h-4 w-4 shrink-0" />}
                        {selectedText}
                    </span>
                ) : (
                    <SelectValue placeholder="Any (auto — any healthy)" />
                )}
            </SelectTrigger>
            {/* position="popper": the default "item-aligned" mode mispositions the
                dropdown off-screen inside a Dialog (the dialog's translate transform
                breaks item-aligned math), so options couldn't be clicked. Popper mode
                anchors under the trigger with collision handling. */}
            <SelectContent position="popper" sideOffset={4} className="max-h-[300px] w-[var(--radix-select-trigger-width)] max-w-[var(--radix-select-trigger-width)]">
                {/* Always offer "Any" — so if a 2nd instance appears later the node
                    uses Any rather than staying hard-pinned to an old instance. */}
                <SelectItem value={AUTO}>Any (auto — any healthy)</SelectItem>
                {value && !selectedInst && currentId && (
                    <SelectItem value={currentId} className="text-destructive">
                        <span className="flex items-center gap-1.5">
                            <CircleAlert className="h-4 w-4 shrink-0" />
                            {currentId}{value.host ? ` @ ${value.host}` : ""} (offline)
                        </span>
                    </SelectItem>
                )}
                {list.map((inst) => (
                    <SelectItem key={inst.id} value={inst.instance_id}>
                        <span className="block truncate">{instanceLabel(inst)}</span>
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
};

// Worker display NAME (BE-provided via /worker-replicas), or "" when no online
// instance reports one.
const useWorkerName = (workerType: string): string => {
    const { data } = useQueryWorkerTypeReplicas(workerType);
    return data?.find((i) => i.name)?.name || "";
};

// One row = identity (left, fixed width, truncates) + picker (right, fills rest so
// the full instance label is visible without truncation).
const PerNodeRow = ({ node, workerType, value, onChange }: {
    node: IEditorNodeValue;
    workerType: string;
    value: IWorkerConfigPin | undefined;
    onChange: (pin: IWorkerConfigPin | null) => void;
}) => {
    const name = useWorkerName(workerType);
    const title = node.data.title || node.data.name || node.id;
    const subtitle = name ? `${name} · ${workerType}` : workerType;
    const children: ReactNode = (
        <InstancePicker workerType={workerType} value={value} onChange={onChange} />
    );
    return (
        <div className="flex items-center justify-between gap-4">
            <div className="flex w-[190px] shrink-0 flex-col">
                <div className="truncate text-sm font-medium" title={title}>{title}</div>
                <div className="text-muted-foreground truncate text-xs" title={subtitle}>{subtitle}</div>
            </div>
            <div className="min-w-0 flex-1">{children}</div>
        </div>
    );
};

const emptySelection = (): IWorkerConfigSelection => ({ by_worker_type: {}, by_node: {} });

const RunConfigModal = () => {
    const isOpen = useUIPanelStore((s) => s.isOpenRunConfigModal);
    const closeModal = useUIPanelStore((s) => s.closeRunConfigModal);

    const workflowId = useEditorStore((s) => s.workflowInfo?.id) ?? "";
    const nodes = useEditorStore((s) => s.workflowData.nodes);
    const queryClient = useQueryClient();

    const { data: saved } = useWorkerConfigQuery(workflowId, isOpen);
    const { mutate: saveConfig, isPending: isSaving } = useSetWorkerConfig({
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: workerConfigKeys.one(workflowId) });
            toast.success("Worker selection saved");
            closeModal();
        },
        onError: () => toast.error("Failed to save worker selection"),
    });

    const [selection, setSelection] = useState<IWorkerConfigSelection>(emptySelection);

    // Seed local editing state from the server config ONCE per open. Seeding on
    // every `saved` change let a background refetch overwrite the user's in-progress
    // pick (looked like "can't change instance after save"). Reset the guard on close.
    const seededRef = useRef(false);
    useEffect(() => {
        if (!isOpen) { seededRef.current = false; return; }
        if (seededRef.current || saved === undefined) return;
        seededRef.current = true;
        setSelection({
            by_worker_type: {},
            by_node: saved?.selection?.by_node ?? {},
        });
    }, [isOpen, saved]);

    const workerNodes = (nodes ?? [])
        .map((n) => ({ node: n, workerType: nodeWorkerType(n) }))
        .filter((x): x is { node: IEditorNodeValue; workerType: string } => x.workerType !== null);

    const subFlowCount = (nodes ?? []).filter(isSubFlowNode).length;

    const setNodePin = (nodeId: string, pin: IWorkerConfigPin | null) =>
        setSelection((s) => {
            const next = { ...s.by_node };
            if (pin) next[nodeId] = pin;
            else delete next[nodeId];
            return { ...s, by_node: next };
        });

    const pinnedCount = Object.keys(selection.by_node).length;

    const onDone = () => {
        if (!workflowId) return closeModal();
        saveConfig({ workflowId, mode: "PER_NODE", selection: { by_worker_type: {}, by_node: selection.by_node } });
    };
    const onClear = () => setSelection(emptySelection());

    return (
        <Dialog open={isOpen} onOpenChange={(o) => !o && closeModal()}>
            {/* closeOnOverlayClick=false: a Select dropdown's outside-click was
                bubbling to the Dialog and closing the whole modal. Modal now
                closes only via X / Done / Close. */}
            <DialogContent className="max-w-3xl sm:max-w-3xl" closeOnOverlayClick={false}>
                <DialogHeader>
                    <DialogTitle>Select worker instance (debug)</DialogTitle>
                    <DialogDescription>
                        Pin which worker instance runs each node so a run is deterministic and
                        debuggable. Saved on this flow (server-side) and applied to every run —
                        including triggers/schedules. Leave a node on “Any” to keep normal behavior.
                    </DialogDescription>
                </DialogHeader>

                {subFlowCount > 0 && (
                    <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        This flow calls {subFlowCount} sub-flow{subFlowCount > 1 ? "s" : ""}. Worker
                        selection applies to <strong>this flow only</strong> — nodes inside the called
                        sub-flow(s) are not shown here and use their own flow’s selection.
                    </div>
                )}

                <ScrollArea className="max-h-[60vh] min-h-[80px] pr-3">
                    <div className="flex flex-col gap-3 py-2">
                        {workerNodes.length === 0 && (
                            <p className="text-muted-foreground text-sm">No worker nodes in this flow.</p>
                        )}
                        {workerNodes.map(({ node, workerType }) => (
                            <PerNodeRow
                                key={node.id}
                                node={node}
                                workerType={workerType}
                                value={selection.by_node[node.id]}
                                onChange={(pin) => setNodePin(node.id, pin)}
                            />
                        ))}
                    </div>
                </ScrollArea>

                <DialogFooter className="items-center justify-between sm:justify-between">
                    <span className="text-muted-foreground text-xs">
                        {pinnedCount > 0 ? `${pinnedCount} pinned` : "nothing pinned (Any)"}
                    </span>
                    <div className="flex gap-2">
                        <Button variant="outline" onClick={onClear} disabled={pinnedCount === 0 || isSaving}>
                            Clear
                        </Button>
                        <Button onClick={onDone} disabled={isSaving}>
                            {isSaving ? "Saving…" : "Done"}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default RunConfigModal;
