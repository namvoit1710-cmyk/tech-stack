import type { ClassicScheme, RenderEmit } from "rete-react-plugin";
import type { Input, Output, Socket } from "rete/_types/presets/classic";
import { SocketItem } from "./socket-item";

type SocketEntry<S extends ClassicScheme> = [
    string,
    Output<Socket> | Input<Socket>
];

interface Props<S extends ClassicScheme> {
    side: "input" | "output";
    entries: SocketEntry<S>[];
    nodeId: string;
    emit: RenderEmit<S>;
    readOnly?: boolean;
}

export function SocketColumn<S extends ClassicScheme>({
    side,
    entries,
    nodeId,
    emit,
    readOnly,
}: Props<S>) {
    return (
        <div className={`socket-column socket-column--${side}`}>
            {entries.map(
                ([key, socket]) =>
                    socket && (
                        <SocketItem
                            key={key}
                            side={side}
                            socketKey={key}
                            nodeId={nodeId}
                            payload={socket.socket}
                            emit={emit}
                            label={socket.label}
                            direction="vertical"
                            readOnly={readOnly}
                        />
                    )
            )}
        </div>
    );
}