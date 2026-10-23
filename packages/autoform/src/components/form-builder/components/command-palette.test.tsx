/// <reference types="@testing-library/jest-dom" />

import type React from "react";

import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// Popover pieces are rendered inline (no Radix portal/positioning) so PopoverContent is
// always present in the DOM — the same pattern used by avatar-user.test.tsx /
// user-dropdown.test.tsx. Props (incl. onWheel) are forwarded so the real handler under
// test still runs.
rs.mock("@ldc/ui/components/popover", () => ({
    __esModule: true,
    Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    PopoverAnchor: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    PopoverContent: ({ children, ...rest }: React.ComponentProps<"div">) => <div {...rest}>{children}</div>,
}));

import { DesignerProvider } from "../hooks/use-designer-context";
import type { UseDesignerReturn } from "../hooks/use-form-designer";
import { CLOSED_PALETTE, CommandPalette } from "./command-palette";

afterEach(() => {
    cleanup();
});

function renderPalette(onCanvasWheel: (e: React.WheelEvent) => void) {
    const ctx = {
        nodes: [],
        addField: rs.fn(),
        addContainer: rs.fn(),
        addContainerToSlot: rs.fn(),
        addFieldToOdataSlot: rs.fn(),
    } as unknown as UseDesignerReturn;

    return render(
        <DesignerProvider value={ctx}>
            <div data-testid="canvas-dnd-layer" onWheel={onCanvasWheel}>
                <CommandPalette
                    state={{ ...CLOSED_PALETTE, open: true, point: { x: 10, y: 10 } }}
                    onOpenChange={rs.fn()}
                />
            </div>
        </DesignerProvider>
    );
}

describe("CommandPalette — context-menu wheel scroll (SA-2159)", () => {
    it("gives the CommandList a bounded, scrollable container", () => {
        renderPalette(rs.fn());

        const list = document.querySelector('[data-slot="command-list"]');
        expect(list).not.toBeNull();
        expect(list).toHaveClass("overflow-y-auto");
        expect(list?.className).toMatch(/max-h-/);
    });

    it("stops the wheel event from reaching the canvas/dnd layer beneath the menu", () => {
        const canvasWheelHandler = rs.fn();
        renderPalette(canvasWheelHandler);

        const list = document.querySelector('[data-slot="command-list"]') as HTMLElement;
        fireEvent.wheel(list, { deltaY: 100, bubbles: true });

        expect(canvasWheelHandler).not.toHaveBeenCalled();
    });
});
