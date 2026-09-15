import logging
import asyncio

import pytest
import httpx

from worker_sdk.layer4_frameworks.logger.standard_logger import StandardLogger
from worker_sdk.layer4_frameworks.providers.monitoring.prometheus_monitor import PrometheusMonitor
from worker_sdk.layer4_frameworks.providers.storage.local_storage import LocalStorage
from worker_sdk.layer4_frameworks.providers.data_io.local_input_reader import LocalInputReader
from worker_sdk.layer4_frameworks.providers.data_io.local_output_writer import LocalOutputWriter
from worker_sdk.layer4_frameworks.providers.registry.http_worker_registry import HttpWorkerRegistry
from worker_sdk.layer1_domain.value_objects.chunk_options import ChunkOptions
from worker_sdk.layer1_domain.value_objects.data_metadata import DataMetadata
from worker_sdk.layer1_domain.value_objects.input_reference import InputReference
from worker_sdk.layer1_domain.value_objects.output_reference import OutputReference
from worker_sdk.layer1_domain.value_objects.write_options import WriteOptions
from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus


# ==================== StandardLogger ====================

def test_standard_logger_info(capsys):
    logger = StandardLogger()
    logger.info("test message")
    # Just verify no exception is raised


def test_standard_logger_info_with_kwargs(capsys):
    logger = StandardLogger()
    logger.info("test message", key="value")


def test_standard_logger_error(capsys):
    logger = StandardLogger()
    logger.error("error message")


def test_standard_logger_error_with_kwargs(capsys):
    logger = StandardLogger()
    logger.error("error message", detail="fail")


def test_standard_logger_warning(capsys):
    logger = StandardLogger()
    logger.warning("warn message")


def test_standard_logger_warning_with_kwargs(capsys):
    logger = StandardLogger()
    logger.warning("warn message", code=123)


def test_standard_logger_debug(capsys):
    logger = StandardLogger()
    logger.debug("debug message")


def test_standard_logger_debug_with_kwargs(capsys):
    logger = StandardLogger()
    logger.debug("debug message", step=1)


# ==================== PrometheusMonitor ====================

def test_prometheus_monitor_track(capsys):
    monitor = PrometheusMonitor()
    monitor.track("my_metric", 42.0)
    captured = capsys.readouterr()
    assert "my_metric" in captured.out
    assert "42.0" in captured.out


def test_prometheus_monitor_track_format(capsys):
    monitor = PrometheusMonitor()
    monitor.track("requests_total", 1)
    captured = capsys.readouterr()
    assert "[PROMETHEUS]" in captured.out


# ==================== LocalStorage ====================

def test_local_storage_save(capsys):
    storage = LocalStorage()
    storage.save("test.txt", b"hello")
    captured = capsys.readouterr()
    assert "test.txt" in captured.out


def test_local_storage_get():
    storage = LocalStorage()
    result = storage.get("test.txt")
    assert result == b"fake_content"


# ==================== LocalInputReader ====================

@pytest.mark.asyncio
async def test_local_input_reader_fetch_all(tmp_path):
    test_file = tmp_path / "data.bin"
    test_file.write_bytes(b"hello world")
    reader = LocalInputReader()
    ref = InputReference(uri=f"file://{test_file}")
    result = await reader.fetch_all(ref)
    assert result == b"hello world"


@pytest.mark.asyncio
async def test_local_input_reader_read_chunked(tmp_path):
    test_file = tmp_path / "chunked.bin"
    test_file.write_bytes(b"abcdefghij")
    reader = LocalInputReader()
    ref = InputReference(uri=f"file://{test_file}")
    opts = ChunkOptions(chunk_size=4)
    chunks = []
    async for chunk in reader.read_chunked(ref, opts):
        chunks.append(chunk)
    assert b"".join(chunks) == b"abcdefghij"


@pytest.mark.asyncio
async def test_local_input_reader_stream(tmp_path):
    test_file = tmp_path / "stream.bin"
    test_file.write_bytes(b"stream data")
    reader = LocalInputReader()
    ref = InputReference(uri=f"file://{test_file}")
    chunks = []
    async for chunk in reader.stream(ref):
        chunks.append(chunk)
    assert b"".join(chunks) == b"stream data"


@pytest.mark.asyncio
async def test_local_input_reader_read_range(tmp_path):
    test_file = tmp_path / "range.bin"
    test_file.write_bytes(b"0123456789")
    reader = LocalInputReader()
    ref = InputReference(uri=f"file://{test_file}")
    result = await reader.read_range(ref, offset=3, length=4)
    assert result == b"3456"


@pytest.mark.asyncio
async def test_local_input_reader_get_metadata(tmp_path):
    test_file = tmp_path / "meta.json"
    test_file.write_bytes(b'{"key": "value"}')
    reader = LocalInputReader()
    ref = InputReference(uri=f"file://{test_file}")
    result = await reader.get_metadata(ref)
    assert isinstance(result, DataMetadata)
    assert result.size_bytes == 16


# ==================== LocalOutputWriter ====================

@pytest.mark.asyncio
async def test_local_output_writer_write():
    writer = LocalOutputWriter()
    ref = OutputReference(uri="file:///tmp/out")
    opts = WriteOptions()
    await writer.write(ref, b"data", opts)


@pytest.mark.asyncio
async def test_local_output_writer_create_multipart():
    writer = LocalOutputWriter()
    ref = OutputReference(uri="file:///tmp/out")
    opts = WriteOptions()
    await writer.create_multipart(ref, [b"part1", b"part2"], opts)


@pytest.mark.asyncio
async def test_local_output_writer_create_stream():
    writer = LocalOutputWriter()
    ref = OutputReference(uri="file:///tmp/out")
    opts = WriteOptions()

    async def gen():
        yield b"chunk"

    await writer.create_stream(ref, gen(), opts)


@pytest.mark.asyncio
async def test_local_output_writer_write_batch():
    writer = LocalOutputWriter()
    ref1 = OutputReference(uri="file:///tmp/out1")
    ref2 = OutputReference(uri="file:///tmp/out2")
    opts = WriteOptions()
    await writer.write_batch([ref1, ref2], [b"d1", b"d2"], opts)


# ==================== HttpWorkerRegistry ====================

def test_http_worker_registry_init():
    registry = HttpWorkerRegistry()
    assert registry.base_url is not None
    assert registry._client is not None


@pytest.mark.asyncio
async def test_http_worker_registry_register(monkeypatch):
    registry = HttpWorkerRegistry()

    async def mock_post(url, json=None):
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"worker_id": "w-123"}
        return MockResp()

    monkeypatch.setattr(registry._client, "post", mock_post)

    reg = WorkerRegistration(
        worker_type="etl", version="1.0", endpoint="http://localhost:35000"
    )
    worker_id = await registry.register(reg)
    assert worker_id == "w-123"


@pytest.mark.asyncio
async def test_http_worker_registry_heartbeat(monkeypatch):
    registry = HttpWorkerRegistry()

    calls = []
    async def mock_post(url, json=None):
        calls.append((url, json))
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"acknowledged": True, "re_register": False}
        return MockResp()

    monkeypatch.setattr(registry._client, "post", mock_post)

    result = await registry.heartbeat("w-123", WorkerStatus.HEALTHY)
    assert len(calls) == 1
    assert "w-123" in calls[0][0]
    assert calls[0][1] == {"status": "healthy"}
    assert result["acknowledged"] is True
    assert result["re_register"] is False


@pytest.mark.asyncio
async def test_http_worker_registry_deregister(monkeypatch):
    registry = HttpWorkerRegistry()

    calls = []
    async def mock_post(url, json=None):
        calls.append(url)
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
        return MockResp()

    monkeypatch.setattr(registry._client, "post", mock_post)

    await registry.deregister("w-123")
    assert len(calls) == 1
    assert "deregister" in calls[0]


@pytest.mark.asyncio
async def test_http_worker_registry_close():
    registry = HttpWorkerRegistry()
    await registry.close()
    # Verify client was closed (subsequent calls would fail)
