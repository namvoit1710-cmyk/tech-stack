import datetime as dt

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.layer1_domain.enums.transport_state import TransportState
from app.layer3_adapters.controllers.restful.v1.dtos.base_input import BaseInput
from app.layer3_adapters.controllers.restful.v1.dtos.base_output import BaseOutput
from app.layer4_frameworks.security.authentication import require_bearer_token


def test_base_input_requires_subclass_implementation():
	with pytest.raises(NotImplementedError):
		BaseInput().to_dataclass()


def test_base_output_requires_subclass_implementation():
	with pytest.raises(NotImplementedError):
		BaseOutput.from_dataclass(object())


def test_transport_state_values_are_stable():
	assert TransportState.IDLE.value == "idle"
	assert TransportState.PROCESSING.value == "processing"
	assert TransportState.WAITING.value == "waiting"
	assert TransportState.RECEIVED.value == "received"
	assert TransportState.RESPONDING.value == "responding"
	assert TransportState.COMPLETED.value == "completed"
	assert TransportState.ERROR.value == "error"


def test_require_bearer_token_rejects_missing_credentials():
	with pytest.raises(HTTPException, match="Missing bearer token") as error:
		require_bearer_token(None)

	assert error.value.status_code == 401


def test_require_bearer_token_rejects_non_bearer_scheme():
	credentials = HTTPAuthorizationCredentials(scheme="Basic", credentials="token")

	with pytest.raises(HTTPException, match="Missing bearer token"):
		require_bearer_token(credentials)


def test_require_bearer_token_decodes_valid_token_without_verifying_signature():
	token = jwt.encode({"sub": "user-1", "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)}, "secret", algorithm="HS256")
	credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

	payload = require_bearer_token(credentials)

	assert payload["sub"] == "user-1"


def test_require_bearer_token_rejects_expired_token():
	token = jwt.encode({"sub": "user-1", "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)}, "secret", algorithm="HS256")
	credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

	with pytest.raises(HTTPException, match="Token has expired"):
		require_bearer_token(credentials)


def test_require_bearer_token_rejects_invalid_token():
	credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")

	with pytest.raises(HTTPException, match="Invalid bearer token") as error:
		require_bearer_token(credentials)

	assert error.value.status_code == 401
