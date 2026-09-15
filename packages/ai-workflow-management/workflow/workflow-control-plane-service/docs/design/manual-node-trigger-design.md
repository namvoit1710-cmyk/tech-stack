# Manual Node Trigger Mode — Design Document

**Date:** 2026-04-21  
**Status:** Implemented  
**Author:** AI Assistant  
**Story:** Enable granular, on-demand node execution for debugging, HITL, phased execution, error recovery, and AI control

---

## 1. Problem Statement

Currently, the AI Workflow executes workflows in a fully automated manner once a run is initiated. This limits operational flexibility for:

1. **Debugging scenarios:** Users cannot pause and inspect intermediate node outputs before proceeding
2. **Conditional manual intervention:** Some workflows require HITL (Human-in-the-Loop) approval between nodes
3. **Phased execution:** Long-running workflows benefit from manual pacing to monitor resource consumption and logs
4. **Error recovery:** Manual triggering allows operators to skip failed nodes and retry specific steps
5. **AI control:** Currently AI could not fully control workflow, only workflow automation, and pending when ask user input, and this is the part the AI jump in

---

## 2. Proposed Solution: Manual Trigger Mode

Implement a **manual execution mode** where:

1. User creates a new workflow run specifying `execution_mode: "manual"`
2. Run is created in `SUSPENDED` state (new status value)
3. Run remains at the first executable nodes (entry nodes identified but not auto-executed)
4. User explicitly triggers each node via UI or API
5. Execution proceeds node-by-node, storing checkpoint state after each completion
6. User can inspect outputs, conditionally trigger downstream nodes, skip, or retry

---

## 3. Architecture Changes

### 3.1 New Run Status: `SUSPENDED`

Add new status to `RunStatus` enum for manual-mode runs that are waiting for node triggers:

```python
# app/layer1_domain/value_objects/run_status.py
class RunStatus(str, Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"
    SUSPENDED = "SUSPENDED"  # NEW: Manual mode waiting for node trigger
```

**Status Distinction:**
- `PAUSED`: Auto-mode run temporarily paused (can be resumed to continue auto-execution)
- `SUSPENDED`: Manual-mode run waiting for explicit node trigger (never auto-advances)

### 3.2 New Execution Mode on WorkflowRun

Add `execution_mode` field to `WorkflowRun` entity:

```python
# app/layer1_domain/entities/workflow_run.py
@dataclass(kw_only=True)
class WorkflowRun(BaseDomainEntity):
    # ... existing fields ...
    execution_mode: str = "auto"  # "auto" | "manual"
```

### 3.3 RunContext Extensions

Track triggerable nodes and execution checkpoint:

```python
# app/layer1_domain/entities/workflow_run.py
@dataclass(kw_only=True)
class RunContext:
    # ... existing fields ...
    # Manual-mode specific fields
    triggerable_nodes: set[str] = field(default_factory=set)  # Nodes ready to be triggered
    triggered_nodes: set[str] = field(default_factory=set)    # Nodes that have been triggered
```

---

## 4. API Changes

### 4.1 Create Run with Manual Mode

**Endpoint:** `POST /runs`

**Request changes:**
```json
{
  "workflow_id": "wf-123",
  "input_data": {},
  "execution_mode": "manual"  // NEW optional field, default "auto"
}
```

**Response when `execution_mode: "manual"`:**
```json
{
  "id": "run-abc",
  "workflow_id": "wf-123",
  "status": "SUSPENDED",
  "execution_mode": "manual",
  "triggerable_nodes": ["trigger_1"],  // Entry nodes ready for trigger
  "started_at": "2026-04-21T10:00:00Z"
}
```

### 4.2 Trigger Node Execution (NEW Endpoint)

**Endpoint:** `POST /runs/{run_id}/nodes/{node_id}/trigger`

**Request:**
```json
{
  "force": false  // Optional: force retry even if node already completed/failed
}
```

**Response:**
```json
{
  "triggered": true,
  "task_id": "task-xyz",
  "node_id": "http_request_1",
  "node_name": "Fetch Customer Data",
  "status": "RUNNING",
  "next_triggerable_nodes": []  // Empty until this node completes
}
```

**Error cases:**
- `400 BAD_REQUEST`: Node is not in `triggerable_nodes` set
- `400 BAD_REQUEST`: Run is not in `SUSPENDED` status
- `404 NOT_FOUND`: Run or node not found

---

## 5. Use Cases & Domain Services

### 5.1 Modified: StartRunUseCase

When `execution_mode == "manual"`:
1. Create run with status `SUSPENDED` instead of `RUNNING`
2. DO NOT enqueue `StartRunAction`
3. Compute and set `triggerable_nodes` = entry nodes
4. Emit `run.suspended` event instead of `run.started`

```python
# Pseudo-code modification
if req.execution_mode == "manual":
    run.status = RunStatus.SUSPENDED
    run.context.triggerable_nodes = set(dag_info.entry_nodes)
    self._engine.emit_run_suspended(run)
    # DO NOT enqueue StartRunAction
else:
    run.status = RunStatus.RUNNING
    self._engine.emit_run_started(run)
    await self._queue.enqueue(StartRunAction(run_id=run.id))
```

### 5.2 New: TriggerNodeUseCase

**File:** `app/layer2_application/features/trigger_node/use_cases/trigger_node_use_case.py`

```python
@dataclass
class TriggerNodeCommand:
    run_id: str
    node_id: str
    force: bool = False

@dataclass
class TriggerNodeResult:
    triggered: bool = False
    task_id: str = ""
    node_id: str = ""
    node_name: str = ""
    status: str = ""
    next_triggerable_nodes: list[str] = field(default_factory=list)
    error: str = ""
    error_code: str = ""

class TriggerNodeUseCase:
    async def execute(self, command: TriggerNodeCommand) -> TriggerNodeResult:
        # 1. Validate run exists and is in SUSPENDED state
        # 2. Validate node_id is in triggerable_nodes (or force=True)
        # 3. Create task for the node
        # 4. Update run context: move node from triggerable to triggered
        # 5. Set run status to RUNNING temporarily
        # 6. Enqueue ExecuteNodeAction for the node
        # 7. Return task info
```

---

## 6. Orchestration Engine Changes

### 6.1 Post-Task Completion in Manual Mode

When a task completes and `run.execution_mode == "manual"`:

1. DO NOT auto-dispatch successors
2. Update `run.context.triggerable_nodes` with eligible successors
3. Update `run.context.completed_nodes`
4. Transition run status back to `SUSPENDED`
5. Emit `run.suspended` event with updated triggerable nodes

```python
# In orchestration_engine.py, after task completion
if run.execution_mode == "manual":
    # Compute new triggerable nodes (successors with all predecessors complete)
    successors = dag_info.adjacency.get(completed_task.node_id, [])
    for succ_id in successors:
        preds = dag_info.predecessors.get(succ_id, [])
        if all(p in run.context.completed_nodes for p in preds):
            run.context.triggerable_nodes.add(succ_id)
    
    # Remove completed node from triggerable
    run.context.triggerable_nodes.discard(completed_task.node_id)
    
    # Check if run is complete
    await self.check_run_completion(run, workflow)
    if run.status not in [RunStatus.COMPLETED, RunStatus.FAILED]:
        run.status = RunStatus.SUSPENDED
        self.emit_run_suspended(run)
```

### 6.2 New Event: RunSuspended

```python
@dataclass
class RunSuspended:
    run_id: str
    workflow_id: str
    triggerable_nodes: list[str]
    completed_nodes: list[str]
    root_run_id: str = ""
```

---

## 7. Socket.IO Events

### 7.1 New Events for Manual Mode

| Event | Payload | Description |
|-------|---------|-------------|
| `run.suspended` | `{ run_id, workflow_id, triggerable_nodes[], completed_nodes[] }` | Run waiting for node trigger |
| `node.triggered` | `{ run_id, node_id, task_id }` | Node execution started via manual trigger |
| `manual_mode.node_ready` | `{ run_id, node_id, node_name, can_trigger: true }` | New node available for trigger |

### 7.2 Event Flow Example

```
Client                      Server
  |                            |
  |-- POST /runs (manual) ---->|
  |<-- 200 {status: SUSPENDED} |
  |<== run.suspended ===========|  (WS: entry nodes ready)
  |                            |
  |-- POST /nodes/A/trigger -->|
  |<-- 200 {task_id: T1} ------|
  |<== node.triggered ==========|
  |<== task.dispatched =========|
  |                            |
  |      (worker executes)     |
  |                            |
  |<== task.completed ==========|
  |<== run.suspended ===========|  (WS: successor nodes ready)
  |                            |
```

---

## 8. Database Schema Changes

### 8.1 WorkflowRun Table

Add columns:
```sql
ALTER TABLE workflow_runs ADD COLUMN execution_mode VARCHAR(20) DEFAULT 'auto';
ALTER TABLE workflow_runs ADD COLUMN triggerable_nodes JSONB DEFAULT '[]';
ALTER TABLE workflow_runs ADD COLUMN triggered_nodes JSONB DEFAULT '[]';
```

### 8.2 Migration

Existing runs default to `execution_mode = 'auto'` with empty triggerable/triggered sets.

---

## 9. Frontend Integration Points

### 9.1 Run Creation Modal

- Add toggle or dropdown: "Execution Mode: Auto / Manual"
- Document: Manual mode is for debugging, HITL, phased execution

### 9.2 Run Viewer (Manual Mode)

- Show "Trigger" button on triggerable nodes
- Visual indicator for:
  - Completed nodes (green checkmark)
  - Running nodes (spinner)
  - Triggerable nodes (blue "Ready" badge)
  - Blocked nodes (gray, tooltip shows blocking predecessors)

### 9.3 Node Context Menu (Manual Mode)

- "Trigger Node" — executes the node
- "View Output" — inspect completed node's output
- "Retry Node" — re-execute completed/failed node

---

## 10. Implementation Tasks

### Phase 1: Core Infrastructure (Backend)

| # | Task | Estimate | Files |
|---|------|----------|-------|
| 1.1 | Add `SUSPENDED` to `RunStatus` enum | 0.5h | `run_status.py` |
| 1.2 | Add `execution_mode`, `triggerable_nodes`, `triggered_nodes` to `WorkflowRun` | 1h | `workflow_run.py` |
| 1.3 | Update `StartRunUseCase` for manual mode | 2h | `start_run_use_case.py` |
| 1.4 | Create `TriggerNodeUseCase` | 3h | New file |
| 1.5 | Update `OrchestrationEngine` for manual mode completion | 3h | `orchestration_engine.py` |

### Phase 2: API Layer

| # | Task | Estimate | Files |
|---|------|----------|-------|
| 2.1 | Update `POST /runs` request DTO | 0.5h | `run_controller.py` |
| 2.2 | Add `POST /runs/{id}/nodes/{node_id}/trigger` endpoint | 1h | `run_controller.py` |
| 2.3 | Update response DTOs with manual mode fields | 1h | `node_data_dtos.py` |

### Phase 3: Realtime Events

| # | Task | Estimate | Files |
|---|------|----------|-------|
| 3.1 | Add `RunSuspended` event and topic | 1h | Events + notifier |
| 3.2 | Add `NodeTriggered` event | 0.5h | Events + notifier |
| 3.3 | Update Socket.IO docs | 0.5h | `socketio-integration-guide.md` |

### Phase 4: Persistence

| # | Task | Estimate | Files |
|---|------|----------|-------|
| 4.1 | Update run repository for new fields | 1h | Repository files |
| 4.2 | Add migration script | 0.5h | Migrations |
| 4.3 | Update projector for manual mode state | 1h | Projector files |

### Phase 5: Testing

| # | Task | Estimate | Files |
|---|------|----------|-------|
| 5.1 | Unit tests for `TriggerNodeUseCase` | 2h | New test file |
| 5.2 | Integration test: full manual mode flow | 3h | New test file |
| 5.3 | Update existing tests for backward compatibility | 1h | Various |

### Phase 6: Documentation

| # | Task | Estimate | Files |
|---|------|----------|-------|
| 6.1 | Update `frontend-integration-guide.md` | 1h | Docs |
| 6.2 | Update `workflow-schema-reference.md` | 0.5h | Docs |
| 6.3 | Create `manual-mode-guide.md` | 2h | New doc |

**Total Estimate:** ~22 hours

---

## 11. Backward Compatibility

- Default `execution_mode` is `"auto"` — existing integrations unchanged
- Existing API contracts preserved; new fields are additive
- `SUSPENDED` status will not appear in runs created without `execution_mode: "manual"`

---

## 12. Open Questions

1. **AI Integration:** How will the AI agent receive notification when a run is suspended and which nodes are triggerable?
   - Suggestion: Subscribe to `run.suspended` Socket.IO event which includes `triggerable_nodes[]`

2. **Partial auto-execution:** Should we support "manual until node X, then auto" mode?
   - Suggestion: Defer to Phase 2; keep Phase 1 simple

3. **Loop handling:** How do manual triggers work for LOOP body nodes?
   - Suggestion: Trigger exposes iteration scope; user triggers per-iteration

---

## 13. References

- [Frontend Integration Guide](../frontend-integration-guide.md)
- [Workflow Schema Reference](../workflow-schema-reference.md)
- [Frontend Interactive Nodes Guide](../frontend-interactive-nodes-guide.md)
- [Orchestration Engine Audit](../issues/orchestration-engine-audit-2026-04-16.md)
