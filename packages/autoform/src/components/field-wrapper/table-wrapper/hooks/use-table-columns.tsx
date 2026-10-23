import type { ColumnDef } from "@ldc/data-table";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Checkbox } from "@ldc/ui/components/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { AlertCircle, AlertTriangle, MoreHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { ErrorBoundary } from "react-error-boundary";
import { Slot, useSlots } from "../../../../contexts/slot.context";
import { getByPath } from "../../../../libs/action-engine";
import type { IField } from "../../../../types/schema";
import ActionCell from "../components/action-cell";
import TableCellField from "../components/table-cell-field";

interface UseTableColumnsOptions {
    tableRowWrapper: IField;
    isReadonly: boolean;
    showAdd: boolean;
    showMinus: boolean;
    onRemoveRow: (id: string) => void;
    onCellChange: (id: string, fieldKey: string, value: any) => void;
    isFiltering?: boolean;
    namePrefix?: string;

    updateRow: (rowId: string, partial: Record<string, any>) => void;
}


const BadgeCell = ({ labels }: { labels: string[] }) => (
    <div className="flex flex-wrap gap-1">
        {labels.map((label) => (
            <Badge key={label} variant="secondary" className="text-xs">
                {label}
            </Badge>
        ))}
    </div>
);

const CheckboxCell = ({ checked }: { checked: boolean }) => (
    <div className="flex items-center justify-center">
        <Checkbox checked={checked} aria-readonly />
    </div>
);

const ValidationIcon = ({ status, message }: { status: "error" | "warning"; message: string }) => {
    const Icon = status === "error" ? AlertCircle : AlertTriangle;
    const colorClass = status === "error" ? "text-destructive" : "text-yellow-500";

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
                    <Icon className={`size-4 ${colorClass} ml-2 inline-block`} />
                </TooltipTrigger>
                <TooltipContent>
                    <p>{message}</p>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
};

export const useTableColumns = ({
    tableRowWrapper,
    isReadonly,
    showAdd,
    showMinus,
    onRemoveRow,
    onCellChange,
    isFiltering,
    namePrefix = "",
    updateRow,
}: UseTableColumnsOptions) => {

    const { slots } = useSlots();
    const entries = useMemo(() => slots["table-row-actions"], [slots]);
    const columnEntries = useMemo(() => slots["table-button-columns"], [slots]);

    const cellChangeRef = useRef(onCellChange);
    useEffect(() => {
        cellChangeRef.current = onCellChange;
    }, [onCellChange]);

    const dataColumns = useMemo<ColumnDef<any>[]>(() => {
        return tableRowWrapper?.fields?.map((field: IField) => ({
            accessorKey: field?.fieldConfig?.controlProps?.aliasKey ?? field?.key,
            header: () => (
                <div className="flex items-center gap-1">
                    {field?.fieldConfig?.wrapperProps?.label}
                    {field?.fieldConfig?.rules?.find((rule) => rule?.method === "required") && (
                        <span className="text-destructive font-bold">*</span>
                    )}
                </div>
            ),
            size: field?.fieldConfig?.wrapperProps?.size ?? 200,
            minSize: Math.min(field?.fieldConfig?.wrapperProps?.minSize ?? 200, field?.fieldConfig?.wrapperProps?.size ?? 200),
            fixed: field?.fieldConfig?.wrapperProps?.fixed,
            cell: ({ row }: any) => {
                const rawKey = field?.fieldConfig?.controlProps?.aliasKey ?? field?.key;

                const validateStatus = row.original.__validate_status as "error" | "warning" | undefined;
                const validateMessage = row.original.__validate_message as string | undefined;
                const validateField = row.original.__validate_field as string | undefined;
                const showValidation = validateStatus && (validateStatus === "error" || validateStatus === "warning") && validateField && validateField === rawKey;

                if (isReadonly) {
                    let content: React.ReactNode;

                    const rawValue = getByPath(row.original, rawKey) ?? row.getValue(rawKey);
                    const resolved = rawValue;

                    if (Array.isArray(resolved)) {
                        content = <BadgeCell labels={resolved} />;
                    } else if (typeof resolved === "boolean") {
                        content = <CheckboxCell checked={resolved} />;
                    } else {
                        content = resolved;
                    }

                    return (
                        <ErrorBoundary
                            fallbackRender={() => null}
                        >
                            <div className="flex items-center gap-1 h-full w-full">
                                {showValidation && (
                                    <ValidationIcon
                                        status={validateStatus}
                                        message={validateMessage ?? ""}
                                    />
                                )}

                                {content}
                            </div>
                        </ErrorBoundary>
                    );
                };

                return (
                    <ErrorBoundary
                        fallbackRender={() => null}
                    >
                        <div className="flex items-center gap-1 h-full w-full">
                            {showValidation && (
                                <ValidationIcon
                                    status={validateStatus}
                                    message={validateMessage ?? ""}
                                />
                            )}

                            <TableCellField
                                field={field}
                                value={getByPath(row.original, rawKey) ?? row.getValue(rawKey)}
                                rowIndex={row.index}
                                rowId={row.original._id}
                                fieldKey={field?.key}
                                name={`${namePrefix}.${row.index}.${field?.key}`}
                                onCellChange={(...args) => cellChangeRef.current(...args)}
                            />
                        </div>
                    </ErrorBoundary>
                );
            },
        })) ?? [];
    }, [tableRowWrapper, isReadonly]);

    const buttonColumn = useMemo<ColumnDef<any>[]>(() => {
        if (!columnEntries?.length) return [];

        return [{
            accessorKey: "button",
            meta: { align: "center" as const },
            fixed: "right",
            minSize: 80,
            header: "",
            cell: ({ row }: any) => <div className="flex items-center justify-center">
                <Slot
                    name="table-button-columns"
                    rowId={row.original._id}
                    rowData={row.original}
                    rowIndex={row.index}
                    updateRowById={(rowId: string, partial: Record<string, any>) => {
                        updateRow(rowId, partial)
                    }}
                    updateRow={(partial: Record<string, any>) => updateRow(row.original._id, partial)}
                />
            </div>,
        }];
    }, [columnEntries]);

    const actionsColumn = useMemo<ColumnDef<any>[]>(() => {
        if (!showMinus && !entries?.length) return [];

        return [{
            accessorKey: "actions",
            meta: { align: "center" as const },
            fixed: "right",
            size: 90,
            header: "",
            cell: ({ row }: any) => {
                return (
                    <div className="flex items-center justify-center gap-2">
                        {showMinus && (<ActionCell rowId={row.original._id} onRemove={onRemoveRow} />)}

                        {entries?.length && (
                            <DropdownMenu>
                                <DropdownMenuTrigger>
                                    <Button variant="ghost" size="icon" disabled={isFiltering}>
                                        <MoreHorizontal className="size-4" />
                                    </Button>
                                </DropdownMenuTrigger>

                                <DropdownMenuContent align="end" className="min-w-fit">
                                    <Slot
                                        name="table-row-actions"
                                        rowId={row.original._id}
                                        rowData={row.original}
                                        rowIndex={row.index}
                                        updateRowById={(rowId: string, partial: Record<string, any>) => updateRow(rowId, partial)}
                                        updateRow={(partial: Record<string, any>) => updateRow(row.original._id, partial)}
                                    />
                                </DropdownMenuContent>
                            </DropdownMenu>
                        )}
                    </div>
                );
            },
        }];
    }, [showAdd, showMinus, onRemoveRow, isFiltering, updateRow, entries]);

    const columns = useMemo<ColumnDef<any>[]>(
        () => [...dataColumns, ...buttonColumn, ...actionsColumn],
        [buttonColumn, dataColumns, actionsColumn]
    );

    const initialColumnVisibility = useMemo(() => {
        const visibility: Record<string, boolean> = {};

        tableRowWrapper?.fields?.forEach((field: IField) => {
            const hidden = field?.fieldConfig?.wrapperProps?.columnvisibility;
            visibility[field.key] = hidden !== "hidden";
        });

        return visibility;
    }, [tableRowWrapper]);

    return { columns, initialColumnVisibility };
};
