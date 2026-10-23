import type { FieldErrors, FieldValues } from "react-hook-form";
import type { BuilderSubmitValidationError } from "../types/builder-submit-validation-state";

const ERROR_METADATA_KEYS = new Set(["message", "type", "ref", "types"]);

const isRecord = (value: unknown): value is Record<string, unknown> => {
    return typeof value === "object" && value !== null;
};

const getErrorSnapshot = (value: Record<string, unknown>): BuilderSubmitValidationError | null => {
    const message = typeof value.message === "string" ? value.message : undefined;
    const type = typeof value.type === "string" ? value.type : undefined;

    if (message === undefined && type === undefined) {
        return null;
    }

    return { message, type };
};

const visitFieldErrors = (
    value: unknown,
    path: string,
    normalizedErrors: Record<string, BuilderSubmitValidationError>,
): void => {
    if (Array.isArray(value)) {
        value.forEach((item, index) => {
            const nextPath = path ? `${path}.${index}` : String(index);
            visitFieldErrors(item, nextPath, normalizedErrors);
        });

        return;
    }

    if (!isRecord(value)) {
        return;
    }

    const errorSnapshot = getErrorSnapshot(value);

    if (path && errorSnapshot) {
        normalizedErrors[path] = errorSnapshot;
    }

    Object.entries(value).forEach(([key, nestedValue]) => {
        if (key === "root") {
            const rootError = isRecord(nestedValue) ? getErrorSnapshot(nestedValue) : null;

            if (path && rootError) {
                normalizedErrors[path] = rootError;
            }

            return;
        }

        if (ERROR_METADATA_KEYS.has(key)) {
            return;
        }

        const nextPath = path ? `${path}.${key}` : key;
        visitFieldErrors(nestedValue, nextPath, normalizedErrors);
    });
};

export const normalizeFormErrors = (errors: FieldErrors<FieldValues>): Record<string, BuilderSubmitValidationError> => {
    const normalizedErrors: Record<string, BuilderSubmitValidationError> = {};

    visitFieldErrors(errors, "", normalizedErrors);

    return normalizedErrors;
};