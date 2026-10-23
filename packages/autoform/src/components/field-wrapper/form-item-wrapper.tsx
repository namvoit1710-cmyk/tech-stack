import { Field, FieldGroup, FieldLabel, FieldSet } from "@ldc/ui/components/field";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { InfoIcon } from "lucide-react";
import { useMemo } from "react";
import type { FieldWrapperProps } from "../../types/schema";

const FormItemWrapper = ({ children, field }: FieldWrapperProps) => {
    const wrapperProps = field?.fieldConfig.wrapperProps ?? {};
    const { label, description } = wrapperProps;
    const rules = field?.fieldConfig.rules ?? [];

    const hasRequiredRule = useMemo(() => {
        return rules.some(rule => rule.method === "required");
    }, [rules]);

    return (
        <FieldSet className="w-full mb-2 last:mb-0">
            <FieldGroup>
                <Field className="gap-1">
                    <FieldLabel htmlFor={field?.key}>
                        {label ?? field?.key}

                        {hasRequiredRule && <span className="text-red-500 -ml-1">*</span>}

                        {description && (
                            <TooltipProvider>
                                <Tooltip>
                                    <TooltipTrigger>
                                        <InfoIcon className="w-4 h-4 text-gray-400" />
                                    </TooltipTrigger>

                                    <TooltipContent>
                                        {description}
                                    </TooltipContent>
                                </Tooltip>
                            </TooltipProvider>
                        )}
                    </FieldLabel>


                    {children}
                </Field>
            </FieldGroup>
        </FieldSet>
    )
}

export default FormItemWrapper;