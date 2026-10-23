/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import Builder from "../../builder/builder";

afterEach(cleanup);

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const CONTROL_ROWS = [
    { id: "1", name: "Ho Chi Minh City" },
    { id: "2", name: "Hanoi" },
    { id: "3", name: "Da Nang" },
];

function makeControlSchema(overrides?: Record<string, unknown>) {
    return {
        fields: [
            {
                key: "city",
                fieldConfig: {
                    fieldWrapper: "FormItemWrapper",
                    fieldControl: "SelectTableControl",
                    controlProps: {
                        options: CONTROL_ROWS,
                        valueKey: "id",
                        labelKey: "name",
                        placeholder: "Select city",
                        ...overrides,
                    },
                },
            },
        ],
    };
}

// ─── SelectTableControl (integration via Builder) ─────────────────────────────

describe("SelectTableControl — via Builder", () => {
    it("renders trigger with placeholder when no default value", () => {
        render(<Builder schema={makeControlSchema() as any} />);
        expect(screen.getByPlaceholderText("Select city")).toBeInTheDocument();
    });

    it("does not open dialog when readOnly=true and trigger is clicked", async () => {
        const user = userEvent.setup();
        render(<Builder schema={makeControlSchema({ readOnly: true }) as any} />);
        await user.click(screen.getByRole("button", { name: /open table/i }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("opens dialog when trigger open button is clicked", async () => {
        const user = userEvent.setup();
        render(<Builder schema={makeControlSchema() as any} />);
        await user.click(screen.getByRole("button", { name: /open table/i }));
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("fires onFormActions with rowData after confirming a selection", async () => {
        const user = userEvent.setup();
        const onFormActions = rs.fn().mockResolvedValue(undefined);
        render(
            <Builder
                schema={makeControlSchema({ selectAction: "onCitySelect" }) as any}
                onFormActions={onFormActions}
            />
        );
        await user.click(screen.getByRole("button", { name: /open table/i }));
        const hcmRow = screen.getByText("Ho Chi Minh City").closest("tr")!;
        await user.click(within(hcmRow).getByRole("radio"));
        await user.click(screen.getByRole("button", { name: /confirm/i }));
        expect(onFormActions).toHaveBeenCalledWith(
            "onCitySelect",
            expect.objectContaining({ rowData: expect.objectContaining({ id: "1" }) })
        );
    });

    it("shows selected label in trigger after confirming", async () => {
        const user = userEvent.setup();
        render(<Builder schema={makeControlSchema() as any} />);
        await user.click(screen.getByRole("button", { name: /open table/i }));
        const hanoiRow = screen.getByText("Hanoi").closest("tr")!;
        await user.click(within(hanoiRow).getByRole("radio"));
        await user.click(screen.getByRole("button", { name: /confirm/i }));
        expect(screen.getByDisplayValue("Hanoi")).toBeInTheDocument();
    });

    it("clears selection when clear button is clicked", async () => {
        const user = userEvent.setup();
        render(
            <Builder
                schema={makeControlSchema() as any}
                defaultValues={{ city: { id: "1", name: "Ho Chi Minh City" } }}
            />
        );
        expect(screen.getByDisplayValue("Ho Chi Minh City")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: /clear/i }));
        expect(screen.queryByDisplayValue("Ho Chi Minh City")).not.toBeInTheDocument();
    });

    it("clear is blocked when readOnly=true", async () => {
        const user = userEvent.setup();
        render(
            <Builder
                schema={makeControlSchema({ readOnly: true }) as any}
                defaultValues={{ city: { id: "1", name: "Ho Chi Minh City" } }}
            />
        );
        await user.click(screen.getByRole("button", { name: /clear/i }));
        expect(screen.getByDisplayValue("Ho Chi Minh City")).toBeInTheDocument();
    });

    it("flags the trigger as invalid when required field is submitted empty", async () => {
        const schema = {
            fields: [
                {
                    key: "city",
                    fieldConfig: {
                        fieldWrapper: "FormItemWrapper",
                        fieldControl: "SelectTableControl",
                        controlProps: { options: CONTROL_ROWS, valueKey: "id", labelKey: "name" },
                        rules: [{ method: "required", message: "City is required" }],
                    },
                },
            ],
        };
        const ref = React.createRef<any>();
        const { container } = render(<Builder ref={ref} schema={schema as any} onSubmit={rs.fn()} />);
        expect(container.querySelector(".ring-destructive")).not.toBeInTheDocument();
        await act(async () => { ref.current?.onSubmit(); });
        await waitFor(() => {
            expect(container.querySelector(".ring-destructive")).toBeInTheDocument();
        });
    });
});
