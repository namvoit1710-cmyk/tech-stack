import { describe, expect, it } from "@rstest/core";
import {
    buildPageArgs,
    buildPostBody,
    DEFAULT_SEARCH_ARG_KEY,
    GATEWAY_INVOKE_DATAPATH,
    GATEWAY_INVOKE_ENDPOINT,
    GATEWAY_INVOKE_SERVICE,
} from "./gateway-invoke";

describe("gateway-invoke constants (SA-2184 — one transport, one helper)", () => {
    it("exposes the invoke_app_function endpoint under the deployed worker type", () => {
        expect(GATEWAY_INVOKE_SERVICE).toBe("workflow");
        expect(GATEWAY_INVOKE_ENDPOINT).toBe("workers/gateway/functions/invoke_app_function/invoke");
        expect(GATEWAY_INVOKE_DATAPATH).toBe("data.result.body");
        expect(DEFAULT_SEARCH_ARG_KEY).toBe("$search");
    });
});

describe("buildPostBody (SA-1551 serverOptions POST args)", () => {
    it("returns the static body unchanged when there are no dependency values", () => {
        const body = { params: { function_id: "fn-1", args: { a: 1 } } };
        expect(buildPostBody(body, {})).toEqual(body);
        expect(buildPostBody(body, undefined)).toEqual(body);
    });

    it("returns an empty object when neither body nor deps are given", () => {
        expect(buildPostBody(undefined, undefined)).toEqual({});
    });

    it("merges dependency values under params.args", () => {
        const body = { params: { function_id: "list_teams" } };
        const out = buildPostBody(body, { credential_ref: "cred-1" });
        expect(out).toEqual({
            params: { function_id: "list_teams", args: { credential_ref: "cred-1" } },
        });
    });

    it("preserves static args already in the body and adds dependency values", () => {
        const body = { params: { function_id: "list_channels", args: { locale: "en" } } };
        const out = buildPostBody(body, { credential_ref: "c1", team_id: "t1" });
        expect(out.params.args).toEqual({ locale: "en", credential_ref: "c1", team_id: "t1" });
    });

    it("dependency values override colliding static args", () => {
        const body = { params: { args: { team_id: "old" } } };
        const out = buildPostBody(body, { team_id: "new" });
        expect(out.params.args.team_id).toBe("new");
    });

    it("creates params.args when the body has no params at all", () => {
        const out = buildPostBody({}, { credential_ref: "c9" });
        expect(out).toEqual({ params: { args: { credential_ref: "c9" } } });
    });

    it("does not mutate the input body", () => {
        const body = { params: { function_id: "fn", args: { a: 1 } } };
        const snapshot = JSON.parse(JSON.stringify(body));
        buildPostBody(body, { credential_ref: "c1" });
        expect(body).toEqual(snapshot);
    });

    it("merges search + deps together (Combobox gateway typeahead args)", () => {
        const body = { params: { app: "file-service", function: "country_list", args: {} } };
        const out = buildPostBody(body, { region_id: "eu", [DEFAULT_SEARCH_ARG_KEY]: "fra" });
        expect(out.params.args).toEqual({ region_id: "eu", $search: "fra" });
    });
});

// ─── buildPageArgs (SA-2192 — page args under the upstream's own names) ────────

describe("buildPageArgs", () => {
    const page = { pageIndex: 2, pageSize: 10 };

    it("defaults to the OData names, so an untouched config is unchanged", () => {
        expect(buildPageArgs(page, undefined, true)).toEqual({ $top: 10, $skip: 20, $count: true });
    });

    it("uses the upstream's own names when bound", () => {
        expect(buildPageArgs(page, { top: "limit", skip: "offset", count: "with_total" }, "true")).toEqual({
            limit: 10,
            offset: 20,
            with_total: "true",
        });
    });

    it("falls back per-slot, so binding one name keeps the others on the default", () => {
        expect(buildPageArgs(page, { skip: "offset" }, true)).toEqual({ $top: 10, offset: 20, $count: true });
    });

    it("an empty name omits that arg — an upstream with no count param gets none", () => {
        expect(buildPageArgs(page, { count: "" }, true)).toEqual({ $top: 10, $skip: 20 });
    });

    it("passes countValue through verbatim (gateway needs the string, GET the boolean)", () => {
        expect(buildPageArgs(page, undefined, "true").$count).toBe("true");
        expect(buildPageArgs(page, undefined, true).$count).toBe(true);
    });

    it("skip is derived from pageIndex * pageSize", () => {
        expect(buildPageArgs({ pageIndex: 0, pageSize: 25 }, undefined, true)).toMatchObject({ $top: 25, $skip: 0 });
        expect(buildPageArgs({ pageIndex: 3, pageSize: 25 }, undefined, true)).toMatchObject({ $skip: 75 });
    });
});
