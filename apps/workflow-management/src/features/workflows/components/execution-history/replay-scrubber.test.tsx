import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import ReplayScrubber from "./replay-scrubber";

const openData = {
    data: {
        replay_session_id: "s1", run_id: "run-1", cursor: 0, total_steps: 4,
        timeline: [
            { index: 1, sequence: 1, event_type: "RunStatusChanged", node_id: null, topic: "t", is_error: false },
            { index: 2, sequence: 2, event_type: "NodeInput", node_id: "n1", topic: "t", is_error: false },
            { index: 3, sequence: 3, event_type: "NodeFailed", node_id: "n1", topic: "t", is_error: true },
            { index: 4, sequence: 4, event_type: "RunStatusChanged", node_id: null, topic: "t", is_error: false },
        ],
    },
};

// A failed node (mirrors the n8n screenshot: Python runner unavailable).
const stateData = {
    data: {
        replay_session_id: "s1", run_id: "run-1", cursor: 3, total_steps: 4, node_id: "n1",
        event: { sequence: 3, event_type: "NodeFailed", timestamp: "", payload: { error_code: "PY_RUNNER_MISSING" } },
        scope_chain: { score: 42, api_key: "***REDACTED***" },
        node_io: { input: { x: 1, password: "***REDACTED***" }, state_updates: null, output: null, output_port: null, taken_edge_ports: { e1: "false" } },
        loop_ctx: {}, completed_nodes: [], skipped_nodes: [],
        node_status: { n1: "failed" },
        node_error: { error_code: "PY_RUNNER_MISSING", error_message: "Python runner unavailable: Python 3 is missing" },
        redacted_keys: ["api_key", "password"], replay_complete: false,
    },
};

// A 250-event run — long enough to page (100/page → 3 pages). Mirrors the
// server contract: open ships only the FIRST page; later pages come from the
// timeline endpoint (mocked below as synchronous slices of this fixture).
const LONG_TIMELINE = Array.from({ length: 250 }, (_, i) => ({
    index: i + 1, sequence: i + 1, event_type: "NodeOutputProduced",
    node_id: `node-${i + 1}`, topic: "t", is_error: false,
}));
const longOpenData = {
    data: {
        replay_session_id: "s2", run_id: "long-run", cursor: 0, total_steps: 250,
        timeline: LONG_TIMELINE.slice(0, 100),
    },
};

rs.mock("../../hooks/apis/workflows", () => ({
    // runId "empty-run" models a run whose event log is empty (backend RUN_NOT_FOUND) —
    // open never succeeds, so total_steps stays 0 and the scrubber shows its empty state.
    // "long-run" returns the first 100 of a 250-event timeline for the paging tests.
    useOpenReplay: (opts?: { onSuccess?: (r: unknown) => void }) => ({
        mutate: (rid: string) => {
            if (rid === "long-run") opts?.onSuccess?.(longOpenData);
            else if (rid !== "empty-run") opts?.onSuccess?.(openData);
        },
        isPending: false,
        isError: false,
    }),
    // Echo the requested index back as the cursor so cursor-follow (page snapping)
    // is observable; omitted index (initial load) keeps the fixture's cursor.
    useReplayStep: (_runId: string, _sid: string, opts?: { onSuccess?: (r: unknown) => void }) => ({
        mutate: (p?: { index?: number | null }) =>
            opts?.onSuccess?.(
                typeof p?.index === "number" && p.index > 0
                    ? { data: { ...stateData.data, cursor: p.index } }
                    : stateData,
            ),
    }),
    useCloseReplay: () => ({ mutate: () => {} }),
    // Server-paged timeline: synchronous slice of the long fixture.
    useReplayTimelinePage: (runId: string, _sid: string, offset: number, limit: number) => ({
        data: runId === "long-run"
            ? { data: { items: LONG_TIMELINE.slice(offset, offset + limit), total: 250, offset, limit } }
            : { data: { items: [], total: 0, offset, limit } },
        isFetching: false,
    }),
}));

describe("ReplayScrubber", () => {
    afterEach(() => cleanup());

    it("renders the timeline and highlights the error event", async () => {
        const { container } = render(<ReplayScrubber runId="run-1" onClose={() => {}} />);
        expect(await screen.findByTestId("replay-scrubber")).toBeInTheDocument();
        expect(screen.getAllByText("RunStatusChanged").length).toBeGreaterThan(0);
        expect(container.querySelector('[data-error="1"]')).toBeInTheDocument(); // the NodeFailed chip
    });

    it("surfaces node status, the taken branch, and an inline error (n8n-style)", async () => {
        render(<ReplayScrubber runId="run-1" onClose={() => {}} />);
        expect((await screen.findByTestId("replay-node")).textContent).toBe("n1");
        expect(screen.getByTestId("replay-status").textContent).toContain("failed");
        expect(screen.getByTestId("replay-branch").textContent).toContain("false");
        expect(screen.getByTestId("replay-error").textContent).toContain("Python runner unavailable");
    });

    it("redacts secrets and never shows plaintext", async () => {
        render(<ReplayScrubber runId="run-1" onClose={() => {}} />);
        const scope = await screen.findByTestId("replay-scope");
        expect(scope.textContent).toContain("***REDACTED***");
        expect(scope.textContent).not.toContain("sk-live");
        expect(screen.getByTestId("replay-redacted").textContent).toContain("api_key");
    });

    it("paints the read-only canvas via the editor handle at the cursor", async () => {
        const editor = {
            setNodeStatus: rs.fn(),
            resetAllNodeStatus: rs.fn(),
            centerOnNode: rs.fn(),
        };
        const editorRef = { current: editor };
        render(<ReplayScrubber runId="run-1" onClose={() => {}} editorRef={editorRef} />);
        await screen.findByTestId("replay-node");
        // node_status at the cursor is { n1: "failed" } → paints n1 failed + centers it.
        expect(editor.resetAllNodeStatus).toHaveBeenCalled();
        expect(editor.setNodeStatus).toHaveBeenCalledWith("n1", "failed");
        expect(editor.centerOnNode).toHaveBeenCalledWith("n1");
    });

    it("shows an empty state (not a dead 0/0 scrubber) when the run has no replayable log", async () => {
        render(<ReplayScrubber runId="empty-run" onClose={() => {}} />);
        expect(await screen.findByTestId("replay-empty")).toBeInTheDocument();
        // no timeline / cursor controls when there is nothing to replay
        expect(screen.queryByTestId("replay-cursor")).not.toBeInTheDocument();
    });

    it("keeps short timelines pager-free (single page)", async () => {
        render(<ReplayScrubber runId="run-1" onClose={() => {}} />);
        await screen.findByTestId("replay-timeline");
        expect(screen.queryByTestId("replay-pager")).not.toBeInTheDocument();
    });

    it("pages a long timeline at 100 events per page inside the fixed scroll frame", async () => {
        render(<ReplayScrubber runId="long-run" onClose={() => {}} />);
        const frame = await screen.findByTestId("replay-timeline");
        // fixed frame: capped height + its own scrollbar (chips can't push the inspector away)
        expect(frame.className).toContain("max-h-56");
        expect(frame.className).toContain("overflow-y-auto");
        // page 1 = exactly 100 chips
        expect(frame.querySelectorAll("button[data-node]")).toHaveLength(100);
        expect(screen.getByText("node-1")).toBeInTheDocument();
        expect(screen.queryByText("node-101")).not.toBeInTheDocument();
        expect(screen.getByTestId("replay-page-label").textContent).toBe("Page 1 / 3 · events 1–100 of 250");
        expect(screen.getByTestId("replay-page-prev")).toBeDisabled();
    });

    it("navigates pages with the pager controls", async () => {
        const user = userEvent.setup();
        render(<ReplayScrubber runId="long-run" onClose={() => {}} />);
        await screen.findByTestId("replay-pager");
        await user.click(screen.getByTestId("replay-page-next"));
        expect(screen.getByText("node-101")).toBeInTheDocument();
        expect(screen.queryByText("node-1")).not.toBeInTheDocument();
        expect(screen.getByTestId("replay-page-label").textContent).toBe("Page 2 / 3 · events 101–200 of 250");
        await user.click(screen.getByTestId("replay-page-next"));
        expect(screen.getByTestId("replay-page-label").textContent).toBe("Page 3 / 3 · events 201–250 of 250");
        expect(screen.getByTestId("replay-page-next")).toBeDisabled();
    });

    it("follows the cursor onto its page when scrubbing", async () => {
        render(<ReplayScrubber runId="long-run" onClose={() => {}} />);
        await screen.findByTestId("replay-pager");
        // scrub to event 205 → cursor echoes back → page snaps to 3, chip is active
        fireEvent.change(screen.getByLabelText("scrubber"), { target: { value: "205" } });
        expect(screen.getByTestId("replay-page-label").textContent).toBe("Page 3 / 3 · events 201–250 of 250");
        expect(screen.getByText("node-205")).toHaveAttribute("data-active", "1");
    });

    it("toggles Input/Output views", async () => {
        const user = userEvent.setup();
        render(<ReplayScrubber runId="run-1" onClose={() => {}} />);
        await screen.findByTestId("replay-io");
        // default = Output
        expect(screen.getByTestId("replay-io").textContent).toContain("taken_edge_ports");
        // switch to Input -> shows the (redacted) resolved input
        await user.click(screen.getByTestId("replay-view-input"));
        const io = screen.getByTestId("replay-io");
        expect(io.textContent).toContain("password");
        expect(io.textContent).toContain("***REDACTED***");
        expect(io.textContent).not.toContain("hunter2");
    });
});
