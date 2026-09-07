import time
import functools
import asyncio
import inspect
import logging
import contextvars
from .profiler import PROFILER


# Using standard python logging connected to our AppLogger setup
log = logging.getLogger("DataFactory.Performance")
_performance_stages = contextvars.ContextVar("performance_stages", default=None)


def record_performance_stage(stage_name: str, elapsed_seconds: float):
    stages = _performance_stages.get()
    if stages is None:
        return
    stages.append((stage_name, elapsed_seconds))


def track_performance(func):
    """
    Decorator to track the execution time, Max/Avg Memory (MB), and Average CPU (%) of a function.
    Provides a detailed, easily readable console report.
    """
    def _log_report(elapsed: float, samples, stages=None):
        stage_report = ""
        if stages:
            stage_parts = [f"{name}={duration:.2f}s" for name, duration in stages]
            stage_report = f"\n   🧭  Stage Timings: {' | '.join(stage_parts)}"

        if samples:
            cpus = [s[1] for s in samples]
            ram_rss = [s[2] for s in samples]

            max_cpu = max(cpus)
            avg_cpu = sum(cpus) / len(cpus)

            max_ram = max(ram_rss)
            avg_ram = sum(ram_rss) / len(ram_rss)

            start_ram = ram_rss[0]
            end_ram = ram_rss[-1]
            net_ram_diff = end_ram - start_ram

            report = (
                f"\n"
                f"==========================================================\n"
                f" 🚀 PERFORMANCE REPORT [{func.__name__}] \n"
                f"==========================================================\n"
                f"   ⏱️  Time Elapsed:  {elapsed:.2f} ms\n"
                f"   🧠  CPU Usage:     Avg: {avg_cpu:.1f}%  | Peak: {max_cpu:.1f}%\n"
                f"   💾  RAM Usage:     Avg: {avg_ram:.2f} MB  | Peak: {max_ram:.2f} MB\n"
                f"   📉  RAM Delta:     Start: {start_ram:.2f} MB -> "
                f"End: {end_ram:.2f} MB (Net: {net_ram_diff:+.2f} MB)\n"
                f"{stage_report}\n"
                f"   📊  Resolution:    {len(samples)} data points collected.\n"
                f"=========================================================="
            )
            log.info(report)
        else:
            log.warning(
                f"⚠️ PERFORMANCE [{func.__name__}]: {elapsed:.2f}ms | "
                f"(Task executed too fast, no background samples captured)"
                f"{f' | Stage Timings: ' + ' | '.join([f'{name}={duration:.2f}s' for name, duration in stages]) if stages else ''}"
            )

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not PROFILER:
                return await func(*args, **kwargs)

            tid = PROFILER.start_monitoring()
            stage_token = _performance_stages.set([])
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                samples = PROFILER.stop_monitoring(tid)
                stages = _performance_stages.get() or []
                _performance_stages.reset(stage_token)
                _log_report(elapsed, samples, stages)

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        if not PROFILER:
            return func(*args, **kwargs)

        tid = PROFILER.start_monitoring()
        stage_token = _performance_stages.set([])
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            samples = PROFILER.stop_monitoring(tid)
            stages = _performance_stages.get() or []
            _performance_stages.reset(stage_token)
            _log_report(elapsed, samples, stages)

    return sync_wrapper

