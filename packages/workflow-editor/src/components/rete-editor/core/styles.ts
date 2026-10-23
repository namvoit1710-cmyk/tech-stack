export const applyContainerStyles = (container: HTMLElement): void => {
  Object.assign(container.style, {
    // Theme-aware: flips with the app's light/dark mode. The dot grid uses the
    // foreground at low alpha so it stays subtle and visible in both modes.
    background: "var(--color-background)",
    backgroundImage:
      "radial-gradient(circle, color-mix(in srgb, var(--color-foreground) 12%, transparent) 1px, transparent 1px)",
    backgroundSize: "20px 20px",
  });
};