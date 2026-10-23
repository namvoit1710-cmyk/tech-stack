/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../contexts/builder.context";
import RadioControl from "./radio-control";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

// After R2, RadioControl calls useServerOptions (useFormContext + useBuilderContext), so
// it must render inside both providers — mirror select-control.test.tsx.
const makeServices = () => ({
    gateway: {
        fetch: () => Promise.resolve({ data: { items: [{ id: "srv", value: "From Server" }] } }),
    },
});

const Wrapper = ({ children, services }: { children: ReactNode; services?: Record<string, unknown> }) => {
    const methods = useForm();
    return (
        <BuilderProvider
            value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {}, services: services as never }}
        >
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};

const staticField = { fieldConfig: { controlProps: {} } } as never;

describe("RadioControl — static (SA-1239, unchanged after R2)", () => {
    it("uses `value` as the visible label when options omit `label`", () => {
        render(
            <Wrapper>
                <RadioControl
                    field={staticField}
                    onChange={() => {}}
                    value="single"
                    options={[
                        { id: "single", value: "Single" },
                        { id: "multiple", value: "Multiple" },
                    ]}
                />
            </Wrapper>
        );
        expect(screen.getByText("Single")).toBeInTheDocument();
        expect(screen.getByText("Multiple")).toBeInTheDocument();
        expect(screen.getAllByRole("radio")).toHaveLength(2);
    });

    it("does not leak serverOptions onto the radiogroup DOM node", () => {
        render(
            <Wrapper>
                <RadioControl
                    field={staticField}
                    onChange={() => {}}
                    value=""
                    serverOptions={{ endpoint: "x", service: "gateway" }}
                    options={[{ id: "a", value: "A" }]}
                />
            </Wrapper>
        );
        expect(screen.getByRole("radiogroup")).not.toHaveAttribute("serveroptions");
    });
});

describe("RadioControl — serverOptions (R2)", () => {
    it("renders server data instead of static options when serverOptions is set", async () => {
        const serverField = {
            fieldConfig: { controlProps: { serverOptions: { endpoint: "opts", service: "gateway" } } },
        } as never;
        render(
            <Wrapper services={makeServices()}>
                <RadioControl
                    field={serverField}
                    onChange={() => {}}
                    value=""
                    serverOptions={{ endpoint: "opts", service: "gateway" }}
                    options={[{ id: "static", value: "Static Only" }]}
                />
            </Wrapper>
        );
        expect(await screen.findByText("From Server")).toBeInTheDocument();
        expect(screen.queryByText("Static Only")).not.toBeInTheDocument();
    });
});
