import { describe, expect, it } from "@rstest/core";
import { resolveTargetContainerId } from "./resolve-target-container";

describe("resolveTargetContainerId", () => {
  it("returns the nearest ancestor data-container-id", () => {
    const root = document.createElement("div");
    root.innerHTML = `
      <div data-container-id="c1">
        <div class="inner"><span class="leaf">x</span></div>
      </div>`;
    const leaf = root.querySelector(".leaf");
    expect(resolveTargetContainerId(leaf)).toBe("c1");
  });

  it("returns the closest (innermost) container when nested", () => {
    const root = document.createElement("div");
    root.innerHTML = `
      <div data-container-id="outer">
        <div data-container-id="inner"><span class="leaf">x</span></div>
      </div>`;
    expect(resolveTargetContainerId(root.querySelector(".leaf"))).toBe("inner");
  });

  it("returns null when no container ancestor exists", () => {
    const root = document.createElement("div");
    root.innerHTML = `<span class="leaf">x</span>`;
    expect(resolveTargetContainerId(root.querySelector(".leaf"))).toBeNull();
  });

  it("returns null for a null target", () => {
    expect(resolveTargetContainerId(null)).toBeNull();
  });
});
