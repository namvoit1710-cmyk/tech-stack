import { useCallback, useMemo, useState } from "react";
import type { ISchema } from "../../../types/schema";
import type {
    DesignerField,
    DesignerNode,
    PaletteFieldDef,
    PaletteWrapperDef
} from "../types";
import {
    collectAllKeys,
    createContainer,
    createField,
    deepClone,
    findNodeById,
    fromISchema,
    insertAt,
    makeSlotKey,
    removeNodeById,
    toISchema,
    uid,
} from "../utils/nodes";

export interface UseDesignerReturn {
    nodes: DesignerNode[];
    selectedId: string | null;
    schema: ISchema;

    selectNode: (id: string | null) => void;
    selectedNode: DesignerNode | null;

    addField: (def: PaletteFieldDef, targetId?: string | null, targetIndex?: number, slotId?: string) => void;
    addContainer: (def: PaletteWrapperDef, targetIndex?: number) => void;
    // Add a container into a specific slot (TabWrapper / SequenceWrapper)
    addContainerToSlot: (def: PaletteWrapperDef, containerId: string, slotId: string, toIndex?: number) => void;
    addFieldToOdataSlot: (def: PaletteFieldDef, containerId: string, slotName: string) => void;

    // Move node to a container/root at index
    moveNode: (nodeId: string, toParentId: string | null, toIndex: number) => void;
    // Move node into a specific slot (TabWrapper / SequenceWrapper)
    moveNodeToSlot: (nodeId: string, containerId: string, slotId: string, toIndex?: number) => void;
    // Reorder within root
    reorderRootNodes: (fromIndex: number, toIndex: number) => void;
    // Reorder within a flat children list (DivWrapper / FragmentWrapper)
    reorderInContainer: (containerId: string, fromIndex: number, toIndex: number) => void;
    // Reorder within a slot children list
    reorderInSlot: (containerId: string, slotId: string, fromIndex: number, toIndex: number) => void;
    // Reorder within table columns
    reorderInColumns: (containerId: string, fromIndex: number, toIndex: number) => void;
    // Reorder within array items
    reorderInArray: (containerId: string, fromIndex: number, toIndex: number) => void;

    updateNode: (id: string, updater: (n: DesignerNode) => DesignerNode) => void;

    addSlot: (containerId: string) => void;
    removeSlot: (containerId: string, slotId: string) => void;
    updateSlotLabel: (containerId: string, slotId: string, label: string) => void;
    updateSlotClassName: (containerId: string, slotId: string, className: string) => void;

    deleteNode: (id: string) => void;
    duplicateNode: (id: string) => void;

    importSchema: (schema: ISchema) => void;
    clearAll: () => void;
}

export function useDesigner(): UseDesignerReturn {
    const [nodes, setNodes] = useState<DesignerNode[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);

    // PERF (SA-2043): both derive from the whole tree — memoise so they don't recompute on renders
    // that didn't change `nodes`/`selectedId` (e.g. drag hover, palette open).
    const schema = useMemo(() => toISchema(nodes), [nodes]);
    const selectedNode = useMemo(
        () => (selectedId ? (findNodeById(nodes, selectedId)?.node ?? null) : null),
        [nodes, selectedId],
    );

    const selectNode = useCallback((id: string | null) => setSelectedId(id), []);

    // ── Add field ──────────────────────────────────────────────────────────────
    const addField = useCallback(
        (def: PaletteFieldDef, targetId: string | null = null, targetIndex = -1, slotId?: string) => {
            setNodes((prev) => {
                const keys = collectAllKeys(prev);
                const field = createField(def, keys);
                if (!targetId) return insertAt(prev, field, targetIndex);
                return addInsideContainer(prev, targetId, field, targetIndex, slotId);
            });
        },
        []
    );

    // ── Add container ──────────────────────────────────────────────────────────
    const addContainer = useCallback((def: PaletteWrapperDef, targetIndex = -1) => {
        setNodes((prev) => insertAt(prev, createContainer(def), targetIndex));
    }, []);

    // ── Move node to container or root ─────────────────────────────────────────
    const moveNode = useCallback(
        (nodeId: string, toParentId: string | null, toIndex: number) => {
            setNodes((prev) => {
                const found = findNodeById(prev, nodeId);
                if (!found) return prev;
                const without = removeNodeById(prev, nodeId);
                const moved = deepClone(found.node);
                if (!toParentId) return insertAt(without, moved, toIndex);
                return addInsideContainer(without, toParentId, moved, toIndex);
            });
        },
        []
    );

    // ── Move node into a specific slot ─────────────────────────────────────────
    const moveNodeToSlot = useCallback(
        (nodeId: string, containerId: string, slotId: string, toIndex = -1) => {
            setNodes((prev) => {
                const found = findNodeById(prev, nodeId);
                if (!found) return prev;
                // Cycle guard: don't move a container into its own descendant slot.
                if (found.node._kind === "container" && findNodeById([found.node], containerId)) {
                    return prev;
                }
                const without = removeNodeById(prev, nodeId);
                const moved = deepClone(found.node);
                return updateInTree(without, containerId, (n) => {
                    if (n._kind !== "container" || !n.slots) return n;
                    return {
                        ...n,
                        slots: n.slots.map((s) => {
                            if (s._id !== slotId) return s;
                            return {
                                ...s,
                                children: insertAt(s.children, moved, toIndex),
                            };
                        }),
                    };
                });
            });
        },
        []
    );

    // ── Add a container into a specific slot ───────────────────────────────────
    const addContainerToSlot = useCallback(
        (def: PaletteWrapperDef, containerId: string, slotId: string, toIndex = -1) => {
            setNodes((prev) => {
                const container = createContainer(def);
                return updateInTree(prev, containerId, (n) => {
                    if (n._kind !== "container" || !n.slots) return n;
                    return {
                        ...n,
                        slots: n.slots.map((s) =>
                            s._id !== slotId
                                ? s
                                : { ...s, children: insertAt(s.children, container, toIndex) }
                        ),
                    };
                });
            });
        },
        []
    );

    // ── Add a field into OdataWrapper slot ──────────────────────────────────────
    const addFieldToOdataSlot = useCallback(
        (def: PaletteFieldDef, containerId: string, slotName: string) => {
            setNodes((prev) => {
                const keys = collectAllKeys(prev);
                const field = createField(def, keys);
                field.fieldConfig = { ...field.fieldConfig, slot: slotName };
                return updateInTree(prev, containerId, (n) => {
                    if (n._kind !== "container") return n;
                    return { ...n, children: [...n.children, field] };
                });
            });
        },
        []
    );

    // ── Reorder root ───────────────────────────────────────────────────────────
    const reorderRootNodes = useCallback((fromIndex: number, toIndex: number) => {
        setNodes((prev) => {
            const next = [...prev];
            const [moved] = next.splice(fromIndex, 1);
            if (!moved) return prev;
            next.splice(toIndex, 0, moved);
            return next;
        });
    }, []);

    // ── Reorder inside a flat container (DivWrapper / FragmentWrapper) ─────────
    const reorderInContainer = useCallback(
        (containerId: string, fromIndex: number, toIndex: number) => {
            setNodes((prev) =>
                updateInTree(prev, containerId, (n) => {
                    if (n._kind !== "container") return n;
                    const next = [...n.children];
                    const [moved] = next.splice(fromIndex, 1);
                    if (!moved) return n;
                    next.splice(toIndex, 0, moved);
                    return { ...n, children: next };
                })
            );
        },
        []
    );

    // ── Reorder inside a slot ──────────────────────────────────────────────────
    const reorderInSlot = useCallback(
        (containerId: string, slotId: string, fromIndex: number, toIndex: number) => {
            setNodes((prev) =>
                updateInTree(prev, containerId, (n) => {
                    if (n._kind !== "container" || !n.slots) return n;
                    return {
                        ...n,
                        slots: n.slots.map((s) => {
                            if (s._id !== slotId) return s;
                            const next = [...s.children];
                            const [moved] = next.splice(fromIndex, 1);
                            if (!moved) return s;
                            next.splice(toIndex, 0, moved);
                            return { ...s, children: next };
                        }),
                    };
                })
            );
        },
        []
    );

    // ── Reorder table columns ──────────────────────────────────────────────────
    const reorderInColumns = useCallback(
        (containerId: string, fromIndex: number, toIndex: number) => {
            setNodes((prev) =>
                updateInTree(prev, containerId, (n) => {
                    if (n._kind !== "container" || !n.columns) return n;
                    const next = [...n.columns];
                    const [moved] = next.splice(fromIndex, 1);
                    if (!moved) return n;
                    next.splice(toIndex, 0, moved);
                    return { ...n, columns: next };
                })
            );
        },
        []
    );

    // ── Reorder array items ─────────────────────────────────────────────────────
    const reorderInArray = useCallback(
        (containerId: string, fromIndex: number, toIndex: number) => {
            setNodes((prev) =>
                updateInTree(prev, containerId, (n) => {
                    if (n._kind !== "container" || !n.arrays) return n;
                    const next = [...n.arrays];
                    const [moved] = next.splice(fromIndex, 1);
                    if (!moved) return n;
                    next.splice(toIndex, 0, moved);
                    return { ...n, arrays: next };
                })
            );
        },
        []
    );

    // ── Update any node ────────────────────────────────────────────────────────
    const updateNode = useCallback(
        (id: string, updater: (n: DesignerNode) => DesignerNode) => {
            setNodes((prev) => updateInTree(prev, id, updater));
        },
        []
    );

    // ── Slot management ────────────────────────────────────────────────────────
    const addSlot = useCallback(
        (containerId: string) => {
            updateNode(containerId, (n) => {
                if (n._kind !== "container" || !n.slots) return n;
                const isSeq = n.wrapperType === "SequenceWrapper";
                const label = isSeq
                    ? `Step ${n.slots.length + 1}`
                    : `Tab ${n.slots.length + 1}`;
                // SA-1053 bug K: seed a stable, unique key (not derived from the label).
                const key = makeSlotKey(isSeq ? "step" : "tab", n.slots.map((s) => s.key));
                return { ...n, slots: [...n.slots, { _id: uid(), key, label, children: [] }] };
            });
        },
        [updateNode]
    );

    const removeSlot = useCallback(
        (containerId: string, slotId: string) => {
            updateNode(containerId, (n) => {
                if (n._kind !== "container" || !n.slots) return n;
                return { ...n, slots: n.slots.filter((s) => s._id !== slotId) };
            });
        },
        [updateNode]
    );

    const updateSlotLabel = useCallback(
        (containerId: string, slotId: string, label: string) => {
            updateNode(containerId, (n) => {
                if (n._kind !== "container" || !n.slots) return n;
                return {
                    ...n,
                    slots: n.slots.map((s) =>
                        s._id === slotId ? { ...s, label } : s
                    ),
                };
            });
        },
        [updateNode]
    );

    const updateSlotClassName = useCallback(
        (containerId: string, slotId: string, className: string) => {
            updateNode(containerId, (n) => {
                if (n._kind !== "container" || !n.slots) return n;
                return {
                    ...n,
                    slots: n.slots.map((s) =>
                        s._id === slotId ? { ...s, className: className || undefined } : s
                    ),
                };
            });
        },
        [updateNode]
    );

    // ── Delete ─────────────────────────────────────────────────────────────────
    const deleteNode = useCallback((id: string) => {
        setNodes((prev) => removeNodeById(prev, id));
        setSelectedId((prev) => (prev === id ? null : prev));
    }, []);

    // ── Duplicate ──────────────────────────────────────────────────────────────
    const duplicateNode = useCallback((id: string) => {
        setNodes((prev) => {
            const found = findNodeById(prev, id);
            if (!found) return prev;
            const clone = deepClone(found.node);
            clone._id = uid();
            if ("key" in clone) clone.key = clone.key + "_copy";
            reassignIds(clone);
            return insertAfterInTree(prev, id, clone);
        });
    }, []);

    const importSchema = useCallback((schema: ISchema) => {
        const newNodes = fromISchema(schema);
        setNodes(newNodes);
        // ponytail: preserve selectedId if node still exists in new schema
        setSelectedId((prev) => (prev && findNodeById(newNodes, prev) ? prev : null));
    }, []);

    const clearAll = useCallback(() => {
        setNodes([]);
        setSelectedId(null);
    }, []);

    return {
        nodes,
        selectedId,
        schema,
        selectNode,
        selectedNode,
        addField,
        addContainer,
        addContainerToSlot,
        addFieldToOdataSlot,
        moveNode,
        moveNodeToSlot,
        reorderRootNodes,
        reorderInContainer,
        reorderInSlot,
        reorderInColumns,
        reorderInArray,
        updateNode,
        addSlot,
        removeSlot,
        updateSlotLabel,
        updateSlotClassName,
        deleteNode,
        duplicateNode,
        importSchema,
        clearAll,
    };
}

// ─── Tree helpers ──────────────────────────────────────────────────────────────

function updateInTree(
    nodes: DesignerNode[],
    id: string,
    updater: (n: DesignerNode) => DesignerNode
): DesignerNode[] {
    return nodes.map((n) => {
        if (n._id === id) return updater(n);
        if (n._kind !== "container") return n;
        return {
            ...n,
            children: updateInTree(n.children, id, updater) as DesignerField[],
            slots: n.slots?.map((s) => ({
                ...s,
                children: updateInTree(s.children, id, updater),
            })),
            columns: n.columns
                ? (updateInTree(n.columns, id, updater) as DesignerField[])
                : undefined,
            arrays: n.arrays
                ? (updateInTree(n.arrays, id, updater) as DesignerField[])
                : undefined,
        };
    });
}

function addInsideContainer(
    nodes: DesignerNode[],
    containerId: string,
    field: DesignerField | DesignerNode,
    index: number,
    slotId?: string
): DesignerNode[] {
    return nodes.map((n) => {
        if (n._kind !== "container") return n;
        if (n._id === containerId) {
            if (n.slots && !slotId) {
                const slots = [...n.slots];
                const first = slots[0];
                if (!first) return n;
                slots[0] = {
                    ...first,
                    children: insertAt(first.children, field, index),
                };
                return { ...n, slots };
            }

            if (n.slots && slotId) {
                return {
                    ...n,
                    slots: n.slots.map((s) =>
                        s._id === slotId
                            ? { ...s, children: insertAt(s.children, field, index) }
                            : s
                    ),
                };
            }

            if (n.columns !== undefined) {
                return { ...n, columns: insertAt(n.columns, field as DesignerField, index) };
            }

            if (n.arrays !== undefined) {
                return { ...n, arrays: insertAt(n.arrays, field as DesignerField, index) };
            }

            return { ...n, children: insertAt(n.children, field as DesignerField, index) };
        }
        return {
            ...n,
            children: addInsideContainer(n.children, containerId, field, index, slotId) as DesignerField[],
            slots: n.slots?.map((s) => ({
                ...s,
                children: addInsideContainer(
                    s.children,
                    containerId,
                    field,
                    index,
                    slotId
                ),
            })),
        };
    });
}

function insertAfterInTree(
    root: DesignerNode[],
    afterId: string,
    node: DesignerNode
): DesignerNode[] {
    const idx = root.findIndex((n) => n._id === afterId);
    if (idx >= 0) return insertAt(root, node, idx + 1);
    return root.map((n) => {
        if (n._kind !== "container") return n;
        return {
            ...n,
            children: insertAfterInTree(n.children, afterId, node) as DesignerField[],
            slots: n.slots?.map((s) => ({
                ...s,
                children: insertAfterInTree(
                    s.children,
                    afterId,
                    node
                ),
            })),
            // Table/OData columns and array-item fields are also part of the tree — descend into
            // them too, or duplicating a field held there is a silent no-op (the clone is built but
            // never placed). Mirrors removeNodeById / updateInTree, which already handle both.
            columns: n.columns
                ? (insertAfterInTree(n.columns, afterId, node) as DesignerField[])
                : undefined,
            arrays: n.arrays
                ? (insertAfterInTree(n.arrays, afterId, node) as DesignerField[])
                : undefined,
        };
    });
}

function reassignIds(node: DesignerNode) {
    node._id = uid();
    if (node._kind === "container") {
        node.children.forEach(reassignIds);
        node.slots?.forEach((s) => {
            s._id = uid();
            s.children.forEach(reassignIds);
        });
        node.columns?.forEach(reassignIds);
        node.arrays?.forEach(reassignIds);
    }
}