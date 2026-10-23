import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render } from "@testing-library/react";
import { StrictMode } from "react";
import type { ISchema } from "../../../types/schema";
import type { UseDesignerReturn } from "./use-form-designer";
import { useFormBuilder } from "./use-form-builder";

// eslint-disable-next-line @typescript-eslint/no-floating-promises
afterEach(() => {
  cleanup();
  rs.useRealTimers();
});

const VALUE: ISchema = {
  fields: [{ key: "a", outputType: "string", fieldConfig: { fieldControl: "InputControl" } }],
};

let designer: UseDesignerReturn;
function Harness({ value, onChange }: { value: ISchema; onChange: (s: ISchema) => void }) {
  designer = useFormBuilder(value, onChange).designer;
  return null;
}

describe("useFormBuilder — controlled value/onChange sync (SA-2043 debounced emit)", () => {
  it("does NOT emit an empty schema on StrictMode mount (regression: flush wiped the parent)", () => {
    const onChange = rs.fn();
    render(
      <StrictMode>
        <Harness value={VALUE} onChange={onChange} />
      </StrictMode>,
    );
    const wipedToEmpty = onChange.mock.calls.some(
      (c) => ((c[0] as ISchema | undefined)?.fields?.length ?? 0) === 0,
    );
    expect(wipedToEmpty).toBe(false);
  });

  it("emits a real edit to onChange only after the debounce settles", () => {
    rs.useFakeTimers();
    const onChange = rs.fn();
    render(<Harness value={VALUE} onChange={onChange} />);

    act(() => {
      const id = designer.nodes[0]!._id;
      designer.updateNode(id, (n) => ({ ...n, key: "renamed" }));
    });
    expect(onChange).not.toHaveBeenCalled(); // debounced — nothing yet

    act(() => {
      rs.advanceTimersByTime(350);
    });
    expect(onChange).toHaveBeenCalled();
    expect((onChange.mock.calls.at(-1)![0] as ISchema).fields[0]!.key).toBe("renamed");
  });

  it("flushes a still-pending edit on unmount, before the debounce fires", () => {
    rs.useFakeTimers();
    const onChange = rs.fn();
    const { unmount } = render(<Harness value={VALUE} onChange={onChange} />);

    act(() => {
      const id = designer.nodes[0]!._id;
      designer.updateNode(id, (n) => ({ ...n, key: "flushed" }));
    });
    onChange.mockClear();

    act(() => {
      unmount();
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect((onChange.mock.calls[0]![0] as ISchema).fields[0]!.key).toBe("flushed");
  });
});
