"""RAM and disk for the control plane (/metrics).

The CP builds ``PrometheusMonitor`` on a PRIVATE ``CollectorRegistry``, and a
private registry does not carry prometheus_client's default collectors. That is
why the live endpoint has had zero ``process_*`` series since it was written:
nothing was ever wrong, they were simply never registered. Verified against
smdg-ai-tenant-1-ai-workflow-control-plane on 2026-08-12 -- 0 lines matching
``^process_|^python_``.

So RSS comes from the library's own ``ProcessCollector``: reimplementing
/proc parsing here would be a second, worse copy of code that already ships.
What the library does NOT give is the part that decides whether this pod gets
OOM-killed -- the *container* limit and usage, which live in cgroup, not in the
process -- and disk, which it does not cover at all.

Two constraints shape every test below:

* ``ProcessCollector`` emits NOTHING when /proc is absent (Windows dev boxes,
  where these tests run). Asserting on ``process_resident_memory_bytes`` values
  would pass on CF and fail locally, so the standard collectors are tested by
  REGISTRATION and the custom one by injected readers. A test that cannot run
  on the machine that runs it is not a test.
* An unknown number must produce NO SERIES, never 0. A memory limit of 0 reads
  as "utilisation is infinite" and a disk total of 0 reads as "the disk is
  full" -- both are alarms invented by the absence of data.
"""

from __future__ import annotations

import logging

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor
from app.layer4_frameworks.monitoring.resource_collector import (
    ResourceCollector,
    install_resource_collector,
    install_standard_process_collectors,
    read_allocated_blocks,
    read_container_memory_limit,
    read_container_memory_usage,
    read_malloc_info,
    read_peak_rss,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

class _Stat:
    def __init__(self, dev: int) -> None:
        self.st_dev = dev


def _fake_fs(sizes: dict, devices: dict | None = None):
    """(disk_usage, stat) pair over an in-memory description of the fs."""
    devices = devices or {p: i for i, p in enumerate(sizes)}

    def disk_usage(path):
        if path not in sizes:
            raise FileNotFoundError(path)
        return sizes[path]

    def stat(path):
        if path not in devices:
            raise FileNotFoundError(path)
        return _Stat(devices[path])

    return disk_usage, stat


def _families(collector) -> dict:
    return {f.name: f for f in collector.collect()}


def _samples(collector) -> dict:
    out = {}
    for fam in collector.collect():
        for s in fam.samples:
            key = (s.name, tuple(sorted(s.labels.items())))
            out[key] = s.value
    return out


def _one(collector, name, **labels):
    return _samples(collector)[(name, tuple(sorted(labels.items())))]


# --------------------------------------------------------------------------
# the standard collectors -- registration, not values
# --------------------------------------------------------------------------

class TestStandardCollectorsAreRegistered:
    def test_a_private_registry_starts_without_process_metrics(self):
        """The bug being fixed, pinned as a test: this is why the live endpoint
        had no RSS."""
        monitor = PrometheusMonitor()
        text = generate_latest(monitor.registry).decode()
        assert "process_resident_memory_bytes" not in text
        assert "python_info" not in text

    def test_installing_adds_the_process_collector(self):
        monitor = PrometheusMonitor()
        assert install_standard_process_collectors(monitor) is True
        names = {type(c).__name__ for c in monitor.registry._collector_to_names}
        assert "ProcessCollector" in names

    def test_installing_adds_platform_and_gc_collectors(self):
        monitor = PrometheusMonitor()
        install_standard_process_collectors(monitor)
        names = {type(c).__name__ for c in monitor.registry._collector_to_names}
        assert "PlatformCollector" in names
        assert "GCCollector" in names

    def test_python_info_appears_on_every_platform(self):
        """PlatformCollector needs no /proc, so this one IS assertable here and
        proves the registration actually reached the scrape."""
        monitor = PrometheusMonitor()
        install_standard_process_collectors(monitor)
        assert "python_info" in generate_latest(monitor.registry).decode()

    def test_installing_twice_is_harmless(self):
        """Double bootstrap happens in tests; a duplicate registration raises
        ValueError inside prometheus_client and must not reach the caller."""
        monitor = PrometheusMonitor()
        assert install_standard_process_collectors(monitor) is True
        assert install_standard_process_collectors(monitor) is False
        assert "python_info" in generate_latest(monitor.registry).decode()

    def test_a_monitor_without_a_registry_is_skipped(self):
        class _Stub:
            pass
        assert install_standard_process_collectors(_Stub()) is False


# --------------------------------------------------------------------------
# container memory limit
# --------------------------------------------------------------------------

class TestContainerMemoryLimit:
    def test_cgroup_v2_max_file_is_read(self):
        files = {"/sys/fs/cgroup/memory.max": "1073741824\n"}
        assert read_container_memory_limit(read_text=files.get, environ={}) == 1073741824

    def test_cgroup_v2_unlimited_reads_as_unknown(self):
        """cgroup v2 writes the literal string 'max' for no limit. Parsed as a
        number it would be a crash; defaulted to 0 it would be a false alarm."""
        files = {"/sys/fs/cgroup/memory.max": "max\n"}
        assert read_container_memory_limit(read_text=files.get, environ={}) is None

    def test_cgroup_v1_limit_file_is_read_when_v2_is_absent(self):
        files = {"/sys/fs/cgroup/memory/memory.limit_in_bytes": "536870912\n"}
        assert read_container_memory_limit(read_text=files.get, environ={}) == 536870912

    def test_cgroup_v1_unlimited_sentinel_reads_as_unknown(self):
        """cgroup v1 has no 'max': unlimited is a number so large it is really
        a sentinel. Reported as a real limit it makes utilisation read 0.00%
        forever, which is worse than reporting nothing."""
        files = {"/sys/fs/cgroup/memory/memory.limit_in_bytes": "9223372036854771712\n"}
        assert read_container_memory_limit(read_text=files.get, environ={}) is None

    def test_cloud_foundry_env_is_the_last_resort(self):
        assert read_container_memory_limit(
            read_text=lambda _p: None, environ={"MEMORY_LIMIT": "1024M"},
        ) == 1024 * 1024 * 1024

    def test_cloud_foundry_env_accepts_a_gigabyte_suffix(self):
        assert read_container_memory_limit(
            read_text=lambda _p: None, environ={"MEMORY_LIMIT": "2G"},
        ) == 2 * 1024 * 1024 * 1024

    def test_cgroup_wins_over_the_environment(self):
        files = {"/sys/fs/cgroup/memory.max": "1073741824\n"}
        got = read_container_memory_limit(
            read_text=files.get, environ={"MEMORY_LIMIT": "512M"},
        )
        assert got == 1073741824, "cgroup is the kernel's answer; env is a hint"

    def test_nothing_readable_is_unknown_not_zero(self):
        assert read_container_memory_limit(read_text=lambda _p: None, environ={}) is None

    def test_garbage_is_unknown_not_a_crash(self):
        files = {"/sys/fs/cgroup/memory.max": "not-a-number"}
        assert read_container_memory_limit(read_text=files.get, environ={}) is None

    def test_a_reader_that_raises_is_unknown_not_a_crash(self):
        def boom(_path):
            raise PermissionError("sandboxed")
        assert read_container_memory_limit(read_text=boom, environ={}) is None


class TestContainerMemoryUsage:
    def test_cgroup_v2_current_is_read(self):
        files = {"/sys/fs/cgroup/memory.current": "268435456\n"}
        assert read_container_memory_usage(read_text=files.get) == 268435456

    def test_cgroup_v1_usage_is_read_when_v2_is_absent(self):
        files = {"/sys/fs/cgroup/memory/memory.usage_in_bytes": "134217728\n"}
        assert read_container_memory_usage(read_text=files.get) == 134217728

    def test_nothing_readable_is_unknown(self):
        assert read_container_memory_usage(read_text=lambda _p: None) is None


# --------------------------------------------------------------------------
# what the collector emits
# --------------------------------------------------------------------------

class TestMemorySeries:
    def test_limit_and_usage_are_emitted_when_known(self):
        c = ResourceCollector(
            disk_paths=(), memory_limit_reader=lambda: 1073741824,
            memory_usage_reader=lambda: 268435456,
        )
        assert _one(c, "wcp_memory_limit_bytes") == 1073741824
        assert _one(c, "wcp_memory_container_usage_bytes") == 268435456

    def test_an_unknown_limit_emits_NO_series(self):
        """Not zero. A limit of 0 makes every utilisation query divide by zero
        or read as infinite -- an alarm invented out of missing data."""
        c = ResourceCollector(
            disk_paths=(), memory_limit_reader=lambda: None,
            memory_usage_reader=lambda: None,
        )
        names = {s[0] for s in _samples(c)}
        assert "wcp_memory_limit_bytes" not in names
        assert "wcp_memory_container_usage_bytes" not in names

    def test_a_reader_that_raises_does_not_break_the_scrape(self):
        def boom():
            raise OSError("cgroup vanished")
        c = ResourceCollector(
            disk_paths=(), memory_limit_reader=boom, memory_usage_reader=boom,
            # Silenced too, or this asserts "allocated_blocks exists" -- true on
            # every platform, and already covered by TestAllocatedBlocks.
            peak_rss_reader=lambda: None, allocated_blocks_reader=lambda: None,
            malloc_info_reader=lambda: None,
        )
        assert _samples(c) == {}

    def test_a_failing_memory_reader_does_not_cost_the_DISK_metrics(self):
        """Each reader is guarded individually, not just the whole collect().

        With only the outer guard, one unreadable cgroup file takes the entire
        scrape down to nothing -- so a container running without cgroup access
        would silently lose its disk metrics too, for an unrelated reason.
        """
        def boom():
            raise OSError("cgroup vanished")
        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=boom, memory_usage_reader=boom,
        )
        assert _one(c, "wcp_disk_total_bytes", path="/") == 1000


class TestDiskSeries:
    def test_total_used_and_free_are_emitted_per_path(self):
        du, st = _fake_fs({"/": (1000, 400, 600)})
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        assert _one(c, "wcp_disk_total_bytes", path="/") == 1000
        assert _one(c, "wcp_disk_used_bytes", path="/") == 400
        assert _one(c, "wcp_disk_free_bytes", path="/") == 600

    def test_used_and_free_account_for_the_total(self):
        """Guards the classic swap: shutil returns (total, used, free) and a
        transposition here is invisible until the disk-full alert never fires."""
        du, st = _fake_fs({"/": (1000, 400, 600)})
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        s = _samples(c)
        total = s[("wcp_disk_total_bytes", (("path", "/"),))]
        used = s[("wcp_disk_used_bytes", (("path", "/"),))]
        free = s[("wcp_disk_free_bytes", (("path", "/"),))]
        assert used < total and free < total
        assert used + free <= total

    def test_two_paths_on_different_devices_both_report(self):
        du, st = _fake_fs(
            {"/": (1000, 400, 600), "/tmp": (200, 50, 150)},
            devices={"/": 1, "/tmp": 2},
        )
        c = ResourceCollector(
            disk_paths=("/", "/tmp"), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        assert _one(c, "wcp_disk_total_bytes", path="/") == 1000
        assert _one(c, "wcp_disk_total_bytes", path="/tmp") == 200

    def test_two_paths_on_the_SAME_device_report_once(self):
        """In a CF container /tmp is usually the same filesystem as /. Emitting
        both makes ``sum(wcp_disk_used_bytes)`` double-count a single disk, and
        someone will eventually sum it."""
        du, st = _fake_fs(
            {"/": (1000, 400, 600), "/tmp": (1000, 400, 600)},
            devices={"/": 1, "/tmp": 1},
        )
        c = ResourceCollector(
            disk_paths=("/", "/tmp"), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        s = _samples(c)
        paths = [lbl[0][1] for (name, lbl) in s if name == "wcp_disk_total_bytes"]
        assert paths == ["/"], "first path wins, the duplicate device is dropped"

    def test_a_missing_path_is_skipped_not_fatal(self):
        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})
        c = ResourceCollector(
            disk_paths=("/", "/does-not-exist"), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        s = _samples(c)
        paths = {lbl[0][1] for (name, lbl) in s if name == "wcp_disk_total_bytes"}
        assert paths == {"/"}

    def test_every_path_failing_still_yields_a_clean_scrape(self):
        du, st = _fake_fs({})
        c = ResourceCollector(
            disk_paths=("/", "/tmp"), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
            peak_rss_reader=lambda: None, allocated_blocks_reader=lambda: None,
            malloc_info_reader=lambda: None,
        )
        assert _samples(c) == {}

    def test_nothing_readable_advertises_no_disk_metric_at_all(self):
        """Asserted on the rendered exposition, not on samples.

        An empty ``GaugeMetricFamily`` still renders its HELP and TYPE lines, so
        a sample-level assertion cannot tell "there is no disk here" apart from
        "the disk metric exists and is empty". The second reads, to anyone
        building a panel, as a metric they can rely on.
        """
        du, st = _fake_fs({})
        registry = CollectorRegistry()
        registry.register(ResourceCollector(
            disk_paths=("/", "/tmp"), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
            peak_rss_reader=lambda: None, allocated_blocks_reader=lambda: None,
            malloc_info_reader=lambda: None,
        ))
        assert "wcp_disk_total_bytes" not in generate_latest(registry).decode()

    def test_a_device_probe_that_fails_still_reports_the_path(self):
        """st_dev is only a de-duplication hint. Losing it must cost the dedupe,
        never the measurement."""
        du, _ = _fake_fs({"/": (1000, 400, 600)})

        def stat(_path):
            raise PermissionError("no stat")
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=stat,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        assert _one(c, "wcp_disk_total_bytes", path="/") == 1000


# --------------------------------------------------------------------------
# scrape-safety and shape
# --------------------------------------------------------------------------

class TestTheScrapeIsNeverTakenDown:
    def test_a_total_failure_returns_no_families_and_logs(self, caplog):
        class _Exploding(ResourceCollector):
            def _read_disk(self):
                raise RuntimeError("boom")
        c = _Exploding(
            disk_paths=("/",),
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        )
        with caplog.at_level(logging.WARNING):
            assert list(c.collect()) == []
        assert any("resource" in r.getMessage().lower() for r in caplog.records)

    def test_it_renders_through_generate_latest(self):
        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})
        registry = CollectorRegistry()
        registry.register(ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=lambda: 2048, memory_usage_reader=lambda: 1024,
        ))
        text = generate_latest(registry).decode()
        assert 'wcp_disk_total_bytes{path="/"} 1000.0' in text
        assert "wcp_memory_limit_bytes 2048.0" in text

    def test_every_family_carries_help_text(self):
        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=lambda: 2048, memory_usage_reader=lambda: 1024,
        )
        for fam in c.collect():
            assert fam.documentation.strip(), f"{fam.name} has no HELP"

    def test_collect_is_read_only_and_repeatable(self):
        calls = []
        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})

        def counting(path):
            calls.append(path)
            return du(path)
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=counting, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
            # Pinned: sys.getallocatedblocks() is a LIVE counter that moves
            # between two calls because collecting itself allocates. That is
            # correct for a gauge and would make an equality check flap.
            peak_rss_reader=lambda: 1_000, allocated_blocks_reader=lambda: 2_000,
            malloc_info_reader=lambda: None,
        )
        first, second = _samples(c), _samples(c)
        assert first == second
        assert len(calls) == 2, "one statvfs per path per scrape, no caching, no fan-out"


class TestInstallation:
    def test_install_registers_on_the_monitor_registry(self):
        monitor = PrometheusMonitor()
        assert install_resource_collector(monitor) is not None
        assert any(type(c).__name__ == "ResourceCollector"
                   for c in monitor.registry._collector_to_names)

    def test_install_is_idempotent(self):
        monitor = PrometheusMonitor()
        assert install_resource_collector(monitor) is not None
        assert install_resource_collector(monitor) is None

    def test_a_monitor_without_a_registry_gets_nothing(self):
        class _Stub:
            pass
        assert install_resource_collector(_Stub()) is None

    @pytest.mark.parametrize("name", [
        "wcp_memory_limit_bytes",
        "wcp_memory_container_usage_bytes",
        "wcp_disk_total_bytes",
        "wcp_disk_used_bytes",
        "wcp_disk_free_bytes",
    ])
    def test_the_metric_names_are_the_ones_the_dashboard_queries(self, name):
        """The Grafana dashboard is built from these exact strings. Renaming one
        here silently empties a panel there."""
        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=lambda: 2048, memory_usage_reader=lambda: 1024,
        )
        assert name in _families(c)


# --------------------------------------------------------------------------
# leak signals -- the three numbers that decide "leak or high-water mark"
#
# Added after the 2026-08-12 analysis, which could only reach its conclusion by
# correlating unrelated counters in Prometheus after the fact. RSS alone cannot
# separate "live objects accumulating" from "the allocator kept freed arenas",
# and those have opposite fixes.
# --------------------------------------------------------------------------

def _leak_collector(**overrides):
    """A collector with no disk and no cgroup, so only leak signals remain."""
    du, st = _fake_fs({})
    kwargs = dict(
        disk_paths=(), disk_usage=du, stat=st,
        memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
        peak_rss_reader=lambda: None,
        allocated_blocks_reader=lambda: None,
        malloc_info_reader=lambda: None,
    )
    kwargs.update(overrides)
    return ResourceCollector(**kwargs)


class TestPeakRss:
    def test_it_is_emitted_when_the_kernel_answers(self):
        c = _leak_collector(peak_rss_reader=lambda: 551_350_272)
        assert _one(c, "wcp_memory_peak_rss_bytes") == 551_350_272

    def test_no_series_where_the_platform_cannot_answer(self):
        """Windows has no ``resource`` module. A 0 here would read as
        "this process never used memory", which is worse than a blank panel."""
        registry = CollectorRegistry()
        registry.register(_leak_collector(peak_rss_reader=lambda: None))
        assert "wcp_memory_peak_rss_bytes" not in generate_latest(registry).decode()

    def test_kilobytes_are_converted_to_bytes(self, monkeypatch):
        """``ru_maxrss`` is KiB on Linux. Reporting it raw would understate the
        peak by 1024x — and it would still look like a plausible number."""
        import sys as _sys
        import types

        fake = types.ModuleType("resource")
        fake.RUSAGE_SELF = 0
        fake.getrusage = lambda who: types.SimpleNamespace(ru_maxrss=143_507)
        monkeypatch.setitem(_sys.modules, "resource", fake)

        assert read_peak_rss() == 143_507 * 1024


class TestAllocatedBlocks:
    def test_it_answers_on_every_platform(self):
        """Unlike ru_maxrss and mallinfo this needs no OS support, so it is the
        one leak signal guaranteed to exist wherever the service runs."""
        assert read_allocated_blocks() > 0

    def test_it_is_emitted(self):
        c = _leak_collector(allocated_blocks_reader=lambda: 108_638)
        assert _one(c, "wcp_memory_allocated_blocks") == 108_638


class TestMallocInfo:
    def test_all_five_fields_become_series(self):
        c = _leak_collector(malloc_info_reader=lambda: {
            "arena": 400, "in_use": 250, "free": 150, "mmapped": 70,
            "free_chunks": 12,
        })
        assert _one(c, "wcp_memory_malloc_arena_bytes") == 400
        assert _one(c, "wcp_memory_malloc_in_use_bytes") == 250
        assert _one(c, "wcp_memory_malloc_free_bytes") == 150
        assert _one(c, "wcp_memory_malloc_mmapped_bytes") == 70
        assert _one(c, "wcp_memory_malloc_free_chunks") == 12

    def test_free_chunks_reads_ordblks_not_a_neighbouring_field(self, monkeypatch):
        """``mallinfo2`` is POSITIONAL and ``ordblks`` sits between ``arena``
        and ``smblks``. Reading the wrong slot would report the fastbin count
        as the free-chunk count and silently invert the bytes/chunks ratio the
        whole trim decision rests on — with no error and a plausible number."""
        import ctypes as _ctypes
        from app.layer4_frameworks.monitoring import resource_collector as rc

        crafted = rc._MallInfo2(
            arena=1, ordblks=2, smblks=3, hblks=4, hblkhd=5,
            usmblks=6, fsmblks=7, uordblks=8, fordblks=9, keepcost=10,
        )

        class _FakeFn:
            def __init__(self, value):
                self._value = value
                self.restype = None
                self.argtypes = None

            def __call__(self):
                return self._value

        class _FakeLibc:
            def __init__(self, fn):
                self.mallinfo2 = fn

        monkeypatch.setattr(
            _ctypes, "CDLL", lambda *a, **k: _FakeLibc(_FakeFn(crafted)))
        assert rc.read_malloc_info() == {
            "arena": 1, "mmapped": 5, "in_use": 8, "free": 9, "free_chunks": 2,
        }

    def test_nothing_is_emitted_off_glibc(self):
        """musl (Alpine) and Windows have no mallinfo2. A zero free_bytes would
        say "no retention", which is the opposite of "we cannot tell"."""
        registry = CollectorRegistry()
        registry.register(_leak_collector(malloc_info_reader=lambda: None))
        text = generate_latest(registry).decode()
        assert "wcp_memory_malloc_" not in text

    def test_a_partial_or_junk_reading_emits_only_what_is_numeric(self):
        c = _leak_collector(malloc_info_reader=lambda: {
            "arena": 400, "in_use": None, "free": "150", "mmapped": True,
            "free_chunks": False,
        })
        names = _families(c)
        assert "wcp_memory_malloc_arena_bytes" in names
        # None, a str and a bool are all refused rather than coerced
        assert "wcp_memory_malloc_in_use_bytes" not in names
        assert "wcp_memory_malloc_free_bytes" not in names
        assert "wcp_memory_malloc_mmapped_bytes" not in names
        assert "wcp_memory_malloc_free_chunks" not in names

    def test_the_struct_matches_glibc(self):
        """Field ORDER is the contract — mallinfo2 is positional, so a wrong
        order silently reports in_use as free and inverts the diagnosis."""
        from app.layer4_frameworks.monitoring.resource_collector import _MallInfo2
        names = [n for n, _ in _MallInfo2._fields_]
        assert names == [
            "arena", "ordblks", "smblks", "hblks", "hblkhd",
            "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost",
        ]

    def test_an_absent_libc_is_not_an_error(self, monkeypatch):
        import ctypes as _ctypes
        monkeypatch.setattr(
            _ctypes, "CDLL",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no libc here")))
        assert read_malloc_info() is None


class TestOneLeakSignalCannotCostTheOthers:
    def test_a_failing_peak_reader_still_leaves_the_blocks_metric(self):
        def boom():
            raise OSError("getrusage exploded")

        c = _leak_collector(
            peak_rss_reader=boom, allocated_blocks_reader=lambda: 4242)
        assert _one(c, "wcp_memory_allocated_blocks") == 4242

    def test_a_failing_malloc_reader_still_leaves_the_peak(self):
        def boom():
            raise OSError("libc went away")

        c = _leak_collector(
            malloc_info_reader=boom, peak_rss_reader=lambda: 999)
        assert _one(c, "wcp_memory_peak_rss_bytes") == 999

    def test_leak_signals_do_not_cost_the_disk_metrics(self):
        def boom():
            raise OSError("all three readers are unhappy")

        du, st = _fake_fs({"/": (1000, 400, 600)}, devices={"/": 1})
        c = ResourceCollector(
            disk_paths=("/",), disk_usage=du, stat=st,
            memory_limit_reader=lambda: None, memory_usage_reader=lambda: None,
            peak_rss_reader=boom, allocated_blocks_reader=boom,
            malloc_info_reader=boom,
        )
        assert _one(c, "wcp_disk_total_bytes", path="/") == 1000

# ----------------------------------------------------------------------
# malloc_trim
# ----------------------------------------------------------------------

class _FakeTrim:
    """Stands in for glibc. ``arena`` shrinks by ``release`` when called."""

    def __init__(self, release: int = 0, rc: int = 1, raises: bool = False):
        self.release = release
        self.rc = rc
        self.raises = raises
        self.calls: list = []
        self.restype = None
        self.argtypes = None

    def __call__(self, pad):
        if self.raises:
            raise OSError("boom")
        self.calls.append(int(getattr(pad, "value", pad)))
        return self.rc


def _patch_libc(monkeypatch, trim, arena_before=1000, arena_after=None):
    """Point resource_collector at a fake libc and a scripted mallinfo."""
    import ctypes as _ct
    from app.layer4_frameworks.monitoring import resource_collector as rc

    class _Lib:
        malloc_trim = trim

    monkeypatch.setattr(_ct, "CDLL", lambda *a, **k: _Lib())
    seq = [{"arena": arena_before},
           {"arena": arena_before if arena_after is None else arena_after}]
    monkeypatch.setattr(rc, "read_malloc_info", lambda: seq.pop(0) if seq else {})
    return rc


class TestMallocTrim:
    def test_absent_glibc_returns_none_not_zero(self):
        """A zero would read as 'nothing was reclaimable'.

        The truth on musl/Windows is 'this platform cannot be asked', and those
        are different facts — the same reason ``read_malloc_info`` returns None.
        """
        import ctypes
        import platform
        from app.layer4_frameworks.monitoring.resource_collector import malloc_trim

        if platform.system() == "Linux":
            try:
                ctypes.CDLL("libc.so.6", use_errno=False).malloc_trim
            except (OSError, AttributeError):
                assert malloc_trim() is None
            return
        assert malloc_trim() is None

    def test_arena_delta_is_reported_under_its_own_name(self, monkeypatch):
        """Named for what it measures. It used to be ``released_bytes``, and
        that name is why the janitor logged "released nothing" for a trim that
        returned 758 MiB of RSS to the OS: ``mallinfo2.arena`` is SBRK'd ADDRESS
        SPACE, and glibc frees interior pages with ``MADV_DONTNEED``, which
        leaves the address space mapped."""
        rc = _patch_libc(monkeypatch, _FakeTrim(rc=1),
                         arena_before=900_000_000, arena_after=120_000_000)
        out = rc.malloc_trim()
        assert out["arena_released_bytes"] == 780_000_000
        assert "released_bytes" not in out, (
            "the ambiguous name must not survive; it is the whole defect"
        )
        assert out["arena_before"] == 900_000_000
        assert out["arena_after"] == 120_000_000
        assert out["returned"] == 1
        assert out["duration_ms"] >= 0

    def test_returned_1_with_zero_released_is_kept_distinct(self, monkeypatch):
        """glibc gave pages back but the arena high-water did not move.

        That is a different fact from 'there was nothing to release', so the
        two are reported separately rather than collapsed into one number.
        """
        rc = _patch_libc(monkeypatch, _FakeTrim(rc=1),
                         arena_before=500, arena_after=500)
        out = rc.malloc_trim()
        assert out["returned"] == 1
        assert out["arena_released_bytes"] == 0

    def test_pad_is_passed_through_and_clamped(self, monkeypatch):
        trim = _FakeTrim()
        rc = _patch_libc(monkeypatch, trim)
        rc.malloc_trim(pad=65536)
        rc.malloc_trim(pad=-1)
        assert trim.calls == [65536, 0], (
            "a negative pad must not reach glibc as a huge size_t"
        )

    def test_a_failing_call_never_propagates(self, monkeypatch):
        """A trim is an optimisation. It must never break its caller."""
        rc = _patch_libc(monkeypatch, _FakeTrim(raises=True))
        assert rc.malloc_trim() is None

    def test_missing_symbol_is_none(self, monkeypatch):
        import ctypes as _ct
        from app.layer4_frameworks.monitoring import resource_collector as rc

        class _NoSym:
            def __getattr__(self, name):
                raise AttributeError(name)

        monkeypatch.setattr(_ct, "CDLL", lambda *a, **k: _NoSym())
        assert rc.malloc_trim() is None


class TestReadRssBytes:
    """The reader that ``malloc_trim`` was missing.

    Without it the trim measured ``mallinfo2.arena`` — SBRK'd address space —
    and so reported that it had released nothing while it was handing 758 MiB
    back to the OS. Measured on tenant-1 2026-08-21 right after a trim:

        [heap]  Size 617 508 kB   Rss 41 532 kB   Private_Dirty 41 532 kB
    """

    def test_it_reads_the_resident_field_not_the_size_field(self, tmp_path,
                                                            monkeypatch):
        """Field 1 of ``/proc/self/statm`` is total program size and field 2 is
        resident. Reading the first would report the 603 MiB of mapped address
        space as if it were resident — the exact confusion being fixed."""
        from app.layer4_frameworks.monitoring import resource_collector as rc

        statm = tmp_path / "statm"
        # size=150000 pages, resident=10383 pages (the measured heap: 617 508 kB
        # mapped, 41 532 kB resident, at 4 KiB pages).
        statm.write_text("150000 10383 900 1 0 2000 0")
        real_open = open

        def fake_open(path, *a, **k):
            if path == "/proc/self/statm":
                return real_open(str(statm), *a, **k)
            return real_open(path, *a, **k)

        monkeypatch.setattr("builtins.open", fake_open)
        monkeypatch.setattr(rc.os, "sysconf", lambda name: 4096,
                            raising=False)
        assert rc.read_rss_bytes() == 10383 * 4096

    def test_absent_proc_returns_none_not_zero(self, monkeypatch):
        """A 0 would read as "the process is resident in no memory", and a
        delta against it would look like a gigantic release."""
        from app.layer4_frameworks.monitoring import resource_collector as rc

        def boom(path, *a, **k):
            raise OSError("no /proc here")

        monkeypatch.setattr("builtins.open", boom)
        assert rc.read_rss_bytes() is None

    def test_it_is_not_the_high_water_mark(self):
        """``read_peak_rss`` is ``ru_maxrss``, which by construction can never
        come down. Using it to measure a reclaim always reports zero."""
        import inspect

        from app.layer4_frameworks.monitoring import resource_collector as rc

        src = inspect.getsource(rc.read_rss_bytes)
        body = src.split('"""')[-1]
        assert "getrusage" not in body, (
            "read_rss_bytes was reimplemented on the high-water mark, which "
            "can never show a release"
        )
        assert "statm" in body


class TestTrimReportsRss:
    def test_rss_delta_is_reported(self, monkeypatch):
        from app.layer4_frameworks.monitoring import resource_collector as rc

        seq = iter([927 * 1024 * 1024, 169 * 1024 * 1024])
        r = _patch_libc(monkeypatch, _FakeTrim(rc=1),
                        arena_before=1144, arena_after=1144)
        monkeypatch.setattr(r, "read_rss_bytes", lambda: next(seq))
        out = r.malloc_trim()
        assert out["rss_released_bytes"] == 758 * 1024 * 1024
        assert out["arena_released_bytes"] == 0, (
            "the arena is flat and that is exactly the live case: it cannot "
            "see MADV_DONTNEED"
        )

    def test_unreadable_rss_is_none_not_zero(self, monkeypatch):
        from app.layer4_frameworks.monitoring import resource_collector as rc

        r = _patch_libc(monkeypatch, _FakeTrim(rc=1),
                        arena_before=900, arena_after=100)
        monkeypatch.setattr(r, "read_rss_bytes", lambda: None)
        out = r.malloc_trim()
        assert out["rss_released_bytes"] is None
        assert out["arena_released_bytes"] == 800
