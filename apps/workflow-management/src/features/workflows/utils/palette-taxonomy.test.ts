import { describe, expect, it } from "@rstest/core";
import type { NodeKind } from "@/features/workflows/types/node-pallete";
import {
    CORE_BUCKETS,
    DEFAULT_TINT,
    KIND_LABEL_KEY,
    TRIGGERS_LABEL_KEY,
    VENDOR_BLURB,
    bandOf,
    bandOfCollection,
    collectionOf,
    kindLabelKey,
    kindOf,
    nodeTileStyle,
    previewNames,
    stripCollectionPrefix,
    tintOf,
    vendorOf,
} from "./palette-taxonomy";

// SA-1734: a compile-time witness over the closed `NodeKind` union (mirrors the BE's
// NodeKind(str, Enum)), not a hand-typed string list. If a kind is ADDED to the union
// and this witness is not updated to match, TypeScript refuses to compile this file
// (a missing or excess property on a `Record<NodeKind, true>` is a type error) — so a
// new kind breaks the BUILD, not just a runtime assertion, until it is bucketed here too.
const KIND_WITNESS: Record<NodeKind, true> = {
    trigger: true,
    action: true,
    read: true,
    logic: true,
    human: true,
    transform: true,
    util: true,
};
// Object.keys already returns string[] — no cast needed, and the exhaustiveness
// guarantee lives entirely in KIND_WITNESS's Record<NodeKind, true> shape above.
const VALID_KINDS: string[] = Object.keys(KIND_WITNESS);

const item = (text: string, category: string, kind?: string) => ({
    text,
    type: "Node" as const,
    original: { id: text, name: text, category, kind } as any,
});

describe("bandOf", () => {
    it("maps the BE collection order onto an origin band", () => {
        expect(bandOf(0)).toBe("core");        // Core
        expect(bandOf(10)).toBe("connector");  // any connector
        expect(bandOf(90)).toBe("mine");       // "My Nodes"
    });
});

describe("bandOfCollection — SA-1930: band decided by what a collection's nodes ARE, not by its name/order", () => {
    it("routes a collection whose nodes are worker-backed to connector", () => {
        expect(bandOfCollection(new Set(["worker"]), 10)).toBe("connector");
    });

    it("routes a collection whose nodes are business-authored to mine — THE regression: a business author's own collection (order 10, same as any connector) must not be filed under Apps & Connectors", () => {
        expect(bandOfCollection(new Set(["business"]), 10)).toBe("mine");
    });

    it("routes a collection whose nodes are builtin to core", () => {
        expect(bandOfCollection(new Set(["builtin"]), 0)).toBe("core");
    });

    it("worker wins when a collection holds both a worker node and a business node", () => {
        expect(bandOfCollection(new Set(["worker", "business"]), 10)).toBe("connector");
    });

    it("falls back to bandOf(order) for an empty or unrecognised provider set", () => {
        expect(bandOfCollection(new Set(), 10)).toBe(bandOf(10));
        expect(bandOfCollection(new Set(["unknown-provider" as any]), 90)).toBe(bandOf(90));
    });

    it("BLOCKER regression: builtin wins over business — a user can name their OWN collection \"Core\" (the node-definition builder's Collection field is free text; the BE's _sanitize_collection only strips \".\", no reserved-name guard), and if business won this check the real Core collection's providers ({builtin, business}) would route to \"mine\", stranding every builtin", () => {
        expect(bandOfCollection(new Set(["builtin", "business"]), 0)).toBe("core");
    });

    it("builtin wins over worker too, for the same reason — an unrecognised/mixed combination must never bump a real builtin collection out of core", () => {
        expect(bandOfCollection(new Set(["builtin", "worker"]), 0)).toBe("core");
        expect(bandOfCollection(new Set(["builtin", "worker", "business"]), 0)).toBe("core");
    });
});

describe("CORE_BUCKETS", () => {
    it("covers every kind in the vocabulary exactly once (nothing can fall out of Core)", () => {
        const bucketed = CORE_BUCKETS.flatMap((b) => b.kinds);
        expect(new Set(bucketed).size).toBe(bucketed.length); // disjoint
        expect([...bucketed].sort()).toEqual([...VALID_KINDS].sort()); // total
    });

    it("keeps Triggers as its own bucket (the mockup omits it; a canvas with a trigger would lose the Trigger node)", () => {
        expect(CORE_BUCKETS[0]?.id).toBe("triggers");
        expect(CORE_BUCKETS[0]?.kinds).toEqual(["trigger"]);
        expect(CORE_BUCKETS[0]?.labelKey).toBe(TRIGGERS_LABEL_KEY);
    });

    it("carries a human label, so a consumer falling back to it never renders a machine id", () => {
        expect(CORE_BUCKETS.map((b) => b.label)).toEqual([
            "Triggers",
            "Flow & Logic",
            "Approvals & Forms",
            "Data & Files",
        ]);
        for (const bucket of CORE_BUCKETS) {
            expect(bucket.label).not.toBe(bucket.id);
        }
    });
});

describe("KIND_LABEL_KEY — a kind section's label is i18n, never the BE's English", () => {
    it("carries a key for every kind in the BE vocabulary", () => {
        // The BE's _KIND_LABEL map is English-only ("Actions", "Find & Read", …). A
        // connector's kind sections take their label from it, so without a key here a
        // jp user reads English headers next to correctly-translated Core buckets.
        expect(Object.keys(KIND_LABEL_KEY).sort()).toEqual([...VALID_KINDS].sort());
    });

    it("has ONE key for the trigger label — the Core bucket and a connector's TRIGGERS section share it", () => {
        expect(TRIGGERS_LABEL_KEY).toBe(KIND_LABEL_KEY.trigger);
        expect(CORE_BUCKETS[0]?.labelKey).toBe(KIND_LABEL_KEY.trigger);
    });

    it("returns nothing for a kind it does not know, so the BE's label stays the honest fallback", () => {
        expect(kindLabelKey("trigger")).toBe(TRIGGERS_LABEL_KEY);
        expect(kindLabelKey("read")).toBe("palette_kind_read");
        // a kind the BE adds tomorrow: we have no translation, so we must not invent one
        expect(kindLabelKey("notify")).toBeUndefined();
        expect(kindLabelKey("")).toBeUndefined();
    });
});

describe("tintOf — GROUP chrome only", () => {
    it("gives read and action distinct section rules", () => {
        expect(tintOf("read").rule).not.toBe(tintOf("action").rule);
    });

    it("falls back for an unknown kind", () => {
        expect(tintOf("nonsense")).toBe(DEFAULT_TINT);
    });
});

describe("nodeTileStyle — a node keeps its OWN colour", () => {
    it("paints a leaf from the BE colour, never from its kind", () => {
        // every jira-worker node carries WORKER_COLOR #0052CC (Atlassian blue)
        expect(nodeTileStyle({ color: "#0052CC", kind: "read" } as any)).toEqual({
            color: "#0052CC",
            backgroundColor: "color-mix(in srgb, #0052CC 10%, transparent)",
        });
        // a Core builtin picks its own (Loop #F97316, Merge #10B981, …)
        expect(nodeTileStyle({ color: "#F97316", kind: "logic" } as any).color).toBe("#F97316");
        // SA-1736 MUST NOT REGRESS: the colour a user picked in the builder's
        // ColorPicker survives into the palette. Overriding it with a kind tint
        // would silently throw away the feature we shipped in sub-task B.
        expect(nodeTileStyle({ color: "#10B981", kind: "action" } as any).color).toBe("#10B981");
    });

    it("returns no style when the BE sends no colour", () => {
        expect(nodeTileStyle({} as any)).toEqual({});
    });
});

describe("vendorOf", () => {
    it("returns a vouched-for vendor and nothing for the rest", () => {
        expect(VENDOR_BLURB.Jira).toBe("Atlassian");
        expect(vendorOf("Jira")).toBe("Atlassian");
        expect(vendorOf("Unknown Co")).toBeUndefined();
    });
});

describe("stripCollectionPrefix", () => {
    it("strips the collection prefix inside its own group", () => {
        expect(stripCollectionPrefix("Jira: Create Issue", "Jira")).toBe("Create Issue");
    });

    it("leaves a name without the prefix untouched", () => {
        expect(stripCollectionPrefix("Create Issue", "Jira")).toBe("Create Issue");
        expect(stripCollectionPrefix("Condition", "Core")).toBe("Condition");
    });
});

describe("previewNames", () => {
    it("joins the first few member names", () => {
        const items = ["Condition", "Loop", "Merge", "Switch", "Parallel"].map((n) => item(n, "Core.logic"));
        expect(previewNames(items)).toBe("Condition · Loop · Merge · Switch");
    });
});

describe("kindOf", () => {
    it("prefers the BE kind field", () => {
        expect(kindOf({ category: "Jira.action", kind: "read" } as any)).toBe("read");
    });

    it("falls back to the category suffix when kind is absent", () => {
        expect(kindOf({ category: "Jira.read" } as any)).toBe("read");
        expect(kindOf(undefined)).toBe("");
    });
});

describe("collectionOf", () => {
    it("prefers the BE collection field", () => {
        expect(collectionOf({ original: { category: "Jira.action", collection: "Jira" } as any })).toBe("Jira");
    });

    it("falls back to the category prefix when collection is absent", () => {
        expect(collectionOf({ original: { category: "Jira.read" } as any })).toBe("Jira");
    });

    it("falls back to the whole category for a flat key with no dot", () => {
        expect(collectionOf({ original: { category: "business" } as any })).toBe("business");
    });
});
