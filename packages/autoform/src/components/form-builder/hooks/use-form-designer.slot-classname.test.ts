import { describe, expect, it } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import { WRAPPER_DEFS } from "../constants/palette";
import type { DesignerContainer } from "../types";
// Real hook is `useDesigner` (no props); the palette-add method is `addContainer`.
import { useDesigner } from "./use-form-designer";

const tabDef = WRAPPER_DEFS.find((d) => d.wrapperType === "TabWrapper")!;

describe("updateSlotClassName", () => {
  it("sets className on the addressed slot only", () => {
    const { result } = renderHook(() => useDesigner());

    act(() => result.current.addContainer(tabDef));

    const container = result.current.nodes[0] as DesignerContainer;
    const [slot1, slot2] = container.slots!;

    act(() => result.current.updateSlotClassName(container._id, slot1._id, "p-6 bg-muted"));

    const after = result.current.nodes[0] as DesignerContainer;
    expect(after.slots?.[0]?.className).toBe("p-6 bg-muted");
    expect(after.slots?.[1]?.className).toBeUndefined();
    expect(after.slots?.[1]?._id).toBe(slot2._id);
  });
});
