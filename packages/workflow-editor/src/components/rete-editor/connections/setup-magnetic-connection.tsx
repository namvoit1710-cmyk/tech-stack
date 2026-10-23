import type { NodeEditor } from "rete";
import { ClassicPreset } from "rete";
import type { ConnectionPlugin } from "rete-connection-plugin";
import { useMagneticConnection } from "../connections/magnetic-connection";
import type { AreaExtra, Schemes } from "../types";

export function SetupMagneticConnection(
    connection: ConnectionPlugin<Schemes, AreaExtra>,
    editor: NodeEditor<Schemes>
) {
    useMagneticConnection(connection, {
        async createConnection(from, to) {
            if (from.side === to.side) return;

            const [source, target] = from.side === "output" ? [from, to] : [to, from];
            const sourceNode = editor.getNode(source.nodeId);
            const targetNode = editor.getNode(target.nodeId);

            await editor.addConnection(
                new ClassicPreset.Connection(
                    sourceNode!,
                    source.key as never,
                    targetNode!,
                    target.key as never
                )
            );
        },

        display(from, to) {
            return from.side !== to.side;
        },

        offset(socket, position) {
            const socketRadius = 10;
            return {
                x: position.x + (socket.side === "input" ? -socketRadius : socketRadius),
                y: position.y,
            };
        },
    });
}