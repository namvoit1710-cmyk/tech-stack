import type { DragEvent } from "react";

import { Input } from "@ldc/ui/components/input";

const SINGLE_EXPRESSION = /^\{\{\$[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+|\[\d+\])*\}\}$/;

export const isSingleExpression = (value: string): boolean => SINGLE_EXPRESSION.test(value.trim());

export interface OptionsVariableInputProps {
    value: string;
    onChange: (next: string) => void;
}

export function OptionsVariableInput({ value, onChange }: OptionsVariableInputProps) {
    const handleDrop = (e: DragEvent<HTMLInputElement>) => {
        const dropped = e.dataTransfer.getData("text/plain");
        if (!dropped) return;
        e.preventDefault();
        onChange(dropped.trim());
    };

    const invalid = value.trim() !== "" && !isSingleExpression(value);

    return (
        <div className="px-3 mb-2 flex flex-col gap-1">
            <Input
                aria-label="Options variable"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                placeholder="{{$node.response_body.items}}"
                aria-invalid={invalid}
                className="h-8 text-xs! font-mono border-dashed"
            />
            {invalid ? (
                <p role="alert" className="text-[10px] text-destructive">
                    Expected a single {"{{$source.path}}"} expression with no spaces or extra text.
                </p>
            ) : (
                <p className="text-[10px] text-muted-foreground">
                    Drag an array from the output panel. Value key / Label key map each item.
                </p>
            )}
        </div>
    );
}

export default OptionsVariableInput;
