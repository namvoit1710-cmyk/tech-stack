/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useBulkUpload } from "./use-bulk-upload";
import type { UseBulkUploadOptions } from "./use-bulk-upload";

afterEach(cleanup);

interface Deferred<T> {
    promise: Promise<T>;
    resolve: (v: T) => void;
    reject: (e?: unknown) => void;
}
function deferred<T>(): Deferred<T> {
    let resolve!: (v: T) => void;
    let reject!: (e?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

/** A controllable fake uploader that records concurrency, order and abort signals. */
function makeFake() {
    const calls: { file: File; signal: AbortSignal; d: Deferred<string> }[] = [];
    const uploadFile: UseBulkUploadOptions["uploadFile"] = (file, { signal }) => {
        const d = deferred<string>();
        calls.push({ file, signal, d });
        return d.promise;
    };
    return { calls, uploadFile };
}

const file = (name: string) => new File(["x"], name, { type: "text/plain" });

describe("useBulkUpload (SA-2040 R5)", () => {
    it("never runs more than `concurrency` uploads at once", () => {
        const fake = makeFake();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 2 })
        );

        act(() => result.current.start([file("a"), file("b"), file("c"), file("d"), file("e")]));

        // Only C started synchronously; the rest stay queued.
        expect(fake.calls).toHaveLength(2);
        const statuses = result.current.items.map((i) => i.status);
        expect(statuses.filter((s) => s === "uploading")).toHaveLength(2);
        expect(statuses.filter((s) => s === "queued")).toHaveLength(3);
    });

    it("pulls the next queued file as one finishes (pump)", async () => {
        const fake = makeFake();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 2 })
        );
        act(() => result.current.start([file("a"), file("b"), file("c")]));

        await act(async () => {
            fake.calls[0]!.d.resolve("id-a");
        });

        // a done → c starts; still ≤ 2 in flight.
        expect(fake.calls).toHaveLength(3);
    });

    it("aggregates file_ids in SELECTION order, not completion order", async () => {
        const fake = makeFake();
        const onChange = rs.fn();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 3, onChange })
        );
        act(() => result.current.start([file("a"), file("b"), file("c")]));

        // Complete out of order: c, then a, then b.
        await act(async () => fake.calls[2]!.d.resolve("id-c"));
        await act(async () => fake.calls[0]!.d.resolve("id-a"));
        await act(async () => fake.calls[1]!.d.resolve("id-b"));

        await waitFor(() =>
            expect(onChange).toHaveBeenLastCalledWith(["id-a", "id-b", "id-c"])
        );
    });

    it("keeps succeeded files when one fails, and retry re-runs only that file", async () => {
        const fake = makeFake();
        const onChange = rs.fn();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 2, onChange })
        );
        act(() => result.current.start([file("a"), file("b")]));

        await act(async () => fake.calls[0]!.d.reject(new Error("network")));
        await act(async () => fake.calls[1]!.d.resolve("id-b"));

        // Batch not aborted: b succeeded, a is retryable.
        expect(result.current.items[0]!.status).toBe("error");
        expect(result.current.items[1]!.status).toBe("done");
        expect(onChange).toHaveBeenLastCalledWith(["id-b"]);

        act(() => result.current.retry(result.current.items[0]!.id));
        expect(fake.calls).toHaveLength(3); // a re-queued and launched
        await act(async () => fake.calls[2]!.d.resolve("id-a"));

        await waitFor(() =>
            // a is index 0, so selection order is [a, b].
            expect(onChange).toHaveBeenLastCalledWith(["id-a", "id-b"])
        );
    });

    it("abort() cancels every in-flight upload", () => {
        const fake = makeFake();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 5 })
        );
        act(() => result.current.start([file("a"), file("b")]));
        expect(fake.calls.every((c) => c.signal.aborted)).toBe(false);

        act(() => result.current.abort());

        expect(fake.calls.every((c) => c.signal.aborted)).toBe(true);
    });

    it("abort() does NOT relaunch queued items when a stale in-flight upload later settles", async () => {
        // Review fix: on unmount, upload-control calls abort() (not reset), leaving items queued.
        const fake = makeFake();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 2 })
        );
        act(() => result.current.start([file("a"), file("b"), file("c"), file("d")]));
        expect(fake.calls).toHaveLength(2); // a, b in flight; c, d queued

        act(() => result.current.abort());
        // A stale in-flight promise settles AFTER abort — its finally must NOT pump c/d.
        await act(async () => fake.calls[0]!.d.resolve("id-a"));

        expect(fake.calls).toHaveLength(2); // still 2 — c/d never relaunched
    });

    it("remove() drops an item (aborting it if in-flight) and re-emits the remaining file_ids", async () => {
        const fake = makeFake();
        const onChange = rs.fn();
        const { result } = renderHook(() =>
            useBulkUpload({ uploadFile: fake.uploadFile, concurrency: 3, onChange })
        );
        act(() => result.current.start([file("a"), file("b")]));
        await act(async () => fake.calls[0]!.d.resolve("id-a"));
        await act(async () => fake.calls[1]!.d.resolve("id-b"));

        const bId = result.current.items[1]!.id;
        act(() => result.current.remove(bId));

        expect(result.current.items.map((i) => i.file.name)).toEqual(["a"]);
        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(["id-a"]));
    });
});
