/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../contexts/builder.context";
import UploadControl from "./upload-control";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

// `services.file` at runtime is the `ServiceHandler` produced by `use-builder-services.ts`
// (fetch/post/put/patch/delete, headers-only third arg) — NOT the raw `APISdk` instance, so
// the mock mirrors that real, narrower shape rather than inventing cancel-token methods.
function makeServices(post: (...args: unknown[]) => Promise<unknown>) {
    return {
        file: {
            post,
            fetch: rs.fn(),
            put: rs.fn(),
            patch: rs.fn(),
            delete: rs.fn(),
        },
    };
}

function renderUpload(props: Record<string, unknown>, post: (...args: unknown[]) => Promise<unknown>) {
    return render(
        <BuilderProvider
            value={{
                schema: { fields: [] },
                fieldComponent: {},
                wrapperComponent: {},
                services: makeServices(post) as never,
            }}
        >
            <UploadControl {...(props as never)} />
        </BuilderProvider>,
    );
}

const file = (name: string) => new File(["x"], name, { type: "text/plain" });

// happy-dom's global `URL` resolves to Node's native implementation here, whose
// `createObjectURL` rejects this environment's `File` instances ("must be an instance of
// Blob"). `@ldc/ui`'s `Upload.FileItem` calls `URL.createObjectURL(file)` unconditionally to
// render a thumbnail — a pre-existing test-environment gap in a package this change does not
// touch. Stub it so the file list can render without touching `@ldc/ui`.
if (typeof URL.createObjectURL !== "function" || !("__sa2040Stub" in URL.createObjectURL)) {
    const stub = Object.assign(() => "blob:mock", { __sa2040Stub: true });
    URL.createObjectURL = stub as typeof URL.createObjectURL;
    URL.revokeObjectURL = () => {};
}

// Select files by assigning `input.files` directly and firing `change`, rather than
// `@testing-library/user-event`'s `.upload()`. `userEvent.upload()` first simulates a real
// click on the (hidden) file input; that click bubbles to `UploadDropzone`'s wrapping
// `role="button"` div, whose `onClick` calls `fileInputRef.current?.click()` to reopen the
// picker — a pre-existing re-entrancy bug in `@ldc/ui`'s Upload component (not something this
// change touches) that recurses into a real stack overflow under happy-dom. Setting `.files`
// and firing `change` directly exercises the exact same `handleInputChange` production path
// without tripping that click loop.
function selectFiles(input: HTMLInputElement, files: File[]) {
    Object.defineProperty(input, "files", { value: files, writable: true, configurable: true });
    fireEvent.change(input);
}

describe("UploadControl — end-to-end upload flow (SA-2040)", () => {
    // ── TEST CASE 1: SINGLE file ────────────────────────────────────────────────
    it("single mode: uploads the one file (POST carries it) and publishes a SCALAR file_id", async () => {
        const onChange = rs.fn();
        const post = rs.fn(async () => ({ file_id: "solo" }));
        renderUpload({ name: "doc", onChange }, post); // no `multiple` → single mode

        const input = document.querySelector('input[type="file"]') as HTMLInputElement;
        selectFiles(input, [file("resume.pdf")]);

        // Value published to the form is the bare file_id string (not an array).
        await waitFor(() =>
            expect(onChange).toHaveBeenLastCalledWith({ target: { name: "doc", value: "solo" } })
        );
        // Exactly one server upload, and it actually carried the selected file.
        expect(post).toHaveBeenCalledTimes(1);
        const url = post.mock.calls[0]![0] as string;
        const formData = post.mock.calls[0]![1] as FormData;
        expect(url).toContain("filename=resume.pdf");
        expect((formData.get("file") as File).name).toBe("resume.pdf");
    });

    // ── TEST CASE 2: MULTIPLE files ─────────────────────────────────────────────
    it("multiple mode: uploads EVERY file (one POST each) and publishes file_id[] in SELECTION order", async () => {
        const onChange = rs.fn();
        // Resolve each POST to `id-<filename>` so the aggregated order is unambiguous.
        const post = rs.fn(async (url: string) => {
            const name = decodeURIComponent(url.split("filename=")[1] ?? "");
            return { file_id: `id-${name}` };
        });
        renderUpload({ name: "docs", multiple: true, onChange }, post);

        const input = document.querySelector('input[type="file"]') as HTMLInputElement;
        selectFiles(input, [file("a"), file("b"), file("c")]);

        // Final value is the ARRAY of all three ids, in the order they were selected.
        await waitFor(() =>
            expect(onChange).toHaveBeenLastCalledWith({ target: { name: "docs", value: ["id-a", "id-b", "id-c"] } })
        );
        // One real upload per file — the whole batch went to the server, not just files[0].
        expect(post).toHaveBeenCalledTimes(3);
    });
});

function FormWrapper({ values, children }: { values: Record<string, unknown>; children: ReactNode }) {
    const methods = useForm({ defaultValues: values });
    return <FormProvider {...methods}>{children}</FormProvider>;
}

function renderReadOnly(
    props: Record<string, unknown>,
    fetch: (...args: unknown[]) => Promise<unknown>,
    formValues?: Record<string, unknown>,
) {
    const services = { file: { ...makeServices(rs.fn()).file, fetch } };
    const control = (
        <BuilderProvider
            value={{
                schema: { fields: [] },
                fieldComponent: {},
                wrapperComponent: {},
                services: services as never,
            }}
        >
            <UploadControl {...({ readOnly: true, ...props } as never)} />
        </BuilderProvider>
    );
    return render(formValues ? <FormWrapper values={formValues}>{control}</FormWrapper> : control);
}

describe("UploadControl — original filename (SA-2979)", () => {
    it("reports the field name with upload_success so the form can keep the original filename", async () => {
        const onFormActions = rs.fn();
        const response = { file_id: "fid-1", filename: "CR_Product.csv" };
        renderUpload({ name: "file", onChange: rs.fn(), onFormActions }, rs.fn(async () => response));

        const input = document.querySelector('input[type="file"]') as HTMLInputElement;
        selectFiles(input, [file("CR_Product.csv")]);

        await waitFor(() =>
            expect(onFormActions).toHaveBeenCalledWith("upload_success", { response, name: "file" })
        );
    });

    it("read-only shows the filename stored next to the field instead of the file_id", () => {
        const fetch = rs.fn(async () => ({}));
        renderReadOnly({ name: "file", value: "fid-1" }, fetch, { file: "fid-1", file_name: "CR_Product.csv", file_size: 10 });

        expect(screen.getByText("CR_Product.csv")).toBeTruthy();
        expect(screen.queryByText("fid-1")).toBeNull();
        expect(fetch).not.toHaveBeenCalled();
    });

    it("read-only falls back to the file service's original filename and size when none is stored", async () => {
        const fetch = rs.fn(async () => ({
            current_version: { original_filename: "legacy.xlsx", raw_size_bytes: 3 * 1024 * 1024, content_type: "application/vnd.ms-excel" },
        }));
        renderReadOnly({ name: "file", value: "fid-2" }, fetch, { file: "fid-2" });

        await waitFor(() => expect(screen.getByText("legacy.xlsx")).toBeTruthy());
        expect(screen.getByText("XLSX · 3.0 MB")).toBeTruthy();
        expect(fetch).toHaveBeenCalledWith("/files/fid-2");
    });

    it("read-only shows the file type and size stored next to the field without calling the file service", () => {
        const fetch = rs.fn(async () => ({}));
        renderReadOnly(
            { name: "file", value: "fid-1" },
            fetch,
            { file: "fid-1", file_name: "CR_Product.csv", file_size: 2048 },
        );

        expect(screen.getByText("CR_Product.csv")).toBeTruthy();
        expect(screen.getByText("CSV · 2 KB")).toBeTruthy();
        expect(fetch).not.toHaveBeenCalled();
    });

    it("read-only with a stored name but no size still asks the file service for the size", async () => {
        const fetch = rs.fn(async () => ({ current_version: { original_filename: "other.csv", raw_size_bytes: 512 } }));
        renderReadOnly({ name: "file", value: "fid-4" }, fetch, { file: "fid-4", file_name: "CR_Product.csv" });

        await waitFor(() => expect(screen.getByText("CSV · 512 B")).toBeTruthy());
        expect(screen.getByText("CR_Product.csv")).toBeTruthy();
    });

    it("read-only keeps showing the file_id when the filename cannot be resolved", async () => {
        const fetch = rs.fn(async () => {
            throw new Error("404");
        });
        renderReadOnly({ name: "file", value: "fid-3" }, fetch);

        await waitFor(() => expect(fetch).toHaveBeenCalled());
        expect(screen.getByText("fid-3")).toBeTruthy();
    });

    it("read-only download uses the resolved filename", () => {
        const onFormActions = rs.fn();
        renderReadOnly(
            { name: "file", value: "fid-1", onFormActions },
            rs.fn(async () => ({})),
            { file: "fid-1", file_name: "CR_Product.csv", file_size: 10 },
        );

        fireEvent.click(screen.getByRole("button"));

        expect(onFormActions).toHaveBeenCalledWith("download_file", { file_id: "fid-1", name: "CR_Product.csv" });
    });
});
