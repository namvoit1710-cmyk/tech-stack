"""RAM and disk for the control plane, read at scrape time.

Two separate things live here, because they come from two different places.

**The process.** ``PrometheusMonitor`` builds a PRIVATE ``CollectorRegistry``,
and a private registry does not carry prometheus_client's default collectors.
That is why the live ``/metrics`` has never had a single ``process_*`` series --
not a bug in the exporter, simply a registration that was never made. Installing
the library's own ``ProcessCollector`` gives RSS, virtual size, CPU seconds, open
file descriptors and start time for free; reimplementing /proc parsing here would
be a second, worse copy of shipped code.

**The container.** What the library cannot know is the part that decides whether
this pod gets OOM-killed: the *cgroup* limit and usage. RSS is the process,
the limit is the box it lives in, and only the second one explains a restart.
This matters directly for SA-2254, which caps every cache against an *estimated*
byte budget -- until now nothing measured the real number those estimates were
supposed to approximate.

Disk is not covered by any standard collector at all, and the CP writes
file-backed node output, so it gets read here too.

Design rules, both learned the hard way:

* **An unknown number emits NO SERIES, never 0.** A memory limit of 0 makes
  every utilisation query read as infinite; a disk total of 0 reads as "full".
  Both are alarms invented out of missing data, which is worse than a blank panel.
* **A scrape must never take the pod down**, and a failed scrape must never look
  like "everything is at zero" -- the same rule ``CacheBudgetCollector`` follows.
"""

from __future__ import annotations

import ctypes
import time
import logging
import os
import shutil
import sys
from typing import Callable, Iterable, Optional, Sequence

from prometheus_client import GCCollector, PlatformCollector, ProcessCollector
from prometheus_client.core import GaugeMetricFamily

logger = logging.getLogger(__name__)

_CGROUP_V2_LIMIT = "/sys/fs/cgroup/memory.max"
_CGROUP_V1_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
_CGROUP_V2_USAGE = "/sys/fs/cgroup/memory.current"
_CGROUP_V1_USAGE = "/sys/fs/cgroup/memory/memory.usage_in_bytes"

# cgroup v1 has no "max" keyword: an unlimited container reports a number so
# large it is really a sentinel. Reported as a real limit it pins utilisation at
# 0.00% forever, which is a lie that never alerts.
_V1_UNLIMITED_FLOOR = 1 << 62

_SIZE_SUFFIXES = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}

# Cloud Foundry gives the app one filesystem; /tmp and the droplet usually sit
# on it. They are probed separately anyway because that is NOT guaranteed, and
# identical devices are de-duplicated below so a sum can never double-count.
DEFAULT_DISK_PATHS: tuple[str, ...] = ("/", "/tmp", "/home/vcap/app")


def _default_read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:  # noqa: BLE001 - absent cgroup is the normal case off-Linux
        return None


def _read_int(read_text: Callable[[str], Optional[str]], path: str) -> Optional[int]:
    """An int from a cgroup file, or None for absent / 'max' / unparseable."""
    try:
        raw = read_text(path)
    except Exception:  # noqa: BLE001 - a sandboxed read must not crash a scrape
        return None
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_size(value: Optional[str]) -> Optional[int]:
    """Parse Cloud Foundry's ``MEMORY_LIMIT`` (e.g. ``1024M``) into bytes."""
    if not value:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    multiplier = 1
    if text[-1] in _SIZE_SUFFIXES:
        multiplier = _SIZE_SUFFIXES[text[-1]]
        text = text[:-1]
    if text.endswith("B"):
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def read_container_memory_limit(
    *,
    read_text: Optional[Callable[[str], Optional[str]]] = None,
    environ: Optional[dict] = None,
) -> Optional[int]:
    """Bytes this container may use, or None when nothing authoritative says.

    cgroup first because it is the kernel's own answer and it is what the OOM
    killer consults; the Cloud Foundry environment variable is a last-resort
    hint that describes intent rather than enforcement.
    """
    read_text = read_text or _default_read_text
    environ = os.environ if environ is None else environ

    value = _read_int(read_text, _CGROUP_V2_LIMIT)
    if value is not None and value < _V1_UNLIMITED_FLOOR:
        return value

    value = _read_int(read_text, _CGROUP_V1_LIMIT)
    if value is not None and value < _V1_UNLIMITED_FLOOR:
        return value

    try:
        return _parse_size(environ.get("MEMORY_LIMIT"))
    except Exception:  # noqa: BLE001
        return None


def read_peak_rss() -> Optional[int]:
    """The highest RSS this process has EVER reached, in bytes.

    Why this is not redundant with ``process_resident_memory_bytes``: Prometheus
    scrapes every 30s (measured on tenant-1), and the growth this exists to
    explain arrives in bursts — one 5-minute window on 2026-08-12 added 103 MiB
    on two TASK executions. A peak that lives and dies between scrapes is
    invisible to sampling, and all you see afterwards is the plateau it left.

    ``ru_maxrss`` is the kernel's own high-water mark, so no sampling is needed
    and no background task has to exist. Because it is MONOTONIC, an ``increase()``
    over a window says a NEW high-water was set inside that window — which is the
    "when" that sampling was supposed to provide, for one syscall.

    Linux reports kilobytes. ``resource`` does not exist on Windows, so this
    returns None there rather than inventing a number.
    """
    try:
        import resource as _resource
    except ImportError:
        return None
    return int(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss) * 1024


def read_rss_bytes() -> Optional[int]:
    """CURRENT resident set size, in bytes. None where ``/proc`` is absent.

    Deliberately not ``getrusage``: that is ``ru_maxrss``, a HIGH-WATER mark,
    which by construction can never show a release. ``read_peak_rss`` exists for
    the opposite question and the two must not be confused — using the high-water
    to measure a reclaim is how ``malloc_trim`` came to report that it had freed
    nothing while it was in fact freeing 758 MiB.

    Measured on tenant-1 2026-08-21, immediately after a trim (``/proc/8/smaps``)::

        [heap]  Size 617 508 kB   Rss 41 532 kB   Private_Dirty 41 532 kB

    603 MiB of address space, 40 MiB of it resident. That gap is exactly what
    this reads and what ``mallinfo2.arena`` cannot see.

    Field 2 of ``/proc/self/statm`` is resident PAGES; the page size comes from
    the OS rather than a hard-coded 4096 so this stays correct on a kernel with
    larger pages.
    """
    try:
        with open("/proc/self/statm", "r") as handle:
            resident_pages = int(handle.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    try:
        page = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):  # pragma: no cover
        page = 4096
    return resident_pages * int(page)


def read_allocated_blocks() -> Optional[int]:
    """Blocks currently allocated by CPython's allocator.

    **This is the leak discriminator**, and it is why this function is worth more
    than any RSS number. RSS cannot tell "Python is holding live objects" apart
    from "Python freed them and the allocator kept the arenas" — and those two
    have opposite fixes. This can:

    * blocks flat while RSS climbs  -> allocator retention, not a leak
    * blocks climbing with RSS      -> live objects accumulating, a real leak

    O(1): a counter pymalloc already maintains. Available on every platform.
    """
    return int(sys.getallocatedblocks())


class _MallInfo2(ctypes.Structure):
    """glibc's ``struct mallinfo2`` — every field ``size_t``.

    ``mallinfo`` (no 2) uses ``int`` fields that silently overflow past 2 GiB,
    which is inside this pod's 2111 MiB limit, so only the 2 variant is read.
    """

    _fields_ = [
        ("arena", ctypes.c_size_t),     # non-mmapped bytes obtained from the OS
        ("ordblks", ctypes.c_size_t),   # COUNT of free chunks across the arenas
        ("smblks", ctypes.c_size_t),
        ("hblks", ctypes.c_size_t),
        ("hblkhd", ctypes.c_size_t),    # bytes in mmapped regions
        ("usmblks", ctypes.c_size_t),
        ("fsmblks", ctypes.c_size_t),
        ("uordblks", ctypes.c_size_t),  # bytes actually in use
        ("fordblks", ctypes.c_size_t),  # bytes free INSIDE the arenas
        ("keepcost", ctypes.c_size_t),
    ]


def read_malloc_info() -> Optional[dict]:
    """glibc arena accounting, or None where glibc is not the allocator.

    ``fordblks`` is the number this was added for: bytes the process has freed
    but **not returned to the OS**. That is the concrete size of the gap between
    "Python let go" and "RSS came down", i.e. the retention that makes RSS a
    high-water mark rather than a measure of live data.

    ``ordblks`` is the COUNT of those free chunks, and it is what turns
    ``fordblks`` from a number into a decision. ``fordblks / ordblks`` is the
    mean free chunk, and a page (4 KiB) is the threshold that matters:
    ``malloc_trim`` releases whole free PAGES, so free space held in sub-page
    fragments can never be reclaimed no matter how many bytes it adds up to.
    Measured in this pod's own container (glibc 2.41, 2026-08-17): at a fixed
    ~10,039 free chunks, a trim took **0.3 ms** when they were 2 KiB and
    **54.6 ms** when they were 32 KiB — a 180x spread with the chunk COUNT
    held constant. Cost and benefit both track releasable pages, not chunks,
    so neither can be predicted from ``fordblks`` alone.

    Absent on musl (Alpine) and on Windows — the symbol simply will not resolve,
    and no series is emitted rather than a zero that would read as "no retention".
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=False)
        fn = libc.mallinfo2
    except (OSError, AttributeError):
        return None
    fn.restype = _MallInfo2
    fn.argtypes = []
    info = fn()
    return {
        "arena": int(info.arena),
        "mmapped": int(info.hblkhd),
        "in_use": int(info.uordblks),
        "free": int(info.fordblks),
        "free_chunks": int(info.ordblks),
    }


def malloc_trim(pad: int = 0) -> Optional[dict]:
    """Ask glibc to return free pages at the top of its arenas to the OS.

    Returns ``{"rss_released_bytes", "rss_before", "rss_after",
    "arena_released_bytes", "arena_before", "arena_after", "duration_ms",
    "returned"}``, or ``None`` where glibc is not the allocator — the same discipline
    ``read_malloc_info`` uses, because a zero here would read as "nothing was
    reclaimable" when the truth is "this platform cannot be asked".

    **Measure before you schedule this.** ``read_malloc_info`` already carries
    the decision rule: ``free / free_chunks`` above one page means the retention
    is page-releasable and a trim will return it; below a page it is sub-page
    fragmentation and the call costs time for nothing. Measured on tenant-1
    2026-08-20: mean free chunk **176.9 KiB** with **779 MiB free inside an
    829.8 MiB arena**, i.e. 94% of the arena releasable and 43x over the page
    threshold — so on THIS pod, at that moment, a trim had a great deal to give
    back.

    **It is synchronous and it blocks.** The benchmark in ``read_malloc_info``
    is 0.3 ms at 2 KiB chunks and 54.6 ms at 32 KiB with the chunk COUNT held
    constant; cost tracks releasable PAGES, and the pod's chunks are larger than
    both benchmark points. On a control plane pinned at ``instances: 1`` running
    an asyncio loop, that is event-loop time. Call it off the request path — an
    idle timer, never inside a materialize — and watch
    ``event_loop_lag_seconds`` when you first turn it on.

    ``pad`` is glibc's: bytes to KEEP at the top of the heap. 0 releases all it
    can; a non-zero pad leaves headroom so the next allocation does not
    immediately fault the pages back in.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=False)
        fn = libc.malloc_trim
    except (OSError, AttributeError):
        return None
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_size_t]

    before = read_malloc_info() or {}
    rss_before = read_rss_bytes()
    started = time.perf_counter()
    try:
        returned = int(fn(ctypes.c_size_t(max(0, int(pad)))))
    except Exception:  # noqa: BLE001 - never let a trim break the caller
        logger.debug("malloc_trim call failed", exc_info=True)
        return None
    duration_ms = (time.perf_counter() - started) * 1000.0
    after = read_malloc_info() or {}
    rss_after = read_rss_bytes()

    a_before = before.get("arena")
    a_after = after.get("arena")
    arena_released = (
        int(a_before) - int(a_after)
        if isinstance(a_before, int) and isinstance(a_after, int)
        else None
    )
    rss_released = (
        int(rss_before) - int(rss_after)
        if isinstance(rss_before, int) and isinstance(rss_after, int)
        else None
    )
    return {
        # RSS is the answer to "did the OS get memory back", and it is the
        # number this function exists to produce. Measured on tenant-1
        # 2026-08-21: one trim took RSS from 927.7 MiB to 169.4 MiB while
        # ``arena`` did not move one byte, because glibc releases INTERIOR free
        # pages with ``MADV_DONTNEED`` — the address space stays mapped, the
        # residency goes. Read straight off the container afterwards:
        #
        #     [heap]  Size 617 508 kB   Rss 41 532 kB   Private_Dirty 41 532 kB
        #
        # 603 MiB mapped, 40 MiB resident.
        "rss_released_bytes": rss_released,
        "rss_before": rss_before,
        "rss_after": rss_after,
        # ``mallinfo2.arena`` is SBRK'd ADDRESS SPACE, not residency, so it only
        # moves when the heap top itself shrinks. Kept because a top shrink is a
        # real and different event, but named for what it measures: reporting
        # this as "released_bytes" is what made the janitor log "released
        # nothing" for a trim that returned 758 MiB to the OS.
        "arena_released_bytes": arena_released,
        "arena_before": a_before,
        "arena_after": a_after,
        "duration_ms": duration_ms,
        # glibc returns 1 if it gave memory back, 0 if it could not. Kept
        # separate from both deltas: it is glibc's own answer, and it agreed
        # with RSS and disagreed with arena on every trim measured so far.
        "returned": returned,
    }


def read_container_memory_usage(
    *, read_text: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[int]:
    """Bytes the whole container is currently charged for, or None.

    Deliberately distinct from RSS: this includes page cache and every child
    process, and it is the figure the OOM killer compares against the limit.
    A pod can be killed while RSS looks calm.
    """
    read_text = read_text or _default_read_text
    for path in (_CGROUP_V2_USAGE, _CGROUP_V1_USAGE):
        value = _read_int(read_text, path)
        if value is not None:
            return value
    return None


class ResourceCollector:
    """Container memory and disk usage as Prometheus gauges.

    A prometheus_client *custom collector*: ``collect()`` runs inside
    ``generate_latest``, so every number is read at the moment it is asked for.
    Nothing is pushed and nothing runs on a timer.
    """

    def __init__(
        self,
        *,
        disk_paths: Optional[Sequence[str]] = None,
        disk_usage: Optional[Callable] = None,
        stat: Optional[Callable] = None,
        memory_limit_reader: Optional[Callable[[], Optional[int]]] = None,
        memory_usage_reader: Optional[Callable[[], Optional[int]]] = None,
        peak_rss_reader: Optional[Callable[[], Optional[int]]] = None,
        allocated_blocks_reader: Optional[Callable[[], Optional[int]]] = None,
        malloc_info_reader: Optional[Callable[[], Optional[dict]]] = None,
    ) -> None:
        self._disk_paths = tuple(
            DEFAULT_DISK_PATHS if disk_paths is None else disk_paths
        )
        self._disk_usage = disk_usage or shutil.disk_usage
        self._stat = stat or os.stat
        self._memory_limit = memory_limit_reader or read_container_memory_limit
        self._memory_usage = memory_usage_reader or read_container_memory_usage
        self._peak_rss = peak_rss_reader or read_peak_rss
        self._allocated_blocks = allocated_blocks_reader or read_allocated_blocks
        self._malloc_info = malloc_info_reader or read_malloc_info

    # -- collection ------------------------------------------------------

    def collect(self) -> Iterable[GaugeMetricFamily]:
        try:
            return (list(self._read_memory())
                    + list(self._read_leak_signals())
                    + list(self._read_disk()))
        except Exception:
            # A scrape must never take the pod down, and a failed scrape must
            # never be indistinguishable from "everything reads zero".
            logger.warning("resource metrics collection failed", exc_info=True)
            return []

    def _safe(self, reader: Callable[[], Optional[int]]) -> Optional[int]:
        try:
            return reader()
        except Exception:  # noqa: BLE001
            logger.debug("resource reader failed", exc_info=True)
            return None

    def _read_memory(self) -> list:
        families = []
        limit = self._safe(self._memory_limit)
        if limit is not None:
            families.append(GaugeMetricFamily(
                "wcp_memory_limit_bytes",
                "Bytes this container may use before the OOM killer acts "
                "(cgroup, falling back to Cloud Foundry MEMORY_LIMIT). Absent "
                "when no source is authoritative.",
                value=float(limit),
            ))
        usage = self._safe(self._memory_usage)
        if usage is not None:
            families.append(GaugeMetricFamily(
                "wcp_memory_container_usage_bytes",
                "Bytes the whole container is charged for, including page cache "
                "and child processes. This, not RSS, is what the OOM killer "
                "compares against wcp_memory_limit_bytes.",
                value=float(usage),
            ))
        return families

    def _read_leak_signals(self) -> list:
        """The three numbers that answer "leak or high-water mark?".

        Read together they are a decision procedure, which no one of them is
        alone:

        ==========================  ====================  =====================
        wcp_memory_allocated_blocks wcp_memory_malloc_    verdict
                                    free_bytes
        ==========================  ====================  =====================
        flat while RSS climbs       large / climbing      allocator retention
        climbing with RSS           flat                  a real leak
        ==========================  ====================  =====================

        and ``wcp_memory_peak_rss_bytes`` says WHEN, because it only ever moves
        when a new high-water is set.

        Once the verdict is "allocator retention", ``wcp_memory_malloc_free_bytes
        / wcp_memory_malloc_free_chunks`` says whether anything can be DONE about
        it: a mean free chunk above one page is reclaimable by ``malloc_trim``,
        below a page is not. Those two questions are separate and this reads
        them in order — is it a leak, and if not, is the retention releasable.
        """
        families = []
        peak = self._safe(self._peak_rss)
        if peak is not None:
            families.append(GaugeMetricFamily(
                "wcp_memory_peak_rss_bytes",
                "Highest RSS this process has ever reached (kernel ru_maxrss). "
                "Monotonic, so increase() over a window means a new peak was set "
                "in it — a spike between two 30s scrapes is otherwise invisible.",
                value=float(peak),
            ))
        blocks = self._safe(self._allocated_blocks)
        if blocks is not None:
            families.append(GaugeMetricFamily(
                "wcp_memory_allocated_blocks",
                "Blocks currently allocated by CPython. Flat while RSS climbs "
                "means the allocator is holding freed arenas (not a leak); "
                "climbing with RSS means live objects are accumulating (a leak).",
                value=float(blocks),
            ))
        info = None
        try:
            info = self._malloc_info()
        except Exception:  # noqa: BLE001
            logger.debug("malloc info reader failed", exc_info=True)
        if info:
            for field, name, help_text in (
                ("arena", "wcp_memory_malloc_arena_bytes",
                 "Non-mmapped bytes glibc holds from the OS."),
                ("in_use", "wcp_memory_malloc_in_use_bytes",
                 "Bytes actually in use inside those arenas."),
                ("free", "wcp_memory_malloc_free_bytes",
                 "Bytes freed but NOT returned to the OS. This is the size of "
                 "the gap between 'Python let go' and 'RSS came down'."),
                ("mmapped", "wcp_memory_malloc_mmapped_bytes",
                 "Bytes in mmapped regions, returned to the OS when freed."),
                ("free_chunks", "wcp_memory_malloc_free_chunks",
                 "COUNT of free chunks holding wcp_memory_malloc_free_bytes. "
                 "Divide the bytes by this: a mean above one page (4 KiB) means "
                 "the retention is page-releasable (malloc_trim would reclaim "
                 "it); below a page it is sub-page fragmentation that no trim "
                 "can return, however many bytes it totals."),
            ):
                value = info.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    families.append(GaugeMetricFamily(
                        name, help_text, value=float(value)))
        return families

    def _read_disk(self) -> list:
        total = GaugeMetricFamily(
            "wcp_disk_total_bytes", "Filesystem size behind this path.",
            labels=["path"])
        used = GaugeMetricFamily(
            "wcp_disk_used_bytes", "Bytes used on the filesystem behind this path.",
            labels=["path"])
        free = GaugeMetricFamily(
            "wcp_disk_free_bytes", "Bytes free on the filesystem behind this path.",
            labels=["path"])

        seen_devices: set = set()
        reported = False
        for path in self._disk_paths:
            try:
                usage = self._disk_usage(path)
            except Exception:  # noqa: BLE001 - a path that is not mounted here
                continue
            try:
                device = self._stat(path).st_dev
            except Exception:  # noqa: BLE001
                # st_dev is only a de-duplication hint. Losing it costs the
                # dedupe, never the measurement.
                device = None
            if device is not None:
                if device in seen_devices:
                    continue
                seen_devices.add(device)
            total.add_metric([path], float(usage[0]))
            used.add_metric([path], float(usage[1]))
            free.add_metric([path], float(usage[2]))
            reported = True

        return [total, used, free] if reported else []


def _has_collector(registry, class_name: str) -> bool:
    """Is a collector of this class already on the registry?

    Checked by type rather than by catching ValueError, because a collector that
    emits nothing on this platform (``ProcessCollector`` off Linux) registers
    zero metric names, so a duplicate registration raises nothing to catch.
    """
    try:
        return any(
            type(collector).__name__ == class_name
            for collector in registry._collector_to_names
        )
    except Exception:  # noqa: BLE001
        return False


def install_standard_process_collectors(monitor) -> bool:
    """Register prometheus_client's own process/platform/GC collectors.

    Returns True when at least one was newly added. ``ProcessCollector`` yields
    nothing where /proc is absent (Windows dev machines) but registering it is
    still correct -- the exporter runs on Linux.
    """
    registry = getattr(monitor, "registry", None)
    if registry is None:
        return False
    installed = False
    for collector_cls in (ProcessCollector, PlatformCollector, GCCollector):
        if _has_collector(registry, collector_cls.__name__):
            continue
        try:
            collector_cls(registry=registry)
            installed = True
        except ValueError:
            logger.debug("%s already registered", collector_cls.__name__)
    return installed


def install_resource_collector(monitor, **kwargs):
    """Attach :class:`ResourceCollector` to ``monitor``'s Prometheus registry.

    Returns the collector, or None when the monitor has no registry (the stub
    used in tests) or one is already installed.
    """
    registry = getattr(monitor, "registry", None)
    if registry is None:
        return None
    if _has_collector(registry, "ResourceCollector"):
        return None
    collector = ResourceCollector(**kwargs)
    try:
        registry.register(collector)
    except ValueError:
        logger.debug("resource collector already registered")
        return None
    return collector
