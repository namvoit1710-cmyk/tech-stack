import { Button } from "@ldc/ui/components/button";
import { Label } from "@ldc/ui/components/label";
import { PlusIcon, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { Controller, useFieldArray, useFormContext } from "react-hook-form";
import type { IField } from "../../types/schema";
import BuilderField from "./builder-field";

/**
 * Renders an array-of-objects as a labelled table: a header row of column
 * labels plus one editable row per item, with add / remove controls.
 *
 * Used for the backend's `Widget.ARRAY_TABLE` fields (served with
 * `fieldWrapper: "TableRowWrapper"`), e.g. the WORKFLOW node's `input_mapping`
 * (`param_name` / `param_value` rows). The data behaviour mirrors
 * `BuilderArrayField` exactly — same `useFieldArray` wiring and the synthetic
 * per-row `field_id` that downstream consumers (e.g. SWITCH port sync) read —
 * so only the visual layout differs.
 */
const stripCellLabel = (cell: IField): IField => ({
    ...cell,
    fieldConfig: { ...cell.fieldConfig, fieldWrapper: "FragmentWrapper", wrapperProps: {} },
});

const BuilderTableField: React.FC<{
    field: IField;
    path: string[];
}> = ({ field, path }) => {
    const rowTemplate = field.fields?.[0];
    const columns = useMemo(() => rowTemplate?.fields ?? [], [rowTemplate]);

    const { control } = useFormContext();
    const { append, remove, fields } = useFieldArray({
        control,
        name: `${path.join(".")}`,
        keyName: "_id",
    });

    const defaultValue = useMemo(() => {
        return columns.reduce((acc, col) => {
            acc[col.key] = col.default ?? null;
            return acc;
        }, {} as Record<string, unknown>);
    }, [columns]);

    const label: string | undefined = field.fieldConfig.wrapperProps?.label;

    return (
        <div className="flex flex-col gap-2 my-4">
            {label && (
                <Label className="border-b border-b-gray-200 w-full pb-2">{label}</Label>
            )}

            <div className="flex flex-col gap-1 pl-1">
                {!!fields.length && (
                    <div className="flex items-center gap-2">
                        <span className="w-8 shrink-0" />
                        {columns.map((col) => (
                            <div
                                key={col.key}
                                className="flex-1 min-w-[150px] text-xs font-medium text-muted-foreground"
                            >
                                {col.fieldConfig?.wrapperProps?.label ?? col.key}
                            </div>
                        ))}
                    </div>
                )}

                {!fields.length && (
                    <p className="text-muted-foreground text-center py-2">
                        No {label?.toLowerCase() ?? "items"}
                    </p>
                )}

                {fields.map((item, index) => (
                    <div
                        key={item._id}
                        className="flex items-start gap-2 border-b border-b-border py-1"
                    >
                        <Button
                            onClick={() => remove(index)}
                            variant="outline"
                            size="icon"
                            className="mt-1 w-8 shrink-0 cursor-pointer"
                        >
                            <Trash2 className="size-3 text-destructive" />
                        </Button>

                        <Controller
                            name={`${path.join(".")}.${index.toString()}.field_id`}
                            control={control}
                            defaultValue={item._id}
                            render={() => <></>}
                        />

                        {columns.map((col) => (
                            <div
                                key={`${path.join(".")}.${index.toString()}.${col.key}`}
                                className="flex-1 min-w-[150px]"
                            >
                                <BuilderField
                                    field={stripCellLabel(col)}
                                    path={[...path, index.toString(), col.key]}
                                />
                            </div>
                        ))}
                    </div>
                ))}
            </div>

            <Button onClick={() => append(defaultValue)} variant="outline">
                <PlusIcon />
            </Button>
        </div>
    );
};

export default BuilderTableField;
