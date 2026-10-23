// SA-1372 Replay scrubber — pure helpers (unit-testable without React/DOM).
import type { IReplayTimelineEntry } from "../../types/replay";

/** Clamp a cursor to the valid [0, total] range. */
export const clampCursor = (cursor: number, total: number): number =>
  Math.max(0, Math.min(total, cursor));

/** Chip label for a timeline entry: node id when present, else the event type. */
export const timelineLabel = (entry: IReplayTimelineEntry): string =>
  entry.node_id ? entry.node_id : entry.event_type;

// Timeline paging — long runs (1M+ events) must not grow the chip area or the
// network payload unboundedly; the server serves TIMELINE_PAGE_SIZE-event pages
// and the chip frame scrolls (see replay-scrubber.tsx).
export const TIMELINE_PAGE_SIZE = 100;

/** Number of pages for a timeline of `length` entries (always ≥ 1). */
export const pageCount = (length: number, pageSize = TIMELINE_PAGE_SIZE): number =>
  Math.max(1, Math.ceil(length / pageSize));

/** 0-based page that contains the entry at 0-based position `pos` (-1 → page 0). */
export const pageOf = (pos: number, pageSize = TIMELINE_PAGE_SIZE): number =>
  Math.max(0, Math.floor(pos / pageSize));
