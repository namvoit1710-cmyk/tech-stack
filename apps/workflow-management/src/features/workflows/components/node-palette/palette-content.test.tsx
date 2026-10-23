import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import NodePaletteContent from "./palette-content";
import { RECENTS_KEY } from "@/features/workflows/stores/palette-recents";

const leaf = (id: string, text: string, kind: string, collection: string, provider = "worker") => ({
    text,
    type: "Node" as const,
    icon: "",
    original: { id, name: text, category: `${collection}.${kind}`, kind, collection, provider, color: "" } as any,
});

const jiraCreate = leaf("w:c", "Jira: Create Issue", "action", "Jira");
const jiraGet = leaf("w:g", "Jira: Get Issue", "read", "Jira");
const jiraWhoami = leaf("w:w", "Jira: Test / Whoami", "util", "Jira");
const slackPost = leaf("s:p", "Slack: Post Message", "action", "Slack");
const coreTrigger = leaf("b:t", "Trigger", "trigger", "Core", "builtin");
// NOT named "Condition". The Core logic node is also a suggested CHIP, so a bucket whose
// preview equals its only member's name makes every preview assertion ambiguous. With two
// members the preview is the joined string the hook really produces — and it is unique.
const coreIfElse = leaf("b:cond", "If / Else", "logic", "Core", "builtin");
const coreMerge = leaf("b:merge", "Merge", "logic", "Core", "builtin");
const mine = leaf("m:g", "Google Search", "read", "My Nodes", "business");
// SA-1930: a business author named THEIR OWN collection "Finance" — not the default "My
// Nodes". It must get a row (like a connector's), not be flattened into the band header.
const financeReport = leaf("f:r", "Finance: Report", "action", "Finance", "business");

const baseMenu = {
    nodeMenuItems: [jiraCreate, jiraGet, jiraWhoami, slackPost, coreTrigger, coreIfElse, coreMerge, mine, financeReport],
    isLoading: false,
    total: 9,
    bands: [
        {
            id: "core",
            collections: [
                {
                    key: "Core",
                    label: "Core",
                    count: 3,
                    sections: [
                        { key: "triggers", label: "Triggers", labelKey: "palette_kind_trigger", kind: "trigger", icon: "zap", count: 1, items: [coreTrigger], preview: "Trigger" },
                        { key: "flow-logic", label: "Flow & Logic", labelKey: "palette_bucket_flow_logic", kind: "logic", icon: "git-branch", count: 2, items: [coreIfElse, coreMerge], preview: "If / Else · Merge" },
                    ],
                },
            ],
        },
        {
            id: "connector",
            collections: [
                {
                    key: "Jira",
                    label: "Jira",
                    vendor: "Atlassian",
                    count: 3,
                    sections: [
                        { key: "Jira.action", label: "Actions", labelKey: "palette_kind_action", kind: "action", count: 1, items: [jiraCreate] },
                        { key: "Jira.read", label: "Find & Read", labelKey: "palette_kind_read", kind: "read", count: 1, items: [jiraGet] },
                        { key: "Jira.util", label: "Utilities", labelKey: "palette_kind_util", kind: "util", count: 1, items: [jiraWhoami] },
                    ],
                },
                {
                    key: "Slack",
                    label: "Slack",
                    count: 1,
                    sections: [
                        { key: "Slack.action", label: "Actions", labelKey: "palette_kind_action", kind: "action", count: 1, items: [slackPost] },
                    ],
                },
            ],
        },
        {
            id: "mine",
            collections: [
                {
                    key: "My Nodes",
                    label: "My Nodes",
                    count: 1,
                    sections: [{ key: "My Nodes.read", label: "Find & Read", labelKey: "palette_kind_read", kind: "read", count: 1, items: [mine] }],
                },
                {
                    key: "Finance",
                    label: "Finance",
                    count: 1,
                    sections: [{ key: "Finance.action", label: "Actions", labelKey: "palette_kind_action", kind: "action", count: 1, items: [financeReport] }],
                },
            ],
        },
    ],
};

/**
 * The BE gives order 10 to ANY collection that is not Core / "My Nodes" — including one a
 * business author names themselves. An author who names a collection "triggers" collides,
 * key for key, with the Core TRIGGERS bucket's machine id.
 */
const collisionPing = leaf("t:p", "Ping", "action", "triggers", "business");
const collisionMenu = {
    ...baseMenu,
    bands: [
        {
            id: "core",
            collections: [
                {
                    key: "Core",
                    label: "Core",
                    count: 1,
                    // no `preview` here: the Core Trigger node's name IS "Trigger", so a preview
                    // would put that same string in the collapsed row and make "did the bucket
                    // actually open?" unprovable.
                    sections: [
                        { key: "triggers", label: "Triggers", labelKey: "palette_kind_trigger", kind: "trigger", icon: "zap", count: 1, items: [coreTrigger] },
                    ],
                },
            ],
        },
        {
            id: "connector",
            // two connectors, so the single-connector auto-expand does not open one for us
            collections: [
                {
                    key: "triggers",
                    label: "triggers",
                    count: 1,
                    sections: [
                        { key: "triggers.action", label: "Actions", labelKey: "palette_kind_action", kind: "action", count: 1, items: [collisionPing] },
                    ],
                },
                baseMenu.bands[1]?.collections[1],
            ],
        },
    ],
};

/** A band id no layout knows. It must render NOTHING — never silently as a connector. */
const unknownBandMenu = {
    ...baseMenu,
    bands: [
        ...baseMenu.bands,
        {
            id: "future",
            collections: [
                {
                    key: "Quantum",
                    label: "Quantum",
                    count: 1,
                    sections: [{ key: "Quantum.action", label: "Actions", kind: "action", count: 1, items: [leaf("q:1", "Entangle", "action", "Quantum")] }],
                },
            ],
        },
    ],
};

/**
 * SA-1930: a business leaf can sit INSIDE a connector band collection — the collection is
 * filed by what its nodes are, and worker wins (see bandOfCollection). Its CUSTOM badge
 * must survive that placement: it is a property of the node, not of the band it's in.
 */
const jiraCustomBusiness = leaf("w:custom", "Jira: My Custom", "action", "Jira", "business");
const businessInConnectorMenu = {
    ...baseMenu,
    nodeMenuItems: [...baseMenu.nodeMenuItems, jiraCustomBusiness],
    bands: baseMenu.bands.map((band) =>
        band.id !== "connector"
            ? band
            : {
                  ...band,
                  collections: band.collections.map((collection) =>
                      collection.key !== "Jira"
                          ? collection
                          : {
                                ...collection,
                                count: collection.count + 1,
                                sections: collection.sections.map((section) =>
                                    section.key !== "Jira.action"
                                        ? section
                                        : { ...section, count: section.count + 1, items: [...section.items, jiraCustomBusiness] },
                                ),
                            },
                  ),
              },
    ),
};

let menu: any = baseMenu;

rs.mock("@/features/workflows/hooks/use-merge-nodes", () => ({
    default: () => menu,
}));

rs.mock("@ldc/workflow-editor", () => ({
    DynamicNodeIcon: () => <span data-testid="node-icon" />,
    createEditorNode: (node: unknown) => node,
}));

const messages: Record<string, string> = {
    palette_band_core: "Core",
    palette_band_connector: "Apps & Connectors",
    palette_band_mine: "My Nodes · Built from primitives",
    palette_kind_trigger: "Triggers",
    palette_kind_action: "Actions",
    palette_kind_read: "Find & Read",
    palette_kind_util: "Utilities",
    palette_bucket_flow_logic: "Flow & Logic",
    palette_operations: "{{count}} operations",
    palette_no_results: 'No nodes match "{{query}}"',
    palette_custom_badge: "CUSTOM",
    search_node_placeholder_count: "Search {{total}}+ nodes",
};

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({
        t: (key: string, opts?: Record<string, unknown>) =>
            (messages[key] ?? key).replace(/\{\{(\w+)\}\}/g, (_, k: string) => String(opts?.[k] ?? "")),
    }),
}));

// The palette reads canvas state from the editor store (SA-1739). Mutated per-test.
const canvas = { nodes: [] as any[] };
rs.mock("@/features/workflows/stores/editor-stores", () => ({
    useEditorStore: (sel: any) => sel({ workflowData: { nodes: canvas.nodes } }),
}));

/** The expandable bucket/collection row that owns this label. */
const rowFor = (label: string) => screen.getByText(label).closest('[role="button"]') as HTMLElement;

describe("NodePaletteContent — visual system (SA-1930)", () => {
    beforeEach(() => {
        menu = baseMenu;
        canvas.nodes = [{ data: { node_type: "TRIGGER", category: "Core.trigger" } }];
        window.localStorage.removeItem(RECENTS_KEY);
    });
    afterEach(cleanup);

    it("renders the three origin bands", () => {
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.getByText("Apps & Connectors")).toBeTruthy();
        expect(screen.getByText("My Nodes · Built from primitives")).toBeTruthy();
    });

    it("shows Core buckets including Triggers, with their previews", () => {
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.getByText("Flow & Logic")).toBeTruthy();
        expect(screen.getByText("If / Else · Merge")).toBeTruthy(); // the preview subtitle
    });

    it("shows a connector subtitle with its vendor and operation count", () => {
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.getByText("Atlassian · 3 operations")).toBeTruthy();
        // Slack has no vendor. Scoped to its own row: MINE's "Finance" collection (SA-1930)
        // also reads "1 operations", so the bare text is no longer unique on the page.
        expect(within(rowFor("Slack")).getByText("1 operations")).toBeTruthy();
    });

    it("expands a connector IN PLACE and keeps a second one open (multi-open, no drawer)", async () => {
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.queryByText("Create Issue")).toBeNull();

        await user.click(screen.getByText("Jira"));
        expect(screen.getByText("Create Issue")).toBeTruthy(); // prefix stripped
        expect(screen.getByText("Get Issue")).toBeTruthy();

        await user.click(screen.getByText("Slack"));
        expect(screen.getByText("Post Message")).toBeTruthy();
        expect(screen.getByText("Create Issue")).toBeTruthy(); // Jira stayed open
    });

    it("shows a connector's utilities immediately, with no disclosure step", async () => {
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        await user.click(screen.getByText("Jira"));
        expect(screen.getByText("Test / Whoami")).toBeTruthy();
    });

    it("badges a business node under MY NODES", () => {
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.getByText("Google Search")).toBeTruthy();
        expect(screen.getByText("CUSTOM")).toBeTruthy();
    });

    it("renders the default 'My Nodes' collection's leaves flat, with no row for it", () => {
        render(<NodePaletteContent onSelectNode={() => {}} />);

        // The band header already says "My Nodes · Built from primitives"; a row named
        // "My Nodes" inside it would be a redundant second row saying the same thing.
        expect(screen.getByText("Google Search")).toBeTruthy();
        expect(screen.queryByText("My Nodes")).toBeNull();
    });

    it("gives a named collection under MINE ('Finance') a row, collapsed by default, that expands in place", async () => {
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.getByText("Finance")).toBeTruthy(); // the row itself
        expect(screen.queryByText("Report")).toBeNull(); // prefix-stripped leaf, hidden until expanded

        await user.click(screen.getByText("Finance"));
        expect(screen.getByText("Report")).toBeTruthy();
    });

    it("badges a business leaf even when its collection is filed under a CONNECTOR band (a user named their collection 'Jira')", async () => {
        menu = businessInConnectorMenu;
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        // Before expanding Jira: only Google Search's (flat, under MY NODES) badge is visible.
        expect(screen.getAllByText("CUSTOM")).toHaveLength(1);

        await user.click(screen.getByText("Jira"));
        expect(screen.getByText("My Custom")).toBeTruthy();
        // …now a SECOND badge, on the business leaf living inside the connector's own row.
        expect(screen.getAllByText("CUSTOM")).toHaveLength(2);
    });

    it("adds a node from a chip and records it as a recent", async () => {
        const user = userEvent.setup();
        const onSelectNode = rs.fn();
        render(<NodePaletteContent onSelectNode={onSelectNode} />);

        // fresh user -> suggested chips: biggest connector's first action + first read, then Core logic
        await user.click(screen.getByRole("button", { name: "Jira: Create Issue" }));

        expect(onSelectNode).toHaveBeenCalledWith(jiraCreate.original);
        expect(JSON.parse(window.localStorage.getItem(RECENTS_KEY) ?? "[]")).toEqual(["w:c"]);
    });

    it("shows no trigger nudge and does not dim the bands when the canvas has no trigger", () => {
        canvas.nodes = [];
        render(<NodePaletteContent onSelectNode={() => {}} />);

        expect(screen.queryByText("Start with a trigger")).toBeNull();
        expect(document.querySelector(".opacity-50")).toBeNull();
    });

    it("search replaces the bands with one flat ranked list (SA-1739 never-worse)", async () => {
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        await user.type(screen.getByPlaceholderText("Search 9+ nodes"), "issue");

        // Wait for the 500ms debounce to actually land — the bands are gone. Only THEN are
        // the matches below provably result rows; before it they could still be the chips
        // (which carry the same full text and are on screen before a key is pressed).
        await waitFor(() => {
            expect(screen.queryByText("Apps & Connectors")).toBeNull();
        });

        // Chips are hidden while searching, so these are the flat list: full text, un-stripped.
        expect(screen.getByText("Jira: Get Issue")).toBeTruthy();
        expect(screen.getByText("Jira: Create Issue")).toBeTruthy();
    });

    it("says so when a search matches nothing, instead of showing a blank panel", async () => {
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        await user.type(screen.getByPlaceholderText("Search 9+ nodes"), "zzzz");

        expect(await screen.findByText('No nodes match "zzzz"')).toBeTruthy();
        expect(screen.queryByText("Apps & Connectors")).toBeNull();
    });

    it("scopes expansion per band, so a collection named like a Core bucket cannot toggle it", async () => {
        // A business author named their collection "triggers" — the Core TRIGGERS bucket's
        // machine id. One boolean behind both rows would open and close them together.
        menu = collisionMenu;
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        await user.click(screen.getByText("Triggers")); // the Core bucket
        expect(screen.getByText("Trigger")).toBeTruthy(); // its node is showing
        expect(screen.queryByText("Actions")).toBeNull(); // …and the collection stayed SHUT

        await user.click(screen.getByText("triggers")); // the author's collection
        expect(screen.getByText("Actions")).toBeTruthy(); // now it is open
        expect(screen.getByText("Trigger")).toBeTruthy(); // …and the Core bucket stayed open
    });

    it("renders nothing for a band id it does not know — never silently as a connector", () => {
        menu = unknownBandMenu;
        render(<NodePaletteContent onSelectNode={() => {}} />);

        // The three known bands still render…
        expect(screen.getByText("Apps & Connectors")).toBeTruthy();
        // …and the unknown one contributes nothing at all. The old else-fallback would have
        // rendered "Quantum" as an expandable connector.
        expect(screen.queryByText("Quantum")).toBeNull();
        expect(screen.queryByText("palette_band_future")).toBeNull();
    });

    it("lets a keyboard user expand a collection row it announces as a button", async () => {
        const user = userEvent.setup();
        render(<NodePaletteContent onSelectNode={() => {}} />);

        const jira = rowFor("Jira");
        expect(jira.getAttribute("tabindex")).toBe("0"); // reachable, not merely announced

        jira.focus();
        await user.keyboard("{Enter}");
        expect(screen.getByText("Create Issue")).toBeTruthy();

        await user.keyboard(" ");
        expect(screen.queryByText("Create Issue")).toBeNull(); // Space collapses it again
    });
});
