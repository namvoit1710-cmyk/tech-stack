import { cn } from "@ldc/ui";
import type { ClassicScheme, RenderEmit } from "rete-react-plugin";
import { Presets } from "rete-react-plugin";
import type { Socket } from "rete/_types/presets/classic";
import type { EditorDirection } from "../../types";

const { RefSocket } = Presets.classic;

type SocketSide = "input" | "output";

interface Props<S extends ClassicScheme> {
    side: SocketSide;
    socketKey: string;
    nodeId: string;
    payload: Socket;
    emit: RenderEmit<S>;
    label?: string;
    direction: EditorDirection;
    readOnly?: boolean;
}

export function SocketItem<S extends ClassicScheme>({
    side,
    socketKey,
    nodeId,
    payload,
    emit,
    label,
    direction,
    readOnly,
}: Props<S>) {
    const isHorizontal = direction === "horizontal";
    const isInput = side === "input";

    return (
        <div
            className={cn(
                "socket-item",
                `socket-item--${side}`,
                `socket-item--${direction}`,
                readOnly && "socket-item--readonly",
            )}
            data-testid={`${side}-${socketKey}`}
        >
            {isHorizontal && isInput && label && (
                <div className="socket-item__label" data-testid={`${side}-title`}>
                    {label}
                </div>
            )}

            <RefSocket
                name={`${side}-socket`}
                side={side}
                emit={emit}
                socketKey={socketKey}
                nodeId={nodeId}
                payload={payload}
            />

            {isHorizontal && !isInput && label && (
                <div className="socket-item__label" data-testid={`${side}-title`}>
                    {label}
                </div>
            )}

            {!isHorizontal && label && (
                <div className="socket-item__label socket-item__label--vertical" data-testid={`${side}-title`}>
                    {label}
                </div>
            )}
        </div>
    );
}