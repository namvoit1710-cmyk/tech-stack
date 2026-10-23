/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DateTimePickerControl from "./date-time-picker-control";

afterEach(() => {
  cleanup();
});

describe("DateTimePickerControl — rendering", () => {
  it("renders date placeholder when no value", () => {
    render(<DateTimePickerControl name="dt" onChange={rs.fn()} />);
    expect(screen.getByText("Pick a date")).toBeInTheDocument();
  });

  it("renders custom placeholder from prop", () => {
    render(
      <DateTimePickerControl
        name="dt"
        placeholder="Select datetime"
        onChange={rs.fn()}
      />
    );
    expect(screen.getByText("Select datetime")).toBeInTheDocument();
  });

  it("renders custom placeholder from controlProps fallback", () => {
    render(
      <DateTimePickerControl
        name="dt"
        onChange={rs.fn()}
        field={{
          key: "dt",
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
      <DateTimePickerControl
        name="dt"
        placeholder="Direct"
        onChange={rs.fn()}
        field={{
          key: "dt",
          outputType: "string",
          fieldConfig: {
            controlProps: { placeholder: "From controlProps" },
          },
        }}
      />
    );
    expect(screen.getByText("Direct")).toBeInTheDocument();
    expect(screen.queryByText("From controlProps")).not.toBeInTheDocument();
  });

  it("date trigger is disabled when disabled prop is true", () => {
    render(<DateTimePickerControl name="dt" onChange={rs.fn()} disabled />);
    const button = screen.getByRole("button");
    expect(button).toBeDisabled();
  });

  it("date trigger has aria-invalid when error provided", () => {
    render(
      <DateTimePickerControl name="dt" onChange={rs.fn()} error="Required" />
    );
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-invalid", "true");
  });

  it("time picker is disabled when no date selected (in popup)", async () => {
    const user = userEvent.setup();
    render(<DateTimePickerControl name="dt" onChange={rs.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick a date" }));
    const group = screen.getByRole("group", { name: "Time picker" });
    expect(group).toHaveAttribute("data-disabled");
  });
});

describe("DateTimePickerControl — value display", () => {
  it("shows formatted local date when UTC value provided", () => {
    render(
      <DateTimePickerControl
        name="dt"
        value="2026-06-18T07:30:00.000Z"
        onChange={rs.fn()}
      />
    );
    expect(screen.queryByText("Pick a date")).not.toBeInTheDocument();
  });

  it("time picker is enabled when UTC value provided (in popup)", async () => {
    const user = userEvent.setup();
    render(
      <DateTimePickerControl
        name="dt"
        value="2026-06-18T07:30:00.000Z"
        onChange={rs.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /^2026-/ }));
    const group = screen.getByRole("group", { name: "Time picker" });
    expect(group).not.toHaveAttribute("data-disabled");
  });
});

describe("DateTimePickerControl — timeMode resolution", () => {
  it("defaults to 24h mode — no period spinbutton (in popup)", async () => {
    const user = userEvent.setup();
    render(
      <DateTimePickerControl
        name="dt"
        value="2026-06-18T07:30:00.000Z"
        onChange={rs.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /^2026-/ })); // open popup (value present → date label)
    expect(
      screen.queryByRole("spinbutton", { name: "period" })
    ).not.toBeInTheDocument();
  });

  it("resolves 12h mode from controlProps (in popup)", async () => {
    const user = userEvent.setup();
    render(
      <DateTimePickerControl
        name="dt"
        value="2026-06-18T07:30:00.000Z"
        onChange={rs.fn()}
        field={{
          key: "dt",
          outputType: "string",
          fieldConfig: {
            controlProps: { timeMode: "12h" },
          },
        }}
      />
    );
    await user.click(screen.getByRole("button", { name: /^2026-/ }));
    expect(
      screen.getByRole("spinbutton", { name: "period" })
    ).toBeInTheDocument();
  });

  it("direct timeMode prop overrides controlProps (in popup)", async () => {
    const user = userEvent.setup();
    render(
      <DateTimePickerControl
        name="dt"
        value="2026-06-18T07:30:00.000Z"
        timeMode="24h"
        onChange={rs.fn()}
        field={{
          key: "dt",
          outputType: "string",
          fieldConfig: {
            controlProps: { timeMode: "12h" },
          },
        }}
      />
    );
    await user.click(screen.getByRole("button", { name: /^2026-/ }));
    expect(
      screen.queryByRole("spinbutton", { name: "period" })
    ).not.toBeInTheDocument();
  });
});

describe("DateTimePickerControl — onChange adapter", () => {
  it("calls onChange with { target: { name, value } } shape when date cleared", async () => {
    const user = userEvent.setup();
    const handleChange = rs.fn();
    const { container } = render(
      <DateTimePickerControl
        name="startDt"
        value="2026-06-18T07:30:00.000Z"
        onChange={handleChange}
      />
    );
    const clearButton = container.querySelector(
      'button[tabindex="-1"]'
    ) as HTMLButtonElement;
    expect(clearButton).not.toBeNull();
    await user.click(clearButton);
    expect(handleChange).toHaveBeenCalledWith(
      expect.objectContaining({
        target: expect.objectContaining({
          name: "startDt",
          value: undefined,
        }),
      })
    );
  });

  it("calls onChange with a UTC ISO string when a time segment is changed then Confirmed", async () => {
    const user = userEvent.setup();
    const handleChange = rs.fn();
    render(
      <DateTimePickerControl
        name="dt"
        value="2026-06-18T07:30:00.000Z"
        onChange={handleChange}
      />
    );
    await user.click(screen.getByRole("button", { name: /^2026-/ }));
    const hoursSpinbutton = screen.getByRole("spinbutton", { name: "hours" });
    await user.click(hoursSpinbutton);
    await user.keyboard("{ArrowUp}");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(handleChange).toHaveBeenCalled();
    const callArg = handleChange.mock.calls.at(-1)?.[0];
    expect(callArg?.target.name).toBe("dt");
    expect(callArg?.target.value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  });
});

describe("DateTimePickerControl — mode single|range (SA-2038 R4)", () => {
  it("renders the range trigger when mode=range via controlProps", () => {
    render(
      <DateTimePickerControl
        name="dt"
        onChange={rs.fn()}
        field={{ key: "dt", outputType: "object", fieldConfig: { controlProps: { mode: "range" } } }}
      />,
    );
    expect(screen.getByText("Pick a date-time range")).toBeInTheDocument();
  });

  it("direct mode prop overrides controlProps", () => {
    render(
      <DateTimePickerControl
        name="dt"
        mode="single"
        onChange={rs.fn()}
        field={{ key: "dt", outputType: "string", fieldConfig: { controlProps: { mode: "range" } } }}
      />,
    );
    expect(screen.queryByText("Pick a date-time range")).not.toBeInTheDocument();
    expect(screen.getByText("Pick a date")).toBeInTheDocument();
  });
});
