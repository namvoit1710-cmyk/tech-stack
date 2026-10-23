import { afterEach, beforeAll, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SimpleSelect } from "./simple-select";

// Radix Select drives selection through pointer capture and scrolls the active
// item into view; jsdom implements neither. Stubs, not behaviour under test.
beforeAll(() => {
  Element.prototype.scrollIntoView = rs.fn() as unknown as Element["scrollIntoView"];
  Element.prototype.hasPointerCapture = (() => false) as Element["hasPointerCapture"];
  Element.prototype.setPointerCapture = (() => undefined) as Element["setPointerCapture"];
  Element.prototype.releasePointerCapture = (() => undefined) as Element["releasePointerCapture"];
});

afterEach(() => cleanup());

const OPTIONS = [
  { value: "a", label: "Apple" },
  { value: "b", label: "Banana" },
];

/** Open the panel. Radix opens on ArrowDown, which needs none of the pointer APIs. */
const open = (el: HTMLElement) => fireEvent.keyDown(el, { key: "ArrowDown" });
/**
 * Commit an item. Radix binds selection to the item's own keydown for
 * SELECTION_KEYS ([" ", "Enter"]) and to pointerup — but pointerup is guarded so
 * the gesture that opened the panel cannot also pick a row, which in jsdom means
 * it never fires. Enter has no such guard and needs no pointer-capture stubs.
 */
const choose = (label: string) =>
  fireEvent.keyDown(screen.getByRole("option", { name: label }), { key: "Enter" });

describe("SimpleSelect", () => {
  it("keeps its options out of the DOM until opened, then renders them", () => {
    render(<SimpleSelect data-testid="sel" value="" onValueChange={rs.fn()} options={OPTIONS} placeholder="Pick one" />);
    // Closed: the panel is portalled on demand, so nothing but the trigger exists.
    expect(screen.queryByRole("option", { name: "Apple" })).toBeNull();
    open(screen.getByTestId("sel"));
    expect(screen.getByRole("option", { name: "Apple" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Banana" })).toBeTruthy();
  });

  it("emits the option's value on select", () => {
    const onValueChange = rs.fn();
    render(<SimpleSelect data-testid="sel" value="" onValueChange={onValueChange} options={OPTIONS} placeholder="Pick one" />);
    open(screen.getByTestId("sel"));
    choose("Banana");
    expect(onValueChange).toHaveBeenCalledWith("b");
  });

  it("emits \"\" — not the sentinel — when the empty row is chosen", () => {
    // The whole reason the sentinel exists: Radix throws on <SelectItem value="">,
    // but every call site here treats "" as "nothing selected". It must not leak.
    const onValueChange = rs.fn();
    render(<SimpleSelect data-testid="sel" value="a" onValueChange={onValueChange} options={OPTIONS} emptyLabel="None" />);
    open(screen.getByTestId("sel"));
    choose("None");
    expect(onValueChange).toHaveBeenCalledWith("");
  });

  it("shows the empty row's label on the trigger when nothing is selected", () => {
    render(<SimpleSelect data-testid="sel" value="" onValueChange={rs.fn()} options={OPTIONS} emptyLabel="Select a value" />);
    expect(screen.getByTestId("sel").textContent).toContain("Select a value");
  });

  it("shows the placeholder when there is no empty row and no value", () => {
    render(<SimpleSelect data-testid="sel" value="" onValueChange={rs.fn()} options={OPTIONS} placeholder="Pick one" />);
    expect(screen.getByTestId("sel").textContent).toContain("Pick one");
  });

  it("shows the selected option's label on the trigger", () => {
    render(<SimpleSelect data-testid="sel" value="b" onValueChange={rs.fn()} options={OPTIONS} emptyLabel="None" />);
    expect(screen.getByTestId("sel").textContent).toContain("Banana");
  });

  it("disables the trigger", () => {
    render(<SimpleSelect data-testid="sel" value="" onValueChange={rs.fn()} options={OPTIONS} disabled emptyLabel="None" />);
    expect((screen.getByTestId("sel") as HTMLButtonElement).disabled).toBe(true);
  });

  it("drops an option whose value is \"\" rather than letting Radix throw", () => {
    // Data-driven lists (gateway apps, server options) can produce one; it must
    // not take the whole panel down.
    render(
      <SimpleSelect
        data-testid="sel"
        value=""
        onValueChange={rs.fn()}
        options={[{ value: "", label: "Ghost" }, ...OPTIONS]}
        emptyLabel="None"
      />
    );
    open(screen.getByTestId("sel"));
    expect(screen.queryByRole("option", { name: "Ghost" })).toBeNull();
    expect(screen.getByRole("option", { name: "Apple" })).toBeTruthy();
  });
});
