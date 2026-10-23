import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { renderHook } from "@testing-library/react";

const useQueryNodeCatalogMock = rs.fn();

rs.mock("@/features/workflows/hooks/apis/node-pallete", () => ({
    useQueryNodeCatalog: useQueryNodeCatalogMock,
}));

const def = (id: string, name: string, category: string, kind: string, collection: string, provider = "worker") => ({
    id,
    name,
    description: "",
    provider,
    node_type: "TASK",
    category,
    kind,
    collection,
    icon: "",
    color: "",
    tags: [],
    spec_version: "1.0.0",
    visibility: "VISIBLE",
    runtime: "WORKER",
    ports: {},
});

const catalog = {
    status: "ok",
    data: {
        definitions: [
            def("b:t", "Trigger", "Core.trigger", "trigger", "Core", "builtin"),
            def("b:cond", "Condition", "Core.logic", "logic", "Core", "builtin"),
            def("b:loop", "Loop", "Core.logic", "logic", "Core", "builtin"),
            def("b:out", "Output", "Core.util", "util", "Core", "builtin"),
            def("w:c", "Jira: Create Issue", "Jira.action", "action", "Jira"),
            def("w:u", "Jira: Update Issue", "Jira.action", "action", "Jira"),
            def("w:g", "Jira: Get Issue", "Jira.read", "read", "Jira"),
            def("w:w", "Jira: Test / Whoami", "Jira.util", "util", "Jira"),
            def("m:g", "Google Search", "My Nodes.read", "read", "My Nodes", "business"),
        ],
        category_tree: {
            "Core.trigger": ["b:t"],
            "Core.logic": ["b:cond", "b:loop"],
            "Core.util": ["b:out"],
            "Jira.action": ["w:c", "w:u"],
            "Jira.read": ["w:g"],
            "Jira.util": ["w:w"],
            "My Nodes.read": ["m:g"],
        },
        categories: {
            Core: { label: "Core", order: 0 },
            "Core.trigger": { label: "Triggers", order: 1 },
            "Core.logic": { label: "Logic & Flow", order: 4 },
            "Core.util": { label: "Utilities", order: 7 },
            Jira: { label: "Jira", order: 10 },
            "Jira.action": { label: "Actions", order: 2 },
            "Jira.read": { label: "Find & Read", order: 3 },
            "Jira.util": { label: "Utilities", order: 7 },
            "My Nodes": { label: "My Nodes", order: 90 },
            "My Nodes.read": { label: "Find & Read", order: 3 },
        },
        generated_at: "",
        total: 9,
    },
};

/**
 * The shapes the happy-path fixture never produces, and which the regrouping can lose:
 *  - a Core kind no CORE_BUCKET claims ("notify" — a kind the BE adds tomorrow),
 *  - a legacy FLAT category key with no "." at all,
 *  - a business author's own collection: the BE gives order 10 to ANY bare key that is
 *    not "Core"/"My Nodes", so "Sales" lands in the connector band without being one.
 */
const extendedCatalog = {
    status: "ok",
    data: {
        definitions: [
            ...catalog.data.definitions,
            def("b:notify", "Notify", "Core.notify", "notify", "Core", "builtin"),
            def("b:flat", "Legacy Core Node", "Core", "", "Core", "builtin"),
            def("x:l", "Legacy Node", "legacy", "", "legacy"),
            def("s:r", "Sales: Report", "Sales.action", "action", "Sales", "business"),
            def("s:c", "Sales: Cleanup", "Sales.util", "util", "Sales", "business"),
        ],
        category_tree: {
            ...catalog.data.category_tree,
            "Core.notify": ["b:notify"],
            Core: ["b:flat"],
            legacy: ["x:l"],
            "Sales.action": ["s:r"],
            "Sales.util": ["s:c"],
        },
        categories: {
            ...catalog.data.categories,
            "Core.notify": { label: "Notifications", order: 8 },
            legacy: { label: "Legacy", order: 50 },
            Sales: { label: "Sales", order: 10 },
            "Sales.action": { label: "Actions", order: 2 },
            "Sales.util": { label: "Utilities", order: 7 },
        },
        generated_at: "",
        total: 14,
    },
};

const bandOf = (result: any, id: string) => result.current.bands.find((b: any) => b.id === id);

const allCollections = (result: any) => result.current.bands.flatMap((b: any) => b.collections);

/**
 * SA-1930 FIX fixture: the BE gives order 10 to Jira (a real connector), to "Finance" (a
 * business author's own collection), and to "Mixed" (a collection that happens to hold
 * BOTH a worker node and a business node) alike. Band placement must be decided by what
 * the collection's nodes ARE (`provider`) — not by that shared order.
 */
const providerCatalog = {
    status: "ok",
    data: {
        definitions: [
            def("b:t", "Trigger", "Core.trigger", "trigger", "Core", "builtin"),
            def("w:c", "Jira: Create Issue", "Jira.action", "action", "Jira", "worker"),
            def("f:r", "Finance: Report", "Finance.action", "action", "Finance", "business"),
            def("mx:w", "Mixed: Worker Node", "Mixed.action", "action", "Mixed", "worker"),
            def("mx:b", "Mixed: Business Node", "Mixed.util", "util", "Mixed", "business"),
        ],
        category_tree: {
            "Core.trigger": ["b:t"],
            "Jira.action": ["w:c"],
            "Finance.action": ["f:r"],
            "Mixed.action": ["mx:w"],
            "Mixed.util": ["mx:b"],
        },
        categories: {
            Core: { label: "Core", order: 0 },
            "Core.trigger": { label: "Triggers", order: 1 },
            Jira: { label: "Jira", order: 10 },
            "Jira.action": { label: "Actions", order: 2 },
            Finance: { label: "Finance", order: 10 },
            "Finance.action": { label: "Actions", order: 2 },
            Mixed: { label: "Mixed", order: 10 },
            "Mixed.action": { label: "Actions", order: 2 },
            "Mixed.util": { label: "Utilities", order: 7 },
        },
        generated_at: "",
        total: 5,
    },
};

describe("useGenerateWorkerMenu — band decided by provider, not order (SA-1930 fix)", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useQueryNodeCatalogMock.mockReturnValue({ data: providerCatalog, isLoading: false });
    });

    it("THE regression: a business-only collection ('Finance', order 10 — the same order the BE gives any connector) lands in mine, not connector, and carries no vendor", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const mineKeys = bandOf(result, "mine").collections.map((c: any) => c.key);
        expect(mineKeys).toContain("Finance");

        const connectorKeys = bandOf(result, "connector").collections.map((c: any) => c.key);
        expect(connectorKeys).not.toContain("Finance");

        const finance = allCollections(result).find((c: any) => c.key === "Finance");
        expect(finance.vendor).toBeUndefined();
    });

    it("a worker-backed collection (Jira, order 10) still lands in connector, with its vendor", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const connectorKeys = bandOf(result, "connector").collections.map((c: any) => c.key);
        expect(connectorKeys).toContain("Jira");

        const jira = allCollections(result).find((c: any) => c.key === "Jira");
        expect(jira.vendor).toBe("Atlassian");
    });

    it("Core (order 0, builtin) still lands in core", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const coreKeys = bandOf(result, "core").collections.map((c: any) => c.key);
        expect(coreKeys).toContain("Core");
    });

    it("a collection holding BOTH a worker node and a business node lands in connector — worker wins", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const connectorKeys = bandOf(result, "connector").collections.map((c: any) => c.key);
        expect(connectorKeys).toContain("Mixed");

        const mineKeys = bandOf(result, "mine").collections.map((c: any) => c.key);
        expect(mineKeys).not.toContain("Mixed");
    });

    it("never-worse: every definition still reachable exactly once, and a collection's count equals what it renders", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const ids = allCollections(result)
            .flatMap((c: any) => c.sections)
            .flatMap((s: any) => s.items)
            .map((i: any) => i.original?.id);
        expect(ids).toHaveLength(providerCatalog.data.total);
        expect(new Set(ids).size).toBe(providerCatalog.data.total);

        for (const collection of allCollections(result)) {
            const rendered = collection.sections.reduce((sum: number, s: any) => sum + s.count, 0);
            expect(rendered).toBe(collection.count);
        }
    });
});

/**
 * BLOCKER regression fixture: a business node whose author named their OWN collection
 * "Core" — legal today, because the node-definition builder's Collection field is free
 * text and the BE's `_sanitize_collection` only strips "." (no reserved-name guard). This
 * makes the REAL "Core" collection's providers = {builtin, business}. Priority must put
 * builtin first, or the whole "Core" collection — all 4 builtins here, all 18 in prod —
 * gets filed under "mine", and the core band (now empty) is dropped by
 * `.filter((b) => b.collections.length > 0)`.
 */
const coreHijackCatalog = {
    status: "ok",
    data: {
        definitions: [
            def("b:t", "Trigger", "Core.trigger", "trigger", "Core", "builtin"),
            def("b:cond", "Condition", "Core.logic", "logic", "Core", "builtin"),
            def("b:loop", "Loop", "Core.logic", "logic", "Core", "builtin"),
            def("b:out", "Output", "Core.util", "util", "Core", "builtin"),
            def("biz:c", "My Sneaky Node", "Core.action", "action", "Core", "business"),
        ],
        category_tree: {
            "Core.trigger": ["b:t"],
            "Core.logic": ["b:cond", "b:loop"],
            "Core.util": ["b:out"],
            "Core.action": ["biz:c"],
        },
        categories: {
            Core: { label: "Core", order: 0 },
            "Core.trigger": { label: "Triggers", order: 1 },
            "Core.logic": { label: "Logic & Flow", order: 4 },
            "Core.util": { label: "Utilities", order: 7 },
            "Core.action": { label: "Actions", order: 2 },
        },
        generated_at: "",
        total: 5,
    },
};

/**
 * A business node filed into the SAME collection key as a real connector ("Jira") — a
 * deliberate merge per the SA-1734 spec. worker must still beat business, so the whole
 * collection stays a connector shelf; the business node itself keeps `provider: "business"`
 * (PaletteNodeRow's CUSTOM badge reads the NODE's provider, never the band it renders in).
 */
const jiraCollisionCatalog = {
    status: "ok",
    data: {
        definitions: [
            def("w:c", "Jira: Create Issue", "Jira.action", "action", "Jira", "worker"),
            def("biz:j", "My Jira Helper", "Jira.util", "util", "Jira", "business"),
        ],
        category_tree: {
            "Jira.action": ["w:c"],
            "Jira.util": ["biz:j"],
        },
        categories: {
            Jira: { label: "Jira", order: 10 },
            "Jira.action": { label: "Actions", order: 2 },
            "Jira.util": { label: "Utilities", order: 7 },
        },
        generated_at: "",
        total: 2,
    },
};

describe("useGenerateWorkerMenu — BLOCKER: a business node cannot hijack the CORE band by naming its collection \"Core\" (SA-1930 fix)", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useQueryNodeCatalogMock.mockReturnValue({ data: coreHijackCatalog, isLoading: false });
    });

    it("THE regression: the core band survives, still holds the \"Core\" collection, and every builtin definition renders inside it — none stranded under MY NODES", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const coreBand = bandOf(result, "core");
        expect(coreBand).toBeDefined();
        expect(coreBand.collections.map((c: any) => c.key)).toContain("Core");

        // The hijacked "Core" collection — builtins and all — must not have been filed as "mine".
        const mineKeys = (bandOf(result, "mine")?.collections ?? []).map((c: any) => c.key);
        expect(mineKeys).not.toContain("Core");

        const builtinIds = coreHijackCatalog.data.definitions
            .filter((d) => d.provider === "builtin")
            .map((d) => d.id);
        const coreItemIds = coreBand.collections
            .flatMap((c: any) => c.sections)
            .flatMap((s: any) => s.items)
            .map((i: any) => i.original?.id);
        for (const id of builtinIds) {
            expect(coreItemIds).toContain(id);
        }
    });
});

describe("useGenerateWorkerMenu — a business node sharing a real connector's collection name (SA-1930 fix)", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useQueryNodeCatalogMock.mockReturnValue({ data: jiraCollisionCatalog, isLoading: false });
    });

    it("a business node filed into \"Jira\" stays inside the connector band (worker beats business), and keeps provider \"business\" — the CUSTOM badge follows the NODE, not the band", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const connectorKeys = bandOf(result, "connector").collections.map((c: any) => c.key);
        expect(connectorKeys).toContain("Jira");
        const mineKeys = (bandOf(result, "mine")?.collections ?? []).map((c: any) => c.key);
        expect(mineKeys).not.toContain("Jira");

        const jira = allCollections(result).find((c: any) => c.key === "Jira");
        const bizItem = jira.sections
            .flatMap((s: any) => s.items)
            .find((i: any) => i.original?.id === "biz:j");
        expect(bizItem).toBeDefined();
        expect(bizItem.original.provider).toBe("business");
    });
});

describe("useGenerateWorkerMenu — bands (SA-1930)", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useQueryNodeCatalogMock.mockReturnValue({ data: catalog, isLoading: false });
    });

    it("splits the catalog into core / connector / mine, in that order", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        expect(result.current.bands.map((b) => b.id)).toEqual(["core", "connector", "mine"]);
        expect(result.current.total).toBe(9);
    });

    it("folds Core into buckets, keeping Triggers and dropping empty buckets", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const core = bandOf(result, "core").collections[0];
        expect(core.sections.map((s: any) => s.key)).toEqual(["triggers", "flow-logic", "data-files"]);

        const triggers = core.sections.find((s: any) => s.key === "triggers");
        expect(triggers.count).toBe(1);
        expect(triggers.labelKey).toBe("palette_kind_trigger");

        const flow = core.sections.find((s: any) => s.key === "flow-logic");
        expect(flow.count).toBe(2);
        expect(flow.preview).toBe("Condition · Loop");

        // "Data & Files" absorbs util (Output). "approvals-forms" has no member -> dropped.
        expect(core.sections.find((s: any) => s.key === "data-files").count).toBe(1);
    });

    it("builds a connector with kind sections, a vendor and a count", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const jira = bandOf(result, "connector").collections[0];
        expect(jira.label).toBe("Jira");
        expect(jira.vendor).toBe("Atlassian");
        expect(jira.count).toBe(4);

        expect(jira.sections.map((s: any) => s.label)).toEqual(["Actions", "Find & Read", "Utilities"]);
        expect(jira.sections.find((s: any) => s.kind === "action").items.map((i: any) => i.text)).toEqual([
            "Jira: Create Issue",
            "Jira: Update Issue",
        ]);
    });

    it("gives a connector with no trigger nodes NO trigger section at all", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // Live data killed the "a connector with no triggers is a fact worth telling"
        // rationale: NO connector ever has a trigger, so the synthesised section was
        // wallpaper, not signal. It must not appear at all.
        const jira = bandOf(result, "connector").collections[0];
        expect(jira.sections.every((s: any) => s.kind !== "trigger")).toBe(true);

        // The synthesised section always contributed 0 — removing it must not move the
        // count away from the real items (2 action + 1 read + 1 util).
        expect(jira.count).toBe(4);
    });

    it("gives EVERY kind section an i18n key — the BE's kind labels are English-only", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // A connector's sections take `label` from the BE's categories, whose _KIND_LABEL
        // map is English-only. Core buckets are translated; without this, a jp user reads
        // "Actions"/"Find & Read"/"Utilities" in English right next to them.
        const jira = bandOf(result, "connector").collections[0];
        expect(jira.sections.map((s: any) => s.labelKey)).toEqual([
            "palette_kind_action",
            "palette_kind_read",
            "palette_kind_util",
        ]);

        // …and the same holds for the "mine" band, whose leaf subtitle renders the label.
        const mine = bandOf(result, "mine").collections[0];
        expect(mine.sections.map((s: any) => s.labelKey)).toEqual(["palette_kind_read"]);
    });

    it("gives a Core bucket a human label, not its machine id", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const core = bandOf(result, "core").collections[0];
        const flow = core.sections.find((s: any) => s.key === "flow-logic");
        expect(flow.label).toBe("Flow & Logic");
        expect(core.sections.find((s: any) => s.key === "triggers").label).toBe("Triggers");
    });

    it("never-worse: every definition is reachable through exactly one band", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const ids = result.current.bands
            .flatMap((b) => b.collections)
            .flatMap((c) => c.sections)
            .flatMap((s) => s.items)
            .map((i) => i.original?.id);

        expect(new Set(ids).size).toBe(9);
        expect(ids).toHaveLength(9);
    });
});

describe("useGenerateWorkerMenu — never-worse under regrouping (SA-1930)", () => {
    beforeEach(async () => {
        await Promise.resolve(rs.resetModules());
        await Promise.resolve(rs.clearAllMocks());
        useQueryNodeCatalogMock.mockReturnValue({ data: extendedCatalog, isLoading: false });
    });

    it("keeps a Core kind that NO bucket claims, instead of dropping it", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // "notify" is in no CORE_BUCKET. The bucket reassignment must not swallow it.
        const core = bandOf(result, "core").collections[0];
        const orphan = core.sections.find((s: any) => s.key === "Core.notify");
        expect(orphan).toBeDefined();
        expect(orphan.count).toBe(1);
        expect(orphan.items.map((i: any) => i.text)).toEqual(["Notify"]);
    });

    it("leaves an UNKNOWN kind without an i18n key, so it honestly falls back to the BE's label", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // "notify" is a kind we have no translation for. Inventing a key would render the
        // raw key string; falling back to the BE's label is the only honest option.
        const core = bandOf(result, "core").collections[0];
        const orphan = core.sections.find((s: any) => s.key === "Core.notify");
        expect(orphan.labelKey).toBeUndefined();
        expect(orphan.label).toBe("Notifications");
    });

    it("keeps a flat CORE key — the case that would vanish all of Core", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // A bare "Core" key has kind "", which no bucket claims. If the bucket reassignment
        // replaces `sections` wholesale, this node — and its count — leave the palette.
        const core = bandOf(result, "core").collections[0];
        const flat = core.sections.find((s: any) => s.key === "Core");
        expect(flat).toBeDefined();
        expect(flat.items.map((i: any) => i.text)).toEqual(["Legacy Core Node"]);

        // …and the buckets are still there next to it.
        expect(core.sections.some((s: any) => s.key === "flow-logic")).toBe(true);
    });

    it("keeps a legacy FLAT category key (no dot) and its node", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const legacy = allCollections(result).find((c: any) => c.key === "legacy");
        expect(legacy).toBeDefined();
        expect(legacy.count).toBe(1);
        expect(legacy.sections.flatMap((s: any) => s.items).map((i: any) => i.text)).toEqual(["Legacy Node"]);
    });

    it("does not treat a business author's own collection as a connector", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // "Sales" is order 10 (so: connector BAND) but ships no worker nodes. Giving it a
        // vendor subtitle like a real connector's would be nonsense — a business author who
        // names their collection "Jira" must not get an "Atlassian" subtitle on their own nodes.
        const sales = allCollections(result).find((c: any) => c.key === "Sales");
        expect(sales.vendor).toBeUndefined();

        // …while a real connector still gets its vendor subtitle.
        const jira = allCollections(result).find((c: any) => c.key === "Jira");
        expect(jira.vendor).toBe("Atlassian");
    });

    it("invariant: a collection's count equals what it actually renders", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        // A count summed from a DIFFERENT array than the one rendered is a header that lies.
        for (const collection of allCollections(result)) {
            const rendered = collection.sections.reduce((sum: number, s: any) => sum + s.count, 0);
            expect(rendered).toBe(collection.count);
        }
    });

    it("invariant: every definition survives regrouping, exactly once", async () => {
        const { default: useGenerateWorkerMenu } = await import("./use-merge-nodes");
        const { result } = renderHook(() => useGenerateWorkerMenu());

        const ids = allCollections(result)
            .flatMap((c: any) => c.sections)
            .flatMap((s: any) => s.items)
            .map((i: any) => i.original?.id);

        expect(ids).toHaveLength(extendedCatalog.data.total);
        expect(new Set(ids).size).toBe(extendedCatalog.data.total);
    });
});
