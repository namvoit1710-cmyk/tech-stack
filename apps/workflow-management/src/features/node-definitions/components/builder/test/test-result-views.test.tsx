/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

// Stub i18n (return the key) and the heavy JSON viewer so we test the panels, not them.
rs.mock("@/hooks/use-language", () => ({ useLanguage: () => ({ t: (k: string) => k }) }));
rs.mock("@/components/json-view/json-view", () => ({ default: () => null }));

import {
    IdleHint,
    NotTestablePanel,
    OutputsPanel,
    RequestErrorPanel,
    ResolvedInputPanel,
    TestStatusBadge,
    WorkerErrorBlock,
    isFailedStatus,
    isSuccessStatus,
} from "./test-result-views";

afterEach(() => cleanup());

describe("test-result status helpers", () => {
    it("classifies success / completed and failed / error", () => {
        expect(isSuccessStatus("COMPLETED")).toBe(true);
        expect(isSuccessStatus("success")).toBe(true);
        expect(isFailedStatus("FAILED")).toBe(true);
        expect(isFailedStatus("error")).toBe(true);
        expect(isFailedStatus("COMPLETED")).toBe(false);
    });
});

describe("test result views", () => {
    it("renders the status badge with timing", () => {
        render(<TestStatusBadge status="COMPLETED" executionMs={12} />);
        expect(screen.getByText("COMPLETED")).toBeInTheDocument();
        expect(screen.getByText("12ms")).toBeInTheDocument();
    });

    it("renders resolved-input and outputs panel headers", () => {
        render(<ResolvedInputPanel value={{ a: 1 }} />);
        expect(screen.getByText("node_definition_builder.test_resolved_input")).toBeInTheDocument();
        cleanup();
        render(<OutputsPanel value={{ ok: true }} />);
        expect(screen.getByText("node_definition_builder.test_outputs")).toBeInTheDocument();
    });

    it("renders the 422 not-testable panel", () => {
        render(<NotTestablePanel />);
        expect(screen.getByText("node_definition_builder.test_on_canvas_title")).toBeInTheDocument();
    });

    it("renders request error and worker error messages", () => {
        render(<RequestErrorPanel message="boom" />);
        expect(screen.getByText("boom")).toBeInTheDocument();
        cleanup();
        render(<WorkerErrorBlock message="worker nope" />);
        expect(screen.getByText("worker nope")).toBeInTheDocument();
    });

    it("renders the idle hint", () => {
        render(<IdleHint />);
        expect(screen.getByText("node_definition_builder.test_idle_message")).toBeInTheDocument();
    });
});
