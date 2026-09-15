"""Run a local worker that serves ONE node type, to test a flow end to end.

    python tests/local_one_node.py cs_cancel_request
    python tests/local_one_node.py cs_detect_request cs_cancel_request
    CS_STUB=1 python tests/local_one_node.py cs_cancel_request   # + the stub backend

**Do not use main.py for this.** It registers all eight cs_* node types, and the deployed
cs-worker already serves them: two workers claiming one worker_type split the executor's
queue, so a real customer request lands on whichever leased it first. A laptop has no
credentials, so half of them would come back "no target configured". Registering only the
node under test has no overlap, and that is checked below — the script refuses to start if
another worker_type would come with it.

Verified on 2026-09-07 while testing cs_cancel_request: with only the new type registered,
the production cs_detect_request replicas stayed at age 6s and 29s throughout.

With CS_STUB=1 a target `TEST` / `LOCAL` is configured against tests/stub_backend.py, so
the flow's branches can be exercised without real credentials and without touching a real
change request. Without it, no credentials are configured at all and every call stops at
the login — which is also a safe and useful test of the flow's fault branch.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CS = os.path.dirname(HERE)
SDK = os.path.join(os.path.dirname(CS), "worker-sdk")
sys.path[:0] = [CS, SDK]
os.chdir(CS)

WANT = [a for a in sys.argv[1:] if not a.startswith("-")]
if not WANT:
    raise SystemExit(__doc__)

os.environ.setdefault("APP_MODE", "PULL")
os.environ.setdefault("WORKER_TENANT", "")
os.environ.setdefault("REGISTRY_URL",
                      "https://smdg-ai-tenant-1-ai-workflow-worker-executor"
                      ".cfapps.br10.hana.ondemand.com")
os.environ.setdefault("WORKER_MAX_CONCURRENT", "1")
os.environ.setdefault("LEASE_MAX_BATCH", "1")

if os.environ.get("CS_STUB") == "1":
    # API_URL + TOKEN_URL overrides are enough for the registry to build a usable target
    # (APP_PREFIX/REGION exist only to derive those two), and CLIENT_SECRET must be
    # non-empty or the target is marked unusable.
    os.environ.setdefault("LOG_WORKER_CLIENT_ID_TEST_LOCAL", "stub-client")
    os.environ.setdefault("LOG_WORKER_CLIENT_SECRET_TEST_LOCAL", "stub-secret")
    os.environ.setdefault("LOG_WORKER_API_URL_TEST_LOCAL",
                          "http://127.0.0.1:8799/CommonProcessService")
    os.environ.setdefault("LOG_WORKER_REQUESTOR_URL_TEST_LOCAL",
                          "http://127.0.0.1:8799/RequestorService")
    os.environ.setdefault("LOG_WORKER_TOKEN_URL_TEST_LOCAL",
                          "http://127.0.0.1:8799/oauth/token")

from worker_sdk import run_worker                      # noqa: E402
from app.config import settings as app_settings        # noqa: E402
from app.node_types import NODE_TYPES                  # noqa: E402

subset = [n for n in NODE_TYPES if n.worker_type in WANT]
unknown = [w for w in WANT if not any(n.worker_type == w for n in NODE_TYPES)]
if unknown:
    raise SystemExit("not in NODE_TYPES: %s. Available: %s"
                     % (", ".join(unknown), ", ".join(n.worker_type for n in NODE_TYPES)))
if len(subset) != len(set(WANT)):
    raise SystemExit("asked for %d type(s) but matched %d definitions — refusing, because "
                     "registering more than was named is how a laptop ends up competing "
                     "with the deployed worker for a queue nobody meant to share"
                     % (len(set(WANT)), len(subset)))

others = [n.worker_type for n in NODE_TYPES if n.worker_type not in WANT]
print("[local] serving ONLY: %s" % ", ".join(WANT))
print("[local] NOT registering %d other type(s), so those keep going to the deployed "
      "worker: %s" % (len(others), ", ".join(others)))
if "cs_detect_request" in WANT:
    print("[local] NOTE: cs_detect_request IS deployed, so this DOES share its queue. A "
          "real message may land here. The gate fails open when it cannot probe a CR "
          "(decision=run), and every log-reading node stays with the deployed worker, so "
          "the answer still comes out right — but stop this as soon as the test is done.")
print("[local] registry: %s" % os.environ["REGISTRY_URL"])
print("[local] stub target: %s"
      % ("TEST/LOCAL -> 127.0.0.1:8799" if os.environ.get("CS_STUB") == "1"
         else "none (no credentials — calls stop at the login)"))

run_worker(
    app_settings=app_settings,
    features_path=os.path.join(CS, "app", "features"),
    base_module="app.features",
    node_types=subset,
)
