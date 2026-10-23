type AnyRow = Record<string, unknown>;

const getRowId = (row: AnyRow): string =>
    (row?._id ?? row?.id ?? row?.ID) as string;

/**
 * SA-1239 AC6 — shape the table's row selection into the node `selected` output.
 *
 * - `single`   → the chosen row (object), or `null` when nothing is selected.
 * - `multiple` → the chosen rows (array); empty array when nothing is selected.
 *
 * Row identity mirrors the DataTable engine (`_id` ?? `id` ?? `ID`).
 */
export function resolveSelectedOutput(
    rows: AnyRow[] | undefined,
    rowSelections: Record<string, boolean> | undefined,
    mode: "single" | "multiple",
): AnyRow | AnyRow[] | null {
    const selected = (rows ?? []).filter((row) => !!rowSelections?.[getRowId(row)]);

    if (mode === "single") {
        return selected[0] ?? null;
    }

    return selected;
}
