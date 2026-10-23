import { useCallback, useRef, useState } from "react";

export interface BulkUploadItem {
    id: string;
    file: File;
    status: "queued" | "uploading" | "done" | "error";
    progress: number;
    fileId?: string;
    error?: unknown;
}

export interface UseBulkUploadOptions {
    /** Upload one file; resolve its server file_id. MUST honour the abort signal. */
    uploadFile: (
        file: File,
        ctx: { onProgress: (pct: number) => void; signal: AbortSignal },
    ) => Promise<string>;
    /** Called with the succeeded file_ids in SELECTION order whenever that set changes. */
    onChange?: (fileIds: string[]) => void;
    /** Max concurrent uploads (browsers cap ~6 conns/origin). Default 5. */
    concurrency?: number;
}

export interface UseBulkUploadReturn {
    items: BulkUploadItem[];
    /** Enqueue new files (appended in order) and pump the pool. */
    start: (files: File[]) => void;
    /** Re-queue a failed item and pump. */
    retry: (id: string) => void;
    /** Drop an item (aborting it if in-flight) and re-emit — e.g. the user removed a file. */
    remove: (id: string) => void;
    /** Cancel every in-flight upload. */
    abort: () => void;
    /** Abort + clear all state. */
    reset: () => void;
}

let _seq = 0;
const nextId = () => `bu_${++_seq}`;

export function useBulkUpload(options: UseBulkUploadOptions): UseBulkUploadReturn {
    const [items, setItems] = useState<BulkUploadItem[]>([]);

    // Authoritative mutable mirror + latest-options, so the async pool never reads stale state.
    const itemsRef = useRef<BulkUploadItem[]>([]);
    const optsRef = useRef(options);
    optsRef.current = options;
    const controllersRef = useRef<Map<string, AbortController>>(new Map());
    const activeRef = useRef(0);

    // Latest-ref handlers to break the pump<->launch recursion without TDZ / stale closures.
    const launchRef = useRef<(item: BulkUploadItem) => void>(() => {});
    const pumpRef = useRef<() => void>(() => {});

    const commit = useCallback((next: BulkUploadItem[]) => {
        itemsRef.current = next;
        setItems(next);
    }, []);

    const patch = useCallback(
        (id: string, partial: Partial<BulkUploadItem>) => {
            commit(itemsRef.current.map((it) => (it.id === id ? { ...it, ...partial } : it)));
        },
        [commit],
    );

    const emit = useCallback(() => {
        const ids = itemsRef.current
            .filter((it) => it.status === "done" && it.fileId)
            .map((it) => it.fileId as string);
        optsRef.current.onChange?.(ids);
    }, []);

    launchRef.current = (item: BulkUploadItem) => {
        const controller = new AbortController();
        controllersRef.current.set(item.id, controller);
        activeRef.current += 1;
        patch(item.id, { status: "uploading", progress: 0 });

        optsRef.current
            .uploadFile(item.file, {
                onProgress: (pct) => patch(item.id, { progress: pct }),
                signal: controller.signal,
            })
            .then((fileId) => {
                if (controller.signal.aborted) return;
                patch(item.id, { status: "done", progress: 100, fileId });
                emit();
            })
            .catch((error) => {
                if (controller.signal.aborted) return; // aborted (abort()/remove()) — ignore the late rejection
                patch(item.id, { status: "error", error });
            })
            .finally(() => {
                // If abort()/remove() already cleared this item's controller, the slot was already
                // accounted for — do NOT re-decrement activeRef or pump(), which would relaunch
                // still-queued items after an abort/unmount (SA-2040 R5 review fix). Only the
                // controller that still owns this id finalises the slot.
                if (controllersRef.current.get(item.id) !== controller) return;
                controllersRef.current.delete(item.id);
                activeRef.current = Math.max(0, activeRef.current - 1);
                pumpRef.current();
            });
    };

    pumpRef.current = () => {
        const limit = optsRef.current.concurrency ?? 5;
        while (activeRef.current < limit) {
            const next = itemsRef.current.find((it) => it.status === "queued");
            if (!next) break;
            launchRef.current(next);
        }
    };

    const start = useCallback(
        (files: File[]) => {
            const added: BulkUploadItem[] = files.map((file) => ({
                id: nextId(),
                file,
                status: "queued",
                progress: 0,
            }));
            commit([...itemsRef.current, ...added]);
            pumpRef.current();
        },
        [commit],
    );

    const retry = useCallback(
        (id: string) => {
            const it = itemsRef.current.find((x) => x.id === id);
            if (!it || it.status !== "error") return;
            patch(id, { status: "queued", error: undefined, progress: 0 });
            pumpRef.current();
        },
        [patch],
    );

    const remove = useCallback(
        (id: string) => {
            const controller = controllersRef.current.get(id);
            if (controller) {
                controller.abort();
                controllersRef.current.delete(id);
                activeRef.current = Math.max(0, activeRef.current - 1);
            }
            commit(itemsRef.current.filter((it) => it.id !== id));
            emit();
            pumpRef.current();
        },
        [commit, emit],
    );

    const abort = useCallback(() => {
        controllersRef.current.forEach((c) => c.abort());
        controllersRef.current.clear();
        activeRef.current = 0;
    }, []);

    const reset = useCallback(() => {
        abort();
        commit([]);
    }, [abort, commit]);

    return { items, start, retry, remove, abort, reset };
}
