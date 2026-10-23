import type { IValidationMethod } from "../../../types/validation";
import { RUNTIME_HONORED_METHODS } from "../../../utils/validation";

export const RULE_METHODS: IValidationMethod[] = [
  "required", "min", "max", "length",
  "trim", "includes", "startsWith", "endsWith", "pattern", "regex",
  "gt", "gte", "lt", "lte", "int", "positive", "negative", "multipleOf",
  "refine", "superRefine",
];

// Methods that carry no value at all (the row is a bare flag).
const NO_VALUE_METHODS = new Set<IValidationMethod>([
  "required", "trim", "int", "positive", "negative",
]);

// Methods whose value is a JS expression the runtime `refine` handler evaluates. They are NOT
// no-value: `refine` reads `rule.value`, so hiding the input made the field always-invalid.
const EXPRESSION_METHODS = new Set<IValidationMethod>(["refine", "superRefine"]);

const NUMERIC_METHODS = new Set<IValidationMethod>([
  "min", "max", "length", "gt", "gte", "lt", "lte", "multipleOf",
]);

export type RuleValueKind = "none" | "number" | "text" | "expression";

/** What kind of input the value box should be for this method. */
export function ruleValueKind(method: IValidationMethod): RuleValueKind {
  if (EXPRESSION_METHODS.has(method)) return "expression";
  if (NO_VALUE_METHODS.has(method)) return "none";
  if (NUMERIC_METHODS.has(method)) return "number";
  return "text";
}

export function ruleTakesValue(method: IValidationMethod): boolean {
  return ruleValueKind(method) !== "none";
}

/**
 * Whether `createZodSchema` actually enforces this method at runtime. Only `required` + `refine`
 * are wired today; every other method is offered by the editor but silently ignored, so the editor
 * flags the un-enforced ones instead of pretending they run.
 */
export function isRuleEnforced(method: IValidationMethod): boolean {
  return RUNTIME_HONORED_METHODS.includes(method);
}

/** Coerce the raw input string to the type the method expects. */
export function coerceRuleValue(method: IValidationMethod, raw: string): string | number {
  if (NUMERIC_METHODS.has(method)) {
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  }
  return raw;
}
