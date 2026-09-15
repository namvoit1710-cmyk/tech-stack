"""Resource limits for the runner child, and the messages the user sees.

Shared by the parent (``SubprocessCodeExecutor``, which reads the limits from the
environment and hands them to the child) and by the child (``runner/__main__``,
which applies them to itself). One definition, so the number in the error message
is always the number that was actually enforced.

The ceilings are DERIVED from the pod, not hard-coded
----------------------------------------------------
The worker and every runner child share a single container quota. A code-node result
costs the *parent* ~5-6x its serialized size — ``communicate()`` buffers the child's
whole stdout as a string, then ``json.loads`` builds the object graph on top of it —
and the child costs ~3x more while producing it. So the quota is spent in multiples of
the OUTPUT size, times the number of scripts in flight:

    usable        = pod_quota - worker_baseline - headroom
    per_script    = usable / max_concurrent
    memory ceiling = per_script                       (RLIMIT_AS on the child)
    output ceiling = per_script / PARENT_AMPLIFICATION

A hard-coded "24 MB" would be a lie the moment someone edits ``memory:`` in
deployment.yml or changes the concurrency. So we measure the two inputs at startup —
the real container quota and the worker's real baseline RSS — and compute the rest.
Bump the pod to 512 MB and the ceilings follow on their own; raise the concurrency and
they shrink to pay for it.

Measured 2026-07-14 in a real 256 MB cgroup with swap off (the CF container's shape).
Before this, the child had no memory limit at all: a script allocated 712 MB inside a
256 MB container and the kernel OOM-killer took the *worker* down with it. And a mere
~55 MB of in-flight output — one big node, or a handful of ordinary ones at once —
SIGKILLed the worker.

* ``CODE_NODE_MAX_CONCURRENT`` (1) — scripts running at once in this worker process.
  This is the one true *input*: it buys throughput and it is paid for out of the
  ceilings above. Per-script ceilings alone do NOT keep the container alive — the SDK
  runs sync handlers on a pool of ``MAX_WORKER_THREADS`` (8), so eight individually
  legal nodes still add up. Measured: four concurrent nodes returning 20 MB each, every
  one under its output ceiling, still OOM-killed the worker.
* ``CODE_NODE_MAX_MEMORY_MB`` / ``CODE_NODE_MAX_OUTPUT_MB`` — derived as above. Set
  either explicitly to override the derivation; set to 0 to disable that limit entirely.

The deployment also sets ``WORKER_MAX_CONCURRENT=2`` (SA-1530), which bounds tasks
in-flight *end to end* — released only after the result has been streamed and the
callback sent, so it is what stops finished-but-still-held results from piling up. At
capacity it answers 429 + Retry-After and the executor requeues, so nothing is lost.
This module's gate is the belt to that pair of braces: it also covers the sync
``/execute`` and gRPC paths, which have no SA-1530 gate.
"""

import json
import os

ENV_MAX_MEMORY_MB = "CODE_NODE_MAX_MEMORY_MB"
ENV_MAX_OUTPUT_MB = "CODE_NODE_MAX_OUTPUT_MB"
ENV_MAX_CONCURRENT = "CODE_NODE_MAX_CONCURRENT"

DEFAULT_MAX_CONCURRENT = 1

# What a result costs the PARENT, as a multiple of its serialized size: communicate()
# holds the whole stdout as a string, then json.loads builds the object graph on top of
# it. Measured: a 9.8 MB result cost the parent 64.6 MB of peak RSS.
PARENT_AMPLIFICATION = 6

# Kept back for allocator fragmentation, the worker growing after startup, and the
# request/response machinery around a task.
HEADROOM_FRACTION = 0.15
MIN_HEADROOM_MB = 32

# Floors, so a mis-sized pod degrades into small-but-working rather than zero.
MIN_MEMORY_MB = 64
MIN_OUTPUT_MB = 4

# Used only when the quota / baseline cannot be read (Windows dev, odd container).
FALLBACK_POD_MEMORY_MB = 256
FALLBACK_BASELINE_MB = 96


def pod_memory_mb() -> int:
    """The container's memory quota — the number the OOM killer actually enforces.

    Cloud Foundry sets ``MEMORY_LIMIT`` and ``VCAP_APPLICATION.limits.mem``; a plain
    container has the cgroup file. Try all three before falling back to a guess.
    """
    raw = os.environ.get("MEMORY_LIMIT", "").strip().rstrip("mMbB")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)

    try:
        limits = json.loads(os.environ.get("VCAP_APPLICATION", "{}")).get("limits", {})
        if int(limits.get("mem", 0)) > 0:
            return int(limits["mem"])
    except (ValueError, TypeError, AttributeError):
        pass

    # Our OWN cgroup first, then the mount root. Inside a container they are the same
    # file (the namespace makes the container's cgroup the root); outside one, only the
    # leaf carries the limit that actually applies to this process.
    leaf = ""
    try:
        with open("/proc/self/cgroup") as fh:
            leaf = fh.read().strip().split(":", 2)[2]
    except (OSError, IndexError):
        pass

    candidates = [
        f"/sys/fs/cgroup{leaf}/memory.max" if leaf else "",
        "/sys/fs/cgroup/memory.max",                     # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",   # cgroup v1
    ]
    for path in filter(None, candidates):
        try:
            with open(path) as fh:
                value = fh.read().strip()
        except OSError:
            continue
        if value.isdigit():
            mb = int(value) // (1024 * 1024)
            # An unlimited cgroup reports "max", or a number near 2^63. Ignore both.
            if 0 < mb < 1024 * 1024:
                return mb

    return FALLBACK_POD_MEMORY_MB


def worker_baseline_mb() -> int:
    """The worker's own resident memory, before it runs any script.

    Measured, not assumed: FastAPI + the SDK + the event-mesh client are not free, and
    whatever they cost is not available to the code node.
    """
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    return FALLBACK_BASELINE_MB


def derive_limits(
    pod_mb: int, baseline_mb: int, max_concurrent: int
) -> tuple[int, int, int]:
    """(memory ceiling, output ceiling, effective concurrency) for the pod's budget.

    Everything the code node may touch has to fit in what is left of the pod after the
    worker itself, divided by however many scripts may run at once.

    Concurrency is the *requested* input, not a promise: a script needs a floor of
    memory to be worth starting, so if the pod cannot afford the number asked for, the
    honest answer is to run fewer of them — not to hand each one a ceiling that adds up
    to more than the container has. Lowering the ceilings past the floor would just move
    the OOM kill somewhere less obvious.
    """
    headroom = max(MIN_HEADROOM_MB, int(pod_mb * HEADROOM_FRACTION))
    usable = max(0, pod_mb - baseline_mb - headroom)

    affordable = usable // MIN_MEMORY_MB
    effective = max(1, min(max(1, max_concurrent), affordable))

    per_script = usable // effective
    memory_mb = max(MIN_MEMORY_MB, per_script)
    output_mb = max(MIN_OUTPUT_MB, per_script // PARENT_AMPLIFICATION)
    return memory_mb, output_mb, effective


def pod_is_too_small(pod_mb: int, baseline_mb: int) -> bool:
    """True when the pod cannot even afford ONE script at the floor.

    The floors still apply (a degraded code node beats a dead one), but at this point
    the container is over-committed and someone needs to raise ``memory:``.
    """
    headroom = max(MIN_HEADROOM_MB, int(pod_mb * HEADROOM_FRACTION))
    return max(0, pod_mb - baseline_mb - headroom) < MIN_MEMORY_MB


def limit_mb(name: str, default: int) -> int:
    """Read a MB limit from the environment. 0 (or invalid) disables the limit."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(0, value)


# Snapshot the two inputs once, at worker startup, before any script has run — a
# baseline taken mid-task would measure the task, not the worker.
POD_MEMORY_MB = pod_memory_mb()
WORKER_BASELINE_MB = worker_baseline_mb()


def resolve_limits(max_concurrent: int) -> tuple[int, int, int]:
    """The (memory, output, concurrency) this worker will actually enforce, in MB.

    Derived from the pod's real quota and the worker's real baseline, so editing
    ``memory:`` in deployment.yml or the concurrency moves them automatically. An
    explicit ``CODE_NODE_MAX_*_MB`` wins over the derivation (0 disables that limit).
    """
    derived_memory, derived_output, effective = derive_limits(
        POD_MEMORY_MB, WORKER_BASELINE_MB, max_concurrent
    )
    return (
        limit_mb(ENV_MAX_MEMORY_MB, derived_memory),
        limit_mb(ENV_MAX_OUTPUT_MB, derived_output),
        effective,
    )


def describe_budget(max_concurrent: int) -> str:
    """One line for the startup log, so the enforced budget is never a mystery."""
    memory_mb, output_mb, effective = resolve_limits(max_concurrent)
    line = (
        f"code-node memory budget: pod={POD_MEMORY_MB}MB "
        f"worker_baseline={WORKER_BASELINE_MB}MB "
        f"concurrent={effective}"
        + (f" (requested {max_concurrent}, lowered to fit the pod)"
           if effective < max_concurrent else "")
        + f" -> child_memory={memory_mb}MB output={output_mb}MB "
        f"(the parent holds a result at ~{PARENT_AMPLIFICATION}x its serialized size)"
    )
    if pod_is_too_small(POD_MEMORY_MB, WORKER_BASELINE_MB):
        line += (
            f" WARNING: {POD_MEMORY_MB}MB cannot afford even one code script at the "
            f"{MIN_MEMORY_MB}MB floor once the worker's {WORKER_BASELINE_MB}MB is "
            "accounted for — raise `memory:` in deployment.yml"
        )
    return line


def memory_limit_message(max_memory_mb: int) -> str:
    """What a business user sees when their script runs out of memory.

    They cannot see the container, the runner, or the OOM killer — so name the limit
    and the two things they can actually do about it.
    """
    cap = f"{max_memory_mb} MB" if max_memory_mb else "the configured limit"
    return (
        f"Script ran out of memory (limit {cap}). The code node builds the whole "
        "result in memory at once — return fewer rows or fewer fields, or split the "
        "work across several items instead of building one big list."
    )


def output_limit_message(size_bytes: int, max_output_mb: int) -> str:
    """What a business user sees when their script returns too much data."""
    size_mb = size_bytes / (1024 * 1024)
    return (
        f"Script returned {size_mb:.1f} MB of data, over the {max_output_mb} MB limit "
        "for a code node. Return fewer rows or fewer fields — or aggregate the data "
        "here and let a downstream node fetch the detail."
    )


def apply_child_limits(timeout_seconds: int, max_memory_mb: int) -> None:
    """Cap this process (the runner child) before it runs any user code.

    Called by the child after ``exec``, where it is single-threaded — NOT by the
    parent via ``preexec_fn``, which CPython documents as unsafe in a threaded
    process (the worker dispatches use cases with ``asyncio.to_thread``).

    No-op on Windows, which has neither ``setrlimit`` nor process groups.
    """
    if os.name != "posix":  # pragma: no cover - POSIX only
        return

    import resource

    # CPU seconds — guards against busy loops that ignore the wall clock.
    cpu = max(1, timeout_seconds) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))

    # Address space — a runaway allocation dies here as a MemoryError inside the
    # child, instead of eating the container quota it shares with the worker.
    if max_memory_mb > 0:
        cap = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def block_disk_writes() -> None:
    """Forbid writing files to disk (stdout is a pipe, so it is unaffected).

    Applied as early as possible in the child — before the runner imports anything
    that could touch the filesystem.
    """
    if os.name != "posix":  # pragma: no cover - POSIX only
        return

    import resource

    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
