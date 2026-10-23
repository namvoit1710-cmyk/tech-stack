import type { PaginationState } from "@ldc/data-table";
import type { FieldComponentProps } from "../../../../types/schema";

export type OptionRecord = Record<string, unknown>;

export interface ISelectTableControlProps extends FieldComponentProps {
    multiple?: boolean;
    options?: OptionRecord[];
    valueKey?: string;
    labelKey?: string;
    placeholder?: string;
    /** Autoform action name fired on confirm; payload carries the picked record as rowData. */
    selectAction?: string;
    onChange?: (event: { target: { name: string; value: OptionRecord | OptionRecord[] | null } }) => void;
}

export interface ISelectTableTriggerProps {
    value: OptionRecord | OptionRecord[] | null;
    multiple: boolean;
    labelKey: string;
    labelOverride?: string;
    placeholder?: string;
    disabled?: boolean;
    onOpen: () => void;
    onClear: () => void;
}

export interface ISelectTableDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    multiple: boolean;
    valueKey: string;
    labelKey: string;
    data: OptionRecord[];
    loading: boolean;
    error: string | null;
    totalCount: number;
    pagination: PaginationState;
    onPaginationChange: (state: PaginationState) => void;
    searchValue: string;
    onSearchChange: (value: string) => void;
    currentValue: OptionRecord | OptionRecord[] | null;
    onConfirm: (value: OptionRecord | OptionRecord[], selectionId: string) => void;
    selectionId: string | null;
}
