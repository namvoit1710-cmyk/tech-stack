import { Button } from "@ldc/ui/components/button";
import { Label } from "@ldc/ui/components/label";
import { PlusIcon, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { Controller, useFieldArray, useFormContext } from "react-hook-form";
import type { IField } from "../../types/schema";
import { useBuilderContext } from "../../contexts/builder.context";
import BuilderField from "./builder-field";

const BuilderArrayField: React.FC<{
    field: IField;
    path: string[];
}> = ({ field, path }) => {
    const subField = field.fields?.[0];
    const subFieldType = field.fields?.[0]?.outputType;

    const { control } = useFormContext();
    const { append, remove, fields } = useFieldArray({
        control,
        name: `${path.join(".")}`,
        keyName: "_id",
    });

    const { readonly } = useBuilderContext();

    const defaultValue = useMemo(() => {
        if (subFieldType === "object") {
            const obj = subField?.fields?.reduce((acc, curr) => {
                acc[curr.key] = curr.default ?? null;
                return acc;
            }, {} as Record<string, unknown>);
            return obj;
        }

        return {}
    }, [subFieldType])

    return (
        <div className="flex flex-col gap-2 my-4">
            {field.fieldConfig.wrapperProps?.label && (
                <Label className="border-b border-b-gray-200 w-full pb-2">
                    {field.fieldConfig.wrapperProps.label ?? ""}
                </Label>
            )}

            <div className="flex flex-col gap-2 pl-4">
                {!fields.length && (
                    <p className="text-muted-foreground text-center">No {(field.fieldConfig.wrapperProps?.label)?.toLowerCase() ?? "items"}</p>
                )}
                {!!fields.length && fields.map((item, index) => (
                    <div
                        key={item._id}
                        className="flex items-end gap-2 border-b border-b-border"
                    >
                        {!readonly && (
                            <Button
                                onClick={() => remove(index)}
                                variant="outline"
                                size="icon"
                                className="mb-2 cursor-pointer"
                            >
                                <Trash2 className="size-3 text-destructive" />
                            </Button>
                        )}

                        <div className="flex items-center flex-wrap gap-x-2 flex-2">
                            <Controller
                                name={`${path.join(".")}.${index.toString()}.field_id`}
                                control={control}
                                defaultValue={item._id}
                                render={() => (
                                    <></>
                                )}
                            />
                            {(subField?.fields ?? []).map((field) => (
                                <div key={`${path.join(".")}.${index.toString()}.${field.key}`} className="flex-1 min-w-[150px] mb-2">
                                    <BuilderField
                                        field={field}
                                        path={[...path, index.toString(), field.key]}
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>

            {!readonly && (
                <Button
                    onClick={() => append(defaultValue)}
                    variant="outline"
                >
                    <PlusIcon />
                </Button>
            )}
        </div>
    );
};

export default BuilderArrayField;