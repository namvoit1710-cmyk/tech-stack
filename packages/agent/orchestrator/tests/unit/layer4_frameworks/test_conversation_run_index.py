"""SA-892 (N10): the bounded in-memory conv_id -> set[run_id] index."""

import threading

from app.layer4_frameworks.repositories.in_memory_conversation_run_index import (
    InMemoryConversationRunIndex,
)


def test_records_and_returns_run_ids_per_conversation():
    index = InMemoryConversationRunIndex()
    index.record("conv-1", "run-a")
    index.record("conv-1", "run-b")
    index.record("conv-2", "run-c")

    assert index.get_run_ids("conv-1") == {"run-a", "run-b"}
    assert index.get_run_ids("conv-2") == {"run-c"}
    assert index.get_run_ids("missing") == set()


def test_record_is_idempotent_and_ignores_blanks():
    index = InMemoryConversationRunIndex()
    index.record("conv-1", "run-a")
    index.record("conv-1", "run-a")  # duplicate
    index.record("conv-1", "")       # blank run id
    index.record("", "run-z")        # blank conv id

    assert index.get_run_ids("conv-1") == {"run-a"}
    assert index.get_run_ids("") == set()


def test_returned_set_is_a_copy():
    index = InMemoryConversationRunIndex()
    index.record("conv-1", "run-a")
    snapshot = index.get_run_ids("conv-1")
    snapshot.add("run-mutated")
    assert index.get_run_ids("conv-1") == {"run-a"}


def test_run_ids_per_conversation_are_bounded_fifo():
    index = InMemoryConversationRunIndex(max_runs_per_conversation=2)
    index.record("conv-1", "run-1")
    index.record("conv-1", "run-2")
    index.record("conv-1", "run-3")  # evicts oldest (run-1)

    assert index.get_run_ids("conv-1") == {"run-2", "run-3"}


def test_conversations_are_bounded_lru():
    index = InMemoryConversationRunIndex(max_conversations=2)
    index.record("conv-1", "run-1")
    index.record("conv-2", "run-2")
    index.get_run_ids("conv-1")       # touch conv-1 -> conv-2 now LRU
    index.record("conv-3", "run-3")   # evicts conv-2

    assert index.get_run_ids("conv-1") == {"run-1"}
    assert index.get_run_ids("conv-2") == set()
    assert index.get_run_ids("conv-3") == {"run-3"}


def test_concurrent_record_and_read_is_thread_safe():
    """SA-892 L1: hammering record() (writer) + get_run_ids() (reader) from many
    threads must not raise (e.g. 'OrderedDict mutated during iteration') and must
    leave a consistent, bounded state. Fails without the lock; passes with it."""
    index = InMemoryConversationRunIndex(max_conversations=50, max_runs_per_conversation=50)
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def writer(w: int) -> None:
        try:
            barrier.wait()
            for i in range(500):
                index.record(f"conv-{i % 20}", f"run-{w}-{i}")
        except Exception as exc:  # noqa: BLE001 - capture to fail the assertion
            errors.append(exc)

    def reader() -> None:
        try:
            barrier.wait()
            for i in range(500):
                index.get_run_ids(f"conv-{i % 20}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent access raised: {errors[:3]}"
    # caps held under concurrency
    assert len(index.get_run_ids("conv-0")) <= 50
