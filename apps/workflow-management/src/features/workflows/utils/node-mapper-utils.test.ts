import { describe, expect, it } from "@rstest/core";
import type { INodePallete } from "../types/node-pallete";
import { mapNodeToEditorNode } from "./node-mapper-utils";

const makePalette = (id: string): INodePallete => ({
    id,
    name: "Array Transform",
    description: "",
    provider: "business",
    node_type: "TASK",
    category: "business",
    kind: "transform",
    collection: "My Nodes",
    icon: "Cog",
    color: "#3B82F6",
    tags: [],
    spec_version: "1.0.0",
    visibility: "VISIBLE",
    runtime: "WORKER",
    ports: { inputs: [], outputs: [] },
});

describe("mapNodeToEditorNode", () => {
    it("derives node_definition_id from a business: catalog id", () => {
        const node = mapNodeToEditorNode(makePalette("business:b27eeb5f"));
        expect(node.node_definition_id).toBe("b27eeb5f");
        expect(node.worker_type).toBe("business:b27eeb5f");
    });

    it("leaves node_definition_id undefined for non-business nodes", () => {
        const node = mapNodeToEditorNode(makePalette("worker:http_request"));
        expect(node.node_definition_id).toBeUndefined();
    });
});
