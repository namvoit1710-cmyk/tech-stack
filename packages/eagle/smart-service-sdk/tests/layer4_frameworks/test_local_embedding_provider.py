import asyncio
import importlib.util
import types
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "smart_service_sdk"
    / "layer4_frameworks"
    / "embeddings"
    / "local_embedding_provider.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location(
    "test_local_embedding_provider_module",
    MODULE_PATH,
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
provider_module = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(provider_module)
LocalEmbeddingProvider = provider_module.LocalEmbeddingProvider


def test_local_embedding_provider_starts_loading_model_in_background_from_init(
    monkeypatch,
) -> None:
    calls: list[str] = []
    submitted: list[tuple[object, tuple[object, ...]]] = []

    async def _fake_to_thread(func, *args, **kwargs):
        submitted.append((func, args, kwargs))
        return func(*args, **kwargs)

    class _FakeFuture:
        def __init__(self, value):
            self._value = value

        def result(self, timeout=None):
            del timeout
            return self._value

        def done(self):
            return True

        def cancel(self):
            return False

        def cancelled(self):
            return False

    class _FakeModel:
        def encode(self, texts, *, normalize_embeddings):
            assert normalize_embeddings is True
            return [[float(len(text))] for text in texts]

    def _fake_sentence_transformer(model_name: str):
        calls.append(model_name)
        return _FakeModel()

    class _FakeExecutor:
        def __init__(self, *, max_workers: int):
            assert max_workers == 1
            self.shutdown_calls: list[bool] = []

        def submit(self, func, *args):
            submitted.append((func, args))
            return _FakeFuture(func(*args))

        def shutdown(self, *, wait: bool):
            self.shutdown_calls.append(wait)

    async def _fake_wrap_future(future):
        return future.result()

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _fake_sentence_transformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(provider_module.asyncio, "create_task", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(provider_module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(provider_module.asyncio, "wrap_future", _fake_wrap_future)

    provider = LocalEmbeddingProvider(
        model_name="background-model",
        query_instruction="query:",
    )

    assert calls == ["background-model"]
    assert len(submitted) == 1

    vectors = asyncio.run(provider.embed_texts(["alpha", "beta"]))

    assert vectors == [[5.0], [4.0]]
    assert calls == ["background-model"]


def test_local_embedding_provider_caches_loaded_model_between_calls(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class _FakeFuture:
        def __init__(self, value):
            self._value = value

        def result(self, timeout=None):
            del timeout
            return self._value

        def done(self):
            return True

        def cancel(self):
            return False

        def cancelled(self):
            return False

    class _FakeModel:
        def encode(self, texts, *, normalize_embeddings):
            assert normalize_embeddings is True
            return [[float(len(text))] for text in texts]

    def _fake_sentence_transformer(model_name: str):
        calls.append(model_name)
        return _FakeModel()

    class _FakeExecutor:
        def __init__(self, *, max_workers: int):
            assert max_workers == 1

        def submit(self, func, *args):
            return _FakeFuture(func(*args))

        def shutdown(self, *, wait: bool):
            assert wait is False

    async def _fake_wrap_future(future):
        return future.result()

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _fake_sentence_transformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(provider_module.asyncio, "create_task", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(provider_module.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(provider_module.asyncio, "wrap_future", _fake_wrap_future)

    provider = LocalEmbeddingProvider(
        model_name="cached-model",
        query_instruction="query:",
    )

    query_vector = asyncio.run(provider.embed_query("hello"))
    text_vectors = asyncio.run(provider.embed_texts(["world"]))

    assert query_vector == [12.0]
    assert text_vectors == [[5.0]]
    assert calls == ["cached-model"]
