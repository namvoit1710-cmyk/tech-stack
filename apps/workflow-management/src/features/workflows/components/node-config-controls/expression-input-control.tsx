// Expression-aware text/textarea controls for the node-config Builder. Registered under the
// custom keys "ExpressionControl" / "ExpressionTextareaControl" (the Builder merges built-ins
// OVER custom keys, so we cannot reuse "InputControl"; detail-form remaps the schema to these
// keys instead). Thin adapter over the shared ExpressionAutocompleteInput, feeding it the
// node-detail context's design-time / run-time variable suggestions.
import { ExpressionAutocompleteInput } from "@/components/expression-input/expression-autocomplete-input";
import type { IField } from "@ldc/autoform";
import { cn } from "@ldc/ui";
import { forwardRef } from "react";
import { useNodeDetailContext } from "../modals/nodes-detail-modal/node-detail-provider";

type EditableEl = HTMLInputElement | HTMLTextAreaElement;

// Mirrors @ldc/autoform's (unexported) FieldComponentProps spread by the rhf <Controller>.
interface FieldControlProps {
    field?: IField;
    name?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [key: string]: any;
}

const Control = forwardRef<EditableEl, FieldControlProps & { multiline?: boolean }>((props, ref) => {
    const { field: _field, error, multiline, value, onChange, onBlur, className, placeholder, name } = props;
    const { exprSuggestions } = useNodeDetailContext();
    return (
        <ExpressionAutocompleteInput
            ref={ref}
            multiline={multiline}
            value={typeof value === "string" ? value : value == null ? "" : String(value)}
            onChange={(next) => onChange?.(next)}
            onBlur={onBlur}
            error={!!error}
            className={cn("text-sm", className)}
            placeholder={placeholder}
            name={name}
            suggestions={exprSuggestions}
        />
    );
});
Control.displayName = "ExpressionControl";

export const ExpressionInputControl = forwardRef<EditableEl, FieldControlProps>((props, ref) => (
    <Control {...props} ref={ref} />
));
ExpressionInputControl.displayName = "ExpressionInputControl";

export const ExpressionTextareaControl = forwardRef<EditableEl, FieldControlProps>((props, ref) => (
    <Control {...props} multiline ref={ref} />
));
ExpressionTextareaControl.displayName = "ExpressionTextareaControl";
