"""Claude Worker — entry point (MULTI-TYPE, jira-worker / SA-1078 pattern).

Registers three Claude-CLI node types (see ``app/node_types.py``); behaviour is fixed
per type, so there is no ``mode`` field.

Every mode goes through the stock ``run_worker(node_types=...)``: SERVER, HEADLESS and
PULL all handle multi-type natively now.

PULL used to need a private copy of the lease loop here (``app/pull_multi.py``) because
the SDK's pull runner was single-type. That support moved into the SDK, so the copy is
gone. Its two hard-won properties came with it rather than being left behind: the
`WorkerConcurrency` cap on total in-flight tasks (``WORKER_MAX_CONCURRENT``, pinned to 3
in docker-compose.yml — keep it at or below the number of comma-separated
``CLAUDE_WORKER_CLAUDE_CLI_TOKEN`` values, because the engine's ``_TOKEN_IDX`` sticky
index is deliberately unsynchronised and parallel runs share the pool), and batch-wide
lease renewal. The SDK also polls the types concurrently instead of round-robin, so a
multi-minute task on one node type no longer stalls the others.
"""

import os

from worker_sdk import run_worker

from app.layer4_frameworks.config import settings as app_settings
from app.node_types import NODE_TYPES

# Keep the job handle alive for the process lifetime — closing it triggers the kill.
_KILL_ON_EXIT_JOB = None


def _install_kill_on_exit_job() -> None:
    """Windows: put this worker in a Job Object with KILL_ON_JOB_CLOSE so EVERY child
    process (the `cmd /C claude …` runs and their node/claude grandchildren) is killed
    automatically when the worker exits — even a force-kill. Without this, restarting the
    worker mid-run orphans Claude processes that hold temp clones and burn token sessions.
    Best-effort: any failure just leaves the previous (leak-prone) behaviour."""
    global _KILL_ON_EXIT_JOB
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declare full signatures — without these, ctypes treats 64-bit HANDLEs as c_int
        # and the pseudo-handle from GetCurrentProcess overflows ("int too long").
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _EXT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC),
                ("IoInfo", _IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            return
        _KILL_ON_EXIT_JOB = job  # hold the handle open (closing it kills the tree)
        print("[claude-worker] kill-on-exit job installed (children die with the worker)")
    except Exception as exc:  # noqa: BLE001 — never block startup on this
        print(f"[claude-worker] kill-on-exit job not installed: {exc}")


if __name__ == "__main__":
    _install_kill_on_exit_job()
    features_path = os.path.join(os.path.dirname(__file__), "app", "layer2_application", "features")
    base_module = "app.layer2_application.features"

    # One call for every mode — APP_MODE (SERVER | HEADLESS | PULL) selects the
    # transport inside run_worker, and all three register these node types.
    run_worker(
        app_settings=app_settings,
        features_path=features_path,
        base_module=base_module,
        node_types=NODE_TYPES,
    )
