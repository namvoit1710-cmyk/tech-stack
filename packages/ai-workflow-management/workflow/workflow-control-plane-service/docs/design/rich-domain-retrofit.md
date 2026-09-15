# Rich domain retrofit — pure logic becomes entity / value-object methods

**Status:** design proposal (not started)
**Date:** 2026-06-19
**Supersedes:** the "free function + thin `decide_*` wrapper" pattern in
`run-aggregate-decide-evolve-seam.md` and the free-function moves in
`layer2-services-dissolution.md` slices 1–2.

---

## 0. Principle

A **rich domain model**: every piece of pure domain logic lives **as a method on the entity or
value object it concerns** — never as a standalone free "calculate" function. Free functions like
`apply_event(state, env)`, `compute_fanout(...)`, `is_iteration_done(...)`,
`deterministic_event_id(...)` are the anemic pattern we are removing.

Pure calcs with **no natural single entity** (CSV bytes, ID hashing, JSON codec, schema walking,
value comparison) become **dedicated value objects with behavior** — still domain objects, still
methods, never free functions and never "infrastructure dumping ground."

Decision (2026-06-19): **retrofit everything already moved** (decide/evolve + dissolution slices
0–2) into this shape before continuing the `services/` dissolution.

Result-carrying dataclasses (`FanoutDecision`, `CompletionDecision`, `EntryDecision`,
`ProjectedRunState`, `EventLogEnvelope`, `SchemaArtifact`) are value objects and are fine as
frozen dataclasses — they hold data; the *behavior* moves onto entities/VOs.

---

## 1. Function → method mapping

| Current free function / module | New home (method on entity / VO) |
| --- | --- |
| `run_projection.apply_event(state, env)` | `ProjectedRunState.apply(env) -> ProjectedRunState` |
| `snapshot_codec.encode_state/decode_state` | `ProjectedRunState.to_json()` / `ProjectedRunState.from_json(s)` (classmethod); `SCHEMA_VERSION` → class attr |
| `snapshot_shadow_comparator.diff_states(a,b)` | `ProjectedRunState.diff(other) -> list[str]` |
| `topology_builder.TopologyBuilder.prepare(wf)` | `GraphTopology.from_workflow(wf)` (classmethod) |
| `fanout.compute_fanout(...)` | `GraphTopology.compute_fanout(*, causation_id, source_node_id, source_node_type, source_task_status, completed_nodes, expected_port_override) -> FanoutDecision` (reads `self.edge_map/predecessors/adjacency`) |
| `loop_topology.is_iteration_done(...)` | `GraphTopology.iteration_done(run, loop_node_id, iter_idx, workflow) -> bool` |
| `loop_topology.iterate_body_subgraph(...)` | `GraphTopology.body_subgraph(loop_node_id, workflow) -> list[str]` |
| `loop_topology._find_body_entry_node/_post_loop_reachable/_routed_edges` | private methods on `GraphTopology` |
| `loop_topology._is_loop_exit_edge/_is_loop_body_edge` | `WorkflowEdge.is_loop_exit()` / `WorkflowEdge.is_loop_body()` |
| `dag_traversal.collect_downstream_nodes_to_skip(...)` | `GraphTopology.downstream_to_skip(...)` |
| `event_id_helper.deterministic_event_id/child_run_id/outbox_id` | `EventId.derive(...)` / `.derive_child_run(...)` / `.derive_outbox(...)` (value object, classmethods) |
| `csv_ops.*` (byte-level row/column ops, dict↔csv) | `CsvDocument` value object (methods) |
| `value_comparator.compare_values(left, op, right)` | `Comparison.evaluate(left, op, right) -> bool` (value object) |
| `schema_artifact_walker.walk/iter/root_scalar_*` (+ `SchemaArtifact` VO) | `AutoFormSchema` value object: `.artifacts()`, `.scalar_defaults()`, `.scalar_columns()` |
| `node_input_etag_calculator.NodeInputETagCalculator.*` | `NodeInputEtag` value object: `.of(file_ref, schema_version)`, `.as_weak_header()`, `.matches_if_match(header)` |
| `WorkflowRun.decide_successors / decide_loop_iteration` | inline to call the `GraphTopology` methods directly — no free-function wrapper |

`GraphTopology` currently lives inside `entities/workflow_run.py`. It gains ~6 methods, so it is
**extracted to its own `entities/graph_topology.py`** (re-export from `workflow_run` during
transition; importers repointed). `EventLogEnvelope` (already a layer1 VO) is unchanged.

`rebuild_run_state` (layer2 `projection/`) stays a thin shell but calls `state.apply(env)`.

---

## 2. Retrofit slices (each independently green & revertible)

| Slice | Rework | Removes | Verify |
| --- | --- | --- | --- |
| R1 | `ProjectedRunState` gains `apply` / `to_json` / `from_json` / `diff` + `SCHEMA_VERSION`; `rebuild` shell calls `state.apply` | `run_projection` free `apply_event`, `snapshot_codec`, `snapshot_shadow_comparator` | projector/snapshot/codec tests |
| R2 | Extract `GraphTopology` → `entities/graph_topology.py` with `from_workflow`, `compute_fanout`, `iteration_done`, `body_subgraph`, `downstream_to_skip`; `WorkflowEdge.is_loop_exit/is_loop_body`; rewire `WorkflowRun.decide_successors/decide_loop_iteration` | `fanout.py`, `loop_topology.py`, `topology_builder.py`, `dag_traversal.py` | fanout + loop + topology + decide tests |
| R3 | `EventId` value object | `event_id_helper.py` | event-id / outbox / projector tests |
| R4 | `CsvDocument` value object | `csv_ops.py` | csv-ops / data-store tests |
| R5 | `Comparison`, `AutoFormSchema`, `NodeInputEtag` value objects | `value_comparator.py`, `schema_artifact_walker.py`, `node_input_etag_calculator.py` | condition/switch, schema-artifact, etag tests |

Mechanics per slice: add the method/VO → repoint callers to call the method (sweep **whole service
dir incl. `bootstrap.py`**) → delete the old free-function module → `--collect-only` gate →
touched tests → full `tests/unit/` as the final gate. No behaviour change; suite is the oracle.

After R1–R5 the `layer1_domain` root has **no free-function calc modules** — only entities, value
objects (with behavior), and `events.py`/`exceptions.py`. Then the `services/` dissolution resumes
at slice 3 (`node_handlers/`), applying the rich-domain rule to any new extraction.

---

## 3. Caveats

- **Reworks just-completed slices 1–2** and the decide/evolve free functions. Larger churn now,
  but yields the consistent rich-domain model the user wants before more code lands on the old
  pattern.
- `GraphTopology` extraction is itself a cascade (it's imported from `workflow_run` widely) —
  handled by a transitional re-export + scoped repoint, like prior slices.
- Value objects must stay pure (layer1): `EventId` (uuid5), `CsvDocument` (stdlib csv/json),
  `Comparison`, `AutoFormSchema`, `NodeInputEtag` — no I/O, no collaborators.
- No behaviour change is the contract; if a test's *expectation* must change, the cut is wrong.
