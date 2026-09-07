from fastapi import APIRouter, HTTPException, Request, Body, Query
from typing import Optional, List, Dict, Any
from dataclasses import asdict
from pydantic import field_validator

from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_validation_rules,
)
from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import DataValidationCommand


router = APIRouter()


class ValidateCloudFileInput(BaseInputDto):
    file_id: Optional[str] = None
    file_path: Optional[str] = None
    version_id: Optional[str] = None
    file_format: str = "csv"
    rules: Optional[List[Dict[str, Any]]] = None
    rule_set_id: Optional[str] = None
    result_mode: str = "errors_only"
    sheet_names: Optional[List[str]] = None
    header_row: Optional[int] = None

    @field_validator("header_row")
    @classmethod
    def _header_row_is_a_row_index(cls, value):
        if value is not None and value < 0:
            raise ValueError("header_row must be a 0-based row index >= 0")
        return value

    @field_validator("rules")
    @classmethod
    def _rules_are_executable(cls, value):
        # A rule the engine cannot run used to be skipped server-side, so a
        # typo'd type or param came back 200 with zero violations. Refuse it.
        try:
            return validate_validation_rules(value)
        except RuleSpecError as exc:
            raise ValueError(str(exc)) from exc


def get_usecase(request: Request):
    return request.app.state.container.get("data_validation_usecase")


import inspect
from functools import wraps

def monitored(func):
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            request = kwargs.get("request") or next((arg for arg in args if hasattr(arg, "app")), None)
            if request and hasattr(request, "app"):
                container = getattr(request.app.state, "container", {})
                monitor = container.get("performance_monitor") if isinstance(container, dict) else getattr(container, "get", lambda k: None)("performance_monitor")
                if monitor:
                    return await monitor(func)(*args, **kwargs)
            return await func(*args, **kwargs)
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            request = kwargs.get("request") or next((arg for arg in args if hasattr(arg, "app")), None)
            if request and hasattr(request, "app"):
                container = getattr(request.app.state, "container", {})
                monitor = container.get("performance_monitor") if isinstance(container, dict) else getattr(container, "get", lambda k: None)("performance_monitor")
                if monitor:
                    return monitor(func)(*args, **kwargs)
            return func(*args, **kwargs)
        return sync_wrapper


@router.post("")
@monitored
async def validate_file(request: Request, dto: ValidateCloudFileInput = Body(...)):
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")

    file_id = dto.file_id or dto.file_path
    if not file_id:
        raise HTTPException(400, "file_id is required")

    command = DataValidationCommand(
        file_id=file_id,
        version_id=dto.version_id,
        file_format=dto.file_format,
        rules=dto.rules,
        rule_set_id=dto.rule_set_id,
        result_mode=dto.result_mode,
        sheet_names=dto.sheet_names,
        header_row=dto.header_row,
    )

    import asyncio
    import json
    from fastapi.responses import StreamingResponse

    async def stream_result():
        # Start the background task
        task = asyncio.create_task(usecase.execute(command))
        
        # Immediately yield the start of a valid JSON object to bypass the ALB 22s header timeout.
        # We open a dummy string field called "_keep_alive" where we can safely pump spaces!
        yield b'{\n  "_keep_alive": "'
        
        while not task.done():
            # Pump 1024 spaces into the JSON string every 5 seconds.
            # This completely bypasses all CF and AWS ALB idle timeouts, and is 100% valid JSON!
            yield b' ' * 1024
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.TimeoutError:
                pass
                
        # The task is finished. Close the keep_alive string.
        yield b'",\n'
        
        try:
            result = task.result()
            if not result.success:
                result_dict = {"success": False, "message": result.message}
            else:
                result_dict = asdict(result)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            import traceback
            traceback.print_exc()
            result_dict = {"success": False, "message": f"Internal Validation Error: {str(exc)}"}
            
        # Serialize the actual result, strip the opening '{', and yield the rest!
        # This seamlessly merges our result into the JSON object we started.
        final_json = json.dumps(result_dict)
        yield final_json[1:].encode("utf-8")

    return StreamingResponse(stream_result(), media_type="application/json")


@router.get("/result/query")
async def query_validation_result(request: Request):
    """Query result data with OData"""
    try:
        usecase = get_usecase(request)
        # Manually extract from raw query string to support pure OData format
        import urllib.parse
        q = urllib.parse.unquote_plus(str(request.url.query))
        parts = q.split('&')
        odata_parts = [p for p in parts if p.startswith('odata=') or p.startswith('$')]

        f_part = next((p for p in parts if p.startswith('file_id=')), None)
        legacy_f_part = next((p for p in parts if p.startswith('file_name=')), None)
        version_part = next((p for p in parts if p.startswith('version_id=')), None)

        if not f_part and not legacy_f_part:
            f_param = request.query_params.get("file_id") or request.query_params.get("file_name")
            if not f_param:
                raise HTTPException(400, "file_id required")
            file_id = f_param
        else:
            raw_part = f_part or legacy_f_part
            file_id = raw_part.split('=', 1)[1]

        version_id = None
        if version_part:
            version_id = version_part.split('=', 1)[1]
        else:
            version_id = request.query_params.get("version_id")

        odata = "&".join(odata_parts).replace("odata=", "")
        return usecase.query_result_file(file_id, odata, version_id=version_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/result/download")
async def download_validation_result(
    request: Request,
    file_id: Optional[str] = Query(None),
    file_name: Optional[str] = Query(None),
    version_id: Optional[str] = Query(None),
    redirect: bool = False,
):
    """Generate download URL"""
    try:
        usecase = get_usecase(request)
        resolved_file_id = file_id or file_name
        if not resolved_file_id:
            raise HTTPException(400, "file_id required")
        url = usecase.get_download_url(resolved_file_id, version_id=version_id)
        if redirect:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=url)
        return {"file_url": url}
    except Exception as e:
        raise HTTPException(500, str(e))
