import type { ColumnDef, Row } from "@ldc/data-table";
import { useTranslation } from "@ldc/i18n";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Checkbox } from "@ldc/ui/components/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@ldc/ui/components/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { AlertCircle, AlertTriangle, MoreHorizontal, Pencil, Trash } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { Slot, useSlots } from "../../../../contexts/slot.context";
import { getByPath } from "../../../../libs/action-engine/utils";
import type { IField } from "../../../../types/schema";
import { resolveTableCellDisplayValue } from "../utils/resolve-cell-display";
import type { RowDisplayStatus } from "./use-table-data";

type ValidationStatus = "error" | "warning";
type CellRowStatus = RowDisplayStatus | "edited";
type CellDisplayValue = Parameters<typeof resolveTableCellDisplayValue>[0]["value"];

interface ODataRowMeta {
    _status?: RowDisplayStatus;
    __status?: CellRowStatus;
    __validate_status?: ValidationStatus;
    __validate_message?: string;
    __validate_field?: string;
}

type ODataTableRow<T extends Record<"_id", string>> = T & ODataRowMeta & Record<string, unknown>;

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
                <TooltipTrigger>
                    <Icon className={`size-4! ${colorClass} inline-block`} />
                </TooltipTrigger>
                <TooltipContent>
                    <p>{message}</p>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
};

interface UseTableColumnsOptions<T extends Record<"_id", string>> {
    tableRowWrapper: IField;
    isReadonly: boolean;
    selectedIndex: string[];

    isDeletable?: boolean;
    isEditable?: boolean;
    contextReadonly?: boolean;

    sortState: {
        field: string;
        order: "asc" | "desc";
    } | null;
    onSortChange?: (field: string, order: "asc" | "desc") => void;

    onAction?: (action: string, row: Row<T>) => void;

    trackUpdate: (rowId: string, partial: Partial<T> & Record<string, unknown>) => void;
}

export const useTableColumns = <T extends Record<"_id", string>>({
    tableRowWrapper,
    isReadonly,

    isDeletable,
    isEditable,
    contextReadonly = false,

    onAction,

    trackUpdate,
}: UseTableColumnsOptions<T>) => {
    const { t: tCommon } = useTranslation("common");
    const onActionRef = useRef(onAction);
    const tableFields = Array.isArray(tableRowWrapper?.fields) ? tableRowWrapper.fields : [];

    useEffect(() => {
        onActionRef.current = onAction;
    }, [onAction]);

    const dataColumns = useMemo<ColumnDef<T>[]>(() => {
        return tableFields.map((field: IField) => {
            const rules = Array.isArray(field?.fieldConfig?.rules) ? field.fieldConfig.rules : [];

            const rawKey = field?.fieldConfig?.controlProps?.aliasKey ?? field?.key;
            const hasDot = typeof rawKey === "string" && rawKey.includes(".");
            const columnId = typeof rawKey === "string" ? rawKey.replace(/\./g, "_") : rawKey;

            const valueKey = field?.fieldConfig?.controlProps?.valueKey ?? "id";
            const labelKey = field?.fieldConfig?.controlProps?.labelKey ?? "value";
            const rawOptions = field?.fieldConfig?.controlProps?.options;
            const options = Array.isArray(rawOptions) ? rawOptions : [];
            const hasOptions = options.length > 0;

            return {
                id: columnId,
                ...(hasDot
                    ? { accessorFn: (row: T) => getByPath(row, rawKey) }
                    : { accessorKey: rawKey }
                ),
                header: () => (
                    <div className="flex items-center gap-1">
                        {field?.fieldConfig?.wrapperProps?.label}
                        {rules.find((rule) => rule?.method === "required") && (
                            <span className="text-destructive font-bold">*</span>
                        )}
                    </div>
                ),
                meta: { align: field?.fieldConfig?.wrapperProps?.align ?? "left" as const },
                size: field?.fieldConfig?.wrapperProps?.size ?? 200,
                minSize: Math.min(
                    typeof field?.fieldConfig?.wrapperProps?.minSize === "number"
                        ? field.fieldConfig.wrapperProps.minSize
                        : 200,
                    typeof field?.fieldConfig?.wrapperProps?.size === "number"
                        ? field.fieldConfig.wrapperProps.size
                        : 200,
                ),
                fixed: field?.fieldConfig?.wrapperProps?.fixed,
                cell: ({ row }: { row: Row<T> }) => {
                    const rowData = row.original as ODataTableRow<T>;
                    const status = rowData.__status ?? rowData._status ?? "none";
                    const isLocalChange = status === "inserted" || status === "updated" || status === "edited";
                    const rawPathKey = typeof rawKey === "string" ? rawKey : undefined;
                    const pathKey = rawPathKey;

                    const validateStatus = rowData.__validate_status;
                    const validateMessage = rowData.__validate_message;
                    // Only actual issues get an icon. A cleared/"success" status must never
                    // fall through to the warning icon (AlertTriangle) — even if a stale
                    // __validate_message/__validate_field lingers from a prior failed run.
                    const showValidation =
                        (validateStatus === "error" || validateStatus === "warning") &&
                        !!validateMessage &&
                        rowData.__validate_field === rawKey;

                    const rawValue = ((pathKey ? getByPath(rowData, pathKey) : undefined)
                        ?? (rawPathKey ? rowData[rawPathKey] : undefined)) as CellDisplayValue;
                    // Find the option whose value matches the cell value (supports both
                    // `{ id, value }` legacy options and `{ value, label }` status-style options),
                    // used to show the display `label` instead of the raw value.
                    const matchedOption = hasOptions
                        ? options.find(
                            (opt: Record<string, unknown>) =>
                                String(opt?.[valueKey] ?? opt?.value) === String(rawValue),
                        )
                        : undefined;

                    const displayValue = resolveTableCellDisplayValue({
                        value: rawValue,
                        options,
                        valueKey,
                        labelKey,
                        shouldResolveOptions: isLocalChange && hasOptions,
                    });

                    let content: React.ReactNode;

                    if (Array.isArray(displayValue)) {
                        content = <BadgeCell labels={displayValue.map(String)} />;
                    } else if (typeof displayValue === "boolean") {
                        content = <CheckboxCell checked={displayValue} />;
                    } else if (matchedOption && typeof matchedOption.label === "string") {
                        // Prefer the option label so readonly/server rows show "Updated", not "updated".
                        content = matchedOption.label;
                    } else {
                        content = displayValue;
                    }

                    return (
                        <div className="flex items-center gap-2 w-full truncate">
                            {showValidation && (
                                <ValidationIcon
                                    status={validateStatus}
                                    message={validateMessage}
                                />
                            )}
                            <span className="line-clamp-2">
                                {content}
                            </span>
                        </div>
                    );
                },
            };
        });
    }, [tableFields, isReadonly]);

    const { slots } = useSlots();
    // SA-1239: the ⋯ "More Action" menu shows iff the node config registered a
    // row-more-action slot AND the builder is not in read-only context. No
    // isShowRowMoreAction flag; wrapperProps.readonly does not affect this.
    const hasRowMoreActionSlot = useMemo(() => {
        return !contextReadonly && !!slots["row-more-action"];
    }, [slots, contextReadonly]);

    const actionsColumn = useMemo<ColumnDef<T>[]>(() => {
        return [{
            accessorKey: "actions",
            meta: { align: "center" as const },
            fixed: "right",
            size: 120,
            header: "",
            cell: ({ row }) => {
                return (
                    <div className="flex items-center justify-center gap-1">
                        {isEditable && (
                            <Button variant="outline" size="icon-sm" onClick={() => onActionRef.current?.("edit", row)}>
                                <Pencil />
                            </Button>
                        )}

                        {(hasRowMoreActionSlot || isDeletable) && (
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="icon-sm">
                                        <MoreHorizontal />
                                    </Button>
                                </DropdownMenuTrigger>

                                <DropdownMenuContent align="end" className="w-50">
                                    {isDeletable && (
                                        <DropdownMenuItem variant="destructive" onClick={() => onActionRef.current?.("delete", row)}>
                                            <Trash className="mr-2" />
                                            {tCommon("button.delete")}
                                        </DropdownMenuItem>
                                    )}

                                    {hasRowMoreActionSlot && <Slot
                                        name="row-more-action"
                                        rowId={row.original._id}
                                        rowData={row.original}
                                        updateRow={(partial: Partial<T> & Record<string, unknown>) => {
                                            trackUpdate(row.original._id, { ...row.original, ...partial })
                                        }}

                                        updateRowById={(rowId: string, partial: Record<string, any>) => {
                                            trackUpdate(rowId, { ...row.original, ...partial, _action: "none" })
                                        }}
                                    />}
                                </DropdownMenuContent>
                            </DropdownMenu>
                        )}
                    </div>
                );
            },
        }];
    }, [hasRowMoreActionSlot, isDeletable, isEditable]);

    const columns = useMemo<ColumnDef<T>[]>(
        () => [...dataColumns, ...actionsColumn],
        [dataColumns, actionsColumn]
    );

    return { columns };
};
