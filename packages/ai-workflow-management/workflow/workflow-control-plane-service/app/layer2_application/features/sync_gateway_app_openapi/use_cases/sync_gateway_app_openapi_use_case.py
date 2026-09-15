"""Sync a gateway app's functions from its OpenAPI spec (the producer path).

Fetches ``app.spec_url`` (skips when the body hash is unchanged), parses it into
function signatures (domain ``parse_openapi``), and reconciles this app's functions
to match — creating new ones, updating changed signatures, soft-deleting operations
that disappeared, and reactivating ones that came back. Orchestration only: parsing
lives in the domain and the function invariants are reused from the create/update/
delete function use cases (self-assembled from the same repos).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import (
    materialize_auth_header,
    parse_openapi,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.create_gateway_function.use_cases.create_gateway_function_use_case import (
    CreateGatewayFunctionCommand,
    CreateGatewayFunctionUseCase,
)
from app.layer2_application.features.delete_gateway_function.use_cases.delete_gateway_function_use_case import (
    DeleteGatewayFunctionCommand,
    DeleteGatewayFunctionUseCase,
)
from app.layer2_application.features.update_gateway_function.use_cases.update_gateway_function_use_case import (
    UpdateGatewayFunctionCommand,
    UpdateGatewayFunctionUseCase,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    CASMismatch,
    IGatewayAppRepo,
    ListGatewayAppsFilter,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
    ListGatewayFunctionsFilter,
)

_SYNC_ACTOR = "openapi-sync"
# ponytail: one page covers any realistic spec; specs with >100k ops are not a v1 concern.
_ALL = 100_000

SpecFetcher = Callable[[str, dict[str, str], float], Awaitable[bytes]]


async def _default_fetch(url: str, headers: dict[str, str], timeout_s: float) -> bytes:
    import httpx

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp.content


@dataclass
class SyncGatewayAppOpenapiCommand:
    app_name: str = ""
    force: bool = False  # bypass the unchanged-hash short-circuit


@dataclass
class SyncGatewayAppOpenapiResult:
    app: str = ""
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    failed: int = 0        # ops the spec defines but that could not be created/updated
    skipped: bool = False  # spec body unchanged (hash match) — nothing parsed/written
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class SyncGatewayAppOpenapiUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        gateway_function_repo: IGatewayFunctionRepo,
        credential_cipher: Any = None,
        spec_fetcher: SpecFetcher | None = None,
        spec_fetch_timeout_s: float = 10.0,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.app_repo = gateway_app_repo
        self.fn_repo = gateway_function_repo
        self.cipher = credential_cipher
        self._fetch = spec_fetcher or _default_fetch
        self._timeout_s = spec_fetch_timeout_s
        # Reuse the function CRUD invariants — self-assembled from the same repos
        # (the auto-DI container only injects repos, not other use cases).
        self._create_fn = CreateGatewayFunctionUseCase(logger, gateway_app_repo, gateway_function_repo)
        self._update_fn = UpdateGatewayFunctionUseCase(logger, gateway_app_repo, gateway_function_repo)
        self._delete_fn = DeleteGatewayFunctionUseCase(logger, gateway_app_repo, gateway_function_repo)

    async def execute(
        self, command: SyncGatewayAppOpenapiCommand
    ) -> SyncGatewayAppOpenapiResult:
        app = self.app_repo.get_by_name(command.app_name.strip())
        if app is None or not app.is_active:
            return SyncGatewayAppOpenapiResult(
                app=command.app_name.strip(),
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
            )
        if not (app.spec_url or "").strip():
            return SyncGatewayAppOpenapiResult(
                app=app.name,
                error="app has no spec_url configured",
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "spec_url"},
            )

        try:
            body = await self._fetch(app.spec_url.strip(), self._auth_headers(app), self._timeout_s)
        except Exception as exc:  # network / status / transport — all non-fatal
            self.logger.warning(
                "OpenAPI spec fetch failed", app=app.name, url=app.spec_url, detail=str(exc)
            )
            return SyncGatewayAppOpenapiResult(
                app=app.name,
                error=ErrorCode.GATEWAY_SPEC_FETCH_FAILED.default_message,
                error_code=ErrorCode.GATEWAY_SPEC_FETCH_FAILED.code,
                error_details={"detail": str(exc)},
            )

        new_hash = hashlib.sha256(body).hexdigest()
        if new_hash == app.spec_hash and not command.force:
            return SyncGatewayAppOpenapiResult(app=app.name, skipped=True)

        try:
            spec = json.loads(body)
            sigs = parse_openapi(spec, kind=app.kind)
        except (ValueError, TypeError) as exc:
            return SyncGatewayAppOpenapiResult(
                app=app.name,
                error=ErrorCode.GATEWAY_SPEC_INVALID.default_message,
                error_code=ErrorCode.GATEWAY_SPEC_INVALID.code,
                error_details={"detail": str(exc)},
            )

        # Snapshot existing functions once (the empty-spec guard + reconcile share it).
        existing = {
            f.name: f
            for f in self.fn_repo.list_by_app(
                ListGatewayFunctionsFilter(app_id=app.id, include_inactive=True, top=_ALL)
            ).items
        }
        # Safety guard: a transient upstream (deploy blip, auth wall, wrong URL serving
        # an empty-but-valid doc) can parse to ZERO operations. Refuse to soft-delete an
        # app's whole function set on that — and DON'T record the hash, so it self-heals
        # on the next sync once the spec returns. A genuinely empty new app (no existing
        # active functions) falls through and is a no-op.
        # ``force`` is the operator's explicit "yes, wipe to zero" escape hatch.
        active_existing = sum(1 for f in existing.values() if f.is_active)
        if not sigs and active_existing and not command.force:
            self.logger.warning(
                "OpenAPI sync: spec parsed to 0 operations; refusing to delete active functions",
                app=app.name, active=active_existing,
            )
            return SyncGatewayAppOpenapiResult(
                app=app.name,
                error=(
                    f"spec parsed to 0 operations but {active_existing} active "
                    "function(s) exist; refusing to delete"
                ),
                error_code=ErrorCode.GATEWAY_SPEC_INVALID.code,
                error_details={"active_functions": active_existing},
            )

        result = await self._reconcile(app, sigs, existing)

        # Record the sync (CAS — a concurrent poller on another pod just loses the race).
        try:
            expected = app.version
            app.mark_synced(new_hash, updated_by=_SYNC_ACTOR)
            self.app_repo.save(app, expected_version=expected)
        except CASMismatch:
            self.logger.info("OpenAPI sync: app changed concurrently, skipping hash write", app=app.name)
        return result

    async def execute_all(self) -> dict[str, int]:
        """Sync every active app that has a spec_url. Used by the background poller."""
        page = self.app_repo.list_paged(ListGatewayAppsFilter(include_inactive=False, top=_ALL))
        totals = {"apps": 0, "created": 0, "updated": 0, "deleted": 0, "skipped": 0, "failed": 0}
        for app in page.items:
            if not (app.spec_url or "").strip():
                continue
            totals["apps"] += 1
            try:
                r = await self.execute(SyncGatewayAppOpenapiCommand(app_name=app.name))
            except Exception:
                self.logger.exception("OpenAPI sync failed", app=app.name)
                totals["failed"] += 1
                continue
            if r.error:
                totals["failed"] += 1
            elif r.skipped:
                totals["skipped"] += 1
            else:
                totals["created"] += r.created
                totals["updated"] += r.updated
                totals["deleted"] += r.deleted
                totals["failed"] += r.failed
        if totals["apps"]:
            self.logger.info("OpenAPI sync sweep complete", **totals)
        return totals

    # ── internals ────────────────────────────────────────────────────
    async def _reconcile(
        self, app: Any, sigs: list, existing: dict
    ) -> SyncGatewayAppOpenapiResult:
        # ``existing`` matches ALL functions (incl. inactive) — the (app_id, name) unique
        # index spans soft-deleted rows, so a re-added op must reactivate, not re-insert.
        res = SyncGatewayAppOpenapiResult(app=app.name)
        spec_names: set[str] = set()
        for sig in sigs:
            spec_names.add(sig.name)
            fn = existing.get(sig.name)
            if fn is None:
                if await self._create(app, sig):
                    res.created += 1
                else:
                    res.failed += 1
            elif not fn.is_active:
                # Restore a previously-removed operation in place, then apply its signature.
                if await self._reactivate(app, fn, sig):
                    res.created += 1
                else:
                    res.failed += 1
            elif self._changed(fn, sig):
                if await self._update(app, fn, sig):
                    res.updated += 1
                else:
                    res.failed += 1
            else:
                res.unchanged += 1
        # Operations no longer in the spec → soft-delete (only the active ones).
        for name, fn in existing.items():
            if name not in spec_names and fn.is_active:
                r = await self._delete_fn.execute(
                    DeleteGatewayFunctionCommand(app_name=app.name, name=name, deleted_by=_SYNC_ACTOR)
                )
                if r.deleted:
                    res.deleted += 1
        return res

    async def _create(self, app: Any, sig: Any) -> bool:
        r = await self._create_fn.execute(CreateGatewayFunctionCommand(
            app_name=app.name, name=sig.name, method=sig.method,
            path_template=sig.path_template, input_params=sig.input_params,
            output_fields=sig.output_fields, description=sig.description,
            created_by=_SYNC_ACTOR,
        ))
        if r.error:
            self.logger.warning("OpenAPI sync: create failed", app=app.name, fn=sig.name, err=r.error)
        return not r.error

    async def _update(self, app: Any, fn: Any, sig: Any) -> bool:
        r = await self._update_fn.execute(UpdateGatewayFunctionCommand(
            app_name=app.name, name=sig.name, expected_version=fn.version,
            updated_by=_SYNC_ACTOR, new_method=sig.method,
            new_path_template=sig.path_template, new_input_params=sig.input_params,
            new_output_fields=sig.output_fields, new_description=sig.description,
        ))
        if r.error:
            self.logger.warning("OpenAPI sync: update failed", app=app.name, fn=sig.name, err=r.error)
        return not r.error

    async def _reactivate(self, app: Any, fn: Any, sig: Any) -> bool:
        try:
            expected = fn.version
            fn.activate(updated_by=_SYNC_ACTOR)
            self.fn_repo.save(fn, expected_version=expected)
        except CASMismatch as exc:
            self.logger.warning("OpenAPI sync: reactivate conflict", app=app.name, fn=sig.name, detail=str(exc))
            return False
        # Now it is active — apply the (possibly changed) signature via the update UC.
        await self._update(app, fn, sig)
        return True

    @staticmethod
    def _changed(fn: Any, sig: Any) -> bool:
        return (
            fn.method.value != sig.method
            or (fn.path_template.value if fn.path_template else "") != sig.path_template
            or [p.to_dict() for p in fn.input_params] != sig.input_params
            or [o.to_dict() for o in fn.output_fields] != sig.output_fields
            or (fn.description or "") != (sig.description or "")
        )

    def _auth_headers(self, app: Any) -> dict[str, str]:
        if app.auth_type is AuthType.NONE or app.auth_config_encrypted is None or self.cipher is None:
            return {}
        try:
            plaintext = self.cipher.decrypt(app.auth_config_encrypted)
            header = materialize_auth_header(app.auth_type, plaintext)
        except Exception as exc:
            self.logger.warning("OpenAPI sync: auth header unavailable", app=app.name, detail=str(exc))
            return {}
        return {header.name: header.value} if header else {}
