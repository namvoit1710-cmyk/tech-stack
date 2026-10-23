import { describe, expect, it } from "@rstest/core";
import type { IEditorValue } from "@ldc/workflow-editor";
import type { IWorkflow } from "../types/workflows";
import { mapWorkflowToSavePayload } from "./workflow-save-mapper";

describe("workflow-save-mapper", () => {
    describe("mapWorkflowToSavePayload", () => {
        it("should use baseChangeVersion parameter instead of workflow.version", () => {
            // Arrange
            const mockWorkflow: IWorkflow = {
                id: "workflow-123",
                name: "Test Workflow",
                description: "Test description",
                status: "draft",
                version: "v1.0.0", // Original version
                nodes: [],
                edges: [],
                created_at: "2024-01-01",
                updated_at: "2024-01-01",
            };

            const mockEditorValue: IEditorValue = {
                nodes: [
                    {
                        id: "node-1",
                        data: {
                            id: "node-1",
                            title: "Test Node",
                            name: "test-node",
                            node_type: "action",
                            worker_type: "python",
                            node_definition_id: "def-1",
                            parameters: {},
                            ports: {
                                inputs: [],
                                outputs: [],
                            },
                        },
                        position: { x: 100, y: 100 },
                    },
                ],
                connections: [
                    {
                        id: "conn-1",
                        source: "node-1",
                        target: "node-2",
                        sourceOutput: "out-1",
                        targetInput: "in-1",
                    },
                ],
            };

            const baseChangeVersion = "v2.0.0"; // New version to use

            // Act
            const result = mapWorkflowToSavePayload(
                mockWorkflow,
                mockEditorValue,
                baseChangeVersion
            );

            // Assert
            expect(result.version).toBe(baseChangeVersion);
            expect(result.version).not.toBe(mockWorkflow.version);
            expect(result.id).toBe(mockWorkflow.id);
            expect(result.name).toBe(mockWorkflow.name);
            expect(result.nodes).toHaveLength(1);
            expect(result.edges).toHaveLength(1);
        });

        it("should correctly map nodes from editorValue", () => {
            // Arrange
            const mockWorkflow: IWorkflow = {
                id: "workflow-123",
                name: "Test Workflow",
                status: "draft",
                version: "v1.0.0",
                nodes: [],
                edges: [],
                created_at: "2024-01-01",
                updated_at: "2024-01-01",
            };

            const mockEditorValue: IEditorValue = {
                nodes: [
                    {
                        id: "node-1",
                        data: {
                            id: "node-1",
                            title: "Test Node",
                            name: "test-node",
                            node_type: "action",
                            parameters: { key: "value" },
                            ports: {
                                inputs: [
                                    {
                                        id: "in-1",
                                        label: "Input 1",
                                        data_type: "string",
                                        required: true,
                                    },
                                ],
                                outputs: [
                                    {
                                        id: "out-1",
                                        label: "Output 1",
                                        data_type: "string",
                                        required: false,
                                    },
                                ],
                            },
                        },
                        position: { x: 100, y: 200 },
                    },
                ],
                connections: [],
            };

            const baseChangeVersion = "v2.0.0";

            // Act
            const result = mapWorkflowToSavePayload(
                mockWorkflow,
                mockEditorValue,
                baseChangeVersion
            );

            // Assert
            expect(result.nodes).toHaveLength(1);
            expect(result.nodes[0].id).toBe("node-1");
            expect(result.nodes[0].name).toBe("Test Node");
            expect(result.nodes[0].node_type).toBe("action");
            expect(result.nodes[0].parameters).toEqual({ key: "value" });
            expect(result.nodes[0].x).toBe(100);
            expect(result.nodes[0].y).toBe(200);
            expect(result.nodes[0].ports?.in).toHaveLength(1);
            expect(result.nodes[0].ports?.out).toHaveLength(1);
        });

        it("should correctly map edges from editorValue", () => {
            // Arrange
            const mockWorkflow: IWorkflow = {
                id: "workflow-123",
                name: "Test Workflow",
                status: "draft",
                version: "v1.0.0",
                nodes: [],
                edges: [],
                created_at: "2024-01-01",
                updated_at: "2024-01-01",
            };

            const mockEditorValue: IEditorValue = {
                nodes: [],
                connections: [
                    {
                        id: "edge-1",
                        source: "node-1",
                        target: "node-2",
                        sourceOutput: "out-1",
                        targetInput: "in-2",
                    },
                    {
                        id: "edge-2",
                        source: "node-2",
                        target: "node-3",
                        sourceOutput: "out-2",
                        targetInput: "in-3",
                    },
                ],
            };

            const baseChangeVersion = "v2.0.0";

            // Act
            const result = mapWorkflowToSavePayload(
                mockWorkflow,
                mockEditorValue,
                baseChangeVersion
            );

            // Assert
            expect(result.edges).toHaveLength(2);
            expect(result.edges[0].id).toBe("edge-1");
            expect(result.edges[0].source_node_id).toBe("node-1");
            expect(result.edges[0].target_node_id).toBe("node-2");
            expect(result.edges[0].source_port_id).toBe("out-1");
            expect(result.edges[0].target_port_id).toBe("in-2");
        });

        it("should preserve all workflow properties", () => {
            // Arrange
            const mockWorkflow: IWorkflow = {
                id: "workflow-123",
                name: "Test Workflow",
                description: "Test description",
                status: "active",
                purpose: "Testing purposes",
                version: "v1.0.0",
                nodes: [],
                edges: [],
                metadata: { custom_field: "custom_value" },
                routing_path: "/api/workflow/123",
                created_at: "2024-01-01",
                updated_at: "2024-01-02",
            };

            const mockEditorValue: IEditorValue = {
                nodes: [],
                connections: [],
            };

            const baseChangeVersion = "v2.5.0";

            // Act
            const result = mapWorkflowToSavePayload(
                mockWorkflow,
                mockEditorValue,
                baseChangeVersion
            );

            // Assert
            expect(result.id).toBe(mockWorkflow.id);
            expect(result.name).toBe(mockWorkflow.name);
            expect(result.description).toBe(mockWorkflow.description);
            expect(result.status).toBe(mockWorkflow.status);
            expect(result.purpose).toBe(mockWorkflow.purpose);
            expect(result.metadata).toEqual(mockWorkflow.metadata);
            expect(result.routing_path).toBe(mockWorkflow.routing_path);
            expect(result.version).toBe(baseChangeVersion);
            expect(result.version).not.toBe(mockWorkflow.version);
        });
    });
});
