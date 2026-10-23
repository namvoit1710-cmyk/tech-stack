/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SelectTableDialog from "./select-table-dialog";

afterEach(cleanup);

// ─── Shared fixtures ──────────────────────────────────────────────────────────

const ROWS = [
    { _id: "1", id: "1", name: "Alice", role: "admin" },
    { _id: "2", id: "2", name: "Bob", role: "user" },
    { _id: "3", id: "3", name: "Charlie", role: "user" },
    { _id: "4", id: "4", name: "Dave", role: "admin" },
];

const defaultProps = {
    multiple: false as const,
    valueKey: "id",
    labelKey: "name",
    data: ROWS,
    loading: false,
    error: null,
    totalCount: ROWS.length,
    pagination: { pageIndex: 0, pageSize: 10 },
    onPaginationChange: rs.fn(),
    searchValue: "",
    onSearchChange: rs.fn(),
    currentValue: null,
    onConfirm: rs.fn(),
    selectionId: null,
};

const getRowByText = (text: string): HTMLTableRowElement => {
    const row = screen.getByText(text).closest("tr");
    if (!row) throw new Error(`Row not found for "${text}"`);
    return row;
};

// ─── open / close ─────────────────────────────────────────────────────────────

describe("SelectTableDialog — open/close", () => {
    it("renders the dialog when open=true", () => {
        render(<SelectTableDialog {...defaultProps} open={true} onOpenChange={rs.fn()} />);
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("does not render dialog content when open=false", () => {
        render(<SelectTableDialog {...defaultProps} open={false} onOpenChange={rs.fn()} />);
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
});

// ─── columns and search ───────────────────────────────────────────────────────

describe("SelectTableDialog — columns and search", () => {
    it("auto-derives columns from row keys and renders them as headers", () => {
        render(<SelectTableDialog {...defaultProps} open={true} onOpenChange={rs.fn()} />);
        expect(screen.getByText("id")).toBeInTheDocument();
        expect(screen.getByText("name")).toBeInTheDocument();
        expect(screen.getByText("role")).toBeInTheDocument();
    });

    it("renders a search input inside the dialog", () => {
        render(<SelectTableDialog {...defaultProps} open={true} onOpenChange={rs.fn()} />);
        expect(screen.getByRole("searchbox")).toBeInTheDocument();
    });

    it("calls onSearchChange when search input changes", async () => {
        const user = userEvent.setup();
        const onSearchChange = rs.fn();
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                onSearchChange={onSearchChange}
            />
        );
        await user.type(screen.getByRole("searchbox"), "ali");
        expect(onSearchChange).toHaveBeenCalled();
        expect(onSearchChange.mock.calls.length).toBe(3);
    });
});

// ─── single mode selection ────────────────────────────────────────────────────

describe("SelectTableDialog — single mode", () => {
    it("pre-selects the row matching selectionId (page 0, row 1 = Bob) on open", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                selectionId="0_1"
            />
        );
        expect(within(getRowByText("Bob")).getByRole("radio")).toBeChecked();
    });

    it("calls onConfirm with the selected full row object when Confirm is clicked", async () => {
        const user = userEvent.setup();
        const onConfirm = rs.fn();
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                onConfirm={onConfirm}
            />
        );
        await user.click(within(getRowByText("Alice")).getByRole("radio"));
        await user.click(screen.getByRole("button", { name: /confirm/i }));
        expect(onConfirm).toHaveBeenCalledWith({ _id: "1", id: "1", name: "Alice", role: "admin" }, "0_0");
    });

    it("calls onOpenChange(false) and does not call onConfirm when Cancel is clicked", async () => {
        const user = userEvent.setup();
        const onConfirm = rs.fn();
        const onOpenChange = rs.fn();
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={onOpenChange}
                onConfirm={onConfirm}
            />
        );
        await user.click(screen.getByRole("button", { name: /cancel/i }));
        expect(onConfirm).not.toHaveBeenCalled();
        expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("shows loading state when loading=true and disables Confirm button", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                data={[]}
                loading={true}
                totalCount={0}
            />
        );
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
    });
});

// ─── primitive currentValue ───────────────────────────────────────────────────

describe("SelectTableDialog — primitive currentValue", () => {
    it("pre-selects row matching selectionId when currentValue is primitive", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                currentValue={"2" as any}
                selectionId="0_1"
            />
        );
        expect(within(getRowByText("Bob")).getByRole("radio")).toBeChecked();
    });

    it("does not pre-select any row when primitive currentValue matches no row", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                currentValue={"999" as any}
            />
        );
        screen.getAllByRole("radio").forEach((r) => expect(r).not.toBeChecked());
    });
});

// ─── confirm button guard ─────────────────────────────────────────────────────

describe("SelectTableDialog — confirm button guard", () => {
    it("Confirm button is disabled when no row is selected", () => {
        render(<SelectTableDialog {...defaultProps} open={true} onOpenChange={rs.fn()} />);
        expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
    });

    it("Confirm button enabled after selecting a row", async () => {
        const user = userEvent.setup();
        render(<SelectTableDialog {...defaultProps} open={true} onOpenChange={rs.fn()} />);
        await user.click(within(getRowByText("Alice")).getByRole("radio"));
        expect(screen.getByRole("button", { name: /confirm/i })).not.toBeDisabled();
    });
});

// ─── multi mode ───────────────────────────────────────────────────────────────

describe("SelectTableDialog — multi mode", () => {
    it("uses checkboxes instead of radios for multi-select", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                multiple={true}
                currentValue={[]}
            />
        );
        expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);
    });

    it("no rows pre-checked on open when selectionId is null (multi-select)", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                multiple={true}
                currentValue={[]}
                selectionId={null}
            />
        );
        screen.getAllByRole("checkbox").forEach((r) => expect(r).not.toBeChecked());
    });

    it("calls onConfirm with array of full row objects when Confirm is clicked", async () => {
        const user = userEvent.setup();
        const onConfirm = rs.fn();
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                multiple={true}
                currentValue={[]}
                onConfirm={onConfirm}
            />
        );
        await user.click(within(getRowByText("Alice")).getByRole("checkbox"));
        await user.click(within(getRowByText("Bob")).getByRole("checkbox"));
        await user.click(screen.getByRole("button", { name: /confirm/i }));
        expect(onConfirm).toHaveBeenCalledWith([
            { _id: "1", id: "1", name: "Alice", role: "admin" },
            { _id: "2", id: "2", name: "Bob", role: "user" },
        ], "");
    });
});

// ─── loading UI ───────────────────────────────────────────────────────────────

describe("SelectTableDialog — loading UI", () => {
    it("renders loader-bar element when loading=true", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                loading={true}
                error={null}
            />
        );
        expect(document.querySelector(".loader-bar")).toBeInTheDocument();
    });

    it("does not render loader-bar when loading=false", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                loading={false}
                error={null}
            />
        );
        expect(document.querySelector(".loader-bar")).not.toBeInTheDocument();
    });
});

// ─── error UI ─────────────────────────────────────────────────────────────────

describe("SelectTableDialog — error UI", () => {
    it("shows error message below table when error is set", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                error="404 Not Found"
            />
        );
        expect(screen.getByText("404 Not Found")).toBeInTheDocument();
        expect(screen.queryByRole("table")).toBeInTheDocument();
    });

    it("shows table and no error message when error is null", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                error={null}
            />
        );
        expect(screen.queryByText(/not found/i)).not.toBeInTheDocument();
        expect(screen.getByRole("table")).toBeInTheDocument();
    });

    it("retry button calls onSearchChange to trigger refetch", async () => {
        const user = userEvent.setup();
        const onSearchChange = rs.fn();
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                error="500 Server Error"
                searchValue="test"
                onSearchChange={onSearchChange}
            />
        );
        await user.click(screen.getByRole("button", { name: /retry/i }));
        expect(onSearchChange).toHaveBeenCalledWith("test");
    });
});

// ─── duplicate valueKey selection ────────────────────────────────────────────

describe("SelectTableDialog — duplicate valueKey selection", () => {
    const DUP_ROWS = [
        { _id: "r1", country: "VN", city: "HCM" },
        { _id: "r2", country: "VN", city: "Hanoi" },
        { _id: "r3", country: "US", city: "NY" },
    ];

    it("selects no row when selectionId is null", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                data={DUP_ROWS}
                totalCount={DUP_ROWS.length}
                selectionId={null}
                error={null}
            />
        );
        expect(within(getRowByText("HCM")).getByRole("radio")).not.toBeChecked();
        expect(within(getRowByText("Hanoi")).getByRole("radio")).not.toBeChecked();
    });

    it("selects row at selectionId index on current page", () => {
        render(
            <SelectTableDialog
                {...defaultProps}
                open={true}
                onOpenChange={rs.fn()}
                data={DUP_ROWS}
                totalCount={DUP_ROWS.length}
                selectionId="0_2"
                error={null}
            />
        );
        expect(within(getRowByText("NY")).getByRole("radio")).toBeChecked();
        expect(within(getRowByText("HCM")).getByRole("radio")).not.toBeChecked();
    });
});
