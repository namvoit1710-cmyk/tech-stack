import * as React from "react";

/** The design's canvas/inspector split collapses below this width. */
export const INSPECTOR_BREAKPOINT = 860;

/** True when the viewport is narrower than the inspector breakpoint (860px). */
export function useIsNarrow() {
  const [isNarrow, setIsNarrow] = React.useState<boolean | undefined>(undefined);
  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${INSPECTOR_BREAKPOINT - 1}px)`);
    const onChange = () => setIsNarrow(window.innerWidth < INSPECTOR_BREAKPOINT);
    mql.addEventListener("change", onChange);
    setIsNarrow(window.innerWidth < INSPECTOR_BREAKPOINT);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return !!isNarrow;
}

/**
 * Like {@link useIsNarrow} but measures a specific CONTAINER (via ResizeObserver) instead of the
 * window. Used when the builder is embedded (e.g. inside the node-detail form) and must slide its
 * inspector based on its own width, not the viewport. Initial synchronous measure in a layout
 * effect avoids a first-paint flash.
 */
export function useContainerNarrow(ref: React.RefObject<HTMLElement | null>): boolean {
  const [isNarrow, setIsNarrow] = React.useState(false);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setIsNarrow(el.getBoundingClientRect().width < INSPECTOR_BREAKPOINT);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return isNarrow;
}
