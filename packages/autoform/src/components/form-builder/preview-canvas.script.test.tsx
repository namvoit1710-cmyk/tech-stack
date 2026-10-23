/// <reference types="@testing-library/jest-dom" />
// TEMP repro (SA-2177 debug) — does an Events-authored script actually RUN in Preview?
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Builder from "../builder/builder";
import { toISchema } from "./utils/nodes";
import type { DesignerField } from "./types";

afterEach(() => cleanup());

const scriptedNode = {
  _id: "n1", _kind: "field", key: "qty", outputType: "string",
  fieldConfig: {
    fieldControl: "InputControl", fieldWrapper: "FormItemWrapper",
    wrapperProps: { label: "Qty" }, controlProps: { "aria-label": "qty" },
    script: { onChange: "async function onChange(ctx){ ctx.setValue('sink', 'RAN:' + ctx.event.value); }" },
  },
} as unknown as DesignerField;

const sinkNode = {
  _id: "n2", _kind: "field", key: "sink", outputType: "string",
  fieldConfig: {
    fieldControl: "InputControl", fieldWrapper: "FormItemWrapper",
    wrapperProps: { label: "Sink" }, controlProps: { "aria-label": "sink", readOnly: true },
  },
} as unknown as DesignerField;

describe("Preview runs authored scripts", () => {
  it("onChange script authored on a node runs after toISchema → Builder", async () => {
    const schema = toISchema([scriptedNode, sinkNode]);
    // 1) config propagates through the converter (Preview's exact input)
    expect(schema.fields[0].fieldConfig.script?.onChange).toContain("ctx.setValue");
    // 2) render exactly like PreviewCanvas: <Builder schema={...} /> (no services)
    render(<Builder schema={schema} />);
    await userEvent.type(screen.getByLabelText("qty"), "5");
    // 3) did the script actually execute? (observable via ctx.setValue on a sibling)
    expect((screen.getByLabelText("sink") as HTMLInputElement).value).toBe("RAN:5");
  });

  it("button onClick script runs in Preview (the case the user hit)", async () => {
    const btnNode = {
      _id: "b1", _kind: "field", key: "go", outputType: "string",
      fieldConfig: {
        fieldControl: "ButtonControl", fieldWrapper: "FragmentWrapper",
        controlProps: { label: "Go" },
        script: { onClick: "async function onClick(ctx){ ctx.setValue('sink', 'CLICKED'); }" },
      },
    } as unknown as DesignerField;
    const schema = toISchema([btnNode, sinkNode]);
    render(<Builder schema={schema} />);
    await userEvent.click(screen.getByRole("button", { name: /go/i }));
    await waitFor(() =>
      expect((screen.getByLabelText("sink") as HTMLInputElement).value).toBe("CLICKED"),
    );
  });
});
