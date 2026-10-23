import { cn } from "@ldc/ui";
import UiwJsonView from "@uiw/react-json-view";

const resolveKeyPath = ({
    keyName,
    keys,
    raw,
    replacePathKey,
}: {
    keyName: string | number
    keys?: (string | number)[]
    raw?: object
    replacePathKey?: string
}) => {

    const joinedPath = Array.isArray(keys)
        ? keys.map(String).join(".")
        : String(keyName)

    if (!replacePathKey || !raw || typeof raw !== "object") {
        return joinedPath
    }

    const rawRecord = raw as Record<string, unknown>
    const topLevelKey = Array.isArray(keys) && keys.length > 0
        ? String(keys[0])
        : String(keyName)
    const rawEntry = rawRecord[topLevelKey]

    if (!rawEntry || typeof rawEntry !== "object") {
        return joinedPath
    }

    const basePath = (rawEntry as Record<string, unknown>)[replacePathKey]

    if (typeof basePath !== "string" || basePath.length === 0) {
        return joinedPath
    }

    const nestedSegments = Array.isArray(keys)
        ? keys.slice(1).map(String)
        : []

    // The top-level key's display path replaces its own segment; any nested keys
    // below it keep their own names appended (e.g. "artifact.preview" + "total").
    return nestedSegments.length > 0
        ? [basePath, ...nestedSegments].join(".")
        : basePath
}

interface IJsonViewProps {
    value: object;
    prefix?: string;
    className?: string;
    enableClipboard?: boolean;
    draggableKeys?: boolean;
    onKeyDragStart?: (keyPath: string, keyName: string | number) => void;
    collapsed?: boolean | number;
    displayDataTypes?: boolean;
    displayObjectSize?: boolean;
    raw?: object;
    replacePathKey?: string;
}

const JsonView = ({
    value,
    className,
    prefix,
    raw,
    replacePathKey,
    enableClipboard = false,
    draggableKeys = false,
    onKeyDragStart,
    displayDataTypes = false,
    displayObjectSize = false,
}: IJsonViewProps) => {

    return (
        <UiwJsonView
            value={value}
            enableClipboard={enableClipboard}
            collapsed={1}
            displayDataTypes={displayDataTypes}
            displayObjectSize={displayObjectSize}
            className={cn("text-sm", draggableKeys && "[&_.w-rjv-line]:my-1", className)}
        >
            <UiwJsonView.KeyName
                render={(props, result) => {
                    const path = resolveKeyPath({
                        keyName: result.keyName ?? "",
                        keys: result.keys,
                        raw,
                        replacePathKey,
                    })

                    if (!path) {
                        return result.keyName
                    }

                    return (
                        <span
                            {...props}
                            draggable={!!draggableKeys}
                            onDragStart={(e) => {


                                const validValue = (() => {
                                    if (replacePathKey && raw) {
                                        return path
                                    }

                                    return prefix ? prefix.replace("#path", path) : `{{$${path}}}`
                                })();

                                e.dataTransfer.setData("text/plain", `${validValue}`);
                                onKeyDragStart?.(validValue, result.keyName ?? "");
                            }}
                            className={cn(
                                className,
                                draggableKeys &&
                                "cursor-grab active:cursor-grabbing px-1.5 py-0.5 rounded border border-dashed border-blue-300 bg-blue-50/50 text-blue-700 transition-all duration-150",
                                draggableKeys && "hover:bg-blue-100 hover:border-blue-400 hover:shadow-sm",
                                !draggableKeys && "px-1 py-0.5 rounded transition-colors duration-100",
                            )}
                        >
                            {result.keyName}
                        </span>
                    )
                }}
            />
        </UiwJsonView>
    );
};

export default JsonView;