import { cn } from "@ldc/ui";
import { useRef } from "react";
import type { ClassicScheme } from "rete-react-plugin";
import type { IEditorNode } from "../../types";

interface NodeExtraData { width?: number; height?: number }

interface Props<S extends ClassicScheme> {
    data: S["Node"] & NodeExtraData & { original: IEditorNode };
    styles?: () => any;
}

const GroupNodes = (props: Props<ClassicScheme>) => {
    const { data } = props;
    const { original } = data;

    const ref = useRef<HTMLDivElement>(null);

    return (
        <div
            ref={ref}
            data-groupid={data.id}
            className={cn(
                "border border-dashed border-border rounded-lg",
                "p-1"
            )}
            {...props}
        >
            <div
                className="text-sm text-muted"
                data-testid="title"
            >
                {original?.title ?? original?.name ?? data.label}
            </div>
        </div>
    )
}

export default GroupNodes;