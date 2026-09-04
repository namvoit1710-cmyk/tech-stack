# governance-smart-api — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist)
- No `services:` bindings declared in the manifest.
- **HANA** — the app persists to SAP HANA Cloud. ALL `HANA_*` settings (host / schema / user
  / password / port / pool / timeout) are provided **per space** via `cf set-env` or a
  binding, intentionally NOT in this file (so the generated CF manifest never overwrites the
  space's HANA config). A HANA schema instance (`smdg-ai-governance-db`, hana/schema in
  `required-services.yml`) must be provisioned and reachable before start.
  - `AUTO_CREATE_SCHEMA=true` on dev (`false` on qas) → on dev the bound user needs
    schema-create rights; on qas the schema must already exist.

## Runtime secrets (not in git — cf set-env)
- `HANA_PASSWORD` (and the rest of the `HANA_*` connection set) — per space.
- `OPENAI_API_KEY` — optional; blank → the SDK falls back to a local stub (no OpenAI calls).

## External systems
- **OpenAI** — only when `OPENAI_API_KEY` is set. `OPENAI_MODEL=gpt-4.1-mini`,
  `OPENAI_BASE_URL=https://api.openai.com/v1` must be reachable.
- **ML model download** — the SDK loads a sentence-transformers embedding model
  (`EMBEDDING_MODEL_NAME=microsoft/harrier-oss-v1-270m`, 640-dim) + spaCy + torch into memory.
  The container carries the ML stack, so disk is sized to `10240M` and memory `5120M`; the
  space quota must allow it (undersized disk → "uncompressed layer size exceeds quota" →
  container crash → CF "Start app timeout", per SA-1480).

## Verify after deploy
- App `started` (does not crash on start — enough disk/memory for the ML image).
- HANA connectivity confirmed; on dev the schema auto-creates.
- Health/basic REST request answers; if `OPENAI_API_KEY` set, an embedding/LLM path works
  (otherwise the local stub responds).
