import { Eye } from "lucide-react";
import { useState } from "react";

import Builder from "../../builder/builder";
import { cn } from "@ldc/ui";
import { useDesignerCtx } from "../hooks/use-designer-context";
import { toISchema } from "../utils/nodes";


export function PreviewCanvas() {
    const { nodes } = useDesignerCtx();

    const [isValid, setIsValid] = useState(false);

    // Convert designer nodes → ISchema
    const schema = toISchema(nodes);
    const hasFields = schema.fields.length > 0;

    return (
        <div className="flex flex-col h-full bg-background overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2.5 border-b border-border flex-shrink-0">
                <div className="flex items-center gap-2">
                    <Eye className="w-4 h-4 text-muted-foreground" />
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                        Preview
                    </span>
                    {hasFields && (
                        <span className={cn(
                            "text-[9px] font-semibold px-1.5 py-0.5 rounded-full",
                            isValid
                                ? "bg-green-100 text-green-700"
                                : "bg-muted text-muted-foreground"
                        )}>
                            {isValid ? "valid" : "invalid"}
                        </span>
                    )}
                </div>
            </div>

            {/* Builder output */}
            <div className="flex-1 overflow-y-auto flex justify-center px-4 py-6">
                <div className="w-full max-w-[480px]">
                    {!hasFields ? (
                        <EmptyPreview />
                    ) : (
                        <div className="rounded-lg border border-border bg-card p-3 text-sm!">
                            <Builder
                                key={schema.fields.map((f) => f.key).join(",")}
                                schema={schema}
                                onValidChange={setIsValid}
                            />
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function EmptyPreview() {
    return (
        <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
            <div className="w-12 h-12 rounded-xl border-2 border-dashed border-muted-foreground/20 flex items-center justify-center">
                <Eye className="w-6 h-6 text-muted-foreground/20" />
            </div>
            <div>
                <p className="text-sm font-medium text-muted-foreground">No fields yet</p>
                <p className="text-xs text-muted-foreground/60 mt-1">
                    Add fields in the canvas to see the live preview here
                </p>
            </div>
        </div>
    );
}