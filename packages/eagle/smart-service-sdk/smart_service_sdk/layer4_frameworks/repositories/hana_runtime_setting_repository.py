from __future__ import annotations

import asyncio

from smart_service_sdk.layer2_application.repositories.runtime_setting_repository_interface import (
    IRuntimeSettingRepository,
)


class HanaRuntimeSettingRepository(IRuntimeSettingRepository):
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    async def get(self, key: str) -> str | None:
        return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._set_sync, key, value)

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        return await asyncio.to_thread(self._get_many_sync, keys)

    def _get_sync(self, key: str) -> str | None:
        values = self._get_many_sync([key])
        return values.get(key)

    def _set_sync(self, key: str, value: str) -> None:
        connection = self._connection_factory.acquire()
        cursor = connection.cursor()
        try:
            cursor.execute(
                'UPSERT AE_SERVICE_CONFIGURATIONS ("KEY", "VALUE") VALUES (?, ?) WITH PRIMARY KEY',
                (key, value),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            self._connection_factory.release(connection)

    def _get_many_sync(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        placeholders = ", ".join("?" for _ in keys)
        connection = self._connection_factory.acquire()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f'SELECT "KEY", "VALUE" FROM AE_SERVICE_CONFIGURATIONS WHERE "KEY" IN ({placeholders})',
                tuple(keys),
            )
            return {
                str(row[0]): str(row[1])
                for row in cursor.fetchall()
                if row and row[0] is not None and row[1] is not None
            }
        finally:
            cursor.close()
            self._connection_factory.release(connection)
