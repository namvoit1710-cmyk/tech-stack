export function resolveTargetContainerId(target: EventTarget | null): string | null {
  if (!(target instanceof Element)) return null;
  const el = target.closest<HTMLElement>("[data-container-id]");
  return el?.dataset.containerId ?? null;
}

export function resolveTargetSlotId(target: EventTarget | null): string | undefined {
  if (!(target instanceof Element)) return undefined;
  const el = target.closest<HTMLElement>("[data-slot-id]");
  return el?.dataset.slotId ?? undefined;
}
