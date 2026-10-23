/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { IConditionConfig } from "../../../../../types/condition";
import { EShowWhenOperator } from "../../../../../types/condition";
import { ConditionEditor } from "./condition-editor";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

const strField = [{ key: "status", outputType: "string" as const }];

/** Condition is a collapsible section, closed by default (SA-2156) — open it before asserting. */
async function openCondition(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /condition/i }));
}

describe("ConditionEditor — watch dropdown + typed value", () => {
  it("watch field is a dropdown of siblings, not a free-text key box", async () => {
    const user = userEvent.setup();
    render(<ConditionEditor condition={null} siblingFields={strField} onChange={rs.fn()} />);
    await openCondition(user);
    expect(screen.getByRole("combobox", { name: "Watch field" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("e.g. type")).not.toBeInTheDocument();
  });

  it("wraps the rule card in the shared horizontal padding gutter, not flush to the panel edge (SA-2175)", async () => {
    const user = userEvent.setup();
    const { container } = render(<ConditionEditor condition={null} siblingFields={strField} onChange={rs.fn()} />);
    await openCondition(user);
    const card = container.querySelector(".border.border-border.rounded-md");
    expect(card?.parentElement).toHaveClass("px-3");
  });

  it("renders NO value input for exists (truthiness only)", async () => {
    const user = userEvent.setup();
    render(
      <ConditionEditor
        condition={{ fieldKey: "status", operator: EShowWhenOperator.Exists, action: "invisible", conditionValue: "" }}
        siblingFields={strField}
        onChange={rs.fn()}
      />,
    );
    await openCondition(user);
    expect(screen.queryByLabelText("Condition value")).not.toBeInTheDocument();
    expect(screen.getByText(/no value needed/i)).toBeInTheDocument();
  });

  it("writes a real NUMBER for equals on a number field (so === matches)", async () => {
    const onChange = rs.fn();
    const user = userEvent.setup();
    render(
      <ConditionEditor
        condition={{ fieldKey: "age", operator: EShowWhenOperator.Equals, action: "invisible", conditionValue: "" }}
        siblingFields={[{ key: "age", outputType: "number" }]}
        onChange={onChange}
      />,
    );
    await openCondition(user);
    await user.type(screen.getByLabelText("Condition value"), "5");
    const last = onChange.mock.calls.at(-1)?.[0] as IConditionConfig;
    expect(last.conditionValue).toBe(5);
  });

  it("builds an ARRAY for `in` via chips (fixes always-false)", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [c, setC] = useState<IConditionConfig | null>({
        fieldKey: "tags", operator: EShowWhenOperator.In, action: "invisible", conditionValue: [],
      });
      return <ConditionEditor condition={c} siblingFields={[{ key: "tags", outputType: "string" }]} onChange={setC} />;
    }
    render(<Harness />);
    await openCondition(user);
    await user.type(screen.getByLabelText("Add condition value"), "active{Enter}pending{Enter}");
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });
});

describe("ConditionEditor — value source: Static | From field (SA-2156)", () => {
  const siblings = [
    { key: "status", outputType: "string" as const },
    { key: "otherStatus", outputType: "string" as const },
  ];

  /** A controlled harness — `condition` is a prop, so a sequence of interactions needs the
   * component's own state to actually round-trip (mirrors the `in`-chips harness above). */
  function Harness({ onChangeSpy }: { onChangeSpy?: (next: IConditionConfig | null) => void }) {
    const [c, setC] = useState<IConditionConfig | null>({
      fieldKey: "status", operator: EShowWhenOperator.Equals, action: "invisible", conditionValue: "active",
    });
    return (
      <ConditionEditor
        condition={c}
        siblingFields={siblings}
        onChange={(next) => {
          onChangeSpy?.(next);
          setC(next);
        }}
      />
    );
  }

  it("defaults to Static — no valueSource is read as static", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await openCondition(user);
    expect(screen.getByRole("radio", { name: "Static" })).toHaveAttribute("data-state", "on");
    expect(screen.getByLabelText("Condition value")).toBeInTheDocument();
  });

  it("switching to From field hides the static input and shows a sibling-field picker", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await openCondition(user);

    await user.click(screen.getByRole("radio", { name: "From field" }));

    expect(screen.queryByLabelText("Condition value")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Value field" })).toBeInTheDocument();
  });

  it("From field never offers the watched field itself as the value source", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await openCondition(user);

    await user.click(screen.getByRole("radio", { name: "From field" }));
    await user.click(screen.getByRole("combobox", { name: "Value field" }));

    expect(screen.getByRole("option", { name: /otherStatus/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /^status ·/ })).not.toBeInTheDocument();
  });

  it("picking a field writes valueSource:'field' + valueFieldKey", async () => {
    const onChange = rs.fn();
    const user = userEvent.setup();
    render(<Harness onChangeSpy={onChange} />);
    await openCondition(user);

    await user.click(screen.getByRole("radio", { name: "From field" }));
    onChange.mockClear();
    await user.click(screen.getByRole("combobox", { name: "Value field" }));
    await user.click(screen.getByRole("option", { name: /otherStatus/ }));

    const last = onChange.mock.calls.at(-1)?.[0] as IConditionConfig;
    expect(last.valueSource).toBe("field");
    expect(last.valueFieldKey).toBe("otherStatus");
  });

  it("switching back to Static clears valueFieldKey", async () => {
    const user = userEvent.setup();
    const onChange = rs.fn();
    render(
      <ConditionEditor
        condition={{
          fieldKey: "status", operator: EShowWhenOperator.Equals, action: "invisible",
          conditionValue: "", valueSource: "field", valueFieldKey: "otherStatus",
        }}
        siblingFields={siblings}
        onChange={onChange}
      />,
    );
    await openCondition(user);

    await user.click(screen.getByRole("radio", { name: "Static" }));

    const last = onChange.mock.calls.at(-1)?.[0] as IConditionConfig;
    expect(last.valueSource).toBe("static");
    expect(last.valueFieldKey).toBeUndefined();
  });
});
