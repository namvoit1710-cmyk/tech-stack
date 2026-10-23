import { describe, expect, it } from "@rstest/core";
import type { SocketDataChunkPayload } from "../types/socket-event";
import {
    getSocketEventName,
    getSocketEventPayload,
    normalizeSocketPayload,
} from "./socket-data-chunk";

describe("normalizeSocketPayload", () => {
    it("parses a JSON string envelope and returns nested data (happy)", () => {
        const msg = JSON.stringify({ data: { event_type: "workflow:node.completed" } });
        expect(normalizeSocketPayload(msg)).toEqual({ event_type: "workflow:node.completed" });
    });

    it("returns the envelope itself when data is not a record (edge)", () => {
        const envelope = { event_type: "run.status", data: "scalar" };
        // nested parse of "scalar" is not valid JSON record -> null -> falls back to envelope
        expect(normalizeSocketPayload(envelope)).toBe(envelope);
    });

    it("accepts an object envelope directly (happy)", () => {
        const envelope = { data: { a: 1 } };
        expect(normalizeSocketPayload(envelope)).toEqual({ a: 1 });
    });

    it("returns null for an unparseable JSON string (failure)", () => {
        expect(normalizeSocketPayload("{not json")).toBeNull();
    });

    it("returns null for a JSON string that parses to a non-record (invalid)", () => {
        expect(normalizeSocketPayload("42")).toBeNull();
        expect(normalizeSocketPayload("null")).toBeNull();
    });

    it("returns null for non-object primitives (invalid)", () => {
        expect(normalizeSocketPayload(123)).toBeNull();
        expect(normalizeSocketPayload(null)).toBeNull();
        expect(normalizeSocketPayload(undefined)).toBeNull();
    });
});

describe("getSocketEventName", () => {
    it("prefers nested data.event_type and strips the workflow: prefix (happy)", () => {
        const payload = { data: { event_type: "workflow:node.completed" } } as SocketDataChunkPayload;
        expect(getSocketEventName(payload)).toBe("node.completed");
    });

    it("falls back through the priority chain to payload.event (edge)", () => {
        const payload = { event: "run.status" } as SocketDataChunkPayload;
        expect(getSocketEventName(payload)).toBe("run.status");
    });

    it("respects source priority: nested event_type wins over top-level event_type", () => {
        const payload = {
            data: { event_type: "nested.win" },
            event_type: "top.lose",
        } as SocketDataChunkPayload;
        expect(getSocketEventName(payload)).toBe("nested.win");
    });

    it("returns empty string when no event field is a non-empty string (invalid/boundary)", () => {
        expect(getSocketEventName({} as SocketDataChunkPayload)).toBe("");
        expect(getSocketEventName({ event_type: "" } as SocketDataChunkPayload)).toBe("");
    });

    it("only strips a leading workflow: prefix, not mid-string (boundary)", () => {
        const payload = { event: "child:workflow:foo" } as SocketDataChunkPayload;
        expect(getSocketEventName(payload)).toBe("child:workflow:foo");
    });
});

describe("getSocketEventPayload", () => {
    it("returns nested data record when present (happy)", () => {
        const payload = { data: { x: 1 } } as SocketDataChunkPayload;
        expect(getSocketEventPayload(payload)).toEqual({ x: 1 });
    });

    it("parses a JSON-string data field (happy)", () => {
        const payload = { data: JSON.stringify({ y: 2 }) } as SocketDataChunkPayload;
        expect(getSocketEventPayload(payload)).toEqual({ y: 2 });
    });

    it("falls back to the payload itself when data is not a record (edge)", () => {
        const payload = { event: "x", data: 5 } as SocketDataChunkPayload;
        expect(getSocketEventPayload(payload)).toBe(payload);
    });
});
