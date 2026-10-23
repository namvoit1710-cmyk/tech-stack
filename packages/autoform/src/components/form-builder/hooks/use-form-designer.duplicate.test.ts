import { describe, expect, it } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";

import { FIELD_DEFS, WRAPPER_DEFS } from "../constants/palette";
import type { DesignerContainer, DesignerField } from "../types";
import { useDesigner } from "./use-form-designer";

const odataDef = WRAPPER_DEFS.find((d) => d.wrapperType === "OdataWrapper")!;
const inputDef = FIELD_DEFS.find((d) => d.type === "InputControl")!;

describe("duplicateNode — fields inside typed containers", () => {
    // Regression: duplicating a field held in a container's `columns` (OData/Table) was a silent
    // no-op because `insertAfterInTree` only descended into `children` and slot children.
    it("duplicates a field inside OData columns", () => {
        const { result } = renderHook(() => useDesigner());

        act(() => result.current.addContainer(odataDef));
        const odataId = result.current.nodes[0]!._id;
        act(() => result.current.addField(inputDef, odataId));

        const before = result.current.nodes[0] as DesignerContainer;
        expect(before.columns).toHaveLength(1);
        const original = before.columns![0] as DesignerField;

        act(() => result.current.duplicateNode(original._id));

        const after = result.current.nodes[0] as DesignerContainer;
        expect(after.columns).toHaveLength(2);
        // The clone sits right after the original, with a fresh id and a "_copy" key.
        expect(after.columns![0]!._id).toBe(original._id);
        expect(after.columns![1]!._id).not.toBe(original._id);
        expect((after.columns![1] as DesignerField).key).toBe(original.key + "_copy");
    });
});
