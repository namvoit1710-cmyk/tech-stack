import type { PaginationState } from "@ldc/data-table";
import { startTransition, useCallback, useMemo, useState } from "react";
import type { TableDataChangeType } from "./use-table-sync";

let rowIdCounter = 0;
export const generateRowId = () => `_trow_${++rowIdCounter}`;

export const ensureRowIds = (data: any[]): any[] => {
    if (!Array.isArray(data)) return [];
    return data.map(item =>
        item._id ? item : { ...item, _id: generateRowId() }
    );
};

interface UseTableDataOptions {
    emptyRowTemplate: Record<string, any>;
    initialData: any[];
    initialRowCount?: number;
    initialPageIndex?: number;
    initialPageSize?: number;
    pageSize?: number;
    onDataChange: (data: any[], changeType: TableDataChangeType) => void;
}

export const useTableData = ({
    emptyRowTemplate,
    initialData,
    initialRowCount,
    initialPageIndex = 0,
    initialPageSize,
    onDataChange,
}: UseTableDataOptions) => {
    const [tableData, setTableData] = useState<any[]>(() => ensureRowIds(initialData));
    const [rowSelections, setRowSelections] = useState<Record<string, boolean>>({});
    const [pagination, setPagination] = useState<PaginationState>({
        pageIndex: initialPageIndex,
        pageSize: initialPageSize ?? 20
    });
    const [rowCount, setRowCount] = useState(initialRowCount ?? initialData.length);

    const ensureData = useMemo(() => {
        return ensureRowIds(tableData);
    }, [tableData]);

    const selectedIds = useMemo(
        () => Object.keys(rowSelections),
        [rowSelections]
    );

    const applyChange = useCallback((updater: (prev: any[]) => any[], changeType: TableDataChangeType) => {
        setTableData(prev => {
            const next = updater(prev);
            onDataChange(next, changeType);
            return next;
        });
    }, [onDataChange]);

    const handleInsertRow = useCallback(() => {
        const newRow = { ...emptyRowTemplate, _id: generateRowId() };
        applyChange(prev => [newRow, ...prev], "insert");
        startTransition(() => {
            setPagination(prev => ({ ...prev, pageIndex: 0 }));
        });
    }, [emptyRowTemplate, applyChange]);

    const handleRemoveRow = useCallback((id: string) => {
        applyChange(prev => prev.filter((item) => item._id !== id), "remove");
    }, [applyChange]);

    const handleRemoveSelected = useCallback(() => {
        applyChange(prev => prev.filter((item) => !selectedIds.includes(item._id)), "remove");
        setRowSelections({});
    }, [selectedIds, applyChange]);

    const handleCellChange = useCallback((id: string, fieldKey: string, value: any) => {
        applyChange(prev => {
            return prev.map(item =>
                item._id === id ? { ...item, [fieldKey]: value } : item
            );
        }, "cell-change");
    }, [applyChange]);

    const handleRowChange = useCallback((id: string, partial: Record<string, any>) => {
        applyChange(prev =>
            prev.map(item =>
                item._id === id ? { ...item, ...partial } : item
            )
            , "cell-change");
    }, [applyChange]);

    const resetData = useCallback((data: any[], total?: number) => {
        setTableData(ensureRowIds(data));
        if (typeof total === "number") setRowCount(total);
        setRowSelections({});
    }, []);

    return {
        tableData,
        ensureData,

        pagination,
        rowSelections,
        selectedIds,
        rowCount,

        resetData,
        setPagination,
        handleInsertRow,
        handleRemoveRow,
        setRowSelections,
        handleCellChange,
        handleRowChange,
        handleRemoveSelected,
    };
};