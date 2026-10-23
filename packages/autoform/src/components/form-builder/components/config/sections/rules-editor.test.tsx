/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RulesEditor } from "./rules-editor";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

/** Validation is a collapsible section, closed by default (SA-2156) — open it before asserting. */
async function openValidation() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /validation/i }));
  return user;
}

describe("RulesEditor — typed value inputs + runtime honesty", () => {
  it("shows an expression input for refine (was hidden → field always-invalid)", async () => {
    render(<RulesEditor nodeId="n1" rules={[{ method: "refine", value: "value > 5", message: "" }]} onChange={rs.fn()} />);
    await openValidation();
    expect(screen.getByPlaceholderText("expression, e.g. value > 5")).toBeInTheDocument();
  });

  it("flags a non-honored method as not enforced", async () => {
    render(<RulesEditor nodeId="n2" rules={[{ method: "min", value: "3", message: "" }]} onChange={rs.fn()} />);
    await openValidation();
    expect(screen.getByText("Not enforced at runtime yet")).toBeInTheDocument();
  });

  it("does NOT flag required (it is honored)", async () => {
    render(<RulesEditor nodeId="n3" rules={[{ method: "required", value: "", message: "" }]} onChange={rs.fn()} />);
    await openValidation();
    expect(screen.queryByText("Not enforced at runtime yet")).not.toBeInTheDocument();
  });

  it("badges the section header with the rule count", () => {
    render(<RulesEditor nodeId="n4" rules={[{ method: "required", value: "", message: "" }, { method: "min", value: "1", message: "" }]} onChange={rs.fn()} />);
    expect(screen.getByRole("button", { name: /validation/i })).toHaveTextContent("2");
  });

  it("wraps the rule rows in the shared horizontal padding gutter, not flush to the panel edge (SA-2175)", async () => {
    const { container } = render(<RulesEditor nodeId="n5" rules={[{ method: "required", value: "", message: "" }]} onChange={rs.fn()} />);
    await openValidation();
    const card = container.querySelector(".border.border-border.rounded-md");
    expect(card?.parentElement?.parentElement).toHaveClass("px-3");
  });
});
