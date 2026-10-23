import { ChevronDown, Filter, ListIcon, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useChangeTracker } from "../hooks/use-change-tracker";
import useColumnVisible from "../hooks/use-column-visibility";
import { useODataFetch } from "../hooks/use-odata-fetch";
import { useODataState } from "../hooks/use-odata-state";
import { useODataToolbar } from "../hooks/use-odata-toolbar";
import useRowSelection from "../hooks/use-row-selection";
import { useTableData } from "../hooks/use-table-data";
import { resolveSelectedOutput } from "../utils/resolve-selected-output";
import ColumnVisibleDropdown from "./column-visible-dropdown";
import OdataAddEditModal from "./odata-add-edit-modal";
import ODataTable from "./odata-table";

import { cn } from "@ldc/ui";
import { SearchInput } from "@ldc/ui/blocks/search-input/search-input";
import { Button } from "@ldc/ui/components/button";
import { useDebounceValue } from "@ldc/ui/hooks/use-debounce-value";
import { useFormContext, useWatch } from "react-hook-form";
import { useBuilderContext } from "../../../../contexts/builder.context";
import { Slot } from "../../../../contexts/slot.context";
import type { FieldWrapperProps, IField } from "../../../../types/schema";
import MoreActionDropdown from "./more-action-dropdown";

import { useTranslation } from "@ldc/i18n";
import { Alert } from "@ldc/ui/blocks/alert-dialog/alert-dialog";
import "../styles/styles.css";
import ColumnFilterDialog from "./column-filter-dialog";
import ValidationStatusDropdown from "./validation-status-dropdown";

export interface IODataWrapperProps extends FieldWrapperProps {
    field: IField;
    path: string[];
}

const OdataWrapper = (props: IODataWrapperProps) => {
    const { field, path } = props;

    const { t } = useTranslation("common");
    const { t: tAutoform } = useTranslation("autoform");

    const name: string = useMemo(() => path.join("."), [path]);

    const { control, getFieldState, setValue } = useFormContext();
    const { error } = getFieldState(name);
    const { services, registerRefetch, unregisterRefetch, readonly: contextReadonly } = useBuilderContext();

    const [alertInstance, setAlertInstance] = useState<{ title: string, message: string, onConfirm: () => void } | null>(null);

    const fileId = useWatch({
        control,
        name: `__${name}_file_id`,
    }) as string | undefined;

    const wrapperProps = field.fieldConfig.wrapperProps ?? {};
    const {
        isAdd: _isAdd = true,
        isEdit: _isEdit = true,
        isDelete: _isDelete = true,
        isShowColumnSetting = true,
        isShowSearch = true,
    } = wrapperProps;
    const selectionMode = wrapperProps.selectionMode as "single" | "multiple" | undefined;

    const wrapperReadonly = wrapperProps.readonly === true;
    const readonly = contextReadonly || wrapperReadonly;

    const isAdd = readonly ? false : _isAdd;
    const isEdit = readonly ? false : _isEdit;
    const isDelete = readonly ? false : _isDelete;
    // SA-1239: the row-more-action (⋯) menu is gated purely on slot presence in
    // use-table-columns; only the builder's contextReadonly force-hides it. No
    // isShowRowMoreAction flag, and wrapperProps.readonly does NOT hide it.

    const {
        trackInsert, trackUpdate, trackDelete, trackDeleteBatch,
        changeMapRef, version,
    } = useChangeTracker({ name });

    const customHandlers = useMemo(() => ({
        updateTableData: (ctx: any) => {
            const updatedRows = ctx.lastResult?.updatedRows;
            if (!updatedRows || !Array.isArray(updatedRows)) {
                console.warn("[ODataWrapper] updateTableData: no updatedRows in lastResult");
                return;
            }

            updatedRows.forEach(row => {
                if (row._id) {
                    trackUpdate(row._id, row);
                }
            });
        }
    }), [trackUpdate]);

    const {
        pagination,
        setPagination,

        setSort,
        sortState,

        searchValue,
        setSearchValue,
        getFilterString,
        getOrderByString,
    } = useODataState();

    const [debouncedSearchValue] = useDebounceValue(searchValue, 300);

    const { columns, visibleColumns, setVisibleColumns } = useColumnVisible({ field });

    const {
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
    } = useODataToolbar({ columns, visibleColumns });

    const odataService = useODataFetch({
        odataService: services?.odata,
        endpoint: fileId ? `odata/${fileId}/data` : field?.fieldConfig?.wrapperProps?.endpoint,
        pagination,
        defaultOrderBy: getOrderByString(),
        defaultFilter: getFilterString(),
        searchValue: debouncedSearchValue,
        columnFilter: columnFilterString,
    });
    const isLoading = odataService.loading;

    const { tableData } = useTableData({
        serverData: odataService?.data || [],
        changeMapRef,
        version,
    });

    const editedCount = useMemo(
        () => Array.from(changeMapRef.current.values()).filter(r => r._action !== "none").length,
        [version]
    );

    const errorRows = useMemo(
        () => tableData.filter(r => r.__validate_status === "error"),
        [tableData]
    );
    const warningRows = useMemo(
        () => tableData.filter(r => r.__validate_status === "warning"),
        [tableData]
    );
    const displayData = useMemo(() => {
        const activeIds = new Set([...selectedErrorIds, ...selectedWarningIds]);
        if (!activeIds.size) return tableData;
        return tableData.filter(r => activeIds.has(r._id));
    }, [tableData, selectedErrorIds, selectedWarningIds]);

    const showLoading = isLoading;

    const { rowSelections, hasRowSelected, setRowSelections } = useRowSelection();

    // SA-1239 AC6: in selection mode (approval "Input Requested"), publish the current
    // selection as the node `selected` output — object for single, array for multiple.
    // cleanOutput keeps non-"__"-prefixed keys, so `selected` survives form submission.
    useEffect(() => {
        if (!selectionMode) return;
        setValue("selected", resolveSelectedOutput(tableData, rowSelections, selectionMode), {
            shouldDirty: true,
        });
    }, [selectionMode, tableData, rowSelections, setValue]);

    const [isRowActionModal, setIsRowActionModal] = useState(false);
    const [readOnly, setReadOnly] = useState(false);
    const [selectedRow, setSelectedRow] = useState<any>(null);

    const handleRowAction = useCallback((action: string, row: any) => {
        if (action === "edit") {
            setSelectedRow(row);
            setReadOnly(false);
            setIsRowActionModal(true);
        }

        if (action === "delete") {
            setAlertInstance({
                title: t("alert.delete_one", { plural: 1 }),
                message: t("alert.delete_one_message", { plural: 1 }),
                onConfirm: () => {
                    trackDelete(row._id);
                    setAlertInstance(null);
                }
            });
        }
    }, [t, trackDelete]);

    const tableSortState = useMemo(
        () => sortState?.field
            ? { field: sortState.field, order: sortState.order ?? "asc" as const }
            : { field: "", order: "asc" as const },
        [sortState?.field, sortState?.order]
    );

    const handleAddEditSubmit = (rowData: Record<string, any>) => {
        setIsRowActionModal(false);
        setSelectedRow(null);

        if (selectedRow) {
            trackUpdate(selectedRow._id, rowData);
            return;
        }

        trackInsert(rowData);
    }

    const handleRemoveSelectedRows = () => {
        setAlertInstance({
            title: t("alert.delete_many"),
            message: t("alert.delete_many_message"),
            onConfirm: () => {
                const selectedIds = Object.keys(rowSelections).filter((id) => rowSelections[id]);
                trackDeleteBatch(selectedIds);
                setRowSelections({});
                setAlertInstance(null);
            }
        });
    };

    const getRowClassName = useCallback((row: any) => {
        if (row.original.__selected) {
            return "[&_td]:bg-[color-mix(in_oklab,var(--color-blue-500)_10%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-blue-500)_10%,var(--color-background))] border-l-1 border-l-blue-500";
        }

        switch (row.original.__validate_status) {
            case "error": return "[&_td]:bg-[color-mix(in_oklab,var(--color-red-500)_10%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-red-500)_10%,var(--color-background))] border-l-1 border-l-red-500";
            case "warning": return "[&_td]:bg-[color-mix(in_oklab,var(--color-orange-500)_10%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-orange-500)_10%,var(--color-background))] border-l-1 border-l-orange-500";
            case "success": return "[&_td]:bg-[color-mix(in_oklab,var(--color-green-500)_10%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-green-500)_10%,var(--color-background))] border-l-1 border-l-green-500";
        }

        switch (row.original.__status) {
            case "inserted": return "[&_td]:bg-[color-mix(in_oklab,var(--color-purple-500)_10%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-purple-500)_10%,var(--color-background))] border-l-1 border-l-purple-500";
            case "edited": return "[&_td]:bg-[color-mix(in_oklab,var(--color-yellow-500)_10%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-yellow-500)_10%,var(--color-background))] border-l-1 border-l-yellow-500";
            case "deleted": return "[&_td]:bg-[color-mix(in_oklab,var(--color-red-500)_15%,var(--color-background))] [&_td>div]:bg-[color-mix(in_oklab,var(--color-red-500)_15%,var(--color-background))] border-l-1 border-l-red-500 opacity-60 pointer-events-none";
            default: return "";
        }
    }, []);

    useEffect(() => {
        registerRefetch?.(field.key, odataService.refetch);
        return () => unregisterRefetch?.(field.key);
    }, [odataService.refetch, field.key, registerRefetch, unregisterRefetch]);

    return (
        <>
            <section className="flex flex-col gap-2 flex-2 overflow-hidden py-1">
                {/* Row 1: Status bar */}
                <div className="flex items-center gap-2 justify-between">
                    <div className="flex items-center gap-2">
                        <ValidationStatusDropdown
                            status="error"
                            rows={errorRows}
                            selectedIds={selectedErrorIds}
                            onToggle={toggleErrorId}
                            onClear={clearErrorIds}
                            tableData={tableData}
                        />
                        <ValidationStatusDropdown
                            status="warning"
                            rows={warningRows}
                            selectedIds={selectedWarningIds}
                            onToggle={toggleWarningId}
                            onClear={clearWarningIds}
                            tableData={tableData}
                        />

                        <span className="h-4 w-0.5 bg-muted mx-1" />

                        {editedCount > 0 && (
                            <span className="text-sm text-muted-foreground flex items-center gap-1.5">
                                <span className="size-2 rounded-full bg-violet-500 inline-block" />
                                {tAutoform("table.edited_label")} <strong className="text-foreground">{editedCount}</strong>
                            </span>
                        )}
                    </div>

                    {isShowColumnSetting && (
                        <ColumnVisibleDropdown
                            value={visibleColumns}
                            columns={columns}
                            onChange={setVisibleColumns}
                        >
                            <Button variant="outline" size="sm" disabled={isLoading}>
                                <ListIcon className="size-4" />
                                <span>{tAutoform("table.field_list", { count: Object.values(visibleColumns).filter(Boolean).length, total: columns.length })}</span>
                                <ChevronDown className="ml-1 size-4" />
                            </Button>
                        </ColumnVisibleDropdown>
                    )}
                </div>

                {/* Row 2: Actions bar */}
                <div className="flex items-center gap-2 justify-between">
                    <div className="flex items-center gap-2 flex-1">
                        {isShowSearch && (
                            <SearchInput
                                placeholder={tAutoform("table.search_placeholder")}
                                wrapperClassName="flex-1 max-w-80"
                                disabled={isLoading}
                                value={searchValue}
                                onChange={(e) => setSearchValue(e.target.value)}
                            />
                        )}

                        <Button
                            variant="outline"
                            disabled={isLoading}
                            onClick={() => setFilterDialogOpen(true)}
                            className={activeFilterCount > 0 ? "border-primary text-primary" : ""}
                        >
                            <Filter className="size-4" />
                            {tAutoform("table.filter")}
                            {activeFilterCount > 0 && (
                                <span className="ml-1 bg-primary text-primary-foreground rounded-full px-1.5 text-xs font-semibold">
                                    {activeFilterCount}
                                </span>
                            )}
                        </Button>
                    </div>

                    <div className="flex items-center gap-1">
                        {!contextReadonly && (
                            <Slot
                                name="filter"
                                slotData={{
                                    tableData,
                                    rowSelections,
                                    pagination,
                                    sortState,
                                    searchValue,
                                    customHandlers,
                                }}
                            />
                        )}

                        {isDelete && (
                            <Button
                                variant="outline"
                                size="icon"
                                disabled={!hasRowSelected || isLoading}
                                onClick={handleRemoveSelectedRows}
                            >
                                <Minus />
                            </Button>
                        )}

                        {isAdd && (
                            <Button
                                size="icon"
                                variant="outline"
                                disabled={isLoading}
                                onClick={() => { setReadOnly(false); setIsRowActionModal(true); }}
                            >
                                <Plus />
                            </Button>
                        )}

                        <MoreActionDropdown />
                    </div>
                </div>

                <div
                    className={cn(
                        "relative flex-2 flex flex-col overflow-hidden transition-opacity",
                        showLoading ? "opacity-60 pointer-events-none" : ""
                    )}
                >
                    {showLoading && (
                        <div className="loader-bar w-full" />
                    )}

                    <ODataTable
                        field={field}
                        data={displayData}

                        isEditable={isEdit}
                        isDeletable={isDelete}
                        contextReadonly={contextReadonly}
                        selectionMode={selectionMode}

                        rowCount={odataService.totalCount ? odataService.totalCount : tableData.length}
                        manualPagination={!!odataService.totalCount}
                        pagination={pagination}
                        onPaginationChange={setPagination}

                        columnVisibility={visibleColumns}

                        rowSelection={rowSelections}
                        onRowSelectionChange={setRowSelections}

                        trackUpdate={trackUpdate}

                        sortState={tableSortState}
                        onSortChange={setSort}

                        onRowAction={handleRowAction}
                        getRowClassName={getRowClassName}
                    />
                </div>

                {error && (
                    <span className="text-sm text-destructive">
                        {error.message}
                    </span>
                )}
            </section>

            <OdataAddEditModal
                open={isRowActionModal}
                onOpenChange={(open) => {
                    if (!open) {
                        setIsRowActionModal(false);
                        setSelectedRow(null);
                        setReadOnly(false);
                    }
                }}
                readOnly={readOnly}
                field={field}
                isEdit={!!selectedRow}
                defaultValues={selectedRow}

                onSubmit={handleAddEditSubmit}
            />

            <Alert
                {...alertInstance}
                onClose={() => setAlertInstance(null)}
                open={!!alertInstance}
            />

            <ColumnFilterDialog
                open={filterDialogOpen}
                onOpenChange={setFilterDialogOpen}
                columns={filterColumns}
                tableData={tableData}
                value={columnFilters}
                onApply={(filters) => {
                    setColumnFilters(filters);
                    setPagination({ pageIndex: 0 });
                }}
            />
        </>
    )
}

export default OdataWrapper;
