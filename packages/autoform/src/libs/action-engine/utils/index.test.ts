import { describe, expect, it, rs } from "@rstest/core";
import { evalExpr, getByPath, interpolate, interpolateDeep, resolveBody } from "./index";
import type { EngineContext } from "../types";

// Build a minimal EngineContext; only the fields the pure fns read are populated.
const makeCtx = (partial: Partial<EngineContext> = {}): EngineContext =>
    ({
        formValues: {},
        payload: {},
        methods: {} as EngineContext["methods"],
        ui: {} as EngineContext["ui"],
        ...partial,
    }) as EngineContext;

describe("evalExpr", () => {
    // happy — evaluates an expression against context vars
    it("evaluates an expression referencing formValues", () => {
        const ctx = makeCtx({ formValues: { a: 2 } });
        expect(evalExpr("formValues.a * 3", ctx)).toBe(6);
    });

    // happy — Math is available in scope
    it("exposes Math to the expression", () => {
        expect(evalExpr("Math.max(1, 5, 3)", makeCtx())).toBe(5);
    });

    // happy — reads lastResult / rowData / payload
    it("reads lastResult, rowData and payload", () => {
        const ctx = makeCtx({ lastResult: 10, rowData: { x: 1 }, payload: { p: "v" } });
        expect(evalExpr("lastResult + rowData.x", ctx)).toBe(11);
        expect(evalExpr("payload.p", ctx)).toBe("v");
    });

    // failure path — a throwing/invalid expression returns undefined (caught)
    it("returns undefined when the expression throws", () => {
        const warn = rs.spyOn(console, "warn").mockImplementation(() => {});
        expect(evalExpr("nope.deep.value", makeCtx())).toBeUndefined();
        expect(evalExpr("(", makeCtx())).toBeUndefined(); // syntax error at Function construction
        warn.mockRestore();
    });
});

describe("interpolate", () => {
    // happy — substitutes ${...} expressions
    it("substitutes template expressions", () => {
        const ctx = makeCtx({ formValues: { name: "Ada" } });
        expect(interpolate("Hello ${formValues.name}!", ctx)).toBe("Hello Ada!");
    });

    // edge — undefined/null result renders empty string
    it("renders empty string for undefined or null results", () => {
        const ctx = makeCtx({ formValues: { a: null } });
        expect(interpolate("x=${formValues.missing}", ctx)).toBe("x=");
        expect(interpolate("y=${formValues.a}", ctx)).toBe("y=");
    });

    // edge — no placeholders returns the template unchanged
    it("returns a template with no placeholders unchanged", () => {
        expect(interpolate("static text", makeCtx())).toBe("static text");
    });

    // edge — number result is stringified
    it("stringifies non-string results", () => {
        const ctx = makeCtx({ formValues: { n: 42 } });
        expect(interpolate("n=${formValues.n}", ctx)).toBe("n=42");
    });
});

describe("interpolateDeep", () => {
    // happy — $ref: returns the raw evaluated value (not stringified)
    it("resolves a $ref: string to the raw value", () => {
        const ctx = makeCtx({ formValues: { obj: { a: 1 } } });
        expect(interpolateDeep("$ref:formValues.obj", ctx)).toEqual({ a: 1 });
    });

    // happy — plain strings are interpolated
    it("interpolates plain string values", () => {
        const ctx = makeCtx({ formValues: { v: "z" } });
        expect(interpolateDeep("val-${formValues.v}", ctx)).toBe("val-z");
    });

    // happy — recurses arrays and objects
    it("recurses through arrays and objects", () => {
        const ctx = makeCtx({ formValues: { v: "x" } });
        const input = { list: ["${formValues.v}", "static"], nested: { a: "${formValues.v}" } };
        expect(interpolateDeep(input, ctx)).toEqual({
            list: ["x", "static"],
            nested: { a: "x" },
        });
    });

    // edge — non-string primitives pass through unchanged
    it("returns non-string primitives and null unchanged", () => {
        const ctx = makeCtx();
        expect(interpolateDeep(5, ctx)).toBe(5);
        expect(interpolateDeep(true, ctx)).toBe(true);
        expect(interpolateDeep(null, ctx)).toBeNull();
    });
});

describe("getByPath", () => {
    // happy — deep path
    it("reads a nested value by dot path", () => {
        expect(getByPath({ a: { b: { c: 7 } } }, "a.b.c")).toBe(7);
    });

    // edge — missing intermediate returns undefined (optional-chaining reduce)
    it("returns undefined when a path segment is missing", () => {
        expect(getByPath({ a: {} }, "a.b.c")).toBeUndefined();
        expect(getByPath(undefined, "a.b")).toBeUndefined();
    });

    // boundary — single-segment path
    it("reads a single-segment path", () => {
        expect(getByPath({ a: 1 }, "a")).toBe(1);
    });
});

describe("resolveBody", () => {
    // edge — undefined body returns undefined
    it("returns undefined for an undefined body", () => {
        expect(resolveBody(undefined, makeCtx())).toBeUndefined();
    });

    // happy — "formValues" / "rowData" markers return context slices
    it('resolves the "formValues" and "rowData" markers', () => {
        const ctx = makeCtx({ formValues: { f: 1 }, rowData: { r: 2 } });
        expect(resolveBody("formValues", ctx)).toEqual({ f: 1 });
        expect(resolveBody("rowData", ctx)).toEqual({ r: 2 });
    });

    // happy — object body is deep-interpolated
    it("deep-interpolates an object body", () => {
        const ctx = makeCtx({ formValues: { id: "abc" } });
        expect(resolveBody({ crId: "${formValues.id}", const: "k" }, ctx)).toEqual({
            crId: "abc",
            const: "k",
        });
    });
});
