export enum EShowWhenOperator {
    Equals = "equals",
    NotEquals = "notEquals",
    Exists = "exists",
    NotExists = "notExists",
    GreaterThan = "greaterThan",
    LessThan = "lessThan",
    GreaterOrEqual = "greaterOrEqual",
    LessOrEqual = "lessOrEqual",
    Contains = "contains",
    In = "in"
}

export interface IConditionConfig {
    fieldKey: string;
    conditionValue: unknown;
    action: "disabled" | "invisible";
    operator?: EShowWhenOperator;
    /** Where `conditionValue` comes from. Defaults to `"static"` — every existing condition. */
    valueSource?: "static" | "field";
    /** The sibling field to follow when `valueSource` is `"field"`. */
    valueFieldKey?: string;
}
