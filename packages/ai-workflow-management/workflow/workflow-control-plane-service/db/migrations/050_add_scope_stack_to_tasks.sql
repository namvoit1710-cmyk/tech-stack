-- Persist a task's loop scope_stack (JSON list of LoopScope dicts).
-- Event-driven LOOP advancement (maybe_enqueue_loop_iteration) identifies a
-- loop-body completion via task.get_innermost_scope. On a fresh-deserialize
-- repo (HANA) the scope was dropped because it was never a persisted column, so
-- a worker/PENDING body task reloaded at completion had scope_stack None and the
-- loop never advanced past iteration 0. NCLOB holds the JSON. NULL for the
-- (vast majority of) non-loop tasks and pre-migration rows.
ALTER TABLE "AIW_TASKS" ADD ("SCOPE_STACK" NCLOB)
