"""Light unit tests for the governance import controller handlers.

These call the route coroutines directly with a stubbed use case, bypassing
FastAPI's ``Depends``/``TestClient`` machinery — so they need neither the full
SDK container nor the heavy SDK install that the sibling
``test_governance_import_controller.py`` (app-composition) requires.
"""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.layer1_domain.entities.import_data import (
    GovernanceImportAcceptance,
    GovernanceUploadAcceptance,
)
from app.layer3_adapters.controllers.restful.v1 import (
    governance_import_controller as controller,
)


class _StubUseCase:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.commands: list[object] = []

    async def execute(self, command):
        self.commands.append(command)
        if self._error is not None:
            raise self._error
        return self._result


class _FakeUploadFile:
    def __init__(self, filename: str | None, content: bytes = b"") -> None:
        self.filename = filename
        self.file = BytesIO(content)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _fake_request(container: dict) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=container))
    )


def test_dependency_resolvers_read_use_cases_from_container() -> None:
    request = _fake_request(
        {"import_data_usecase": "IDU", "upload_import_file_usecase": "UUC"}
    )

    assert controller.get_import_data_usecase(request) == "IDU"
    assert controller.get_upload_import_file_usecase(request) == "UUC"


def test_import_data_returns_output_dto_on_success() -> None:
    acceptance = GovernanceImportAcceptance(
        job_id="job-1",
        tenant_id="t1",
        status="accepted",
        file_ids=["f1"],
        accepted_at="2026-06-19T00:00:00+00:00",
        file_results=[],
    )
    use_case = _StubUseCase(result=acceptance)
    payload = controller.GovernanceImportInputDto(file_ids=["f1"], tenant_id="t1")

    dto = asyncio.run(controller.import_data(payload, use_case))

    assert dto.job_id == "job-1"
    assert dto.file_ids == ["f1"]
    assert use_case.commands[0].file_ids == ["f1"]
    assert use_case.commands[0].tenant_id == "t1"


def test_import_data_maps_value_error_to_400() -> None:
    use_case = _StubUseCase(error=ValueError("bad file ids"))
    payload = controller.GovernanceImportInputDto(file_ids=[], tenant_id=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(controller.import_data(payload, use_case))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad file ids"


def test_upload_import_file_returns_output_dto_and_closes_file() -> None:
    acceptance = GovernanceUploadAcceptance(
        result_id="gov-upload-1",
        file_name="a.csv",
        content_hash="h",
        tenant_id="t1",
        row_count=1,
        raw_headers=["name"],
        parsed_rows=[{"name": "acme"}],
        created_at=None,
    )
    use_case = _StubUseCase(result=acceptance)
    upload = _FakeUploadFile("a.csv", b"name\nacme\n")

    dto = asyncio.run(controller.upload_import_file(upload, "t1", use_case))

    assert dto.result_id == "gov-upload-1"
    assert dto.row_count == 1
    assert use_case.commands[0].file_name == "a.csv"
    assert upload.closed is True


def test_upload_import_file_maps_value_error_to_400_and_still_closes_file() -> None:
    use_case = _StubUseCase(error=ValueError("empty file"))
    upload = _FakeUploadFile("a.csv", b"")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(controller.upload_import_file(upload, None, use_case))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "empty file"
    # finally-block runs even on the error path.
    assert upload.closed is True


def test_upload_import_file_defaults_missing_filename_to_empty_string() -> None:
    # BOUNDARY: UploadFile.filename may be None -> handler coalesces to "".
    acceptance = GovernanceUploadAcceptance(
        result_id="r",
        file_name="",
        content_hash="h",
        tenant_id=None,
        row_count=0,
        raw_headers=[],
        parsed_rows=[],
        created_at=None,
    )
    use_case = _StubUseCase(result=acceptance)
    upload = _FakeUploadFile(None, b"")

    asyncio.run(controller.upload_import_file(upload, None, use_case))

    assert use_case.commands[0].file_name == ""
