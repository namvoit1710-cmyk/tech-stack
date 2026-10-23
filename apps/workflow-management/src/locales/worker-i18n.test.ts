import { describe, expect, it } from "@rstest/core";
import en from "./en/workflow-management.json";
import jp from "./jp/workflow-management.json";

// "live"/"reconnecting" are gone with the LivePill: the socket is plumbing, and
// a permanent status chip in the header was noise on a fleet-status page.
const KEYS = [
    "tab_title", "page_title",
    "expand_processes", "no_instances", "no_node_types", "empty", "load_error",
    "loading", "refreshing",
];

// The nested per-process table's column labels.
const INST_KEYS = ["process", "status", "host", "heartbeat", "version", "unknown_status"];

describe("workers i18n namespace", () => {
    it("en has the workers namespace with the required keys", () => {
        const w = (en as Record<string, any>).workers;
        expect(w).toBeDefined();
        for (const k of KEYS) expect(typeof w[k]).toBe("string");
        for (const k of INST_KEYS) expect(typeof w.inst[k]).toBe("string");
        expect(typeof w.cards.workers).toBe("string");
        expect(typeof w.col.worker).toBe("string");
    });
    it("jp mirrors the same top-level workers keys", () => {
        const w = (jp as Record<string, any>).workers;
        expect(w).toBeDefined();
        for (const k of KEYS) expect(typeof w[k]).toBe("string");
        for (const k of INST_KEYS) expect(typeof w.inst[k]).toBe("string");
    });
    it("drops the retired LivePill keys from both locales", () => {
        for (const bundle of [en, jp]) {
            const w = (bundle as Record<string, any>).workers;
            expect(w.live).toBeUndefined();
            expect(w.reconnecting).toBeUndefined();
        }
    });
});
