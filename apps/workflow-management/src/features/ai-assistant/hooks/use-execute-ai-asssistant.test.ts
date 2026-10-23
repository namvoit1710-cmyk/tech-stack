import { describe, expect, it } from "@rstest/core";
import type { IDesignStreamEvent } from "../types";
import { stepFromEvent } from "./use-execute-ai-asssistant";

// SA-2218: a fake translator mirroring useLanguage().t(key, default, params) — returns
// the default with {{token}} interpolation, so tests assert on real user-visible text.
const t = ((_key: string, def?: string, params?: Record<string, unknown>) => {
    let out = def ?? _key;
    if (params) {
        for (const [k, v] of Object.entries(params)) {
            out = out.replace(new RegExp(`{{\\s*${k}\\s*}}`, "g"), String(v));
        }
    }
    return out;
}) as unknown as Parameters<typeof stepFromEvent>[1];

const ev = (e: Partial<IDesignStreamEvent> & { event: string }): IDesignStreamEvent => e;

describe("stepFromEvent — real design-stream event vocabulary (SA-2218)", () => {
    it("maps planning to its message", () => {
        const s = stepFromEvent(ev({ event: "planning", message: "Understanding your request…" }), t);
        expect(s).toMatchObject({ kind: "planning", label: "Understanding your request…", status: "running" });
    });

    it("surfaces a tool_call's args_summary as the label (the 'doing' line)", () => {
        const s = stepFromEvent(
            ev({ event: "tool_call", tool: "create_workflow", args_summary: "creating 'Forecast'" }),
            t,
        );
        expect(s).toMatchObject({ kind: "tool_call", label: "creating 'Forecast'", status: "running" });
    });

    it("falls back to the tool name when args_summary is blank", () => {
        const s = stepFromEvent(ev({ event: "tool_call", tool: "list_node_types", args_summary: "  " }), t);
        expect(s?.label).toBe("list_node_types");
    });

    it("maps tool_result summary with an ok status", () => {
        const s = stepFromEvent(ev({ event: "tool_result", tool: "plan_workflow", summary: "3 node(s) planned" }), t);
        expect(s).toMatchObject({ kind: "tool_result", label: "3 node(s) planned", status: "ok" });
    });

    it("counts plan nodes (array) and carries the rationale as detail", () => {
        const s = stepFromEvent(
            ev({ event: "plan", nodes: [{}, {}], rationale: "trigger then fetch" }),
            t,
        );
        expect(s).toMatchObject({ kind: "plan", label: "Planned 2 node(s)", detail: "trigger then fetch", status: "ok" });
    });

    it("marks a review with issues as a warning and joins the issues", () => {
        const s = stepFromEvent(ev({ event: "review", ok: false, issues: ["missing config", "bad id"] }), t);
        expect(s).toMatchObject({ kind: "review", status: "warn", detail: "missing config; bad id" });
    });

    it("marks a passing review ok", () => {
        const s = stepFromEvent(ev({ event: "review", ok: true, issues: [] }), t);
        expect(s).toMatchObject({ kind: "review", status: "ok" });
    });

    it("maps repair_iteration with attempt number and error detail; blocked -> error", () => {
        const s = stepFromEvent(
            ev({ event: "repair_iteration", attempt: 2, error: "dry-run failed", blocked: true }),
            t,
        );
        expect(s).toMatchObject({ kind: "repair", label: "Fixing an issue (attempt 2)…", detail: "dry-run failed", status: "error" });
    });

    it("ignores token-stream and terminal events (handled by the caller, not as steps)", () => {
        expect(stepFromEvent(ev({ event: "thinking", delta: "x" }), t)).toBeNull();
        expect(stepFromEvent(ev({ event: "content_delta", delta: "y" }), t)).toBeNull();
        expect(stepFromEvent(ev({ event: "done", status: "success" }), t)).toBeNull();
        expect(stepFromEvent(ev({ event: "error", message: "boom" }), t)).toBeNull();
    });
});
