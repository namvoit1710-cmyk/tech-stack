from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    APP_NAME: str = "HTTP Request Worker"
    WORKER_NAME: str = "HTTP Request Worker"
    WORKER_TYPE: str = "http_request"
    SERVER_PORT: int = 35000

    # SA-1940 — cap the response body streamed into RAM so a large upstream
    # response cannot OOM the worker (a big GET/report was the single most
    # exposed OOM site). The response is STREAMED and read up to this many
    # bytes; beyond it the read is aborted and the task fails cleanly with a
    # ``response_too_large`` output flag + a bounded snippet (never a silent
    # truncation, never the whole body in RAM). This value is the runtime source
    # of truth — ``main.py`` injects it into the execute use-case. Generous
    # default so normal API responses are unaffected; raise/lower per deployment.
    # NOTE — this is 25x the broker cap, and the two limits used to know nothing
    # about each other. A 13 MB response passed this guard, was called a SUCCESS,
    # and then could not be published at all (1 048 576 B Event Mesh cap) — run
    # edda4f82-4a09-4587-beb5-8ed615ab8219. Raising or lowering this on its own
    # therefore does NOT decide what can get home; the second half of the answer
    # is the worker-sdk's ``RESULT_MAX_OUTPUT_BYTES``, which weighs the finished
    # result and names the field responsible. Change them together, or read the
    # other before changing this one.
    HTTP_MAX_RESPONSE_BYTES: int = 25 * 1024 * 1024  # 25 MiB

    # Ceiling for the node's ``timeout_seconds`` input — both the value the FE
    # form will accept and the runtime guard. Was hard-coded at 300 in two
    # places, so a slow-but-healthy upstream could not be waited on without a
    # code change; it is deployment config now.
    #
    # RAISING THIS IS HALF THE CHANGE. The executor holds its own pending-callback
    # deadline and reports ``Worker http_request did not callback in time`` if it
    # expires first. It derives that deadline from this node's ``timeout_seconds``
    # plus TASK_CALLBACK_GRACE_SECONDS, but clamps the result to
    # TASK_TIMEOUT_MAX_SECONDS (default 3600). So a value above that ceiling is
    # not honoured end-to-end: raise TASK_TIMEOUT_MAX_SECONDS on the executor too.
    HTTP_MAX_TIMEOUT_SECONDS: int = 300

    # Rollback switch for the bounded ``resolved_body`` output. Default FALSE =
    # the node reports the sent body as (size, sha256, first 4 KB) instead of
    # repeating all of it.
    #
    # It defaults off because echoing the whole body is what failed run
    # edda4f82-4a09-4587-beb5-8ed615ab8219 on tenant-1: a file_ref-backed body
    # resolved to 12.9 MB, the worker echoed it, and the executor could not
    # publish a 12 980 452-byte result past the 1 048 576-byte Event Mesh cap
    # (RESULT_TOO_LARGE). Setting this to ``true`` restores the old behaviour
    # and, with it, that failure mode — it exists only so one release can be
    # rolled back without a redeploy of the fix.
    HTTP_ECHO_RESOLVED_BODY: bool = False


settings = Settings()
