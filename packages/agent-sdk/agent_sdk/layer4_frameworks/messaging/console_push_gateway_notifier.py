import logging
from typing import Any

_logger = logging.getLogger(__name__)


class ConsolePushGatewayNotifier:
    async def send_notification(
        self, key: str, data: Any, *, is_final: bool = True
    ) -> None:
        _logger.info(f"[PUSH_GATEWAY] key={key} is_final={is_final} data={data}")

    async def close(self) -> None:
        pass
