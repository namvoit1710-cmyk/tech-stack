"""``stage_record`` outbox handler — Phase 3 Sub 3.2 / 3.4.

Calls ``StageRecorder.record(**stage_kwargs)`` for HITL resume audit
rows. The real recorder accepts the kwargs shape
``run_id``/``event_type``/``from_status``/``to_status``/``node_id``/
``task_id``/``metadata`` (see
``app/layer2_application/events/stage_recorder.py``); the producer side
encodes those kwargs verbatim into ``payload.stage_kwargs`` so the
handler can rehydrate without an adapter layer.
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol

from app.layer4_frameworks.outbox.outbox_errors import (
    PermanentError,
    RetryableError,
)


class IStageRecorderLike(Protocol):
    def record(self, **kwargs: Any) -> Any: ...


class StageRecordHandler:
    def __init__(self, stage_recorder: IStageRecorderLike) -> None:
        self._stage_recorder = stage_recorder

    async def perform(self, entry: Any) -> dict[str, Any]:
        stage_kwargs = entry.payload.get("stage_kwargs") or {}
        if not isinstance(stage_kwargs, dict):
            raise PermanentError(
                "stage_record payload.stage_kwargs must be a dict",
            )
        if not stage_kwargs.get("event_type"):
            raise PermanentError(
                "stage_record stage_kwargs missing event_type",
            )
        try:
            result = self._stage_recorder.record(**stage_kwargs)
            if inspect.isawaitable(result):
                result = await result
        except TypeError as exc:
            raise PermanentError(
                f"stage_record kwargs invalid for StageRecorder.record: {exc}",
            ) from exc
        except Exception as exc:
            raise RetryableError(
                f"stage_recorder.record failed: {exc}",
            ) from exc
        record_id = ""
        if result is not None:
            record_id = str(getattr(result, "id", "") or getattr(result, "record_id", "") or "")
        return {"record_id": record_id} if record_id else {}
