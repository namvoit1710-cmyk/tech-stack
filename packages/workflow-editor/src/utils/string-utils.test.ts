import { describe, expect, it } from "@rstest/core";

import { pascalToKebabCase } from "./string-utils";

// SOURCE: string-utils.ts
//   pascalToKebabCase(str):
//     if (!str) return "";
//     .replace(/([a-z0-9])([A-Z])/g, "$1-$2")   // lower/digit -> Upper boundary
//     .replace(/([A-Z])([A-Z][a-z])/g, "$1-$2") // acronym -> Word boundary
//     .toLowerCase();

describe("pascalToKebabCase", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("converts a simple PascalCase word", () => {
        expect(pascalToKebabCase("HelloWorld")).toBe("hello-world");
    });

    it("converts three PascalCase segments", () => {
        expect(pascalToKebabCase("MyHttpServer")).toBe("my-http-server");
    });

    // ── Edge: acronym handling (second replace) ──────────────────────────────
    it("splits an acronym before a following capitalized word", () => {
        // "XMLParser": X-M-L run then "Parser" -> "XML-Parser" -> "xml-parser"
        expect(pascalToKebabCase("XMLParser")).toBe("xml-parser");
    });

    it("keeps a pure acronym lowercased with no internal dashes", () => {
        // "HTTP": no lower/digit-Upper boundary, no [A-Z][A-Z][a-z] match
        expect(pascalToKebabCase("HTTP")).toBe("http");
    });

    // ── Edge: digits ──────────────────────────────────────────────────────────
    it("splits at a digit-to-uppercase boundary", () => {
        expect(pascalToKebabCase("Node1Type")).toBe("node1-type");
    });

    // ── Invalid / empty input ────────────────────────────────────────────────
    it("returns empty string for empty input", () => {
        expect(pascalToKebabCase("")).toBe("");
    });

    it("returns empty string for a whitespace-free falsy guard only on empty", () => {
        // guard is `!str`; a single space is truthy and passes through unchanged
        expect(pascalToKebabCase(" ")).toBe(" ");
    });

    // ── Boundary: already lowercase / single char ─────────────────────────────
    it("leaves an all-lowercase string unchanged", () => {
        expect(pascalToKebabCase("hello")).toBe("hello");
    });

    it("lowercases a single uppercase char with no boundary", () => {
        expect(pascalToKebabCase("A")).toBe("a");
    });
});
