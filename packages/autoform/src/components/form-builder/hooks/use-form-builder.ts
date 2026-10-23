import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ISchema } from "../../../types/schema";
import { CLOSED_PALETTE } from "../components/command-palette";
import type { CommandPaletteState } from "../components/command-palette";
import { resolveTargetContainerId, resolveTargetSlotId } from "../utils/resolve-target-container";
import { useDesigner } from "./use-form-designer";
import type { UseDesignerReturn } from "./use-form-designer";
import type { OpenPalette } from "./use-palette-trigger";

export type ViewMode = "builder" | "preview";

export interface UseFormBuilderReturn {
  designer: UseDesignerReturn;
  activeId: string | null;
  overId: string | null;
  setActiveId: (id: string | null) => void;
  setOverId: (id: string | null) => void;
  view: ViewMode;
  setView: (v: ViewMode) => void;
  palette: CommandPaletteState;
  setPaletteOpen: (open: boolean) => void;
  openPalette: OpenPalette;
  openFromButton: () => void;
  handleContextMenu: (e: React.MouseEvent) => void;
  addBtnRef: React.RefObject<HTMLButtonElement | null>;
}

export function useFormBuilder(
  value: ISchema,
  onChange: (schema: ISchema) => void,
): UseFormBuilderReturn {
  const designer = useDesigner();

  const [activeId, setActiveId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("builder");
  const [palette, setPalette] = useState<CommandPaletteState>(CLOSED_PALETTE);
  const addBtnRef = useRef<HTMLButtonElement | null>(null);

  // ── Controlled value/onChange sync ──
  // PERF (SA-2043): this emit used to run on EVERY keystroke — `JSON.stringify(designer.schema)` over
  // the whole tree in the render body, plus `onChange(schema)` to the (potentially expensive) parent
  // host. It is now debounced: the internal designer state stays synchronous (canvas + inspector
  // update immediately), but the whole-tree stringify + parent notify only run once typing settles.
  // A still-pending edit is flushed on unmount so nothing is dropped when the builder closes mid-type.
  const EMIT_DEBOUNCE_MS = 300;
  const lastEmittedRef = useRef<string | null>(null);
  const valueJson = useMemo(() => JSON.stringify(value), [value]);
  const importSchema = designer.importSchema;
  const schema = designer.schema;

  // Latest values in refs so the debounce timer + unmount flush never read a stale closure, and so a
  // parent re-rendering with a fresh inline `onChange` can't reset the debounce (starvation).
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const schemaRef = useRef(schema);
  schemaRef.current = schema;
  const valueJsonRef = useRef(valueJson);
  valueJsonRef.current = valueJson;
  // `prevSchemaRef` + `pendingRef` distinguish a REAL user edit (a change to the memoised `schema`
  // reference) from the initial mount and from an in-progress import — so the flush never emits the
  // pre-import empty schema, which under StrictMode's mount→unmount→mount would wipe the parent.
  const prevSchemaRef = useRef(schema);
  const pendingRef = useRef(false);

  useEffect(() => {
    if (valueJson !== lastEmittedRef.current) {
      importSchema(value);
      lastEmittedRef.current = valueJson;
      pendingRef.current = false; // an import is not a user edit
    }
  }, [valueJson, value, importSchema]);

  useEffect(() => {
    if (lastEmittedRef.current === null) return;
    if (schema === prevSchemaRef.current) return; // not a real change (mount / unrelated dep change)
    prevSchemaRef.current = schema;
    pendingRef.current = true;
    const t = setTimeout(() => {
      pendingRef.current = false;
      const schemaJson = JSON.stringify(schema);
      if (schemaJson === lastEmittedRef.current) return;
      lastEmittedRef.current = schemaJson;
      if (schemaJson === valueJson) return; // echo of the controlled value — don't emit back
      onChangeRef.current(schema);
    }, EMIT_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [schema, valueJson]);

  // Flush a still-pending REAL edit if the builder unmounts before the debounce fires.
  useEffect(
    () => () => {
      if (!pendingRef.current) return;
      const schemaJson = JSON.stringify(schemaRef.current);
      if (schemaJson === lastEmittedRef.current || schemaJson === valueJsonRef.current) return;
      lastEmittedRef.current = schemaJson;
      onChangeRef.current(schemaRef.current);
    },
    [],
  );

  // ── Palette handlers ───────────────────────────────────────────────────
  const openPalette = useCallback<OpenPalette>(
    (targetContainerId, point, targetSlotName, targetSlotId) =>
      setPalette({ open: true, point, targetContainerId, targetSlotName, targetSlotId }),
    [],
  );

  const openFromButton = useCallback(() => {
    const r = addBtnRef.current?.getBoundingClientRect();
    setPalette({
      open: true,
      point: r ? { x: r.left, y: r.bottom + 4 } : { x: window.innerWidth / 2 - 150, y: window.innerHeight / 2 - 150 },
      targetContainerId: null,
    });
  }, []);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      if (view !== "builder") return;
      e.preventDefault();
      setPalette({
        open: true,
        point: { x: e.clientX, y: e.clientY },
        targetContainerId: resolveTargetContainerId(e.target),
        targetSlotId: resolveTargetSlotId(e.target),
      });
    },
    [view],
  );

  const setPaletteOpen = useCallback((open: boolean) => {
    setPalette((p) => (open ? p : CLOSED_PALETTE));
  }, []);

  return {
    designer,
    activeId,
    overId,
    setActiveId,
    setOverId,
    view,
    setView,
    palette,
    setPaletteOpen,
    openPalette,
    openFromButton,
    handleContextMenu,
    addBtnRef,
  };
}
