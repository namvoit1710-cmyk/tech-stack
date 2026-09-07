import pytest
from unittest.mock import patch

from app.layer4_frameworks.monitoring.performance_decorator import track_performance, record_performance_stage


@pytest.mark.unit
def test_track_performance_preserves_sync_functions():
    calls = []

    @track_performance
    def sample_sync(value: int) -> int:
        calls.append(value)
        return value + 1

    result = sample_sync(3)

    assert result == 4
    assert calls == [3]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_track_performance_preserves_async_functions():
    calls = []

    @track_performance
    async def sample_async(value: int) -> int:
        calls.append(value)
        return value + 2

    result = await sample_async(5)

    assert result == 7
    assert calls == [5]


@pytest.mark.unit
def test_track_performance_logs_stage_timings():
    class StubProfiler:
        def start_monitoring(self):
            return "tid"

        def stop_monitoring(self, tid):
            return [(0.0, 10.0, 100.0), (0.1, 20.0, 120.0)]

    with patch("app.layer4_frameworks.monitoring.performance_decorator.PROFILER", StubProfiler()), \
         patch("app.layer4_frameworks.monitoring.performance_decorator.log.info") as mock_log_info:
        @track_performance
        def sample_sync() -> str:
            record_performance_stage("download", 2.1)
            record_performance_stage("transform", 0.4)
            record_performance_stage("upload", 18.7)
            return "ok"

        result = sample_sync()

    assert result == "ok"
    logged_report = mock_log_info.call_args[0][0]
    assert "Stage Timings" in logged_report
    assert "download=2.10s" in logged_report
    assert "transform=0.40s" in logged_report
    assert "upload=18.70s" in logged_report