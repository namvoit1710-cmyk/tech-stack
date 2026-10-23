import { useCallback, useMemo, useState } from "react";
import type { FilterCondition } from "../components/column-filter-dialog";

interface UseODataToolbarProps {
    columns: { key: string; label: string }[];
    visibleColumns: Record<string, boolean>;
}

interface UseODataToolbarReturn {
    selectedErrorIds: Set<string>;
    selectedWarningIds: Set<string>;
    toggleErrorId: (id: string, checked: boolean) => void;
    toggleWarningId: (id: string, checked: boolean) => void;
    clearErrorIds: () => void;
    clearWarningIds: () => void;
    columnFilters: Record<string, FilterCondition>;
    setColumnFilters: (filters: Record<string, FilterCondition>) => void;
    filterDialogOpen: boolean;
    setFilterDialogOpen: (open: boolean) => void;
    activeFilterCount: number;
    filterColumns: { key: string; label: string }[];
    columnFilterString: string | undefined;
}

export function buildColumnFilterString(
    filters: Record<string, FilterCondition>
): string | undefined {
    const escape = (v: string) => v.replace(/'/g, "''");

    const parts = Object.entries(filters)
        .filter(([, f]) => f.values.length > 0)
        .map(([field, { operator, values }]) => {
            const ev = values.map(escape);
            if (values.length === 1) {
                const v = ev[0];
                switch (operator) {
                    case "is":           return `${field} eq '${v}'`;
                    case "is_not":       return `${field} ne '${v}'`;
                    case "contains":     return `contains(${field},'${v}')`;
                    case "not_contains": return `not contains(${field},'${v}')`;
                    default:             return undefined;
                }
            }
            switch (operator) {
                case "is":           return `(${ev.map(v => `${field} eq '${v}'`).join(" or ")})`;
                case "is_not":       return `(${ev.map(v => `${field} ne '${v}'`).join(" and ")})`;
                case "contains":     return `(${ev.map(v => `contains(${field},'${v}')`).join(" or ")})`;
                case "not_contains": return `(${ev.map(v => `not contains(${field},'${v}')`).join(" and ")})`;
                default:             return undefined;
            }
        })
        .filter((p): p is string => p !== undefined);

    return parts.length > 0 ? parts.join(" and ") : undefined;
}

export function useODataToolbar({
    columns,
    visibleColumns,
}: UseODataToolbarProps): UseODataToolbarReturn {
    const [selectedErrorIds, setSelectedErrorIds] = useState<Set<string>>(new Set());
    const [selectedWarningIds, setSelectedWarningIds] = useState<Set<string>>(new Set());
    const [columnFilters, setColumnFilters] = useState<Record<string, FilterCondition>>({});
    const [filterDialogOpen, setFilterDialogOpen] = useState(false);

    const toggleErrorId = useCallback((id: string, checked: boolean) =>
        setSelectedErrorIds(prev => {
            const next = new Set(prev);
            checked ? next.add(id) : next.delete(id);
            return next;
        }), []);

    const toggleWarningId = useCallback((id: string, checked: boolean) =>
        setSelectedWarningIds(prev => {
            const next = new Set(prev);
            checked ? next.add(id) : next.delete(id);
            return next;
        }), []);

    const clearErrorIds = useCallback(() => setSelectedErrorIds(new Set()), []);
    const clearWarningIds = useCallback(() => setSelectedWarningIds(new Set()), []);

    const filterColumns = useMemo(
        () => columns.filter(col => visibleColumns[col.key] !== false && !col.key.startsWith("__")),
        [columns, visibleColumns]
    );

    const activeFilterCount = Object.values(columnFilters).filter(f => f.values.length > 0).length;

    const columnFilterString = useMemo(
        () => buildColumnFilterString(columnFilters),
        [columnFilters]
    );

    return {
        selectedErrorIds,
        selectedWarningIds,
        toggleErrorId,
        toggleWarningId,
        clearErrorIds,
        clearWarningIds,
        columnFilters,
        setColumnFilters,
        filterDialogOpen,
        setFilterDialogOpen,
        activeFilterCount,
        filterColumns,
        columnFilterString,
    };
}
