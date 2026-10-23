import { ClassicPreset } from "rete";
import { DEFAULT_SIZE_NODE } from "../../../constants/node";
import type { IEditorNode, NodeExecutionStatus, NodePortConfig, NodeSizeConfig } from "../types";
import { socket } from "./socket";

export class BaseNode extends ClassicPreset.Node {
    width = DEFAULT_SIZE_NODE.WIDTH;
    height = DEFAULT_SIZE_NODE.HEIGHT;
    original: IEditorNode;
    executionStatus: NodeExecutionStatus = "idle";
    parent?: string;

    constructor(
        id?: string,
        label = "Node",
        ports?: NodePortConfig,
        size?: NodeSizeConfig,
        initialData?: IEditorNode
    ) {
        super(label);

        if (size?.width) this.width = size.width;
        if (size?.height) this.height = size.height;

        const inputs = ports?.inputs ?? [{ id: "port", label: "in" }];
        const outputs = ports?.outputs ?? [{ id: "port", label: "out" }];
        this.id = id ?? initialData?.id ?? crypto.randomUUID();
        this.original = initialData!;

        for (const key of inputs) {
            this.addInput(key.id, new ClassicPreset.Input(socket, key.label, true));
        }

        for (const key of outputs) {
            this.addOutput(key.id, new ClassicPreset.Output(socket, key.label));
        }
    }

    public updateNodeLabel(label: string) {
        this.original.title = label;
        this.original.name = label;

        this.label = label;
    }

    public updateNodeInfo(label: string, description: string, instruction: string) {
        this.original.title = label ?? "";
        this.original.name = label ?? "";
        this.original.description = description ?? "";
        this.original.instruction = instruction ?? "";
        this.label = label ?? "";
    }

    public updateParameters(parameters: Record<string, any>) {
        this.original.parameters = parameters;
    }

    public updateInputSocketLabel(portId: string, label: string) {
        const port = this.original.ports.inputs.find((p) => p.id === portId);
        if (port) port.label = label;
        const input = this.inputs[portId];
        if (input) input.label = label;
    }

    public updateOutputSocketLabel(portId: string, label: string) {
        const port = this.original.ports.outputs.find((p) => p.id === portId);
        if (port) port.label = label;
        const output = this.outputs[portId];
        if (output) output.label = label;
    }

    public addOutputSocket(key: string, label: string) {
        if (this.original.ports.outputs.find((port) => port.id === key)) return;

        this.original.ports.outputs.push({ id: key, label });
        this.addOutput(key, new ClassicPreset.Output(socket, label));
    }

    public addInputSocket(key: string, label: string) {
        if (this.original.ports.inputs.find((port) => port.id === key)) return;

        this.original.ports.inputs.push({ id: key, label })
        this.addInput(key, new ClassicPreset.Input(socket, label, true));
    }

    public removeInputSocket(portId: string) {
        this.original.ports.inputs = this.original.ports.inputs.filter((port) => port.id !== portId);
        this.removeInput(portId);
    }

    public removeOutputSocket(portId: string) {
        this.original.ports.outputs = this.original.ports.outputs.filter((port) => port.id !== portId);
        this.removeOutput(portId);
    }

    public syncOutputSockets(
        newPorts: { id: string; label: string }[],
        onBeforeRemove?: (portId: string) => void
    ) {
        const newPortMap = new Map(newPorts.map((p) => [p.id, p.label]));

        const currentPortIds = this.original.ports.outputs.map((p) => p.id);
        for (const portId of currentPortIds) {
            const port = this.original.ports.outputs.find((p) => p.id === portId);
            if (port?.readonly) continue;
            if (!newPortMap.has(portId)) {
                onBeforeRemove?.(portId);
                this.removeOutputSocket(portId);
            }
        }

        for (const [id, label] of newPortMap) {
            const existing = this.original.ports.outputs.find((p) => p.id === id);
            if (existing?.readonly) continue;
            if (this.outputs[id]) {
                if (existing?.label !== label) {
                    this.updateOutputSocketLabel(id, label);
                }
            } else {
                this.addOutputSocket(id, label);
            }
        }
    }
}