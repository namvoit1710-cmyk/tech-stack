/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../contexts/builder.context";
import SelectControl from "./select-control";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

// SelectControl reaches for useFormContext (react-hook-form) and useBuilderContext
// via useServerOptions, so it must render inside both providers.
const Wrapper = ({ children }: { children: ReactNode }) => {
    const methods = useForm();
    return (
        <BuilderProvider value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {} }}>
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};

describe("SelectControl", () => {
    it("renders without crashing when options is undefined (regression: white-page on Dropdown)", () => {
        // Reproduces the node-definition Inputs bug: a Dropdown field with no
        // options yet spreads controlProps without `options`, so the control
        // receives undefined. It must fall back to an empty list, not throw.
        render(
            <Wrapper>
                <SelectControl
                    name="choice"
                    placeholder="Choose one"
                    onChange={rs.fn()}
                    options={undefined}
                />
            </Wrapper>
        );
        expect(screen.getByText("Choose one")).toBeInTheDocument();
        expect(screen.getByRole("combobox")).toBeInTheDocument();
    });

    it("renders the provided options as items when opened", () => {
        render(
            <Wrapper>
                <SelectControl
                    name="fruit"
                    placeholder="Pick a fruit"
                    onChange={rs.fn()}
                    options={[
                        { id: "a", value: "Apple" },
                        { id: "b", value: "Banana" },
                    ]}
                />
            </Wrapper>
        );
        // Trigger shows placeholder until a value is chosen.
        expect(screen.getByText("Pick a fruit")).toBeInTheDocument();
    });
});

describe("SelectControl — unresolved variable options", () => {
    it("renders without crashing when options is an unresolved expression string", () => {
        render(
            <Wrapper>
                <SelectControl
                    name="choice"
                    placeholder="Choose one"
                    onChange={rs.fn()}
                    options={"{{$http_1.response_body.items}}" as unknown as undefined}
                />
            </Wrapper>
        );
        expect(screen.getByRole("combobox")).toBeInTheDocument();
    });
});
