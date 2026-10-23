/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { FormProvider, useForm, useFormContext } from "react-hook-form";
import { BuilderProvider } from "../../contexts/builder.context";
import type { IField } from "../../types/schema";
import InputControl from "./input-control";

afterEach(() => cleanup());

const Wrapper = ({ children }: { children: ReactNode }) => {
    const methods = useForm({ defaultValues: { qty: "", total: "" } });
    return (
        <BuilderProvider value={{ schema: { fields: [] }, fieldComponent: {}, wrapperComponent: {} }}>
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};
const Total = () => { const { watch } = useFormContext(); return <output aria-label="total">{String(watch("total"))}</output>; };
const scriptedField = (): IField => ({
    key: "qty", outputType: "string",
    fieldConfig: { fieldControl: "InputControl", script: {
        onChange: "async function onChange(ctx){ ctx.setValue('total', 'ran:' + ctx.event.value); }",
    } },
});

describe("InputControl onChange script", () => {
    it("runs the script AND preserves the RHF onChange", async () => {
        const rhfOnChange = rs.fn();
        render(<Wrapper><InputControl name="qty" aria-label="qty" field={scriptedField()} onChange={rhfOnChange} /><Total /></Wrapper>);
        await userEvent.type(screen.getByLabelText("qty"), "5");
        expect(rhfOnChange).toHaveBeenCalled();
        expect(screen.getByLabelText("total").textContent).toBe("ran:5");
    });
    it("no-ops when the field has no script", async () => {
        const rhfOnChange = rs.fn();
        render(<Wrapper><InputControl name="qty" aria-label="qty" field={{ key: "qty", outputType: "string", fieldConfig: { fieldControl: "InputControl" } }} onChange={rhfOnChange} /></Wrapper>);
        await userEvent.type(screen.getByLabelText("qty"), "x");
        expect(rhfOnChange).toHaveBeenCalled();
    });
    it("runs the onBlur script", async () => {
        const field: IField = { key: "qty", outputType: "string", fieldConfig: { fieldControl: "InputControl", script: {
            onBlur: "async function onBlur(ctx){ ctx.setValue('total', 'blurred'); }",
        } } };
        render(<Wrapper><InputControl name="qty" aria-label="qty" field={field} /><Total /></Wrapper>);
        await userEvent.click(screen.getByLabelText("qty"));
        await userEvent.tab();
        expect(screen.getByLabelText("total").textContent).toBe("blurred");
    });
});
