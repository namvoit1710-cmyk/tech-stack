import { describe, expect, it } from "@rstest/core";
import { convertKeys, snakeToCamel } from "./parse-string";

describe("snakeToCamel", () => {
    it("converts a snake_case token to camelCase (happy)", () => {
        expect(snakeToCamel("hello_world")).toBe("helloWorld");
    });

    it("converts multiple underscores", () => {
        expect(snakeToCamel("a_b_c")).toBe("aBC");
    });

    it("leaves an already-camel/plain string unchanged (edge)", () => {
        expect(snakeToCamel("alreadyCamel")).toBe("alreadyCamel");
        expect(snakeToCamel("plain")).toBe("plain");
    });

    it("only upcases lowercase letters after underscore — leaves _<digit>/_<upper> as-is (boundary)", () => {
        // regex is /_([a-z])/ so _1 and _A do not match
        expect(snakeToCamel("field_1")).toBe("field_1");
        expect(snakeToCamel("field_A")).toBe("field_A");
    });

    it("handles empty string (edge)", () => {
        expect(snakeToCamel("")).toBe("");
    });
});

describe("convertKeys", () => {
    it("recursively camelCases object keys (happy)", () => {
        expect(convertKeys({ first_name: "a", last_name: "b" })).toEqual({
            firstName: "a",
            lastName: "b",
        });
    });

    it("recurses into nested objects and arrays", () => {
        expect(
            convertKeys({
                outer_key: { inner_key: 1 },
                list_items: [{ item_id: 1 }, { item_id: 2 }],
            }),
        ).toEqual({
            outerKey: { innerKey: 1 },
            listItems: [{ itemId: 1 }, { itemId: 2 }],
        });
    });

    it("maps arrays element-wise (happy)", () => {
        expect(convertKeys([{ a_b: 1 }, { c_d: 2 }])).toEqual([{ aB: 1 }, { cD: 2 }]);
    });

    it("returns primitives unchanged (edge)", () => {
        expect(convertKeys(5)).toBe(5);
        expect(convertKeys("str")).toBe("str");
        expect(convertKeys(true)).toBe(true);
    });

    it("returns null unchanged — null is not treated as object (boundary)", () => {
        expect(convertKeys(null)).toBeNull();
    });

    it("preserves nested null values", () => {
        expect(convertKeys({ some_key: null })).toEqual({ someKey: null });
    });
});
