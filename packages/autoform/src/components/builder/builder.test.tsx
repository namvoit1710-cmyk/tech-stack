import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React, { createRef } from "react";
import { useFormContext } from "react-hook-form";
import type { ISchema } from "../../types/schema";
import Builder from "./builder";
import type { BuilderRef } from "./builder";

type FutureBuilderRef = BuilderRef & {
    validateAndGetFormState: () => Promise<{
        isValid: boolean;
        values: Record<string, unknown>;
        errors: Record<string, { message?: string; type?: string }>;
    }>;
};

rs.mock("./builder-field", () => ({
    __esModule: true,
    default: function MockBuilderField({ field }: { field: { key: string } }) {
        const { register } = useFormContext();

        return <input aria-label={field.key} {...register(field.key)} />;
    },
}));

const schema: ISchema = {
    fields: [
        {
            key: "subId",
            outputType: "string",
            fieldConfig: {
                fieldControl: "InputControl",
                rules: [{ method: "required", message: "Sub ID is required" }],
            },
        },
    ],
};

describe("BuilderRef.validateAndGetFormState", () => {
    void afterEach(() => {
        void cleanup();
        void rs.clearAllMocks();
    });

    it("returns invalid state with normalized errors and entered values", async () => {
        globalThis.React = React;

        const ref = createRef<FutureBuilderRef>();

        render(
            <Builder
                ref={ref}
                schema={schema}
                defaultValues={{ subId: "" }}
            />,
        );

        expect(ref.current).toBeTruthy();

        const state = await ref.current?.validateAndGetFormState();

        expect(state).toEqual({
            isValid: false,
            values: { subId: "" },
            errors: {
                subId: {
                    message: "Sub ID is required",
                    type: "custom",
                },
            },
        });
    });

    it("returns valid state with current values and no errors", async () => {
        globalThis.React = React;

        const ref = createRef<FutureBuilderRef>();

        render(
            <Builder
                ref={ref}
                schema={schema}
                defaultValues={{ subId: "" }}
            />,
        );

        fireEvent.change(screen.getByLabelText("subId"), {
            target: { value: "SUB-1" },
        });

        expect(ref.current).toBeTruthy();

        const state = await ref.current?.validateAndGetFormState();

        expect(state).toEqual({
            isValid: true,
            values: { subId: "SUB-1" },
            errors: {},
        });
    });
});

describe("Builder default-values re-sync", () => {
    void afterEach(() => {
        void cleanup();
        void rs.clearAllMocks();
    });

    // Regression: a re-render that hands a NEW defaultValues object of identical
    // content (e.g. a TanStack Query refetch triggered by a button action) used to
    // re-fire methods.reset() and wipe everything the user entered — including rows
    // added via useFieldArray.append(). A typed scalar models that lost state.
    it("keeps user input when defaultValues is replaced with an identical-content object", () => {
        globalThis.React = React;

        const ref = createRef<FutureBuilderRef>();

        const { rerender } = render(
            <Builder ref={ref} schema={schema} defaultValues={{ subId: "" }} />,
        );

        fireEvent.change(screen.getByLabelText("subId"), {
            target: { value: "SUB-1" },
        });

        // New object reference, same content — must NOT reset the form.
        rerender(
            <Builder ref={ref} schema={schema} defaultValues={{ subId: "" }} />,
        );

        expect((screen.getByLabelText("subId") as HTMLInputElement).value).toBe("SUB-1");
    });

    // Even when the defaults genuinely change, fields the user touched (dirty) are
    // preserved via reset(..., { keepDirtyValues: true }).
    it("preserves dirty fields when defaultValues genuinely change", () => {
        globalThis.React = React;

        const ref = createRef<FutureBuilderRef>();

        const { rerender } = render(
            <Builder ref={ref} schema={schema} defaultValues={{ subId: "" }} />,
        );

        fireEvent.change(screen.getByLabelText("subId"), {
            target: { value: "SUB-1" },
        });

        rerender(
            <Builder ref={ref} schema={schema} defaultValues={{ subId: "FROM-SERVER" }} />,
        );

        expect((screen.getByLabelText("subId") as HTMLInputElement).value).toBe("SUB-1");
    });
});