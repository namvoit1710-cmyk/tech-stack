import { describe, expect, it } from "@rstest/core";
import { renderHook } from "@testing-library/react";
import { useIsNarrow } from "./use-inspector-breakpoint";
import { useContainerNarrow } from "./use-inspector-breakpoint";

function setWidth(w: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: w });
  window.matchMedia = ((q: string) => ({
    matches: w < 860, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

describe("useIsNarrow", () => {
  it("is true below 860px", () => {
    setWidth(390);
    const { result } = renderHook(() => useIsNarrow());
    expect(result.current).toBe(true);
  });
  it("is false at/above 860px", () => {
    setWidth(1200);
    const { result } = renderHook(() => useIsNarrow());
    expect(result.current).toBe(false);
  });
});

class FakeResizeObserver {
  constructor(_cb: ResizeObserverCallback) {}
  observe() {}
  disconnect() {}
}

function refWithWidth(width: number) {
  return { current: { getBoundingClientRect: () => ({ width }) } as unknown as HTMLElement };
}

describe("useContainerNarrow", () => {
  it("is true when the observed element is narrower than 860px", () => {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver;
    const { result } = renderHook(() => useContainerNarrow(refWithWidth(500)));
    expect(result.current).toBe(true);
  });
  it("is false at/above 860px", () => {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver;
    const { result } = renderHook(() => useContainerNarrow(refWithWidth(1000)));
    expect(result.current).toBe(false);
  });
});
