import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import PaletteKindSection from "./palette-kind-section";

rs.mock("@ldc/workflow-editor", () => ({
    DynamicNodeIcon: () => <span data-testid="node-icon" />,
    createEditorNode: (node: unknown) => node,
}));

const messages: Record<string, string> = {
    palette_custom_badge: "CUSTOM",
};

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({
        t: (key: string, opts?: Record<string, unknown>) =>
            (messages[key] ?? key).replace(/\{\{(\w+)\}\}/g, (_, k: string) => String(opts?.[k] ?? "")),
    }),
}));

const leaf = (id: string, text: string, kind: string) => ({
    text,
    type: "Node" as const,
    icon: "",
    original: { id, name: text, category: `Jira.${kind}`, kind, collection: "Jira", color: "#0052CC" } as any,
});

const noop = () => {};

describe("PaletteKindSection", () => {
    afterEach(cleanup);

    it("renders the label, the count and prefix-stripped leaf names", () => {
        const section = {
            key: "Jira.action",
            label: "Actions",
            kind: "action",
            count: 1,
            items: [leaf("w:c", "Jira: Create Issue", "action")],
        };
        render(<PaletteKindSection section={section} collection="Jira" onSelect={noop} onAdd={noop} />);

        expect(screen.getByText("Actions")).toBeTruthy();
        expect(screen.getByText("1")).toBeTruthy();
        expect(screen.getByText("Create Issue")).toBeTruthy();
        expect(screen.queryByText("Jira: Create Issue")).toBeNull();
    });

    it("renders a util section's leaf immediately, with no disclosure step", () => {
        const section = {
            key: "Jira.util",
            label: "Utilities",
            kind: "util",
            count: 1,
            items: [leaf("w:w", "Jira: Test / Whoami", "util")],
        };
        render(<PaletteKindSection section={section} collection="Jira" onSelect={noop} onAdd={noop} />);

        expect(screen.getByText("Test / Whoami")).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
    });
});
