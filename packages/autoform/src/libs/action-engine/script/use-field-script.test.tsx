/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useState } from "react";
import { FormProvider, useForm, useFormContext } from "react-hook-form";
import { toast as mockToast } from "@ldc/ui/blocks/toast/toast";
import { BuilderProvider } from "../../../contexts/builder.context";
import type { IField } from "../../../types/schema";
import { useFieldScript } from "./use-field-script";

rs.mock("@ldc/ui/blocks/toast/toast", () => ({
    toast: {
        error: rs.fn(),
        success: rs.fn(),
        info: rs.fn(),
        warning: rs.fn(),
        loading: rs.fn(),
        dismiss: rs.fn(),
    },
}));

afterEach(() => cleanup());

const inputField = (script: Record<string, string>): IField => ({
    key: "qty", outputType: "number", fieldConfig: { fieldControl: "InputControl", script },
});
const Wrapper = ({ children }: { children: ReactNode }) => {
    const methods = useForm({ defaultValues: { qty: 0, total: 0 } });
    return (
        <BuilderProvider value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {} }}>
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};

describe("useFieldScript", () => {
    it("returns a runner that executes a field's event script against RHF", async () => {
        let captured: unknown;
        function Probe() {
            const run = useFieldScript();
            const methods = useFormContext();
            return (
                <button onClick={async () => {
                    run(inputField({
                        onChange: "async function onChange(ctx){ ctx.setValue('total', Number(ctx.event.value) + 1); }",
                    }), "onChange", { value: 4 });
                    await new Promise((r) => setTimeout(r, 0));
                    captured = methods.getValues("total");
                }}>fire</button>
            );
        }
        const { getByText } = render(<Wrapper><Probe /></Wrapper>);
        (getByText("fire") as HTMLButtonElement).click();
        await new Promise((r) => setTimeout(r, 5));
        expect(captured).toBe(5);
    });
});

describe("useFieldScript without a BuilderProvider", () => {
    it("does not throw when rendered outside a BuilderProvider (scriptless InputControl)", () => {
        const NoBuilderWrapper = ({ children }: { children: ReactNode }) => {
            const methods = useForm({ defaultValues: { qty: 0 } });
            return <FormProvider {...methods}>{children}</FormProvider>;
        };
        function Probe() {
            useFieldScript();
            return <div>ok</div>;
        }

        expect(() => render(<NoBuilderWrapper><Probe /></NoBuilderWrapper>)).not.toThrow();
    });
});

describe("useFieldScript runner stability", () => {
    it("returns the same runner reference across a re-render when called with no args", async () => {
        const runnerRef: { current: unknown } = { current: undefined };
        function Probe() {
            const run = useFieldScript();
            const [, setTick] = useState(0);
            runnerRef.current = run;
            return <button onClick={() => setTick((t) => t + 1)}>tick</button>;
        }

        const { getByText } = render(<Wrapper><Probe /></Wrapper>);
        const first = runnerRef.current;

        // Trigger a Probe-local re-render unrelated to any useFieldScript dependency;
        // the returned runner must remain referentially stable across it.
        await userEvent.click(getByText("tick"));
        const second = runnerRef.current;

        expect(first).toBeDefined();
        expect(second).toBe(first);
    });
});

describe("useFieldScript default toast (SA-2177 visible feedback)", () => {
    it("routes ctx.toast(message, 'error') through the shared @ldc/ui toast.error", async () => {
        function Probe() {
            const run = useFieldScript();
            return (
                <button
                    onClick={() => {
                        run(
                            inputField({
                                onChange: "async function onChange(ctx){ ctx.toast('boom', 'error'); }",
                            }),
                            "onChange",
                            { value: 1 },
                        );
                    }}
                >
                    fire
                </button>
            );
        }
        const { getByText } = render(<Wrapper><Probe /></Wrapper>);
        (getByText("fire") as HTMLButtonElement).click();
        await new Promise((r) => setTimeout(r, 5));
        expect(mockToast.error).toHaveBeenCalledWith("boom");
    });

    it("surfaces a thrown script error via the shared @ldc/ui toast.error", async () => {
        function Probe() {
            const run = useFieldScript();
            return (
                <button
                    onClick={() => {
                        run(
                            inputField({
                                onChange: "async function onChange(ctx){ throw new Error('kaboom'); }",
                            }),
                            "onChange",
                            { value: 1 },
                        );
                    }}
                >
                    fire
                </button>
            );
        }
        const { getByText } = render(<Wrapper><Probe /></Wrapper>);
        (getByText("fire") as HTMLButtonElement).click();
        await new Promise((r) => setTimeout(r, 5));
        expect(mockToast.error).toHaveBeenCalledWith("kaboom");
    });
});
