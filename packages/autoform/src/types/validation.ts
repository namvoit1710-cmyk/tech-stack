export type IStringValidationMethod =
    | "trim" | "includes" | "startsWith"
    | "endsWith" | "pattern" | "regex";

export type INumberValidationMethod =
    | "gt" | "gte" | "lt"
    | "lte" | "int" | "positive"
    | "negative" | "multipleOf";

export type ICommonValidationMethod = "min" | "max" | "length" | "required";

export type ICustomValidationMethod = "refine" | "superRefine";

export type IValidationMethod = IStringValidationMethod | INumberValidationMethod | ICommonValidationMethod | ICustomValidationMethod;

export interface IValidationRules {
    method: IValidationMethod;
    value?: string | number | RegExp;
    message?: string;
}
