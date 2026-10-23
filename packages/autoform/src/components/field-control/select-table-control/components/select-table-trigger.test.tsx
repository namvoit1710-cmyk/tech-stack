/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SelectTableTrigger from "./select-table-trigger";

afterEach(cleanup);

// ─── primitive value (labelOverride) ─────────────────────────────────────────

describe("SelectTableTrigger — primitive value (labelOverride)", () => {
    it("shows labelOverride when provided", () => {
        render(
            <SelectTableTrigger
                value={null}
                multiple={false}
                labelKey="name"
                labelOverride="Ho Chi Minh City"
                placeholder="Select city"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByDisplayValue("Ho Chi Minh City")).toBeInTheDocument();
    });

    it("shows clear button when labelOverride is set even if value is null", () => {
        render(
            <SelectTableTrigger
                value={null}
                multiple={false}
                labelKey="name"
                labelOverride="Ho Chi Minh City"
                placeholder="Select city"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
    });

    it("labelOverride takes precedence over row label", () => {
        render(
            <SelectTableTrigger
                value={{ id: "1", name: "Alice", role: "admin" }}
                multiple={false}
                labelKey="name"
                labelOverride="Override Label"
                placeholder="Select"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByDisplayValue("Override Label")).toBeInTheDocument();
        expect(screen.queryByDisplayValue("Alice")).not.toBeInTheDocument();
    });
});

// ─── single mode ─────────────────────────────────────────────────────────────

describe("SelectTableTrigger — single mode", () => {
    it("shows placeholder when no value is set", () => {
        render(
            <SelectTableTrigger
                value={null}
                multiple={false}
                labelKey="name"
                placeholder="Select a user"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByPlaceholderText("Select a user")).toBeInTheDocument();
    });

    it("shows labelKey value of selected row as plain text", () => {
        render(
            <SelectTableTrigger
                value={{ id: "1", name: "Alice", role: "admin" }}
                multiple={false}
                labelKey="name"
                placeholder="Select a user"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByDisplayValue("Alice")).toBeInTheDocument();
    });

    it("shows a clear button when a value is set", () => {
        render(
            <SelectTableTrigger
                value={{ id: "1", name: "Alice", role: "admin" }}
                multiple={false}
                labelKey="name"
                placeholder="Select a user"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
    });

    it("does not show clear button when no value is set", () => {
        render(
            <SelectTableTrigger
                value={null}
                multiple={false}
                labelKey="name"
                placeholder="Select a user"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
    });

    it("calls onOpen when the open button is clicked", async () => {
        const user = userEvent.setup();
        const onOpen = rs.fn();
        render(
            <SelectTableTrigger
                value={null}
                multiple={false}
                labelKey="name"
                placeholder="Select a user"
                onOpen={onOpen}
                onClear={rs.fn()}
            />
        );
        await user.click(screen.getByRole("button", { name: /open table/i }));
        expect(onOpen).toHaveBeenCalledTimes(1);
    });

    it("calls onClear when the clear button is clicked", async () => {
        const user = userEvent.setup();
        const onClear = rs.fn();
        render(
            <SelectTableTrigger
                value={{ id: "1", name: "Alice", role: "admin" }}
                multiple={false}
                labelKey="name"
                placeholder="Select a user"
                onOpen={rs.fn()}
                onClear={onClear}
            />
        );
        await user.click(screen.getByRole("button", { name: /clear/i }));
        expect(onClear).toHaveBeenCalledTimes(1);
    });
});

// ─── multi mode ──────────────────────────────────────────────────────────────

describe("SelectTableTrigger — multi mode", () => {
    it("shows placeholder when no values are selected", () => {
        render(
            <SelectTableTrigger
                value={[]}
                multiple={true}
                labelKey="name"
                placeholder="Select users"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        expect(screen.getByText("Select users")).toBeInTheDocument();
    });

    it("renders selected rows as chips or overflow badge (happy-dom offsetWidth is 0)", () => {
        const { container } = render(
            <SelectTableTrigger
                value={[
                    { id: "1", name: "Alice", role: "admin" },
                    { id: "2", name: "Bob", role: "user" },
                ]}
                multiple={true}
                labelKey="name"
                placeholder="Select users"
                onOpen={rs.fn()}
                onClear={rs.fn()}
            />
        );
        const hasChips =
            screen.queryByText("Alice") !== null || screen.queryByText("Bob") !== null;
        const hasBadge = container.querySelector("[data-testid='overflow-badge']") !== null;
        expect(hasChips || hasBadge).toBe(true);
    });

    it("shows +X overflow badge when many items selected", () => {
        const manyRows = Array.from({ length: 10 }, (_, i) => ({
            id: String(i),
            name: `User-LongNameThatWillOverflow-${i}`,
        }));
        const { container } = render(
            <div style={{ width: "100px" }}>
                <SelectTableTrigger
                    value={manyRows}
                    multiple={true}
                    labelKey="name"
                    placeholder="Select users"
                    onOpen={rs.fn()}
                    onClear={rs.fn()}
                />
            </div>
        );
        expect(container.querySelector("[data-testid='overflow-badge']")).toBeInTheDocument();
    });
});
