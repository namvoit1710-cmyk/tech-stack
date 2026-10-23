import { NODE_FACTORY_REGISTRY_KEY } from "../config/node-config";
import type { IEditorNode } from "../types";
import { BaseNode } from "./base-node";

export class GroupNode extends BaseNode {
    constructor(
        groupId?: string,
        label?: string,
        initialData?: Partial<IEditorNode>
    ) {
        super(groupId, label ?? NODE_FACTORY_REGISTRY_KEY.GROUP_NODE);

        this.id = groupId ?? initialData?.id ?? crypto.randomUUID();
        this.original = initialData as IEditorNode;
    }
}