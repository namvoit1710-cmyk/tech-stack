import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";

const getFileByIdMock = rs.fn();
const toastErrorMock = rs.fn();

rs.mock("./apis/file", () => ({
    getFileById: getFileByIdMock,
}));

rs.mock("@ldc/ui/blocks/toast/toast", () => ({
    toast: {
        error: toastErrorMock,
    },
}));

const createDeferred = <T,>() => {
    let resolve!: (value: T | PromiseLike<T>) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((innerResolve, innerReject) => {
        resolve = innerResolve;
        reject = innerReject;
    });

    return { promise, resolve, reject };
};

describe("useDownloadArtifact", () => {
    let createObjectUrlSpy: ReturnType<typeof rs.spyOn>;
    let revokeObjectUrlSpy: ReturnType<typeof rs.spyOn>;
    let clickSpy: ReturnType<typeof rs.fn>;
    let removeSpy: ReturnType<typeof rs.fn>;
    let anchor: HTMLAnchorElement;

    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());

        getFileByIdMock.mockResolvedValue(new Blob(["artifact"], { type: "text/plain" }));
        createObjectUrlSpy = rs.spyOn(URL, "createObjectURL").mockReturnValue("blob:artifact");
        revokeObjectUrlSpy = rs.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);

        clickSpy = rs.fn();
        removeSpy = rs.fn();
        anchor = {
            click: clickSpy,
            remove: removeSpy,
            href: "",
            download: "",
        } as unknown as HTMLAnchorElement;

        rs.spyOn(document, "createElement").mockReturnValue(anchor);
        rs.spyOn(document.body, "appendChild").mockImplementation(() => document.body);
    });

    afterEach(async () => {
        await Promise.resolve(rs.restoreAllMocks());
    });

    it("downloads a blob and clears loading state", async () => {
        const { useDownloadArtifact } = await import("./use-download-artifact");
        const { result } = renderHook(() => useDownloadArtifact());

        await act(async () => {
            await result.current.downloadArtifact({ fileId: "42", filename: "report.csv" });
        });

        expect(getFileByIdMock).toHaveBeenCalledWith("42");
        expect(createObjectUrlSpy).toHaveBeenCalled();
        expect(revokeObjectUrlSpy).toHaveBeenCalledWith("blob:artifact");
        expect(anchor.href).toBe("blob:artifact");
        expect(anchor.download).toBe("report.csv");
        expect(clickSpy).toHaveBeenCalledTimes(1);
        expect(removeSpy).toHaveBeenCalledTimes(1);
        expect(result.current.downloadingId).toBeNull();
    });

    it("shows an error toast when the request fails", async () => {
        getFileByIdMock.mockRejectedValueOnce(new Error("network"));

        const { useDownloadArtifact } = await import("./use-download-artifact");
        const { result } = renderHook(() => useDownloadArtifact());

        await act(async () => {
            await result.current.downloadArtifact({ fileId: "42", filename: "report.csv" });
        });

        expect(toastErrorMock).toHaveBeenCalledTimes(1);
        expect(toastErrorMock).toHaveBeenCalledWith("Download failed");
        expect(result.current.downloadingId).toBeNull();
    });

    it("cleans up the blob URL when a DOM step fails after the blob is created", async () => {
        clickSpy.mockImplementationOnce(() => {
            throw new Error("click failed");
        });

        const { useDownloadArtifact } = await import("./use-download-artifact");
        const { result } = renderHook(() => useDownloadArtifact());

        await act(async () => {
            await result.current.downloadArtifact({ fileId: "42", filename: "report.csv" });
        });

        expect(toastErrorMock).toHaveBeenCalledTimes(1);
        expect(toastErrorMock).toHaveBeenCalledWith("Download failed");
        expect(revokeObjectUrlSpy).toHaveBeenCalledWith("blob:artifact");
        expect(removeSpy).toHaveBeenCalledTimes(1);
        expect(result.current.downloadingId).toBeNull();
    });

    it("keeps the latest download marked as loading when an earlier request settles first", async () => {
        const firstDownload = createDeferred<Blob>();
        const secondDownload = createDeferred<Blob>();
        let firstPromise!: Promise<void>;
        let secondPromise!: Promise<void>;

        getFileByIdMock
            .mockImplementationOnce(() => firstDownload.promise)
            .mockImplementationOnce(() => secondDownload.promise);

        const { useDownloadArtifact } = await import("./use-download-artifact");
        const { result } = renderHook(() => useDownloadArtifact());

        act(() => {
            firstPromise = result.current.downloadArtifact({ fileId: "file-a" });
        });

        expect(result.current.downloadingId).toBe("file-a");

        act(() => {
            secondPromise = result.current.downloadArtifact({ fileId: "file-b" });
        });

        expect(result.current.downloadingId).toBe("file-b");
        expect(result.current.isDownloading("file-b")).toBe(true);

        await act(async () => {
            firstDownload.resolve(new Blob(["first"]));
            await firstPromise;
        });

        expect(result.current.downloadingId).toBe("file-b");
        expect(result.current.isDownloading("file-b")).toBe(true);

        await act(async () => {
            secondDownload.resolve(new Blob(["second"]));
            await secondPromise;
        });

        expect(result.current.downloadingId).toBeNull();
        expect(result.current.isDownloading("file-b")).toBe(false);
    });
});