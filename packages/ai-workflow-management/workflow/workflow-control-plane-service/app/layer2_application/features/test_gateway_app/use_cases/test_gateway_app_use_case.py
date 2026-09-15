"""Operator probe: verify an app's base_url is reachable with its credential.

Internal apps: HTTP GET base_url with the injected (decrypted) auth header via the
injected ``http_probe``. External apps: no network probe (no single base) — report
whether the allowlist is configured. Never returns the credential itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.interfaces.credential_cipher_interface import (
    DecryptionFailed,
    ICredentialCipher,
)
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import AppKind, materialize_auth_header
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.http_probe_interface import IHttpProbe
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)


@dataclass
class TestGatewayAppCommand:
    name: str = ""
    timeout_s: float = 5.0


@dataclass
class TestGatewayAppResult:
    reachable: bool = False
    status_code: int = 0
    latency_ms: float = 0.0
    upstream_error: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class TestGatewayAppUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        credential_cipher: ICredentialCipher,
        http_probe: IHttpProbe,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = gateway_app_repo
        self.cipher = credential_cipher
        self.http_probe = http_probe

    async def execute(self, command: TestGatewayAppCommand) -> TestGatewayAppResult:
        app = self.repo.get_by_name(command.name.strip())
        if app is None or not app.is_active:
            return TestGatewayAppResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"name": command.name.strip()},
            )

        if app.kind is AppKind.EXTERNAL:
            # No single base to probe; report allowlist configuration instead.
            configured = bool(app.allowed_hosts and not app.allowed_hosts.is_empty())
            return TestGatewayAppResult(
                reachable=configured,
                upstream_error=(
                    "" if configured else "external app has no allowed_hosts configured"
                ),
            )

        headers: dict[str, str] = {}
        if app.auth_type is not AuthType.NONE and app.auth_config_encrypted is not None:
            try:
                plaintext = self.cipher.decrypt(app.auth_config_encrypted)
                header = materialize_auth_header(app.auth_type, plaintext)
            except DecryptionFailed:
                return TestGatewayAppResult(
                    error=ErrorCode.INTERNAL_ERROR.default_message,
                    error_code=ErrorCode.INTERNAL_ERROR.code,
                )
            except NotImplementedError as exc:
                return TestGatewayAppResult(
                    error=str(exc),
                    error_code=ErrorCode.SERVICE_NOT_CONFIGURED.code,
                )
            if header is not None:
                headers[header.name] = header.value

        probe = await self.http_probe(app.base_url, headers, command.timeout_s)
        return TestGatewayAppResult(
            reachable=probe.reachable,
            status_code=probe.status_code,
            latency_ms=probe.latency_ms,
            upstream_error=probe.error or "",
        )
