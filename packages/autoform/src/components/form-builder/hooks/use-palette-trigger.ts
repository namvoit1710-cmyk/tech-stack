import { createContext, useContext } from "react";

/** Opens the command palette targeting a container (or root when null). */
export type OpenPalette = (
  targetContainerId: string | null,
  point: { x: number; y: number } | null,
  targetSlotName?: string,
  targetSlotId?: string,
) => void;

const PaletteTriggerContext = createContext<OpenPalette | null>(null);

export const PaletteTriggerProvider = PaletteTriggerContext.Provider;

// eslint-disable-next-line @typescript-eslint/no-empty-function
function noop(
  _containerId: string | null,
  _point: { x: number; y: number } | null,
  _slotName?: string,
  _slotId?: string
): void {
  // no-op
}

export function useOpenPalette(): OpenPalette {
  return useContext(PaletteTriggerContext) ?? noop;
}
