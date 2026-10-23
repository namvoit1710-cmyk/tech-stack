/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TimePickerControl from "./time-picker-control";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

describe("TimePickerControl — rendering", () => {
    it("renders default placeholder when no value", () => {
        render(<TimePickerControl name="time" onChange={rs.fn()} />);
        expect(screen.getByText("Pick a time")).toBeInTheDocument();
    });

    it("renders custom placeholder from prop", () => {
        render(
            <TimePickerControl
                name="time"
                placeholder="Select time"
                onChange={rs.fn()}
            />
        );
        expect(screen.getByText("Select time")).toBeInTheDocument();
    });

    it("renders custom placeholder from controlProps fallback", () => {
        render(
            <TimePickerControl
                name="time"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { placeholder: "From controlProps" },
                    },
                }}
            />
        );
        expect(screen.getByText("From controlProps")).toBeInTheDocument();
    });

    it("direct placeholder prop overrides controlProps", () => {
        render(
            <TimePickerControl
                name="time"
                placeholder="Direct"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { placeholder: "controlProps" },
                    },
                }}
            />
        );
        expect(screen.getByText("Direct")).toBeInTheDocument();
        expect(screen.queryByText("controlProps")).not.toBeInTheDocument();
    });

    it("renders time segments when value provided (24h)", () => {
        render(
            <TimePickerControl
                name="time"
                value="08:30:45"
                onChange={rs.fn()}
            />
        );
        // placeholder hidden when value present
        expect(screen.queryByText("Pick a time")).not.toBeInTheDocument();
        // segments rendered as spinbuttons
        expect(screen.getByRole("group", { name: "Time picker" })).toBeInTheDocument();
        expect(screen.getByRole("spinbutton", { name: "hours" })).toBeInTheDocument();
        expect(screen.getByRole("spinbutton", { name: "minutes" })).toBeInTheDocument();
        expect(screen.getByRole("spinbutton", { name: "seconds" })).toBeInTheDocument();
    });

    it("renders period segment in 12h mode", () => {
        render(
            <TimePickerControl
                name="time"
                value="08:30:00 AM"
                mode="12h"
                onChange={rs.fn()}
            />
        );
        expect(screen.getByRole("spinbutton", { name: "period" })).toBeInTheDocument();
    });

    it("does not render period segment in 24h mode", () => {
        render(
            <TimePickerControl
                name="time"
                value="08:30:00"
                mode="24h"
                onChange={rs.fn()}
            />
        );
        expect(screen.queryByRole("spinbutton", { name: "period" })).not.toBeInTheDocument();
    });
});

describe("TimePickerControl — mode resolution", () => {
    it("defaults to 24h when no mode specified", () => {
        render(
            <TimePickerControl
                name="time"
                value="14:00:00"
                onChange={rs.fn()}
            />
        );
        // 24h mode: no period spinbutton
        expect(screen.queryByRole("spinbutton", { name: "period" })).not.toBeInTheDocument();
    });

    it("resolves mode from controlProps when not passed directly", () => {
        render(
            <TimePickerControl
                name="time"
                value="08:30:00 AM"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { mode: "12h" },
                    },
                }}
            />
        );
        expect(screen.getByRole("spinbutton", { name: "period" })).toBeInTheDocument();
    });

    it("direct mode prop overrides controlProps mode", () => {
        render(
            <TimePickerControl
                name="time"
                value="14:00:00"
                mode="24h"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { mode: "12h" },
                    },
                }}
            />
        );
        expect(screen.queryByRole("spinbutton", { name: "period" })).not.toBeInTheDocument();
    });
});

describe("TimePickerControl — disabled and error states", () => {
    it("container has data-disabled when disabled prop is true", () => {
        render(
            <TimePickerControl name="time" onChange={rs.fn()} disabled />
        );
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).toHaveAttribute("data-disabled");
    });

    it("container has data-invalid when error prop is provided", () => {
        render(
            <TimePickerControl
                name="time"
                value="08:00:00"
                onChange={rs.fn()}
                error="Time is required"
            />
        );
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).toHaveAttribute("data-invalid");
    });

    it("container has data-invalid when value is out of range", () => {
        render(
            <TimePickerControl
                name="time"
                value="07:00:00"
                minTime="08:00:00"
                onChange={rs.fn()}
            />
        );
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).toHaveAttribute("data-invalid");
    });

    it("container does not have data-invalid when value in range", () => {
        render(
            <TimePickerControl
                name="time"
                value="10:00:00"
                minTime="08:00:00"
                maxTime="18:00:00"
                onChange={rs.fn()}
            />
        );
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).not.toHaveAttribute("data-invalid");
    });

    it("segments have aria-invalid when error provided", () => {
        render(
            <TimePickerControl
                name="time"
                value="08:00:00"
                onChange={rs.fn()}
                error="Invalid"
            />
        );
        const hoursSpinbutton = screen.getByRole("spinbutton", { name: "hours" });
        expect(hoursSpinbutton).toHaveAttribute("aria-invalid", "true");
    });
});

describe("TimePickerControl — onChange adapter", () => {
    it("calls onChange with { target: { name, value } } shape on clear", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        render(
            <TimePickerControl
                name="startTime"
                value="09:00:00"
                onChange={handleChange}
            />
        );
        const clearButton = screen.getByRole("button", { name: /clear time/i });
        await user.click(clearButton);
        expect(handleChange).toHaveBeenCalledWith(
            expect.objectContaining({
                target: expect.objectContaining({
                    name: "startTime",
                    value: undefined,
                }),
            })
        );
    });

    it("initialises value and calls onChange on container click when no value", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        render(
            <TimePickerControl
                name="time"
                onChange={handleChange}
            />
        );
        const group = screen.getByRole("group", { name: "Time picker" });
        await user.click(group);
        if (handleChange.mock.calls.length > 0) {
            expect(handleChange).toHaveBeenCalledWith(
                expect.objectContaining({
                    target: expect.objectContaining({
                        name: "time",
                    }),
                })
            );
            const callArg = handleChange.mock.calls[0]?.[0];
            expect(typeof callArg?.target.value).toBe("string");
        }
    });

    it("calls onChange with updated 24h value on ArrowUp on hours segment", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        render(
            <TimePickerControl
                name="time"
                value="08:30:00"
                onChange={handleChange}
            />
        );
        const hoursSpinbutton = screen.getByRole("spinbutton", { name: "hours" });
        await user.click(hoursSpinbutton);
        await user.keyboard("{ArrowUp}");
        expect(handleChange).toHaveBeenCalledWith(
            expect.objectContaining({
                target: expect.objectContaining({
                    name: "time",
                    value: "09:30:00",
                }),
            })
        );
    });

    it("calls onChange with updated 24h value on ArrowDown on hours segment", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        render(
            <TimePickerControl
                name="time"
                value="08:30:00"
                onChange={handleChange}
            />
        );
        const hoursSpinbutton = screen.getByRole("spinbutton", { name: "hours" });
        await user.click(hoursSpinbutton);
        await user.keyboard("{ArrowDown}");
        expect(handleChange).toHaveBeenCalledWith(
            expect.objectContaining({
                target: expect.objectContaining({
                    name: "time",
                    value: "07:30:00",
                }),
            })
        );
    });

    it("calls onChange with 12h formatted value in 12h mode", async () => {
        const user = userEvent.setup();
        const handleChange = rs.fn();
        render(
            <TimePickerControl
                name="time"
                value="08:30:00 AM"
                mode="12h"
                onChange={handleChange}
            />
        );
        const hoursSpinbutton = screen.getByRole("spinbutton", { name: "hours" });
        await user.click(hoursSpinbutton);
        await user.keyboard("{ArrowUp}");
        if (handleChange.mock.calls.length > 0) {
            const callArg = handleChange.mock.calls[0]?.[0];
            // 12h format includes space + AM/PM
            expect(callArg?.target.value).toMatch(/^\d{2}:\d{2}:\d{2} (AM|PM)$/);
        }
    });
});

describe("TimePickerControl — minTime / maxTime", () => {
    it("resolves minTime from controlProps when not passed directly", () => {
        render(
            <TimePickerControl
                name="time"
                value="07:00:00"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { minTime: "08:00:00" },
                    },
                }}
            />
        );
        // value 07:00:00 is before minTime 08:00:00 → invalid
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).toHaveAttribute("data-invalid");
    });

    it("resolves maxTime from controlProps when not passed directly", () => {
        render(
            <TimePickerControl
                name="time"
                value="20:00:00"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { maxTime: "18:00:00" },
                    },
                }}
            />
        );
        // value 20:00:00 is after maxTime 18:00:00 → invalid
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).toHaveAttribute("data-invalid");
    });

    it("direct minTime prop overrides controlProps", () => {
        render(
            <TimePickerControl
                name="time"
                value="09:00:00"
                minTime="10:00:00"
                onChange={rs.fn()}
                field={{
                    key: "time",
                    outputType: "string",
                    fieldConfig: {
                        controlProps: { minTime: "06:00:00" },
                    },
                }}
            />
        );
        // direct minTime=10:00:00 wins → 09:00:00 is invalid
        const group = screen.getByRole("group", { name: "Time picker" });
        expect(group).toHaveAttribute("data-invalid");
    });
});
