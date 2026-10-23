import { describe, expect, it } from "@rstest/core";
import { clampCursor, pageCount, pageOf, timelineLabel, TIMELINE_PAGE_SIZE } from "./replay-scrubber-utils";

describe("replay-scrubber-utils", () => {
    it("clampCursor keeps the cursor within [0, total]", () => {
        expect(clampCursor(-5, 8)).toBe(0);
        expect(clampCursor(3, 8)).toBe(3);
        expect(clampCursor(99, 8)).toBe(8);
    });

    it("timelineLabel prefers node_id, falls back to event type", () => {
        expect(timelineLabel({ index: 1, sequence: 1, event_type: "NodeOutputProduced", node_id: "n1", topic: "t" })).toBe("n1");
        expect(timelineLabel({ index: 2, sequence: 2, event_type: "RunStatusChanged", node_id: null, topic: "t" })).toBe("RunStatusChanged");
    });

    it("pages timelines at 100 events per page", () => {
        expect(TIMELINE_PAGE_SIZE).toBe(100);
        expect(pageCount(0)).toBe(1); // empty still renders one (empty) page
        expect(pageCount(100)).toBe(1);
        expect(pageCount(101)).toBe(2);
        expect(pageCount(250)).toBe(3);
    });

    it("pageOf maps an array position to its page (and clamps -1 to page 0)", () => {
        expect(pageOf(0)).toBe(0);
        expect(pageOf(99)).toBe(0);
        expect(pageOf(100)).toBe(1);
        expect(pageOf(204)).toBe(2);
        expect(pageOf(-1)).toBe(0); // cursor 0 = before any event → stay on page 0
    });

});
