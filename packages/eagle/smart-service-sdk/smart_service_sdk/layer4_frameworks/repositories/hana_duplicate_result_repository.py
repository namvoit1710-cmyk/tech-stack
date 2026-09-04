from __future__ import annotations

import asyncio
from typing import Any

from smart_service_sdk.layer2_application.repositories.duplicate_result_repository_interface import (
    IDuplicateResultRepository,
)
from smart_service_sdk.layer4_frameworks.repositories.json_repository_codec import (
    JsonRepositoryCodec,
)

_JSON_CODEC = JsonRepositoryCodec()


class HanaDuplicateResultRepository(IDuplicateResultRepository):
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    async def save_duplicate_result(self, result):
        def _save() -> Any:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO AE_RAG_CHECK_RESULTS (RESULT_ID, TENANT_ID, RECORD_ID, SCORE, CANDIDATES_JSON, DECISION_TRACE_JSON) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        result.id,
                        result.tenant_id,
                        result.record_id,
                        result.score,
                        _JSON_CODEC.dump_json(result.candidates),
                        _JSON_CODEC.dump_json(result.decision_trace),
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        return await asyncio.to_thread(_save)
