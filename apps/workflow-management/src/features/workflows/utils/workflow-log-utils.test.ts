import { describe, expect, it, rs } from "@rstest/core";
import type { IWorkflowLogs } from "../types/workflow-log";
import { formatDuration, formatLogTime, getExecutionDuration } from "./workflow-log-utils";

describe("getExecutionDuration", () => {
    it("returns the absolute delta between now and log.timestamp (happy)", () => {
        const now = 1_000_000;
        rs.spyOn(Date, "now").mockReturnValue(now);
        const log = { timestamp: now - 5_000 } as IWorkflowLogs;
        expect(getExecutionDuration(log)).toBe(5_000);
    });

    it("is absolute even for a future timestamp (edge)", () => {
        const now = 1_000_000;
        rs.spyOn(Date, "now").mockReturnValue(now);
        const log = { timestamp: now + 3_000 } as IWorkflowLogs;
        expect(getExecutionDuration(log)).toBe(3_000);
    });

    it("returns 0 when timestamp equals now (boundary)", () => {
        const now = 1_000_000;
        rs.spyOn(Date, "now").mockReturnValue(now);
        expect(getExecutionDuration({ timestamp: now } as IWorkflowLogs)).toBe(0);
    });
});

describe("formatDuration", () => {
    it("renders sub-second values as ms (happy)", () => {
        expect(formatDuration(0)).toBe("0ms");
        expect(formatDuration(500)).toBe("500ms");
    });

    it("renders seconds with one decimal below a minute (happy)", () => {
        expect(formatDuration(1_000)).toBe("1.0s");
        expect(formatDuration(1_500)).toBe("1.5s");
        expect(formatDuration(59_000)).toBe("59.0s");
    });

    it("crosses the ms→s boundary at exactly 1000 (boundary)", () => {
        expect(formatDuration(999)).toBe("999ms");
        expect(formatDuration(1_000)).toBe("1.0s");
    });

    it("renders minutes and seconds under an hour (happy)", () => {
        // 90_000ms => 1m 30s
        expect(formatDuration(90_000)).toBe("1m 30s");
    });

    it("renders hours and minutes under a day (happy)", () => {
        // 3_600_000ms + 5min = 1h 5m
        expect(formatDuration(3_600_000 + 5 * 60_000)).toBe("1h 5m");
    });

    it("renders days and hours for very long durations (boundary)", () => {
        // 1 day + 2 hours
        expect(formatDuration(24 * 3_600_000 + 2 * 3_600_000)).toBe("1d 2h");
    });

    it("crosses the s→m boundary at exactly 60000 (boundary)", () => {
        expect(formatDuration(59_999)).toBe("60.0s");
        expect(formatDuration(60_000)).toBe("1m 0s");
    });
});

describe("formatLogTime", () => {
    it("zero-pads hours, minutes and seconds (happy/boundary)", () => {
        const epoch = new Date(2026, 0, 1, 3, 5, 9).getTime();
        expect(formatLogTime(epoch)).toBe("03:05:09");
    });

    it("renders two-digit components without extra padding", () => {
        const epoch = new Date(2026, 0, 1, 23, 59, 59).getTime();
        expect(formatLogTime(epoch)).toBe("23:59:59");
    });
});
