import type { PaginationState, Row } from "@ldc/data-table";
import { DataTable } from "@ldc/data-table";
import { memo } from "react";
import type { IField } from "../../../../types/schema";
import { useTableColumns } from "../hooks/use-table-columns";


export interface ODataTableControlProps<T> extends Omit<React.ComponentProps<typeof DataTable>, "columns" | "data"> {
    field: IField;
    data: T[];
    onPaginationChange: (pagination: PaginationState) => void;

    isDeletable?: boolean;
    isEditable?: boolean;
    contextReadonly?: boolean;
    selectionMode?: "single" | "multiple";

    trackUpdate: (rowId: string, partial: Partial<T> & Record<string, unknown>) => void;

    sortState: {
        field: string;
        order: "asc" | "desc";
    } | null;
    onSortChange?: (field: string, order: "asc" | "desc") => void;

    rowSelection?: Record<string, boolean>;
    onRowSelectionChange?: (selectedRowIds: Record<string, boolean>) => void;

    onRowAction?: (action: string, row: T) => void;
}

const ODataTable = <T extends Record<"_id", string>>(props: ODataTableControlProps<T>) => {
    const {
        field,
        data,

        sortState,
        onSortChange,

        isDeletable,
        isEditable,
        contextReadonly,
        selectionMode,

        onRowAction,

        trackUpdate,

        ...dataTableProps
    } = props;

    const subField: IField = field.fields?.[0] ?? field;

    const { columns } = useTableColumns({
        tableRowWrapper: subField,
        isReadonly: field.fieldConfig.wrapperProps?.isReadonly ?? false,
        selectedIndex: [],
        sortState,
        onSortChange,
        isDeletable,
        isEditable,
        contextReadonly,
        onAction: (action: string, row: Row<T>) => {
            onRowAction?.(action, row.original);
        },
        trackUpdate: trackUpdate,
    })

    return (
        <DataTable
            data={data}
            columns={columns}

            enableSelection
            enableMultiRowSelection={selectionMode !== "single"}

            enablePagination
            manualPagination
            enableVirtualization

            {...dataTableProps}
        />
    );
};

export default memo(ODataTable) as typeof ODataTable;