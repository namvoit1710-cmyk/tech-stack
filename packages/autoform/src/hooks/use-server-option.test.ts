import { describe, expect, it } from "@rstest/core";
import { buildPostBody } from "./use-server-option";

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
});
