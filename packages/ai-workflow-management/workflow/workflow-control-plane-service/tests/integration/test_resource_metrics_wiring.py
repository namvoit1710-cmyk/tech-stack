"""RAM and disk are only observable if bootstrap actually registers them.

The collectors are correct in isolation (``tests/unit/test_resource_metrics.py``)
and were still invisible for the whole life of this service, because nothing
registered them on the monitor's PRIVATE registry. That failure mode leaves no
trace: ``/metrics`` returns 200, every existing panel keeps working, and the
RSS series simply does not exist. A unit test cannot catch it -- only a test
that builds the real container and scrapes it can.

Lives in ``tests/integration`` because it builds the whole container (~15s).
"""

from prometheus_client import generate_latest

from bootstrap import build_app_container


def _scrape() -> str:
    container = build_app_container()
    monitor = container["monitor"]
    registry = getattr(monitor, "registry", None)
    assert registry is not None, "production monitor has no Prometheus registry"
    return generate_latest(registry).decode()


def test_the_production_scrape_carries_disk_metrics():
    text = _scrape()
    assert "wcp_disk_total_bytes" in text, (
        "disk metrics missing from the production scrape - "
        "_install_resource_collectors is not wired in bootstrap"
    )
    assert "wcp_disk_used_bytes" in text
    assert "wcp_disk_free_bytes" in text


def test_the_production_scrape_carries_the_standard_process_collectors():
    """Asserted via python_info rather than RSS on purpose: PlatformCollector
    needs no /proc, so this holds on every dev machine, while
    process_resident_memory_bytes would pass on CF and fail on Windows."""
    assert "python_info" in _scrape()


def test_the_process_collector_is_registered_even_where_it_emits_nothing():
    container = build_app_container()
    registry = container["monitor"].registry
    names = {type(c).__name__ for c in registry._collector_to_names}
    assert "ProcessCollector" in names, (
        "ProcessCollector not registered - process_resident_memory_bytes will "
        "be absent on Linux, which is the only platform that matters here"
    )
    assert "ResourceCollector" in names


def test_the_production_scrape_carries_the_leak_discriminator():
    """``wcp_memory_allocated_blocks`` is asserted and the other two leak
    signals are not, on purpose.

    ``ru_maxrss`` needs the ``resource`` module and ``mallinfo2`` needs glibc;
    neither exists on the Windows box these tests run on, and both correctly
    emit NO series there. ``sys.getallocatedblocks()`` needs nothing, so it is
    the one that can be proven wired from here — the others are verified against
    the live pod after deploy.
    """
    assert "wcp_memory_allocated_blocks" in _scrape()
