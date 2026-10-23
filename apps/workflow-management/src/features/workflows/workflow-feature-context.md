# AI Workflow Management: Feature Context & Architecture

This document serves as the primary context for AI models working on the `workflows` feature within the `ai-workflow-management` frontend application. It synthesizes the current implementation architecture and integrates the required principles from domain-specific frontend skills.

---

## 1. Feature Overview

The `workflows` feature handles the core logic and user interface for designing, editing, and managing AI-driven workflows. The heavy lifting of the visual node editor (Rete.js) has been abstracted out to a shared common UI library (`ldc-workflow-editor`). The `workflows` feature acts as the **Data and Business Logic Orchestrator**, providing contextual panels (node palette, logs, headers, detail modals) and handling network state via API requests.

**Directory Structure:**

-   `/components`: UI pieces surrounding the editor.
    -   `/execution-history`: Execution history display panel.
    -   `/loading-overlay`: Blocking loading spinner.
    -   `/logs`: Real-time log section (`logs.tsx`, `log-item.tsx`, `log-list.tsx`).
    -   `/modals`: Various modals.
        -   `nodes-detail-modal/`: Node configuration and I/O payload introspection (`detail-modal.tsx`, `detail-content.tsx`, `detail-input.tsx`, `detail-form.tsx`, floating graph navigation).
        -   `workflow-create-modal.tsx`, `workflow-parameter-modal.tsx`, `workflow-rename-modal.tsx`, `workflow-setting-modal.tsx`.
    -   `/node-palette`: Drawer system for dragging or clicking to add new node types.
    -   `/workflow-header`: Top navigation, rename, publish, save, and run control.
    -   `/workflow-list`: Main dashboard for workflows.
-   `/hooks`: Custom React hooks abstracting complex business logic.
    -   `/apis`: React Query hook files — `workflows.ts`, `configs.ts`, `node-group.ts`, `node-pallete.ts`, `logs.ts`.
    -   `use-workflow-detail-page.ts`: Top-level orchestration hook fetching workflow data.
    -   `use-execute-workflow.ts`: **Core execution logic** — handles run logic, socket listeners, and config param dialogs.
    -   `use-save-workflow.ts`: API handler for saving workflows manually.
    -   `use-node-detail.ts`: Fetches and orchestrates data for the node parameter config and payload I/O.
    -   `use-workflow-log-sync.ts`: Synchronizes logs from editor/execution events to the local storage log store.
-   `/stores`: Client-side state management — split into three independent stores:
    -   `ui-panel-stores.ts` — all modal/panel boolean flags (e.g., node palette open, node popup open).
    -   `editor-stores.ts` — current workflow structure, selected node data, execution runtime statuses, and session info.
    -   `log-store.ts` — persistent local storage store for execution traces.
-   `/types`: TypeScript interfaces — `api.ts`, `configs.ts`, `execution.ts`, `node-comprehensive.ts`, `node-pallete.ts`, `workflow-log.ts`.

### 1.1 Shared Library Integration (`@common/components/ldc-workflow-editor`)

The visual node engine is entirely imported from `ldc-workflow-editor`. The feature code does not directly instantiate Rete.js. It interfaces via the `WorkflowEditor` React component and its `WorkflowEditorHandle`.
All operations like drag-and-drop translations, magnetic connection math, and hook life-cycles like `useEditorSync` are managed externally by that wrapper component.

---

## 2. Integrated Skill Guidelines & Current Implementation

### A. React State Management

The feature successfully implements **Combining Client + Server State**.

-   **Client State (Zustand):** Managed by independent stores — `useUIPanelStore`, `useEditorStore` and `useWorkflowLogStore`.
    -   _Rule enforced:_ Stores must NOT import from each other. Cross-store actions are composed at the page/hook layer.
-   **Server State (React Query / TanStack):** Data fetching handles API calls, caching, and loading flags.
    -   _Rule enforced:_ Do not duplicate server state into Zustand. `useWorkflowDetail` fetches the API and maps it specifically into a format the canvas expects (`IEditorValue`), seeding Zustand once.

### B. React Modernization & Architecture

The codebase relies on highly abstracted, functional React components and Custom Hooks.

-   **Page-level Orchestration:** `WorkflowDetailPage` (`src/pages/workflow-detail.tsx`) acts as a orchestration shell — it reads from `useEditorStore` and `useUIPanelStore` with selective subscriptions and delegates all logic to specialized hooks.
-   **`useImperativeHandle` for child-to-parent Method Calling:** `WorkflowEditor` exposes a typed `WorkflowEditorHandle` (for programmatic node adding, setting node execution status via Socket triggers).
-   _Rule enforced:_ Strict separation between UI presentation (modals/drawers) and complex business logic (hooks routing socket state to UI state).

### C. Advanced JavaScript & TypeScript

-   **Data Mapping:** `node-mapper-utils.ts` isolates the transformation logic from API types to the frontend node models.
-   **Performance:** `WorkflowEditor` is `React.memo` wrapped. Callbacks in `WorkflowDetailPage` (`handleNodeSelected`, `handleChange`) use `useCallback` to maintain reference stability and prevent dragging jitters.

---

## 3. Deep Dive: `workflowData` Lifecycle & Zustand / React Query Boundary

### 3.1 The Two-Phase Lifecycle of `workflowData`

**Phase 1 — Seeding (Server → Zustand, one-time on mount):**

```
API response (useWorkflowById)
    │
    ▼
mapApiToWorkflowValue()       ← Transform API format → IEditorValue (canvas format)
    │
    ▼
setWorkflowData(transformed)  ← Seed into useEditorStore
    │
    ▼
workflowData (useEditorStore) ← Initial canvas snapshot passed to <WorkflowEditor value={...} />
```

**Phase 2 — Live Canvas State (Canvas → Zustand, on user edit):**

```
User adds / removes / connects nodes on canvas
    │
    ▼
<WorkflowEditor onChange={handleChange} />  ← Notifies of payload change via Debounce
    │
    ▼
setWorkflowData(value)        ← Handled in WorkflowDetailPage
    │
    ▼
workflowData (useEditorStore) ← Now = live canvas state
```

---

## 4. Page Architecture: `WorkflowDetailPage`

Located at `src/pages/workflow-detail.tsx`.

**Component Responsibility Map:**

```text
WorkflowDetailPage
├── useWorkflowDetail(workflowId)            → Seeds workflowData, returns loading state
├── useWorkflowLogSync(workflowId)           → Setup log appending callbacks
├── useExecuteWorkflow(...)                  → Binds socket events to UI/store updates
├── useSaveWorkflow(...)                     → API mutator for manual saves
│
├── <WorkflowHeader />                       → Top bar: Name, Run btn, Save btn
├── <WorkflowEditor
│       ref={workflowEditorRef}              ← Imperative API handle
│       value={workflowData}                 ← Current Canvas state
│       onAddNode={handleAddNodeClick}       ← Triggers Palette toggle
│       onChange={handleChange}
│       onExecuteNode={onRunNode}
│       onOpenNodePopup={openNodePopup}
│   />
├── <NodePalette />                          → Drawer to drag-n-drop new nodes
├── <LogSection />                           → Bottom expanding real-time logs
├── <NodesDetailModal>                       → Detailed Modal UI for configuring specific node
└── <WorkflowParameterModal />               → Pre-run config form if workflow requires params
```

**Key Design Decisions:**

-   Component uses **selective subscriptions** across `useEditorStore` and `useUIPanelStore`.
-   `<WorkflowEditor />` handles its own toolbars and menus.
-   Adding a remote log event (like `NODE_EXECUTED`) flows back from the `useExecuteWorkflow` socket layer directly into `addExecutionLog`.

---

## 5. Execution Feature (`useExecuteWorkflow`)

The `useExecuteWorkflow` hook manages:

1. **Socket Connection**: Listens to real-time events (`node.executing`, `node.completed`, `node.failed`).
2. **Execution Mutations**: Calls `runNode` or `runWorkflow` APIs.
3. **Configuration Dialogs**: Checks if a workflow requires parameters and opens the `WorkflowParameterModal` logic.

### 5.1 Architecture Pipeline

```text
Socket event (node.executing / node.completed / node.failed)
         │
         ▼
useExecuteWorkflow(editorRef)
         │
         ├─ setNodeExecution(nodeId, state)     → useEditorStore nodeExecutionMap
         │                                        (Used by Detail Modals, toolbars)
         │
         └─ editorRef.current?.setNodeStatus()  → Sends state downstream to Rete.js engine
                                                  (Triggers green/red node borders and lines)
```

Zustand (`useEditorStore`) tracks `nodeExecutionMap` (a `Record<string, NodeExecutionState>`) where the key is `node_id`.

---

## 6. Logging & Audit System (`useWorkflowLogSync`)

Acts as an **Execution Backbone**, providing deep debugging, audit trails, and state persistence.

### 6.1 Architecture

```text
User action / Socket event -> addLightLog() or addExecutionLog()
        │
        ▼
useWorkflowLogStore (Zustand with localStorage middleware: "workflow-logs")
        │
        ▼
Log Drawer UI (<LogSection>, <LogList>, <LogFilterBar>)
```

-   **Semantic Rules:**
    -   `log.timestamp` in `IWorkflowLogs` represents the Epoch **Start Time**.
    -   Duration is derived logically on render (`Date.now() - log.timestamp`).
    -   Max capacity is 500 logs (FIFO eviction scheme).

---

## 7. Node Detail Panel

The detail modal (`<NodesDetailModal>`) avoids prop-drilling by providing a context (`<NodeDetailProvider>`) wrapping a visually isolated area leveraging layout groups and isolated panels for:

-   Input Scope & Context Variables
-   Actual Worker Form Params
-   JSON Output trees payload viewing.
    It fetches complex payloads only after node executions via orchestrator hook `useNodeDetail()`.

### Floating Navigation

`incomer-nodes.tsx` and `outcomer-nodes.tsx` leverage floating positioning to navigate upstream/downstream seamlessly without closing the modal.

---

**Note to AI Models:** When generating code or refactoring within `src/features/workflows`:

-   Use `useEditorStore` for canvas data, selected node, and execution runtime. Use `useUIPanelStore` for all modal/panel boolean flags. Use `useWorkflowLogStore` for logs.
-   Stores must **NOT** import from each other.
-   Do not write low-level Rete.js syntax here. Delegate structural geometry logic or new plugins to `@common/components/ldc-workflow-editor`.
-   Always cleanly unsubscribe from sockets in hooks using appropriate namespaced cleanup.
