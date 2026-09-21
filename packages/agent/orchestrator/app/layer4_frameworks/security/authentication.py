"""Authentication helpers for FastAPI routes."""

from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError



bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
	credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
	if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Missing bearer token",
			headers={"WWW-Authenticate": "Bearer"},
		)

	try:
		result = jwt.decode(
			credentials.credentials,
			options={
				"verify_signature": False,
				"verify_exp": True,
				"verify_aud": False,
				"verify_iss": False,
			},
			algorithms="RS256",
		)
		return result

	except jwt.ExpiredSignatureError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Token has expired",
			headers={"WWW-Authenticate": "Bearer"},
		) from exc
	except InvalidTokenError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid bearer token",
			headers={"WWW-Authenticate": "Bearer"},
		) from exc
