import { useMemo } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { EShowWhenOperator } from "../types/condition";
import type { IField } from "../types/schema";

const evaluateCondition = (watchValue: unknown, operator: EShowWhenOperator, conditionValue: unknown): boolean => {
    switch (operator) {
        case EShowWhenOperator.Equals:
            return watchValue === conditionValue;
        case EShowWhenOperator.NotEquals:
            return watchValue !== conditionValue;
        case EShowWhenOperator.Exists:
            return !!watchValue;
        case EShowWhenOperator.NotExists:
            return !watchValue;
        case EShowWhenOperator.GreaterThan:
            return Number(watchValue) > Number(conditionValue);
        case EShowWhenOperator.LessThan:
            return Number(watchValue) < Number(conditionValue);
        case EShowWhenOperator.GreaterOrEqual:
            return Number(watchValue) >= Number(conditionValue);
        case EShowWhenOperator.LessOrEqual:
            return Number(watchValue) <= Number(conditionValue);
        case EShowWhenOperator.Contains:
            return (typeof watchValue === "string" || Array.isArray(watchValue))
                ? watchValue.includes(conditionValue as string)
                : false;
        case EShowWhenOperator.In:
            return Array.isArray(conditionValue)
                ? conditionValue.includes(watchValue)
                : false;
        default:
            return false;
    }
}

const useCheckCondition = (field: IField, path: string[]) => {
    const { control } = useFormContext();
    const condition = field.fieldConfig.condition;

    const parentPath = useMemo(() => path.slice(0, -1), [path]);
    const conditionFieldKey = condition?.fieldKey;
    const watchName = conditionFieldKey
        ? `${parentPath.join(".")}.${conditionFieldKey}`
        : "__no_condition__";
    const watchValue = useWatch({
        control,
        name: watchName,
        disabled: !conditionFieldKey
    });

    // `valueSource: "field"` binds the comparison value to another field's LIVE value instead
    // of a static literal. Watched unconditionally (hooks-rules-safe) and branched on below —
    // the same pattern `watchValue` already uses for `disabled: !conditionFieldKey`.
    const valueFieldKey = condition?.valueSource === "field" ? condition.valueFieldKey : undefined;
    const valueWatchName = valueFieldKey
        ? `${parentPath.join(".")}.${valueFieldKey}`
        : "__no_value_field__";
    const watchedFieldValue = useWatch({
        control,
        name: valueWatchName,
        disabled: !valueFieldKey
    });

    // Legacy conditions carry no `valueSource` at all — `!== "field"` reads them as static,
    // same as every condition before this ticket.
    const resolvedConditionValue = valueFieldKey ? watchedFieldValue : condition?.conditionValue;

    const isDisabled: boolean = useMemo(() => {
        if (condition?.action !== "disabled") return false;

        return evaluateCondition(
            watchValue,
            condition.operator ?? EShowWhenOperator.Equals,
            resolvedConditionValue
        );
    }, [watchValue, resolvedConditionValue]);

    const invisible: boolean = useMemo(() => {
        if (condition?.action !== "invisible") return false;

        return evaluateCondition(
            watchValue,
            condition.operator ?? EShowWhenOperator.Equals,
            resolvedConditionValue
        );
    }, [watchValue, resolvedConditionValue]);

    return { isDisabled, invisible };
}

export { useCheckCondition };

