import { beforeEach, describe, expect, it } from "@rstest/core";
import { act } from "@testing-library/react";
import { UNSAVED_VERSION_SENTINEL } from "../hooks/use-workflow-detail-page";
import { useEditorStore } from "../stores/editor-stores";

describe("Version Switching Integration", () => {
  beforeEach(() => {
    act(() => {
      useEditorStore.setState({
        workflowInfo: {
          id: "wf-1",
          version: "v1",
          name: "Test Workflow",
          status: "DRAFT",
          nodes: [],
          edges: [],
        } as never,
        selectedVersionId: "v1",
        baseChangeVersion: null,
        isDirty: false,
        unsavedDraft: null,
        workflowData: { nodes: [], connections: [] },
      });
    });
  });

  it("switches versions and preserves unsavedDraft when isDirty=true", async () => {
    // Setup: Load v1, make edits
    act(() => {
      useEditorStore.setState({
        isDirty: true,
        baseChangeVersion: "v1",
        unsavedDraft: { nodes: [{ id: "1" } as never], connections: [] },
      });
    });

    // Action: Switch to v2 (simulate via store)
    act(() => {
      useEditorStore.setState({
        selectedVersionId: "v2",
        isDirty: false,
        workflowData: { nodes: [], connections: [] },
      });
    });

    // Expect: unsavedDraft and baseChangeVersion preserved
    expect(useEditorStore.getState().baseChangeVersion).toBe("v1");
    expect(useEditorStore.getState().unsavedDraft).toEqual({
      nodes: [{ id: "1" }],
      connections: [],
    });
  });

  it("preserves baseChangeVersion across multiple version switches", async () => {
    // Setup: Edit v1
    act(() => {
      useEditorStore.setState({
        selectedVersionId: "v1",
        baseChangeVersion: "v1",
        isDirty: true,
      });
    });

    // Action: Switch to v2
    act(() => {
      useEditorStore.setState({
        selectedVersionId: "v2",
        isDirty: false,
      });
    });

    expect(useEditorStore.getState().baseChangeVersion).toBe("v1");

    // Action: Switch to v3
    act(() => {
      useEditorStore.setState({
        selectedVersionId: "v3",
      });
    });

    // Expect: baseChangeVersion still "v1"
    expect(useEditorStore.getState().baseChangeVersion).toBe("v1");
  });

  it("restores unsaved draft when clicking 'Unsaved changes'", async () => {
    const draftData = { nodes: [{ id: "draft" } as never], connections: [] };

    // Setup: Edit v1, switch to v2
    act(() => {
      useEditorStore.setState({
        selectedVersionId: "v2",
        baseChangeVersion: "v1",
        isDirty: false,
        unsavedDraft: draftData,
        workflowData: { nodes: [], connections: [] },
      });
    });

    // Action: Click "Unsaved changes" (simulate)
    act(() => {
      const draft = useEditorStore.getState().unsavedDraft;
      useEditorStore.setState({
        selectedVersionId: UNSAVED_VERSION_SENTINEL,
        workflowData: draft,
        isDirty: true,
      });
    });

    // Expect: workflowData restored to draft
    expect(useEditorStore.getState().workflowData).toEqual(draftData);
    expect(useEditorStore.getState().selectedVersionId).toBe(UNSAVED_VERSION_SENTINEL);
  });
});
