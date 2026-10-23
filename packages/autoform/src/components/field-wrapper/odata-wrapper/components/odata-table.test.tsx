/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

// Capture the props ODataTable forwards to the underlying DataTable without
// rendering the full (virtualized) table. Mirrors the mock style in builder.test.tsx.
rs.mock("@ldc/data-table", () => ({
    __esModule: true,
    DataTable: (props: { enableMultiRowSelection?: boolean; enableSelection?: boolean }) => (
        <div
            data-testid="data-table"
            data-enable-multi={String(props.enableMultiRowSelection)}
            data-enable-selection={String(props.enableSelection)}
        />
    ),
}));

// The columns hook pulls in i18n + slot providers we don't need for this wiring test.
rs.mock("../hooks/use-table-columns", () => ({
    __esModule: true,
    useTableColumns: () => ({ columns: [] }),
}));

import ODataTable from "./odata-table";

const field = {
    key: "table_data",
    outputType: "array",
    fieldConfig: { wrapperProps: {} },
    fields: [{ key: "row", outputType: "object", fieldConfig: {}, fields: [] }],
} as any;

const baseProps = {
    field,
    data: [],
    onPaginationChange: () => {},
    trackUpdate: () => {},
    sortState: null,
} as any;

afterEach(cleanup);

const multi = () => screen.getByTestId("data-table").getAttribute("data-enable-multi");

describe("ODataTable — selectionMode → row selector type (SA-1239 AC3)", () => {
    it("single selection disables multi-row selection (renders a radio)", () => {
        render(<ODataTable {...baseProps} selectionMode="single" />);
        expect(multi()).toBe("false");
    });

    it("multiple selection enables multi-row selection (renders checkboxes)", () => {
        render(<ODataTable {...baseProps} selectionMode="multiple" />);
        expect(multi()).toBe("true");
    });

    it("defaults to multiple (backward-compatible) when selectionMode is omitted", () => {
        render(<ODataTable {...baseProps} />);
        expect(multi()).toBe("true");
    });

    it("always keeps row selection enabled", () => {
        render(<ODataTable {...baseProps} selectionMode="single" />);
        expect(screen.getByTestId("data-table").getAttribute("data-enable-selection")).toBe("true");
    });
});
