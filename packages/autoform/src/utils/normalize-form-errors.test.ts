import { describe, expect, it } from "@rstest/core";
import type { FieldErrors, FieldValues } from "react-hook-form";
import { normalizeFormErrors } from "./normalize-form-errors";

describe("normalizeFormErrors", () => {
    it("maps array root errors to the owning field path", () => {
        const errors = {
            items: {
                root: {
                    message: "At least one item is required",
                    type: "custom",
                },
            },
        } as FieldErrors<FieldValues>;

        expect(normalizeFormErrors(errors)).toEqual({
            items: {
                message: "At least one item is required",
                type: "custom",
            },
        });
    });

    it("maps nested field errors to dot paths", () => {
        const errors = {
            addresses: {
                0: {
                    city: {
                        message: "City is required",
                        type: "custom",
                    },
                },
            },
        } as FieldErrors<FieldValues>;

        expect(normalizeFormErrors(errors)).toEqual({
            "addresses.0.city": {
                message: "City is required",
                type: "custom",
            },
        });
    });
});