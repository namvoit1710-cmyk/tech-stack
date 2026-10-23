/// <reference types="@testing-library/jest-dom" />

import { describe, expect, it } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import type { UseFormReturn } from "react-hook-form";
import { FormProvider, useForm } from "react-hook-form";

import { EShowWhenOperator } from "../types/condition";
import type { IField } from "../types/schema";
import { useCheckCondition } from "./use-check-condittion";

function makeField(condition: NonNullable<IField["fieldConfig"]["condition"]> | undefined): IField {
    return {
        key: "target",
        outputType: "string",
        fieldConfig: { condition },
    };
}

/** A `FormProvider` wrapper that stashes its `useForm` instance so the test can drive `setValue`. */
function makeWrapper(defaultValues: Record<string, unknown>) {
    const box: { methods?: UseFormReturn } = {};
    function Wrapper({ children }: { children: ReactNode }) {
        const methods = useForm({ defaultValues });
        box.methods = methods;
        return <FormProvider {...methods}>{children}</FormProvider>;
    }
    return { Wrapper, box };
}

describe("useCheckCondition — static value (unchanged)", () => {
    it("evaluates against the literal conditionValue", () => {
        const { Wrapper } = makeWrapper({ container: { status: "active" } });
        const { result } = renderHook(
            () =>
                useCheckCondition(
                    makeField({ fieldKey: "status", operator: EShowWhenOperator.Equals, action: "invisible", conditionValue: "active" }),
                    ["container", "target"]
                ),
            { wrapper: Wrapper }
        );
        expect(result.current.invisible).toBe(true);
    });

    it("legacy conditions (no valueSource) behave as static", () => {
        const { Wrapper } = makeWrapper({ container: { status: "active" } });
        const { result } = renderHook(
            () =>
                useCheckCondition(
                    makeField({ fieldKey: "status", operator: EShowWhenOperator.NotEquals, action: "disabled", conditionValue: "active" }),
                    ["container", "target"]
                ),
            { wrapper: Wrapper }
        );
        expect(result.current.isDisabled).toBe(false);
    });
});

describe("useCheckCondition — valueSource: 'field' (SA-2156)", () => {
    it("compares against the referenced field's live value, not a literal", () => {
        const { Wrapper, box } = makeWrapper({ container: { status: "active", other: "inactive" } });
        const { result } = renderHook(
            () =>
                useCheckCondition(
                    makeField({
                        fieldKey: "status",
                        operator: EShowWhenOperator.Equals,
                        action: "invisible",
                        conditionValue: "",
                        valueSource: "field",
                        valueFieldKey: "other",
                    }),
                    ["container", "target"]
                ),
            { wrapper: Wrapper }
        );

        // status ("active") !== other ("inactive")
        expect(result.current.invisible).toBe(false);

        act(() => box.methods!.setValue("container.other", "active"));

        // other now matches status live — the condition tracks the field, not a stale literal.
        expect(result.current.invisible).toBe(true);
    });

    it("re-evaluates as the watched field's value changes at runtime", () => {
        const { Wrapper, box } = makeWrapper({ container: { status: "draft", other: "draft" } });
        const { result } = renderHook(
            () =>
                useCheckCondition(
                    makeField({
                        fieldKey: "status",
                        operator: EShowWhenOperator.Equals,
                        action: "disabled",
                        conditionValue: "",
                        valueSource: "field",
                        valueFieldKey: "other",
                    }),
                    ["container", "target"]
                ),
            { wrapper: Wrapper }
        );

        expect(result.current.isDisabled).toBe(true);

        act(() => box.methods!.setValue("container.other", "published"));

        expect(result.current.isDisabled).toBe(false);
    });
});
