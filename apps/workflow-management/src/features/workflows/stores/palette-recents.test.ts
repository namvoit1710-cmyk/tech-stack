import { beforeEach, describe, expect, it } from "@rstest/core";
import { RECENTS_CAP, RECENTS_KEY, chipItems, readRecents, recordRecent, suggestChips } from "./palette-recents";

const item = (id: string, text: string, category: string, kind: string, collection: string) => ({
    text,
    type: "Node" as const,
    original: { id, name: text, category, kind, collection } as any,
});

// A tenant with Core + a 4-node Jira connector + a 1-node Slack connector.
const catalog = [
    item("b:cond", "Condition", "Core.logic", "logic", "Core"),
    item("b:trig", "Trigger", "Core.trigger", "trigger", "Core"),
    item("w:c", "Create Issue", "Jira.action", "action", "Jira"),
    item("w:u", "Update Issue", "Jira.action", "action", "Jira"),
    item("w:g", "Get Issue", "Jira.read", "read", "Jira"),
    item("w:s", "Search (JQL)", "Jira.read", "read", "Jira"),
    item("s:p", "Post Message", "Slack.action", "action", "Slack"),
];

describe("palette-recents", () => {
    beforeEach(() => {
        window.localStorage.removeItem(RECENTS_KEY);
    });

    it("records most-recent-first and dedups", () => {
        recordRecent("w:c");
        recordRecent("w:g");
        recordRecent("w:c");
        expect(readRecents()).toEqual(["w:c", "w:g"]);
    });

    it("caps the history", () => {
        for (let i = 0; i < RECENTS_CAP + 4; i++) recordRecent(`n:${i}`);
        expect(readRecents()).toHaveLength(RECENTS_CAP);
        expect(readRecents()[0]).toBe(`n:${RECENTS_CAP + 3}`);
    });

    it("survives an unreadable store", () => {
        window.localStorage.setItem(RECENTS_KEY, "not json");
        expect(readRecents()).toEqual([]);
    });

    it("suggests the biggest connector's first action + first read, then a Core logic node", () => {
        expect(suggestChips(catalog).map((i) => i.text)).toEqual(["Create Issue", "Get Issue", "Condition"]);
    });

    it("shows recents first, padded with suggestions up to 3", () => {
        recordRecent("s:p");
        expect(chipItems(catalog, readRecents()).map((i) => i.text)).toEqual([
            "Post Message",
            "Create Issue",
            "Get Issue",
        ]);
    });

    it("falls back entirely to suggestions for a fresh user", () => {
        expect(chipItems(catalog, readRecents()).map((i) => i.text)).toEqual([
            "Create Issue",
            "Get Issue",
            "Condition",
        ]);
    });

    it("ignores a recent id that is no longer in the catalog", () => {
        recordRecent("w:deleted");
        expect(chipItems(catalog, readRecents()).map((i) => i.text)).toEqual([
            "Create Issue",
            "Get Issue",
            "Condition",
        ]);
    });
});
