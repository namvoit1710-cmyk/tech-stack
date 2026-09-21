import inspect

from agent_sdk.layer4_frameworks.persistence.checkpoint_store import HanaCheckpointSaver


def test_no_duplicate_methods():
    """Each async method must be defined exactly once inside HanaCheckpointSaver."""
    source = inspect.getsource(HanaCheckpointSaver)
    assert source.count("async def alist(") == 1, "alist defined multiple times"
    assert source.count("async def aput(") == 1, "aput defined multiple times"
    assert (
        source.count("async def aput_writes(") == 1
    ), "aput_writes defined multiple times"
