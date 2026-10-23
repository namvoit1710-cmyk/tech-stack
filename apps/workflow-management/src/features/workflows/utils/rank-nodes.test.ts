import { expect, test } from "@rstest/core";

import { rankNodes } from "./rank-nodes";

function item(text: string, category = "", description = "") {
    return { text, type: "Node" as const, original: { name: text, category, description } as any };
}

test("verb-match ranks first", () => {
    const items = [item("Update via create hook"), item("Create Issue")];
    const r = rankNodes("create", items, { collectionsInUse: new Set<string>() });
    expect(r.map((x) => x.text)).toEqual(["Create Issue", "Update via create hook"]);
});

test("connector-in-use breaks verb ties", () => {
    const items = [item("Get Alpha", "Alpha.read"), item("Get Beta", "Beta.read")];
    const r = rankNodes("get", items, { collectionsInUse: new Set(["Beta"]) });
    expect(r.map((x) => x.text)).toEqual(["Get Beta", "Get Alpha"]);
});

test("substring-only match appears (last, stable); empty query returns items unchanged", () => {
    const items = [item("Find issue"), item("Create Issue")];
    const r = rankNodes("issue", items, { collectionsInUse: new Set<string>() });
    expect(r.map((x) => x.text)).toEqual(["Find issue", "Create Issue"]);
    expect(rankNodes("", items).map((x) => x.text)).toEqual(["Find issue", "Create Issue"]);
});
