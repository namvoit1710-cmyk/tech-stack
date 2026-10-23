import { Button } from "@ldc/ui/components/button";
import { Check, Loader2 } from "lucide-react";
import type { IconName } from "lucide-react/dynamic";
import { DynamicIcon } from "lucide-react/dynamic";
import type { ComponentProps } from "react";
import { useCallback, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useBuilderContext } from "../../contexts/builder.context";
import { useFieldScript } from "../../libs/action-engine/script";
import type { FieldComponentProps, IButtonAction, IField } from "../../types/schema";

const pascalToKebabCase = (str: string): string =>
    str.replace(/([a-z])([A-Z])/g, "$1-$2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1-$2").toLowerCase();

const matchWatchValue = (watched: unknown, expected: unknown): boolean => {
    if (watched === undefined || watched === null) return false;
    if (Array.isArray(watched)) return watched.includes(expected);
    if (typeof watched === "object") return JSON.stringify(watched) === JSON.stringify(expected);
    return watched === expected;
};


export interface IButtonControlProps extends FieldComponentProps, Omit<ComponentProps<typeof Button>, "name" | "onChange" | "value"> {
    label?: string;
    field?: IField;
    action?: string;
    actionPayload?: Record<string, unknown>;
    events?: IButtonAction;
    rowId?: string;
    rowData?: Record<string, any>;
    rowIndex?: number;
    updateRowById?: (rowId: string, partial: Record<string, any>) => void;
    updateRow?: (partial: Record<string, any>) => void;
    loadingOnAction?: boolean;
    confirmBefore?: boolean;
    confirmMessage?: string;
    icon?: string;

    slotData?: Record<string, any>;

    watchField?: string;
    watchValue?: unknown;
}

const ButtonControl = (props: IButtonControlProps) => {
    const {
        label,
        className,
        variant,
        size,

        action,
        actionPayload,
        events,
        loadingOnAction = false,
        confirmBefore = false,
        confirmMessage = "Are you sure?",

        onClick,

        field,
        icon,
        name: _name,

        rowId, rowData, rowIndex, updateRow, updateRowById,

        slotData = {},
        watchField,

        watchValue,

        ...rest
    } = props;

    const { onFormActions } = useBuilderContext();
    const runFieldScript = useFieldScript({ row: { rowId, rowIndex, rowData, updateRow, updateRowById } });
    const formContext = useFormContext();
    const [loading, setLoading] = useState(false);

    const watchedValue = useWatch({ name: watchField ?? "", disabled: !watchField });
    const isSelected = watchField ? matchWatchValue(watchedValue, watchValue) : false;

    const resolvedAction = action ?? events?.action;

    const executeAction = useCallback(async () => {

        if (!resolvedAction || !onFormActions) return;

        const payload: Record<string, unknown> = {
            ...actionPayload,
            ...(events ?? {}),
            formValues: formContext?.getValues?.() ?? {},
            rowId,
            rowData,
            rowIndex,
            updateRow,
            updateRowById,
            ...(slotData ?? {})
        };

        if (loadingOnAction) setLoading(true);

        try {
            await onFormActions(resolvedAction, payload);
        } finally {
            if (loadingOnAction) setLoading(false);
        }
    }, [resolvedAction, onFormActions, actionPayload, events, formContext, loadingOnAction, rowId, rowData, updateRow, updateRowById, slotData]);

    const handleClick = useCallback(async (e: React.MouseEvent<HTMLButtonElement>) => {
        // Run the inline onClick script and, when loadingOnAction is set, show the
        // spinner while it runs (the script is async — e.g. ctx.gateway.invoke).
        if (field?.fieldConfig?.script?.onClick) {
            if (loadingOnAction) setLoading(true);
            try {
                await runFieldScript(field, "onClick", { name: field.key });
            } finally {
                if (loadingOnAction) setLoading(false);
            }
        }

        if (onClick && !resolvedAction) {
            onClick(e);
            return;
        }

        if (confirmBefore) {
            const confirmed = window.confirm(confirmMessage);
            if (!confirmed) return;
        }

        await executeAction();
    }, [field, runFieldScript, onClick, resolvedAction, confirmBefore, confirmMessage, executeAction, loadingOnAction]);

    return (
        <Button
            type="button"
            variant={variant ?? "default"}
            size={size ?? "default"}
            disabled={rest.disabled || loading}
            className={isSelected ? `ring-2 ring-primary bg-primary/15 ${className ?? ""}` : className}
            data-selected={isSelected || undefined}
            onClick={handleClick}
        >
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {!loading && isSelected && <Check className="mr-1 h-4 w-4" />}
            {!loading && !isSelected && icon && (
                <DynamicIcon
                    name={pascalToKebabCase(icon) as IconName}
                    className="mr-1 h-4 w-4"
                    strokeWidth={1.5}
                />
            )}
            {label ?? ""}
        </Button>
    );
};

export default ButtonControl;