import { EShowWhenOperator } from "../../../types/condition";
import type { OutputType } from "../../../types/schema";

/**
 * How the condition VALUE input should behave, decided by the operator first, then the watched
 * field's outputType. This mirrors `use-check-condittion.ts` exactly:
 *  - exists/notExists compare only truthiness → no value at all;
 *  - `in` does `conditionValue.includes(watchValue)` → the value MUST be an array;
 *  - the four comparators wrap both sides in `Number()` → always a number box;
 *  - everything else (equals/notEquals/contains) is a strict compare, so the value has to be the
 *    SAME primitive type as the watched field (5 === "5" is false), driven by its outputType.
 */
export type ConditionValueKind = "none" | "list" | "number" | "boolean" | "text";

const NO_VALUE_OPERATORS = new Set<EShowWhenOperator>([
  EShowWhenOperator.Exists,
  EShowWhenOperator.NotExists,
]);

const NUMERIC_OPERATORS = new Set<EShowWhenOperator>([
  EShowWhenOperator.GreaterThan,
  EShowWhenOperator.LessThan,
  EShowWhenOperator.GreaterOrEqual,
  EShowWhenOperator.LessOrEqual,
]);

export function conditionValueKind(
  operator: EShowWhenOperator | undefined,
  outputType: OutputType | undefined,
): ConditionValueKind {
  if (operator && NO_VALUE_OPERATORS.has(operator)) return "none";
  if (operator === EShowWhenOperator.In) return "list";
  if (operator && NUMERIC_OPERATORS.has(operator)) return "number";
  if (outputType === "number") return "number";
  if (outputType === "boolean") return "boolean";
  return "text";
}

/** Coerce one raw string to the primitive the watched field holds, so strict compares fire. */
function coerceScalar(outputType: OutputType | undefined, raw: string): unknown {
  if (outputType === "number") {
    const n = Number(raw);
    return raw === "" || !Number.isFinite(n) ? "" : n;
  }
  if (outputType === "boolean") return raw === "true";
  return raw;
}

/**
 * Turn the editor's raw input into the exact JS value the runtime comparator expects.
 * `raw` is a string for scalar kinds and a string[] for the `in` list.
 */
export function coerceConditionValue(
  operator: EShowWhenOperator | undefined,
  outputType: OutputType | undefined,
  raw: string | string[],
): unknown {
  const kind = conditionValueKind(operator, outputType);
  if (kind === "none") return undefined;
  if (kind === "list") {
    const arr = Array.isArray(raw)
      ? raw
      : String(raw).split(",").map((s) => s.trim()).filter(Boolean);
    return arr.map((v) => coerceScalar(outputType, v));
  }
  const single = Array.isArray(raw) ? (raw[0] ?? "") : raw;
  if (kind === "number") {
    const n = Number(single);
    return single === "" || !Number.isFinite(n) ? "" : n;
  }
  if (kind === "boolean") return single === "true";
  return single;
}
