# data-factory — deploy prerequisites

Preconditions that must be true in the target env BEFORE deploy. Config values live in
`deployment.yml`; this documents the setup that config assumes. Keep current in the same PR.

## Service instances (must exist)
- No `services:` bindings declared. Persistence is local SQLite
  (`DATABASE_URL=sqlite+aiosqlite:////tmp/app.db`), so no HANA/HDI container is required.

> Note: `/tmp` SQLite is ephemeral per container/restart and per instance — fine for the
> current single-instance (`instances: 1`) config; not a shared/durable store.

## Upstream services (must be deployed & reachable)
- **file-service** — `FILE_SERVER_URL` points at it (`smdg-ai-dev-file-service…/api/v1` on
  dev, `…-file-service-qas…` on qas). data-factory uploads/downloads via file-service, and
  uses presigned upload for large files (`FILE_SERVICE_USE_PRESIGNED_UPLOAD_FOR_LARGE=True`,
  min `100` MB). file-service must be deployed and reachable in the tier, and its presigned
  storage backend working, or large-file flows fail.

## Runtime secrets (not in git)
- None declared.

## Resources
- Memory/disk are large (`9072M` each) to handle in-process batch work
  (`BATCH_SIZE=500000`, `MAX_FILE_SIZE=100`). Ensure the space quota allows it.

## Verify after deploy
- App `started`; REST port (`8000`) answers.
- A round-trip through file-service succeeds (upload a small file, then a >100 MB file to
  exercise the presigned path).
