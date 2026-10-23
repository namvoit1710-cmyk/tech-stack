import type { RowSelectionState } from "@ldc/data-table";
import { DataTable } from "@ldc/data-table";
import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { SearchInput } from "@ldc/ui/blocks/search-input/search-input";
import { Button } from "@ldc/ui/components/button";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@ldc/ui/components/dialog";
import { useEffect, useMemo, useState } from "react";
import type { ISelectTableDialogProps, OptionRecord } from "../types/types";
import { deriveColumns } from "./select-table-columns";

function withIds(rows: OptionRecord[]): (OptionRecord & { _id: string })[] {
    return rows.map((row) => ({
        ...row,
        _id: String((row._id ?? row.id ?? crypto.randomUUID()) as string),
    }));
}

const SelectTableDialog = ({
    open,
    onOpenChange,
    multiple,
    valueKey,
    data,
    loading,
    error,
    totalCount,
    pagination,
    onPaginationChange,
    searchValue,
    onSearchChange,
    currentValue,
    onConfirm,
    selectionId,
}: ISelectTableDialogProps) => {
    const { t } = useTranslation("common");
    const rows = useMemo(() => withIds(data), [data]);
    const columns = useMemo(() => deriveColumns(rows), [rows]);

    const initialSelection = useMemo<RowSelectionState>(() => {
        if (!selectionId) return {};

        const [pageStr, rowStr] = selectionId.split("_");
        const storedPage = Number(pageStr);
        const storedRow = Number(rowStr);

        if (
            Number.isNaN(storedPage) ||
            Number.isNaN(storedRow) ||
            storedPage !== pagination.pageIndex
        ) return {};

        const row = rows[storedRow];
        if (!row) return {};

        return { [row._id]: true };
    }, [selectionId, pagination.pageIndex, rows]);

    const [rowSelection, setRowSelection] = useState<RowSelectionState>(initialSelection);

    useEffect(() => {
        if (!open) return;
        setRowSelection(initialSelection);
    }, [initialSelection, open]);

    const selectedIds = Object.keys(rowSelection).filter((id) => rowSelection[id]);
    const hasSelection = selectedIds.length > 0;

    const handleConfirm = () => {
        const selectedRows = rows.filter((row) => selectedIds.includes(row._id));
        if (multiple) {
            onConfirm(selectedRows, "");
        } else {
            const first = selectedRows[0];
            if (!first) return;
            const rowIndex = rows.indexOf(first);
            const newSelectionId = `${pagination.pageIndex}_${rowIndex}`;
            onConfirm(first, newSelectionId);
        }
        onOpenChange(false);
    };

    const handleCancel = () => {
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="min-w-[90vw] sm:max-w-auto max-h-[85vh] max-w-[90vw]! flex flex-col overflow-hidden">
                <DialogHeader>
                    <DialogTitle>{t("select_placement")}</DialogTitle>
                </DialogHeader>

                <div className="flex flex-1 min-h-0 flex-col gap-3 w-full overflow-hidden">
                    <SearchInput
                        role="searchbox"
                        placeholder="Search..."
                        value={searchValue}
                        onChange={(e) => onSearchChange(e.target.value)}
                        disabled={loading}
                        wrapperClassName="max-w-100"
                    />

                    <div
                        className={cn(
                            "relative flex flex-1 min-h-0 flex-col overflow-hidden transition-opacity",
                            loading ? "opacity-60 pointer-events-none" : ""
                        )}
                    >
                        {loading && <div className="loader-bar w-full" />}

                            {!loading || rows.length > 0 ? (
                            <DataTable
                                data={rows}
                                columns={columns}
                                enableSelection
                                enableMultiRowSelection={multiple}
                                rowSelection={rowSelection}
                                onRowSelectionChange={setRowSelection}
                                enablePagination
                                manualPagination
                                rowCount={totalCount}
                                pagination={pagination}
                                onPaginationChange={onPaginationChange}
                            />
                        ) : null}

                        {error != null && (
                            <div className="flex flex-col items-center justify-center gap-3 py-4">
                                <span className="text-sm text-destructive">{error}</span>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => onSearchChange(searchValue)}
                                >
                                    {t("button.retry")}
                                </Button>
                            </div>
                        )}
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={handleCancel}>
                        {t("button.cancel")}
                    </Button>
                    <Button onClick={handleConfirm} disabled={loading || !hasSelection}>
                        {t("button.confirm")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default SelectTableDialog;
