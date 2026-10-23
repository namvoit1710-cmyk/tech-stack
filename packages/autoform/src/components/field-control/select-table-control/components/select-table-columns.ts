import type { ColumnDef } from "@ldc/data-table";
import type { OptionRecord } from "../types/types";

export function deriveColumns(rows: OptionRecord[]): ColumnDef<OptionRecord & { _id: string }, unknown>[] {
    if (rows.length === 0) return [];
    return Object.keys(rows[0]!)
        .filter((key) => key !== "_id")
        .map((key) => ({
            accessorKey: key,
            header: key,
            cell: ({ getValue }) => String(getValue() ?? ""),
        }));
}
