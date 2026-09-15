-- Migration: move node output OUT of AIW_WORKFLOW_RUNS.CONTEXT.
--
-- Design + every measurement: solace/specs/wcp-node-output-storage-redesign.md
-- (smdg-ai-hub). Step 2 of that document's ordering. These tables are ADDITIVE
-- and nothing reads or writes them yet -- the dual-write lands separately, and
-- CONTEXT is not dropped until reads have been migrated (step 4 before step 5,
-- or every {{$node.output}} resolves EMPTY with no error).
--
-- WHY: CONTEXT is one NCLOB carrying node outputs, loop state, topology and the
-- control sets, and the optimistic-CAS save rewrites all 24 columns every time.
-- Measured on prod-br10 tenant-1 on 2026-08-06: 192.43 MB stored across 6,060
-- runs, 43,199 saves, 1.58 GB rewritten -- 8.4x write amplification. One node
-- completion costs 3 round-trips carrying 2x the full CONTEXT.
--
-- The payload is stored 4-6 times per completion: AIW_TASKS.OUTPUT_METADATA,
-- task_output["metadata"], the flat spread over task_output, the run context
-- under node.id, again under node.name, and twice more in the event log's
-- state_updates. On one real run the node.name alias alone was 46.9% of CONTEXT.
--
-- ============================================================================
-- 1. AIW_RUN_NODE_OUTPUT -- one row per artifact, one shape, no inline branch
-- ============================================================================
--
-- Replaces run.context.variables[node.id] and variables[node.name] with ONE
-- stored row and TWO indexes over it. Querying a node by name stays supported
-- (that requirement is why the alias exists today) without a second copy of the
-- payload -- see IDX_AIW_RNO_NAME below.
--
-- No PREVIEW and no PAYLOAD_INLINE column. GET /data already contracts
-- "identity only -- the frontend queries the file service directly", and
-- /artifacts/{artifact_id}/odata already exists, so the browser fetches rows on
-- demand instead of the control plane guessing a 3-row sample. The inline
-- branch (keeps_inline_output / INLINE_OUTPUT_MAX_BYTES) is what produced
-- SA-1949 and SA-1951, and its 256 KB ceiling has never once fired in
-- production -- 0 events above it out of 15,586. FILE_ID NOT NULL is the
-- schema-level statement that the branch is gone.
--
-- FILE_ID NOT NULL does NOT mean "there is always a file". _store_rows_recursive
-- sets file_id = "" when the payload produces no CSV bytes (an empty value), so
-- the empty string is a legitimate value meaning "no CSV was produced". NOT NULL
-- forbids the NULL that would mean "this row is inline instead" -- which is the
-- state being designed out.
--
-- RUN_ID is NVARCHAR(1024), NOT 36. Composite child-run IDs encode the whole
-- ancestor chain and production already produces 277-char IDs at 5+ levels of
-- nesting -- migration 026 widened AIW_WORKFLOW_RUNS.ID and AIW_TASKS.RUN_ID to
-- 1024 for exactly that reason after HANA error 274 in production. A narrower
-- column here would reject every nested run's output.
--
-- GENERATION is in the PRIMARY KEY. Re-executing an existing run keeps the same
-- RUN_ID and bumps AIW_WORKFLOW_RUNS.GENERATION (the G-guard, migrations 029 and
-- 030). Without it in the key, generation 1's attempt 0 would overwrite
-- generation 0's attempt 0 and the ITERATION/ATTEMPT history would be a lie.
--
-- The artifact tree is ADJACENCY (ARTIFACT_SEQ / PARENT_SEQ / ORDINAL / DEPTH),
-- not a materialized path. Two reasons, both measured over 3,680 refs in the 60
-- heaviest runs:
--   * artifact_id is RELATIVE and COLLIDES -- 55 distinct ids across 2,688
--     children, because add_artifact defaults artifact_id to the child's own
--     dotted segment. A PK on it silently merges two different artifacts.
--   * a path string grows with depth, so "unlimited nesting" eventually
--     overflows any NVARCHAR. With adjacency, depth lives in ROWS and every
--     column keeps a fixed width. Observed depth already reaches 6.
--
-- ARTIFACT_NAME is not decoration -- _reconstruct_recursive re-attaches a child
-- with set_by_path(prow, art["name"], ...), and register_artifacts keys the
-- run-level {{$artifacts.<name>}} registry off it. PARENT_ROW_KEY_COLUMN carries
-- add_artifact's "__parent_row_id" marker. Dropping either breaks the rebuild.
--
-- Row-level linkage (__row_id / __parent_row_id) is NOT here on purpose -- it
-- lives INSIDE the CSV files and stays there untouched. Only the artifact-level
-- tree moves into rows.
CREATE COLUMN TABLE "AIW_RUN_NODE_OUTPUT" (
    "RUN_ID"                NVARCHAR(1024) NOT NULL,
    "NODE_ID"               NVARCHAR(128)  NOT NULL,
    "GENERATION"            INTEGER        NOT NULL DEFAULT 0,
    "ITERATION"             INTEGER        NOT NULL DEFAULT 0,
    "ATTEMPT"               INTEGER        NOT NULL DEFAULT 0,
    "ARTIFACT_SEQ"          INTEGER        NOT NULL DEFAULT 0,
    "NODE_NAME"             NVARCHAR(256),
    "PARENT_SEQ"            INTEGER,
    "ARTIFACT_PATH"         NVARCHAR(256)  NOT NULL DEFAULT '__root__',
    "ARTIFACT_NAME"         NVARCHAR(256)  NOT NULL DEFAULT '',
    "ARTIFACT_TYPE"         NVARCHAR(32)   NOT NULL DEFAULT '',
    "PARENT_ROW_KEY_COLUMN" NVARCHAR(64),
    "ORDINAL"               INTEGER        NOT NULL DEFAULT 0,
    "DEPTH"                 INTEGER        NOT NULL DEFAULT 0,
    "FILE_ID"               NVARCHAR(128)  NOT NULL DEFAULT '',
    "CONTENT_TYPE"          NVARCHAR(64)   NOT NULL DEFAULT 'text/csv',
    "VALUE_KIND"            NVARCHAR(16)   NOT NULL DEFAULT '',
    "ITEM_KIND"             NVARCHAR(16)   NOT NULL DEFAULT '',
    "ROW_COUNT"             INTEGER        NOT NULL DEFAULT 0,
    "PAYLOAD_BYTES"         INTEGER        NOT NULL DEFAULT 0,
    "SCHEMA_HASH"           NVARCHAR(64),
    "REF_VERSION"           INTEGER        NOT NULL DEFAULT 0,
    "ETAG"                  NVARCHAR(128),
    "CREATED_AT"            NVARCHAR(64),
    PRIMARY KEY ("RUN_ID", "NODE_ID", "GENERATION", "ITERATION", "ATTEMPT", "ARTIFACT_SEQ")
);

-- The ONLY secondary index on this table, and the reason "query by id AND by
-- name" costs one stored payload instead of two.
--
-- Deliberately no index on (RUN_ID, NODE_ID, PARENT_SEQ): every tree read is
-- WHERE RUN_ID = ? AND NODE_ID = ?, which is a PRIMARY KEY prefix, and the
-- parent/child re-attachment happens in Python over the rows already returned.
-- An index that no query narrows on is pure write amplification on what will
-- become the hottest insert path in the service.
CREATE INDEX "IDX_AIW_RNO_NAME" ON "AIW_RUN_NODE_OUTPUT" ("RUN_ID", "NODE_NAME");

-- ============================================================================
-- 2. AIW_SCHEMA_DEF -- the columns dictionary
-- ============================================================================
--
-- A descriptor is NOT small: p50 4,977 B, p99 44,025 B, max 60,918 B, with up
-- to 537 columns. But it barely varies: 2,702 refs collapse to 481 distinct
-- (columns, column_types) pairs -- 5.6x, 31.51 MB down to 7.908 MB. Strip
-- columns/column_types/artifacts out of a descriptor and what remains is a flat
-- ~214 B at every percentile.
--
-- So the schema is stored ONCE per distinct shape, keyed by a content hash, and
-- AIW_RUN_NODE_OUTPUT.SCHEMA_HASH points at it. Immutable by construction, so
-- it is cacheable in-process forever and never invalidates.
--
-- COLUMNS_JSON is a deliberate NCLOB exception to the "cap it in the schema"
-- rule below: HANA's NVARCHAR maximum is 5000 and a DESCRIPTOR NVARCHAR(4000)
-- would have rejected 52% of real descriptors. This table is write-once, a few
-- thousand rows, and never on the hot write path.
--
-- COLUMN_COUNT is stored so a schema that bloats is visible without parsing the
-- NCLOB -- the FE needs "columns" to populate the suggestion dropdown, and a
-- 537-column node is the shape worth alerting on.
CREATE COLUMN TABLE "AIW_SCHEMA_DEF" (
    "SCHEMA_HASH"  NVARCHAR(64) NOT NULL,
    "COLUMNS_JSON" NCLOB,
    "COLUMN_COUNT" INTEGER      NOT NULL DEFAULT 0,
    "CREATED_AT"   NVARCHAR(64),
    PRIMARY KEY ("SCHEMA_HASH")
);

-- ============================================================================
-- 3. AIW_RUN_NODE_STATE -- the five small sets that ride the big blob
-- ============================================================================
--
-- Replaces completed_nodes, skipped_nodes, node_visits, pending_revisits and
-- last_output_ports inside CONTEXT. Today marking one node complete rewrites the
-- entire context NCLOB (124 KB on a real run) -- here it is a ~100-byte UPSERT.
--
-- No GENERATION column: this table answers "what is the CURRENT state of this
-- node", and a re-execution resetting it is the correct semantic. History lives
-- in AIW_EVENT_LOG, which stays authoritative.
CREATE COLUMN TABLE "AIW_RUN_NODE_STATE" (
    "RUN_ID"           NVARCHAR(1024) NOT NULL,
    "NODE_ID"          NVARCHAR(128)  NOT NULL,
    "STATE"            NVARCHAR(24)   NOT NULL DEFAULT '',
    "VISITS"           INTEGER        NOT NULL DEFAULT 0,
    "LAST_OUTPUT_PORT" NVARCHAR(64),
    "UPDATED_AT"       NVARCHAR(64),
    PRIMARY KEY ("RUN_ID", "NODE_ID")
);

-- ============================================================================
-- 4. AIW_RUN_VARIABLE -- the context keys that are not node output
-- ============================================================================
--
-- __vars, input, artifacts, run, metadata -- the scopes
-- get_variable_suggestions actually reads (scope_providers.py). One row per key
-- so writing one scope does not rewrite the others.
--
-- __credentials MUST NOT be written here. LAI-19: no secret in a store that is
-- replayable or queryable. It is resolved in memory and never persisted --
-- putting it in a row keyed by run id is how it would leak into every future
-- audit query and every projector replay.
CREATE COLUMN TABLE "AIW_RUN_VARIABLE" (
    "RUN_ID"     NVARCHAR(1024) NOT NULL,
    "VAR_KEY"    NVARCHAR(256)  NOT NULL,
    "VALUE"      NCLOB,
    "UPDATED_AT" NVARCHAR(64),
    PRIMARY KEY ("RUN_ID", "VAR_KEY")
);

-- ============================================================================
-- NOT in this migration, and why
-- ============================================================================
--
-- AIW_RUN_LOOP_STATE was in the sealed design as
-- (RUN_ID, NODE_ID, ITERATION, ITEM_REF, STATE, UPDATED_AT). It is held back
-- because that shape cannot hold what loop_state actually is. Read from
-- event_driven_loop_driver.py:274 -- loop_state is keyed per LOOP NODE, not per
-- iteration, and carries items, current_index, total, completed_iterations,
-- failed_iterations, mode, loop_type, condition, counter_variable,
-- next_dispatch_index, invocation_id, enclosing_scope_stack, parallelism,
-- iteration_results, iteration_result_chunks, iter_progress and iter_failed
-- nodes. A three-scalar per-iteration row would drop most of that and break
-- every loop, which is the highest-blast-radius subsystem in the engine.
--
-- The measurement that motivated the table still stands -- loop_state was 74.7%
-- (1.17 MB) of one run's context -- but the two keys responsible, items and
-- iteration_results, ALREADY have a claim-check path: items may be a file_ref
-- (item_at reads a single row through OData), and _spill_window flushes
-- iteration_results to a chunk file when spilling is enabled. So the 1.17 MB is
-- a threshold-and-flag problem, not a schema problem, and it is cheaper and
-- safer to fix there. A correct loop-state decomposition needs its own pass.
--
-- The ALTERs on AIW_WORKFLOW_RUNS (CURRENT_NODE) and AIW_TASKS
-- (OUTPUT_METADATA_V2 NVARCHAR(5000), the hard cap that stops 654 KB of payload
-- entering a column named metadata) belong to the cutover step, not this one.
-- Adding unused columns to the two hottest tables buys nothing until something
-- writes them, and both need a matching .hdbtable edit to reach an HDI tenant.
--
-- ============================================================================
-- Before the dual-write goes in, read this
-- ============================================================================
--
-- run_file_retention.delete_run_files finds a discarded run's CSVs by walking
-- collect_file_ids(run.context.variables). The moment node output stops living
-- in variables, that walk returns nothing and every re-run silently leaks every
-- file it wrote. Dual-write keeps variables populated, so retention survives
-- steps 2 through 4 -- but step 5 MUST re-point it at AIW_RUN_NODE_OUTPUT in
-- the same change that stops the old write.
