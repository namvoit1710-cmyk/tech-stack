import { beforeEach, describe, expect, it, rs } from "@rstest/core";

const apiGet = rs.fn();
const fileApiGet = rs.fn();

rs.mock("@/lib/api", () => ({
    __esModule: true,
    default: {
        get: apiGet,
    },
    api: {
        get: apiGet,
    },
    fileApi: {
        get: fileApiGet,
    },
}));

describe("file api helpers", () => {
    beforeEach(() => {
        rs.resetModules();
        rs.clearAllMocks();
    });

    it("passes OData query params through Axios config", async () => {
        const response = {
            file_id: "1",
            data: [],
        };
        const params = {
            $top: 5,
            $filter: "status eq 'READY'",
        };
        fileApiGet.mockResolvedValue(response);

        const { getOdataFileById } = await import("./file");
        const result = await getOdataFileById("1", params);

        expect(result).toBe(response);
        expect(fileApiGet).toHaveBeenCalledWith("/odata/1/data", {
            params,
        });
    });

    it("includes params in OData query keys", async () => {
        const { fileQueryKeys } = await import("./file");

        expect(fileQueryKeys.odataById("1", { $top: 5 })).not.toEqual(
            fileQueryKeys.odataById("1", { $top: 10 }),
        );
        expect(fileQueryKeys.odataById("1", { $top: 5 })).not.toEqual(
            fileQueryKeys.odataById("1"),
        );
    });

    it("uses fileApi download endpoint for artifact downloads", async () => {
        const blob = new Blob(["artifact"]);
        fileApiGet.mockResolvedValue(blob);

        const { getFileById } = await import("./file");
        const result = await getFileById("1");

        expect(result).toBe(blob);
        expect(fileApiGet).toHaveBeenCalledWith("/download/1", {
            responseType: "blob",
        });
        expect(apiGet).not.toHaveBeenCalled();
    });
});