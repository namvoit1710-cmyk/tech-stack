import { createContext, useContext } from "react";
import type { UseDesignerReturn } from "./use-form-designer";

const DesignerContext = createContext<UseDesignerReturn | null>(null);

export const DesignerProvider = DesignerContext.Provider;

export function useDesignerCtx(): UseDesignerReturn {
  const ctx = useContext(DesignerContext);
  if (!ctx) throw new Error("useDesignerCtx must be inside <DesignerProvider>");
  return ctx;
}