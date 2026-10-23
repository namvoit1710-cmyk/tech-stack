import JsonView from "@/components/json-view/json-view"
import type { IScopedVariable } from "@/features/workflows/types/node-detail"
import { useMemo } from "react"

const InputSchema = ({ scopePath, draggable = true }: { scopePath: IScopedVariable["paths"], draggable?: boolean }) => {
    // `raw` must carry the full path objects (each has `displayPath`), because
    // JsonView's `replacePathKey="displayPath"` looks up raw[key].displayPath to
    // decide what a dragged key inserts — the full `{{$scope.path}}` expression
    // (e.g. `{{$env.API_TOKEN}}`). If raw held only the sample scalar, the drag
    // would fall back to the bare key ("API_TOKEN"). See resolveKeyPath.
    const raw = useMemo(() => scopePath as unknown as Record<string, unknown>, [scopePath])

    const value = useMemo(() => {
        return Object.entries(scopePath).reduce<Record<string, unknown>>((acc, [key, value]) => {
            acc[key] = value.sample
            return acc
        }, {})
    }, [scopePath])

    return (
        <JsonView
            value={value}
            enableClipboard
            draggableKeys={draggable}
            raw={raw}
            replacePathKey="displayPath"
        />

    )
}

export default InputSchema;