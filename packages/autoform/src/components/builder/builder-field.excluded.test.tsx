/// <reference types="@testing-library/jest-dom" />
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { BuilderProvider } from "../../contexts/builder.context";
import type { IField } from "../../types/schema";
import BuilderField, { EXCLUDED_OUTPUT_TYPE } from "./builder-field";

// builder-field.tsx statically imports OdataWrapper, which (via odata-add-edit-modal.tsx ->
// Builder -> field-wrapper/index.ts) cycles back to OdataWrapper itself. That cycle is latent
// in the module graph today; it only surfaces as a TDZ crash when a test imports BuilderField
// directly (every other test goes through builder.tsx and mocks ./builder-field, so the cycle
// is never walked). Stub the wrapper here — this test only exercises EXCLUDED_OUTPUT_TYPE
// routing for Choice controls, not the Odata/Table wrapper rendering path.
rs.mock("../field-wrapper/odata-wrapper/components/odata-wrapper", () => ({
    __esModule: true,
    default: () => <div>odata-wrapper-stub</div>,
}));

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => cleanup());

const Harness = ({ children }: { children: ReactNode }) => {
    const methods = useForm();
    return (
        <BuilderProvider
            value={{
                schema: { fields: [] },
                fieldComponent: {
                    ComboboxControl: () => <div>combobox-rendered</div>,
                    SelectTableControl: () => <div>select-table-rendered</div>,
                },
                wrapperComponent: {},
            }}
        >
            <FormProvider {...methods}>{children}</FormProvider>
        </BuilderProvider>
    );
};

const arrayField = (fieldControl: string): IField => ({
    key: "picks",
    outputType: "array",
    fieldConfig: { fieldControl, controlProps: { multiple: true } },
});

describe("builder-field — array-output Choice controls render the control, not BuilderArrayField", () => {
    it("renders a multiple ComboboxControl as the control", () => {
        render(
            <Harness>
                <BuilderField field={arrayField("ComboboxControl")} path={["picks"]} />
            </Harness>
        );
        expect(screen.getByText("combobox-rendered")).toBeInTheDocument();
    });

    it("renders a multiple SelectTableControl as the control", () => {
        render(
            <Harness>
                <BuilderField field={arrayField("SelectTableControl")} path={["picks"]} />
            </Harness>
        );
        expect(screen.getByText("select-table-rendered")).toBeInTheDocument();
    });
});

describe("EXCLUDED_OUTPUT_TYPE (SA-2038 addendum)", () => {
    it("keeps DatePickerControl excluded (regression)", () => {
        expect(EXCLUDED_OUTPUT_TYPE.has("DatePickerControl")).toBe(true);
    });

    it("keeps the SA-2036 Choice exclusions (regression — must not be dropped)", () => {
        expect(EXCLUDED_OUTPUT_TYPE.has("ComboboxControl")).toBe(true);
        expect(EXCLUDED_OUTPUT_TYPE.has("SelectTableControl")).toBe(true);
    });

    it("excludes DateTimePickerControl so a range (object) DateTime renders the picker, not BuilderObjectField", () => {
        expect(EXCLUDED_OUTPUT_TYPE.has("DateTimePickerControl")).toBe(true);
    });

    it("excludes UploadControl so a multiple (array) Upload renders the control, not BuilderArrayField (SA-2040)", () => {
        expect(EXCLUDED_OUTPUT_TYPE.has("UploadControl")).toBe(true);
    });
});
