#!/usr/bin/env bash
# Cloud Foundry entrypoint for LaidonLLM (LiteLLM proxy on SAP HANA).
#
#   1. If HANA_HOST is not already set (via `cf set-env`), map the first bound
#      HANA-looking service from VCAP_SERVICES to the HANA_* vars HanaConfig reads.
#   2. Start the proxy on the port Cloud Foundry assigns ($PORT).
#
# Secrets are NEVER baked into the image or committed — they arrive at runtime
# from the bound service or `cf set-env` (see deployment/README.md).
set -euo pipefail

# --- Map a bound HANA service -> HANA_* (only when not explicitly provided) ---
if [ -z "${HANA_HOST:-}" ] && [ -n "${VCAP_SERVICES:-}" ]; then
  hana_exports="$(python3 - <<'PY'
import json, os
vcap = json.loads(os.environ.get("VCAP_SERVICES", "{}"))
for _label, instances in vcap.items():
    for inst in instances or []:
        c = (inst.get("credentials") or {})
        host = c.get("host") or c.get("hostname")
        if not host:
            continue
        print("export HANA_HOST=%r" % host)
        if c.get("port"):     print("export HANA_PORT=%r" % str(c["port"]))
        if c.get("user"):     print("export HANA_USER=%r" % c["user"])
        if c.get("password"): print("export HANA_PASSWORD=%r" % c["password"])
        if c.get("schema"):   print("export HANA_SCHEMA=%r" % c["schema"])
        raise SystemExit(0)
PY
)"
  if [ -n "$hana_exports" ]; then
    eval "$hana_exports"
    echo "cf_entrypoint: mapped bound HANA service -> HANA_HOST=${HANA_HOST}"
  fi
fi

: "${DB_BACKEND:=hana}"; export DB_BACKEND
: "${PORT:=8080}"
: "${LITELLM_CONFIG_PATH:=/app/config.yaml}"
: "${LITELLM_NUM_WORKERS:=1}"

echo "cf_entrypoint: starting LaidonLLM (DB_BACKEND=${DB_BACKEND}) on 0.0.0.0:${PORT}"
exec litellm \
  --config "${LITELLM_CONFIG_PATH}" \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --num_workers "${LITELLM_NUM_WORKERS}"
