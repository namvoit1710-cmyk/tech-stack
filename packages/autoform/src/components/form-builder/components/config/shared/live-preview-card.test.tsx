/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import type { DesignerField } from "../../../types";
import { LivePreviewCard } from "./live-preview-card";

afterEach(cleanup);

function inputField(placeholder: string): DesignerField {
    return {
        _id: "f1",
        _kind: "field",
        key: "email",
        outputType: "string",
        fieldConfig: {
            fieldWrapper: "FormItemWrapper",
            fieldControl: "InputControl",
            wrapperProps: { label: "Email" },
            controlProps: { placeholder },
            rules: [],
            condition: null,
        },
    } as DesignerField;
}

describe("LivePreviewCard — live <Builder> render (SA-2043 fix)", () => {
    it("renders the real control reflecting the field's placeholder", () => {
        render(<LivePreviewCard node={inputField("you@example.com")} />);
        expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument();
    });

    // The reported bug: editing config (e.g. placeholder) must update the preview.
    it("reflects an edited placeholder without a full re-select", () => {
        const { rerender } = render(<LivePreviewCard node={inputField("before")} />);
        expect(screen.getByPlaceholderText("before")).toBeInTheDocument();

        rerender(<LivePreviewCard node={inputField("after")} />);
        expect(screen.getByPlaceholderText("after")).toBeInTheDocument();
        expect(screen.queryByPlaceholderText("before")).not.toBeInTheDocument();
    });
});
