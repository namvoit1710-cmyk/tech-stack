from fastapi import FastAPI
from fastapi import HTTPException
from starlette.requests import Request

from app.layer3_adapters.controllers.restful.v1.usecase_resolver import resolve_usecase


def _make_request_with_container(container):
    app = FastAPI()
    app.state.container = container
    scope = {"type": "http", "app": app, "method": "GET", "path": "/"}
    return Request(scope)


def test_resolve_usecase_returns_registered_usecase():
    usecase = object()
    request = _make_request_with_container({"_usecases": {"conversation.get_conversation": usecase}})

    assert resolve_usecase(request, "conversation.get_conversation") is usecase


def test_resolve_usecase_raises_for_missing_usecase():
    request = _make_request_with_container({})

    try:
        resolve_usecase(request, "conversation.get_conversation")
    except HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail == "Use case 'conversation.get_conversation' is not configured"
    else:
        raise AssertionError("Expected resolve_usecase to raise when a use case is missing")