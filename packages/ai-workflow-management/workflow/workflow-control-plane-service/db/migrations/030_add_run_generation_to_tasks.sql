-- G-guard: stamp each task with the run's generation at creation time.
-- CompleteTaskUseCase / FailTaskUseCase reject callbacks whose
-- ``RUN_GENERATION`` is less than the current ``AIW_WORKFLOW_RUNS.GENERATION``.
-- Default 0 matches the default on the parent run so pre-migration tasks
-- align with pre-migration runs.
ALTER TABLE "AIW_TASKS" ADD ("RUN_GENERATION" INTEGER DEFAULT 0 NOT NULL)
