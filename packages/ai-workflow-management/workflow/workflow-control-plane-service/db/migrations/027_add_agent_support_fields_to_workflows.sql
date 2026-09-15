-- SA-517: Add agent support fields to workflows
-- action: One-line label for the workflow's overall action
-- instruction: Prompt-style brief for the workflow
-- agents_json: JSON array of AgentRef objects (workflow-level allow-list)
-- Note: NCLOB columns cannot have non-NULL defaults in HANA. Application code handles defaults.

ALTER TABLE "AIW_WORKFLOWS" ADD ("ACTION" NCLOB DEFAULT NULL);
ALTER TABLE "AIW_WORKFLOWS" ADD ("INSTRUCTION" NCLOB DEFAULT NULL);
ALTER TABLE "AIW_WORKFLOWS" ADD ("AGENTS_JSON" NCLOB DEFAULT NULL);
