import time
import threading
import psutil
import uuid
from collections import defaultdict


class HighAccuracyProfiler:
    """High accuracy performance profiler using time.perf_counter and psutil.
    Tracks precise Memory (RSS) and multi-core normalized CPU utilization.
    """
    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_init'):
            self.proc = psutil.Process()
            # Initialize CPU percent (first call always returns 0.0)
            self.proc.cpu_percent(interval=None)
            self.num_cores = psutil.cpu_count(logical=True)

            self._tasks = {}
            self._results = defaultdict(list)
            self._lock = threading.Lock()
            self._run = True

            # Start background monitoring thread
            self._monitor_thread = threading.Thread(target=self._loop, daemon=True)
            self._monitor_thread.start()
            self._init = True

    def _loop(self):
        # 10ms resolution for highly accurate profiling (much better than 50ms)
        while self._run:
            with self._lock:
                if self._tasks:
                    t = time.perf_counter()

                    # Normalize CPU usage across all cores (0 - 100% total system utilization)
                    # Polars is highly multi-threaded, so raw CPU percent can be > 100%
                    c = self.proc.cpu_percent(interval=None) / self.num_cores

                    # Extract Memory (Resident Set Size - actual physical RAM used)
                    mem_info = self.proc.memory_info()
                    m_rss = mem_info.rss / (1024 * 1024)  # Convert bytes to MB

                    for tid in self._tasks:
                        self._results[tid].append((t, c, m_rss))

            # Very short sleep to catch sudden memory/cpu spikes during Polars operations
            time.sleep(0.01)

    def start_monitoring(self) -> str:
        tid = str(uuid.uuid4())
        with self._lock:
            self._tasks[tid] = True
        return tid

    def stop_monitoring(self, tid: str) -> list:
        with self._lock:
            self._tasks.pop(tid, None)
            return self._results.pop(tid, [])


from app.layer4_frameworks.config.app_config import settings

try:
    if settings.PERFORMANCE_PROFILING_ENABLED:
        PROFILER = HighAccuracyProfiler()
    else:
        PROFILER = None
except Exception as e:
    print(f"Warning: Failed to initialize HighAccuracyProfiler: {e}")
    PROFILER = None

