/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../contexts/builder.context";
import type { IField } from "../../types/schema";
import ButtonControl from "./button-control";

afterEach(() => cleanup());
const post = rs.fn().mockResolvedValue({ result: { status_code: 200, body: { status: "SUBMITTED" } } });

const Wrapper = ({ children }: { children: ReactNode }) => {
    const methods = useForm({ defaultValues: { status: "" } });
    return (
        <BuilderProvider value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {}, services: { workflow: { post } } as never }}>
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};
const buttonField = (): IField => ({
    key: "saveBtn", outputType: "string",
    fieldConfig: { fieldControl: "ButtonControl", script: {
        onClick: "async function onClick(ctx){ const r = await ctx.gateway.invoke('wf','submit', ctx.formValues); ctx.setValue('status', r.status); }",
    } },
});

describe("ButtonControl onClick script", () => {
    it("invokes gateway.invoke on click", async () => {
        render(<Wrapper><ButtonControl name="saveBtn" field={buttonField()} label="Save" /></Wrapper>);
        await userEvent.click(screen.getByRole("button", { name: /save/i }));
        expect(post).toHaveBeenCalledWith(
            "workers/gateway/functions/invoke_app_function/invoke",
            { params: { app: "wf", function: "submit", args: { status: "" } } },
        );
    });
});

const postWithAction = rs.fn().mockResolvedValue({ result: { status_code: 200, body: { status: "SUBMITTED" } } });
const onFormActions = rs.fn().mockResolvedValue(undefined);

const buttonFieldWithAction = (): IField => ({
    key: "saveBtn", outputType: "string",
    fieldConfig: { fieldControl: "ButtonControl", script: {
        onClick: "async function onClick(ctx){ await ctx.gateway.invoke('wf','submit', ctx.formValues); }",
    } },
});

const WrapperWithAction = ({ children }: { children: ReactNode }) => {
    const methods = useForm({ defaultValues: { status: "" } });
    return (
        <BuilderProvider value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {}, services: { workflow: { post: postWithAction } } as never, onFormActions }}>
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};

describe("ButtonControl with both onClick script and declarative action", () => {
    it("runs the onClick script AND the declarative action on a single click", async () => {
        render(
            <WrapperWithAction>
                <ButtonControl name="saveBtn" field={buttonFieldWithAction()} label="Save" action="submit" />
            </WrapperWithAction>,
        );
        await userEvent.click(screen.getByRole("button", { name: /save/i }));

        expect(onFormActions).toHaveBeenCalledWith("submit", expect.any(Object));

        await waitFor(() => {
            expect(postWithAction).toHaveBeenCalledWith(
                "workers/gateway/functions/invoke_app_function/invoke",
                { params: { app: "wf", function: "submit", args: { status: "" } } },
            );
        });
    });
});
