# INPUT-node data lifecycle — diagrams

Two PlantUML diagrams that together document how INPUT-node payload data is
created, stored, read, edited, and submitted on the workflow control plane.

- `input_data_sequence.puml` — end-to-end sequence across FE, REST controller,
  orchestration engine, INPUT handler, schema walker + provisioner, file
  service, task repo, event log/projector, view store, data factory, and the
  edit-node-data use case.
- `input_data_structure.puml` — structural view of the `input_schema` →
  walker output → `__file_ref` tree → stored state (`task.resolved_input`,
  `view.variables[node_id]`) → `ArtifactDescriptor` response DTO, with the
  File Service sitting under every `file_id`.

## Lifecycle in ten lines

1. **Dispatch.** Engine runs `InputNodeHandler.handle`. It calls
   `walk_schema_artifacts()` (outputType-driven; one `SchemaArtifact` per
   array) and `root_scalar_columns()` (top-level scalars).
2. `provision_schema_artifacts()` uploads a header-only CSV for the root
   (`artifact_type="object_field"`) plus one per declared table
   (`"array_field"` / `"nested_array_field"`), producing a `__file_ref` tree.
3. The PENDING `Task` is created with `resolved_input = provisioned_ref` —
   that tree is the frozen input snapshot.
4. `apply_state_updates_as_files` emits `NodeOutputProduced`; the projector
   writes the same tree into `view.variables[node_id]` (working copy).
5. **Read.** `GET /data?side=input|output|both` builds an
   `ArtifactDescriptor` tree from each side. There is no `scalars` dict — the
   `__root__` CSV's `file_id` is the source of truth for top-level scalars.
6. **Edit.** `PATCH /data` takes `{artifacts: [{artifact_id, operations}, ...]}`
   and applies all ops atomically on a deep-copy of the working ref.
7. `object_field` artifacts accept only `insert` → `_upsert_single_row` edits
   row 1 of the root CSV and swaps its `file_id`.
8. `array_field` / `nested_array_field` artifacts forward ops to
   `INodeDataFactoryService` (insert / edit = delete+insert keyed on
   `__row_id` / delete-by-`__row_id`); the returned `file_id` replaces the
   old one.
9. On commit the engine emits `NodeDataEdited`; the projector rebuilds
   `view.variables[node_id]` with the new file_ids. Pre-image file_ids are
   orphan-GC'd. `task.resolved_input` is untouched.
10. **Submit.** `POST /submit` → `ResumePendingTaskUseCase` →
    `apply_state_updates_as_files({node_id: final_ref})` emits
    `NodeOutputProduced` + `TaskCompleted`. The projector marks the node
    completed and downstream dispatch proceeds.

## Embedded diagrams

### Sequence

```plantuml
!include input_data_sequence.puml
```

### Structure

```plantuml
!include input_data_structure.puml
```

## Key invariants

- `task.resolved_input` is **frozen** at dispatch and always served for
  `side=input`.
- `view.variables[node_id]` is the **working copy** mutated by `PATCH` (via
  the projector) and served for `side=output`.
- All tabular/scalar payloads are file-backed; the control plane only moves
  identity (`file_id`, columns, artifact_type). Row counts and rows come from
  the File Service directly.
