import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ISchema } from "../../types/schema";

afterEach(() => {
  cleanup();
});

rs.mock("../form-builder", () => ({
  default: ({ value, onChange }: { value: ISchema; onChange: (s: ISchema) => void }) => (
    <button
      type="button"
      data-testid="fb-stub"
      onClick={() => onChange({ fields: [{ key: "z", outputType: "string", fieldConfig: {} }] })}
    >
      stub:{value.fields.length}
    </button>
  ),
}));

import FormBuilderControl from "./form-builder-control";

const twoFields = JSON.stringify([
  { key: "a", outputType: "string", fieldConfig: {} },
  { key: "b", outputType: "string", fieldConfig: {} },
]);

describe("FormBuilderControl", () => {
  it("collapsed: shows the field count and key chips, no builder mounted", () => {
    render(<FormBuilderControl name="input_schema" value={twoFields} onChange={() => {}} />);
    expect(screen.getByText("2 fields")).toBeTruthy();
    expect(screen.getByText("a")).toBeTruthy();
    expect(screen.getByText("b")).toBeTruthy();
    expect(screen.queryByTestId("fb-stub")).toBeNull();
  });

  it("invalid JSON collapses to 0 fields without throwing", () => {
    render(<FormBuilderControl name="input_schema" value={"not json"} onChange={() => {}} />);
    expect(screen.getByText("0 fields")).toBeTruthy();
  });

  it("expanding mounts the builder and stringifies its onChange", async () => {
    const onChange = rs.fn();
    render(<FormBuilderControl name="input_schema" value={twoFields} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /edit form/i }));
    const stub = await screen.findByTestId("fb-stub");
    fireEvent.click(stub);
    expect(onChange).toHaveBeenCalledWith(
      JSON.stringify([{ key: "z", outputType: "string", fieldConfig: {} }], null, 2),
    );
    // The stored value MUST parse to a JSON array (the engine's input_schema contract).
    expect(Array.isArray(JSON.parse(onChange.mock.calls.at(-1)![0] as string))).toBe(true);
  });
});
