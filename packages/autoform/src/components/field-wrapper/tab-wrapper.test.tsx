/// <reference types="@testing-library/jest-dom" />

import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import { Tabs } from "@ldc/ui/components/tabs";

import type { IField } from "../../types/schema";
import { TabItemWrapper } from "./tab-wrapper";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
    cleanup();
});

const tabField = {
    key: "general_data",
    outputType: "object",
    fieldConfig: {
        fieldWrapper: "TabItemWrapper",
        wrapperProps: { label: "General Data", className: "p-6" },
    },
} as unknown as IField;

describe("TabItemWrapper", () => {
    it("does not leak wrapperProps onto the DOM", () => {
        const { container } = render(
            <Tabs defaultValue="general_data">
                <TabItemWrapper field={tabField}>
                    <span>child</span>
                </TabItemWrapper>
            </Tabs>
        );

        const content = container.querySelector('[data-slot="tabs-content"]');
        expect(content).not.toBeNull();
        expect(content).not.toHaveAttribute("label");
    });

    it("still applies the tab's className", () => {
        const { container } = render(
            <Tabs defaultValue="general_data">
                <TabItemWrapper field={tabField}>
                    <span>child</span>
                </TabItemWrapper>
            </Tabs>
        );

        expect(container.querySelector('[data-slot="tabs-content"]')).toHaveClass("p-6");
    });
});
