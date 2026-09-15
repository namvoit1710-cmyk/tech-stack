-- SA-2605 cutover RESET: clear all environment + binding data so the section-only
-- model starts from a clean slate. The operator re-creates environments, section
-- key schemas and workflow bindings afterwards (environment values were exported
-- before the cutover). Idempotent: DELETE is safe to re-run and a no-op once empty.
--
-- Order: child/association rows first, then the aggregates, then environments.
DELETE FROM "AIW_SECTION_ENVIRONMENT";
DELETE FROM "AIW_WORKFLOW_SECTION";
DELETE FROM "AIW_WORKFLOW_ENVIRONMENT";
DELETE FROM "AIW_ENV_SECTION_SCHEMA";
DELETE FROM "AIW_ENVIRONMENT";
