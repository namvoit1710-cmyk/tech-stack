"""Soft-delete a gateway app (is_active=False).

Cascades to the app's functions: each is soft-deleted, so deleting an app
cleanly retires every function it exposed. The cascade is best-effort.

Addressable by ``name`` (the everyday handle, what the FE uses) or by ``id``
(the handle that cannot fail). Both exist because the name is a URL path
segment: before ``resource_name`` there was nothing stopping a name that no
route can match, and such a row could not be deleted at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
    ListGatewayFunctionsFilter,
)


@dataclass
class DeleteGatewayAppCommand:
    name: str = ""
    #: Alternative handle. ``id`` wins when both are set. The name is a path
    #: segment, so a row whose name predates the addressability rule cannot be
    #: reached by name at all — see ``resource_name``. ``id`` is a UUID and
    #: always survives a URL, which makes this the handle that cannot fail.
    id: str = ""
    deleted_by: str = ""


@dataclass
class DeleteGatewayAppResult:
    deleted: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DeleteGatewayAppUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        gateway_function_repo: IGatewayFunctionRepo | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = gateway_app_repo
        self.function_repo = gateway_function_repo

    async def execute(self, command: DeleteGatewayAppCommand) -> DeleteGatewayAppResult:
        by_id = bool(command.id.strip())
        if not by_id and not command.name.strip():
            return DeleteGatewayAppResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "name"},
            )
        entity = (
            self.repo.get_by_id(command.id.strip())
            if by_id
            else self.repo.get_by_name(command.name.strip())
        )
        if entity is None:
            return DeleteGatewayAppResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details=(
                    {"id": command.id.strip()} if by_id
                    else {"name": command.name.strip()}
                ),
            )
        if not entity.is_active:
            # Already retired. By name that reads as NOT_FOUND, which is right:
            # the caller looked it up in a list that hides inactive rows.
            # By id the caller is looking straight at the row in an
            # include_inactive listing, and answering "not found" about a row
            # they can see is what made the old behaviour so confusing. Report
            # the state instead, and write nothing.
            if by_id:
                return DeleteGatewayAppResult(deleted=True)
            return DeleteGatewayAppResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"name": command.name.strip()},
            )
        # Cascade: retire the app's functions.
        self._cascade_functions(entity.id, command.deleted_by)
        self.repo.soft_delete(entity.id)
        self.logger.info(
            "Gateway app soft-deleted", name=entity.name, id=entity.id,
            deleted_by=command.deleted_by,
        )
        return DeleteGatewayAppResult(deleted=True)

    def _cascade_functions(self, app_id: str, deleted_by: str) -> None:
        if self.function_repo is None:
            return
        page = self.function_repo.list_by_app(
            ListGatewayFunctionsFilter(app_id=app_id, include_inactive=False, top=10_000)
        )
        for fn in page.items:
            try:
                self.function_repo.soft_delete(fn.id)
            except Exception as exc:  # noqa: BLE001 — keep cascading
                self.logger.warning(
                    "Cascade soft-delete of function failed",
                    function_id=fn.id, error=str(exc),
                )
                continue
        if page.items:
            self.logger.info(
                "Cascaded gateway app delete to functions",
                app_id=app_id, count=len(page.items), deleted_by=deleted_by,
            )
