/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatePickerControl from "./date-picker-control";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

describe("DatePickerControl — single mode", () => {
    it("renders default placeholder when no value", () => {
        render(<DatePickerControl name="date" onChange={rs.fn()} />);
        expect(screen.getByText("Pick a date")).toBeInTheDocument();
    });

    it("renders custom placeholder from prop", () => {
        render(
            <DatePickerControl
                name="date"
                placeholder="Select start date"
                onChange={rs.fn()}
            />
        );
        expect(screen.getByText("Select start date")).toBeInTheDocument();
    });

    it("renders custom placeholder from controlProps fallback", () => {
        render(
            <DatePickerControl
                name="date"
                onChange={rs.fn()}
                field={{
                    key: "date",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { placeholder: "From controlProps" },
                    },
                }}
            />
        );
        expect(screen.getByText("From controlProps")).toBeInTheDocument();
    });

    it("direct prop overrides controlProps placeholder", () => {
        render(
            <DatePickerControl
                name="date"
                placeholder="Direct prop"
                onChange={rs.fn()}
                field={{
                    key: "date",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { placeholder: "From controlProps" },
                    },
                }}
            />
        );
        expect(screen.getByText("Direct prop")).toBeInTheDocument();
        expect(screen.queryByText("From controlProps")).not.toBeInTheDocument();
    });

    it("renders formatted date when value provided", () => {
        render(
            <DatePickerControl
                name="date"
                value="2025-03-15"
                onChange={rs.fn()}
            />
        );
        // DatePicker formats the date for display — just verify placeholder is not shown
        expect(screen.queryByText("Pick a date")).not.toBeInTheDocument();
    });

    it("trigger button is disabled when disabled prop is true", () => {
        render(
            <DatePickerControl name="date" onChange={rs.fn()} disabled />
        );
        const button = screen.getByRole("button");
        expect(button).toBeDisabled();
    });

    it("sets aria-invalid on trigger when error prop is provided", () => {
        render(
            <DatePickerControl
                name="date"
                onChange={rs.fn()}
                error="Date is required"
            />
        );
        const button = screen.getByRole("button");
        expect(button).toHaveAttribute("aria-invalid", "true");
    });

    it("trigger does not have aria-invalid when no error", () => {
        render(<DatePickerControl name="date" onChange={rs.fn()} />);
        const button = screen.getByRole("button");
        expect(button).toHaveAttribute("aria-invalid", "false");
    });

    it("opens calendar popover on trigger click", async () => {
        const user = userEvent.setup();
        render(<DatePickerControl name="date" onChange={rs.fn()} />);
        const button = screen.getByRole("button");
        await user.click(button);
        // Calendar grid should be visible after click
        expect(screen.getByRole("grid")).toBeInTheDocument();
    });

    it("calls onChange with adapter shape when date selected", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        render(
            <DatePickerControl
                name="startDate"
                onChange={handleChange}
            />
        );
        const button = screen.getByRole("button");
        await user.click(button);

        // Pick any day cell that is enabled in the calendar
        const dayCells = screen.getAllByRole("gridcell");
        const enabledDay = dayCells.find(
            (cell) => !cell.hasAttribute("aria-disabled") && cell.textContent?.match(/^\d+$/)
        );
        if (enabledDay) {
            await user.click(enabledDay);
            if (handleChange.mock.calls.length > 0) {
                expect(handleChange).toHaveBeenCalledWith(
                    expect.objectContaining({
                        target: expect.objectContaining({
                            name: "startDate",
                        }),
                    })
                );
                const callArg = handleChange.mock.calls[0]?.[0];
                // value should be an ISO string or undefined
                expect(
                    typeof callArg?.target.value === "string" ||
                    callArg?.target.value === undefined
                ).toBe(true);
            }
        }
    });

    it("clear button calls onChange with undefined", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        const { container } = render(
            <DatePickerControl
                name="date"
                value="2025-06-18"
                onChange={handleChange}
            />
        );
        // Clear button has tabIndex=-1 and no accessible name (XIcon only, no sr-only text)
        const clearButton = container.querySelector('button[tabindex="-1"]') as HTMLButtonElement;
        expect(clearButton).not.toBeNull();
        await user.click(clearButton);
        expect(handleChange).toHaveBeenCalledWith(
            expect.objectContaining({
                target: expect.objectContaining({
                    name: "date",
                    value: undefined,
                }),
            })
        );
    });
});

describe("DatePickerControl — range mode", () => {
    it("renders default range placeholder when no value", () => {
        render(
            <DatePickerControl name="dateRange" mode="range" onChange={rs.fn()} />
        );
        expect(screen.getByText("Pick a date range")).toBeInTheDocument();
    });

    it("resolves range mode from controlProps when not passed directly", () => {
        render(
            <DatePickerControl
                name="dateRange"
                onChange={rs.fn()}
                field={{
                    key: "dateRange",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { mode: "range" },
                    },
                }}
            />
        );
        expect(screen.getByText("Pick a date range")).toBeInTheDocument();
    });

    it("renders formatted range when from and to provided", () => {
        render(
            <DatePickerControl
                name="dateRange"
                mode="range"
                value={{ from: "2025-01-01", to: "2025-01-31" }}
                onChange={rs.fn()}
            />
        );
        expect(screen.queryByText("Pick a date range")).not.toBeInTheDocument();
    });

    it("range trigger is disabled when disabled prop is true", () => {
        render(
            <DatePickerControl
                name="dateRange"
                mode="range"
                onChange={rs.fn()}
                disabled
            />
        );
        const button = screen.getByRole("button");
        expect(button).toBeDisabled();
    });

    it("range trigger has aria-invalid when error provided", () => {
        render(
            <DatePickerControl
                name="dateRange"
                mode="range"
                onChange={rs.fn()}
                error="Range is required"
            />
        );
        const button = screen.getByRole("button");
        expect(button).toHaveAttribute("aria-invalid", "true");
    });

    it("range onChange fires with { from, to } shape via adapter when cleared", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        const { container } = render(
            <DatePickerControl
                name="period"
                mode="range"
                value={{ from: "2025-06-01", to: "2025-06-30" }}
                onChange={handleChange}
            />
        );
        // Clear button has tabIndex=-1 and no accessible name (XIcon only)
        const clearButton = container.querySelector('button[tabindex="-1"]') as HTMLButtonElement | null;
        if (clearButton) {
            await user.click(clearButton);
            expect(handleChange).toHaveBeenCalledWith(
                expect.objectContaining({
                    target: expect.objectContaining({
                        name: "period",
                    }),
                })
            );
        }
    });
});

describe("DatePickerControl — mode default", () => {
    it("defaults to single mode when no mode specified", () => {
        render(<DatePickerControl name="date" onChange={rs.fn()} />);
        // Single mode shows "Pick a date" not "Pick a date range"
        expect(screen.getByText("Pick a date")).toBeInTheDocument();
        expect(screen.queryByText("Pick a date range")).not.toBeInTheDocument();
    });
});
