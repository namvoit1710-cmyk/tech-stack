import { useCallback, useEffect, useMemo } from "react";
import { useFormContext } from "react-hook-form";


import type { PaginationState } from "@ldc/data-table";
import { DataTable } from "@ldc/data-table";
import { Button } from "@ldc/ui/components/button";
import { Minus, Plus } from "lucide-react";
import { useBuilderContext } from "../../../contexts/builder.context";
import { Slot } from "../../../contexts/slot.context";
import type { IField } from "../../../types/schema";
import { useTableColumns } from "./hooks/use-table-columns";
import { useTableData } from "./hooks/use-table-data";
import { useTableSearch } from "./hooks/use-table-search";
import { useTableSync } from "./hooks/use-table-sync";

export interface ITableWrapperProps {
    field: IField;
    path: string[];
}

const TableWrapper = ({ field: fieldControl, path }: ITableWrapperProps) => {
    const name = useMemo(() => path.join("."), [path]);

    const { getValues, clearErrors, getFieldState } = useFormContext();
    const { registerRefetch, readonly: contextReadonly } = useBuilderContext();

    const { error } = getFieldState(name);
    const validationMessage = error?.message ?? (error as any)?.root?.message;

    const subField = fieldControl?.fields?.[0];
    const wrapperProps = fieldControl?.fieldConfig?.wrapperProps ?? {
        enablePagination: true,
    };
    const tableRowWrapper: IField = fieldControl?.fields?.[0]!;

    const isReadonly = contextReadonly || fieldControl?.fieldConfig?.wrapperProps?.readonly || !!subField?.fieldConfig?.wrapperProps?.readonly;
    const showAdd = isReadonly ? false : (fieldControl?.fieldConfig?.wrapperProps?.isAdd || !!subField?.fieldConfig?.wrapperProps?.isAdd);
    const showMinus = isReadonly ? false : (fieldControl?.fieldConfig?.wrapperProps?.isDelete || !!subField?.fieldConfig?.wrapperProps?.isDelete);

    const emptyRowTemplate = useMemo(() => {
        return Object.fromEntries(
            tableRowWrapper?.fields?.filter((f: IField) => f.fieldConfig?.fieldControl !== "ButtonControl")?.map((f: IField) => [f.key, f.default ?? null]) ?? []
        );
    }, [tableRowWrapper]);

    const { syncArrayToRHF } = useTableSync({ name });

    const initialData = useMemo(() => getValues(name) ?? [], [getValues, name]);

    const {
        tableData,
        ensureData,
        rowSelections,
        setRowSelections,
        selectedIds,
        pagination,
        setPagination,
        rowCount,
        handleInsertRow,
        handleRemoveRow,
        handleRemoveSelected,
        handleCellChange,
        handleRowChange,
        resetData,
    } = useTableData({
        initialData,
        emptyRowTemplate,
        onDataChange: syncArrayToRHF,
        initialRowCount: wrapperProps?.rowCount,
        initialPageSize: wrapperProps?.pageSize,
        initialPageIndex: wrapperProps?.pageIndex,
    });

    // Sync external data changes directly from wrapperProps (without re-mounting)
    useEffect(() => {
        if (wrapperProps?.data && Array.isArray(wrapperProps.data)) {
            resetData(wrapperProps.data, wrapperProps?.rowCount);
            syncArrayToRHF(wrapperProps.data, "reset");
        } else if (initialData && initialData.length > 0) {
            syncArrayToRHF(initialData, "reset");
        }
    }, [wrapperProps?.data, wrapperProps?.rowCount, resetData, syncArrayToRHF, initialData]);

    const isSyncServer = wrapperProps?.isSyncServer ?? (!!wrapperProps?.callback || !!wrapperProps?.callApi);
    const resolvedRowCount = isSyncServer
        ? (wrapperProps?.rowCount ?? rowCount)
        : rowCount;
    const resolvedEnablePagination = wrapperProps?.enablePagination
        ?? (resolvedRowCount > (pagination?.pageSize ?? wrapperProps?.pageSize ?? 0));

    const { filteredData, updateSearch, searchValue, clearSearch } = useTableSearch({
        tableData,
        searchConfig: wrapperProps?.search,
        isSyncServer,
    });

    const { columns, initialColumnVisibility } = useTableColumns({
        tableRowWrapper,
        isReadonly,
        showAdd,
        showMinus,
        updateRow: handleRowChange,
        onRemoveRow: (rowId: string) => {
            handleRemoveRow(rowId)
            clearErrors(name);
        },
        onCellChange: handleCellChange,
        isFiltering: !!searchValue,
        namePrefix: name,
    });

    const handlePaginationChange = useCallback(
        (updater: PaginationState) => {
            if (isSyncServer) {
                (wrapperProps?.callback || wrapperProps?.callApi)?.({ ...updater });
            }
            setPagination(updater);
        },
        [pagination, setPagination, isSyncServer, wrapperProps]
    );

    const getRowClassName = useCallback((row: any) => {
        // Translucent tints (color-500/opacity) read on both light and dark backgrounds;
        // the old solid -50 shades washed out to invisible in dark mode.
        if (row.original.__selected) {
            return "bg-blue-500/10! [&_td_.virtualized-table-cell]:bg-blue-500/10! border-l-1 border-l-primary";
        }

        switch (row.original.__validate_status) {
            case "success": return "bg-green-500/10! [&_td_.virtualized-table-cell]:bg-green-500/10! border-l-1 border-l-green-500";
            case "warning": return "bg-yellow-500/10! [&_td_.virtualized-table-cell]:bg-yellow-500/10! border-l-1 border-l-yellow-500";
            case "error": return "bg-red-500/10! [&_td_.virtualized-table-cell]:bg-red-500/10! border-l-1 border-l-red-500";
            default: return "";
        }
    }, []);

    useEffect(() => {
        registerRefetch?.(name, async () => {
            const currentData = getValues(name);
            resetData(currentData ?? []);
        });
    }, [name, resetData, getValues]);

    return (
        <div className="table-form-wrapper flex flex-col gap-4 w-full flex-2 h-full overflow-hidden bg-muted p-4 rounded-3xl">
            {validationMessage && (
                <div className="text-sm text-destructive">
                    {validationMessage}
                </div>
            )}

            <div className="flex items-center justify-between gap-2">

                <div className="relative">

                </div>

                <div className="flex items-center justify-end gap-2">

                    <Slot name="table-notice-content" />

                    <Slot name="table-header-actions" />

                    {showMinus && (
                        <Button
                            variant="destructive"
                            size="icon"
                            disabled={!selectedIds.length}
                            onClick={() => {
                                handleRemoveSelected()

                                clearErrors(name)
                            }}
                        >
                            <Minus />
                        </Button>
                    )}

                    {showAdd && (
                        <Button
                            variant="outline"
                            size="icon"
                            disabled={!!searchValue}
                            onClick={() => {
                                clearErrors(name);
                                handleInsertRow()
                            }}
                        >
                            <Plus />
                        </Button>
                    )}

                </div>
            </div>

            <DataTable
                {...wrapperProps}
                columns={columns}
                className=""
                data={filteredData}
                enableSelection={isReadonly ? false : wrapperProps?.enableSelection ?? true}
                enableMultiRowSelection
                rowSelection={rowSelections}
                onRowSelectionChange={setRowSelections}
                enableVirtualization
                pagination={pagination}
                manualPagination={isSyncServer}
                onPaginationChange={handlePaginationChange}
                rowCount={resolvedRowCount}
                enablePagination={resolvedEnablePagination}
                columnVisibility={initialColumnVisibility}
                getRowClassName={getRowClassName}
            />
        </div>
    );
};

export default TableWrapper;