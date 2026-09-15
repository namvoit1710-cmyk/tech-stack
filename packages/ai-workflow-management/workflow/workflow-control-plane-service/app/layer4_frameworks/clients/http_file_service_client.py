"""HTTP adapter for the external File Service.

Matches the File Service OpenAPI spec:
  POST   /api/v1/upload?filename=...         (multipart/form-data, required ``file`` part)
  GET    /api/v1/download/{file_id}
  DELETE /api/v1/delete/{file_id}
  GET    /api/v1/presigned-url/{file_id}
  GET    /api/v1/odata/{file_id}/data

Uses a reusable ``httpx.AsyncClient`` held on the client instance so the
connection pool is kept warm across calls instead of being rebuilt per-call.
All methods are ``async def``; the caller must ``await`` them.
"""
import asyncio
import csv
import hashlib
import io
import logging
from typing import Any, Optional

import httpx

from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/v1"
# Hardening: a download feeding variable resolution must not be lost to a
# single transient blip (DNS hiccup, dropped keep-alive, 5xx). GET is
# idempotent, so retry transient failures a few times with short backoff.
# Permanent failures (4xx incl. 401/403/404) are NOT retried — they fail fast.
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_BACKOFF_BASE_S = 0.25


# The deployed file service stores the upload filename in a bounded metadata
# field and REJECTS an over-long name. The control plane's logical stems are
# already large — a node-input artifact is keyed ``{run_id}-{node_id}-input-…``
# and two UUIDs alone are 72 chars — so folding a full 64-char SHA-256 on top
# routinely pushed the name past the limit (observed live: a 171-char name the
# file service refused). Keep the disambiguating hash, but make the whole name
# bounded:
#   * a 16-hex (64-bit) content tag keeps names collision-free under one logical
#     stem — the same ``[:16]`` digest convention already used for event ids
#     (``event_id.py``) and run-artifact ids — at 48 fewer chars;
#   * if the name would STILL exceed ``_MAX_UPLOAD_FILENAME_LEN``, trim the
#     human-readable STEM only. The stem is context for a human reading logs; the
#     hash is the sole correctness property (unique-per-content), so it and the
#     extension are never shortened.
# Bounding here — the one choke point every CP CSV upload passes through — is
# portable by construction: it holds on every file-service instance whatever its
# exact limit, with no per-environment config. ``_MAX_UPLOAD_FILENAME_LEN`` is a
# conservative default (well under a 128/255 name column); lower it if a target
# file service is stricter.
_UPLOAD_FILENAME_HASH_HEX = 16
_MAX_UPLOAD_FILENAME_LEN = 100


def _unique_csv_filename(filename: str, content: bytes) -> str:
    """Fold a short content hash into a CSV upload's filename so it is
    unique-per-content AND bounded in length.

    The deployed file service's OData read endpoint
    (``GET /odata/{file_id}/data``, the read-back path GET /data previews use)
    serves rows keyed by *filename*, not ``file_id``: two stored files that
    share a filename return the *first* file's rows (reproduced live in
    isolation — same name → stale sibling's rows, persistent ≥30s; distinct
    names → correct). The control-plane uploads node artifacts under logical,
    NON-unique names (``{node_id}.csv``, ``{node_id}_{name}.csv``,
    ``{artifact_id}.csv``), so re-running a node / ``/test/full`` produces
    distinct ``file_id``s that collide on name → GET /data previews the OLD
    value. Disambiguating the stored name eliminates the collision.

    Keying on the *content* hash (not a random token) keeps uploads
    idempotent: byte-identical content → identical filename → the file service
    dedupes to one ``file_id`` (matching the ``X-Idempotency-Key`` semantics in
    ``data_store_service``). Any real edit changes the bytes → new name → new
    file, never an aliased stale read. Only CSV is rewritten; binary/user
    uploads keep their original (display) filename. The displayed
    ``$artifacts.*.filename`` is sourced from the ref's own ``name`` field, not
    the stored filename, so this is invisible to designers.

    The hash is truncated to ``_UPLOAD_FILENAME_HASH_HEX`` and the whole name is
    capped at ``_MAX_UPLOAD_FILENAME_LEN`` by trimming the STEM (never the hash
    or extension), so an over-long logical name can no longer be rejected by the
    file service. Uniqueness survives the trim: two DIFFERENT contents still
    differ in the hash even when their trimmed stems coincide (identical content
    that also coincides on stem is the same file → correct dedup).
    """
    digest = hashlib.sha256(content or b"").hexdigest()[:_UPLOAD_FILENAME_HASH_HEX]
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    suffix = f"-{digest}.{ext}" if ext else f"-{digest}"
    # Trim the STEM (context only) so ``stem + suffix`` fits the cap. The hash and
    # extension — the parts that carry correctness — are never shortened.
    budget = _MAX_UPLOAD_FILENAME_LEN - len(suffix)
    if budget < 1:
        # Cap misconfigured smaller than the hash itself: keep the unique hash
        # (+ extension) rather than ever truncating the hash.
        return f"{digest}.{ext}" if ext else digest
    return f"{stem[:budget]}{suffix}"


def _unwrap_multipart(raw: bytes, content_type: str) -> bytes:
    """Return the inner file bytes from a ``multipart/form-data`` download.

    The File Service's ``GET /download`` wraps the stored file in a
    single-part ``multipart/form-data`` envelope and (observed) omits the
    boundary from the ``Content-Type`` header, so ``response.content`` is the
    MIME envelope, not the CSV. Parsing that envelope as CSV yields garbage
    and trips a ragged-row crash in ``CsvConverter._unflatten`` downstream.

    Detect the envelope (by content-type, falling back to the ``--`` delimiter
    that opens every multipart body) and return the single part's body. The
    boundary is read from the leading delimiter line since the header omits
    it. Anything that doesn't look like multipart passes through unchanged.
    """
    looks_multipart = (
        content_type.lower().startswith("multipart/")
        or raw[:2] == b"--"
    )
    if not looks_multipart or not raw.startswith(b"--"):
        return raw
    nl = raw.find(b"\r\n")
    if nl == -1:
        return raw
    boundary = raw[:nl]  # b"--<boundary>"
    header_end = raw.find(b"\r\n\r\n", nl)  # blank line ends the part headers
    if header_end == -1:
        return raw
    body_start = header_end + 4
    closing = raw.find(b"\r\n" + boundary, body_start)
    return raw[body_start:closing] if closing != -1 else raw[body_start:]

# Default timeouts in seconds — per-request overrides still allowed via
# ``timeout=`` kwarg on individual calls.
_DEFAULT_TIMEOUT = 60.0
_SHORT_TIMEOUT = 30.0


def _truncate(text: Optional[str], limit: int = 500) -> str:
    """Clamp a response body for logging so we capture the error message
    without dumping a multi-KB HTML error page into the logs."""
    if not text:
        return ""
    return text if len(text) <= limit else f"{text[:limit]}...(+{len(text) - limit} chars)"


class HttpFileServiceClient(IFileServiceClient):
    def __init__(self, file_service_url: str) -> None:
        self._base_url = file_service_url.rstrip("/")
        # Reused across all calls to keep a warm connection pool.
        self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        # Surface the wiring at startup so operators can confirm the control
        # plane is talking to a real file service (not the in-memory mock) and
        # see immediately that no auth header is attached — uploads to an
        # XSUAA/OAuth-gated service will 401 and silently yield file_id="".
        logger.debug(
            "HttpFileServiceClient initialised base_url=%s "
            "(NO Authorization header configured)",
            self._base_url,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "text/csv",
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Upload via ``multipart/form-data`` (``file`` part). Returns
        {file_id, filename, content_type, size_bytes, status}.

        ``idempotency_key`` is forwarded as ``X-Idempotency-Key`` for
        the file service to dedupe replayed uploads (Sub 3.6 chaos
        test invariant). The server side may not honour the header
        yet — this is a forward-compatible plumbing change.
        """
        url = f"{self._base_url}{_API_PREFIX}/upload"
        # Disambiguate CSV filenames so the file service's filename-keyed OData
        # read can never return a same-named sibling's stale rows for a distinct
        # file_id (see ``_unique_csv_filename``). CSV-only: binary/user uploads
        # keep their original display name.
        if (content_type or "").startswith("text/csv"):
            filename = _unique_csv_filename(filename, file_bytes)
        # The file service expects ``multipart/form-data`` with a required
        # ``file`` part (FastAPI ``file: UploadFile = File(...)``). Sending the
        # raw bytes as the request body makes the server reject the call with
        # 422 ``{"loc":["body","file"],"msg":"Field required"}``. Use httpx's
        # ``files=`` so the boundary + ``Content-Type: multipart/form-data``
        # header are generated correctly — do NOT set Content-Type manually,
        # that would clobber the boundary. ``filename`` stays a query param
        # (optional, per the OpenAPI spec).
        # with open(filename, "wb") as f:
        #     f.write(file_bytes)
        #     f.close()
        files = {"file": (filename, file_bytes, content_type)}
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        size = len(file_bytes or b"")

        
        logger.debug(
            "file-service upload START url=%s filename=%s bytes=%d content_type=%s idem=%s",
            url, filename, size, content_type, idempotency_key or "-",
        )
        try:
            response = await self._client.post(
                url,
                files=files,
                params={"filename": filename},
                headers=headers or None,
            )
            response.raise_for_status()
            body = response.json()
            file_id = body.get("file_id", "") if isinstance(body, dict) else ""
            if not file_id:
                # 2xx but no file_id — a "success-shaped" response that still
                # leaves the node with file_id="" and no persisted data.
                logger.error(
                    "file-service upload returned 2xx but NO file_id "
                    "url=%s filename=%s status=%s body=%s",
                    url, filename, response.status_code, _truncate(response.text),
                )
            else:
                logger.debug(
                    "file-service upload OK url=%s filename=%s status=%s file_id=%s",
                    url, filename, response.status_code, file_id,
                )
            return body
        except httpx.HTTPStatusError as exc:
            # The file service answered with a 4xx/5xx — capture the status and
            # body so an auth (401/403) or server (5xx) failure is unambiguous.
            resp = exc.response
            logger.error(
                "file-service upload HTTP ERROR url=%s filename=%s status=%s "
                "reason=%s body=%s -- returning {} (node will get file_id='')",
                url, filename, resp.status_code, resp.reason_phrase,
                _truncate(resp.text),
            )
            return {}
        except Exception as exc:
            # Connection refused / DNS / TLS / timeout — never reached the service.
            logger.error(
                "file-service upload CONNECTION ERROR url=%s filename=%s "
                "error_type=%s error=%s -- returning {} (node will get file_id='')",
                url, filename, type(exc).__name__, exc,
            )
            return {}

    async def download_file(self, file_id: str) -> Optional[bytes]:
        """Download file content by ID.

        The File Service's ``GET /download`` returns the stored file wrapped
        in a single-part ``multipart/form-data`` envelope, so the raw body is
        the MIME wrapper rather than the file bytes. ``_unwrap_multipart``
        extracts the inner part; a plain (non-multipart) response passes
        through unchanged, so this is forward-compatible if the service ever
        returns the raw bytes directly.
        """
        url = f"{self._base_url}{_API_PREFIX}/download/{file_id}"
        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                return _unwrap_multipart(
                    response.content, response.headers.get("content-type", ""),
                )
            except httpx.HTTPStatusError as exc:
                resp = exc.response
                # 4xx (incl. 401/403/404) is permanent — fail fast, no retry.
                if resp.status_code < 500:
                    logger.error(
                        "file-service download HTTP ERROR file_id=%s status=%s "
                        "reason=%s body=%s",
                        file_id, resp.status_code, resp.reason_phrase,
                        _truncate(resp.text),
                    )
                    return None
                # 5xx is transient — retry.
                logger.warning(
                    "file-service download HTTP %s (transient) file_id=%s "
                    "attempt=%d/%d reason=%s",
                    resp.status_code, file_id, attempt, _DOWNLOAD_MAX_ATTEMPTS,
                    resp.reason_phrase,
                )
            except Exception as exc:
                # Connection refused / DNS / TLS / timeout — never reached the
                # service. Transient; retry.
                logger.warning(
                    "file-service download CONNECTION ERROR (transient) "
                    "file_id=%s attempt=%d/%d error_type=%s error=%s",
                    file_id, attempt, _DOWNLOAD_MAX_ATTEMPTS,
                    type(exc).__name__, exc,
                )
            if attempt < _DOWNLOAD_MAX_ATTEMPTS:
                await asyncio.sleep(_DOWNLOAD_BACKOFF_BASE_S * (2 ** (attempt - 1)))
        logger.error(
            "file-service download FAILED after %d attempts file_id=%s "
            "(returning None — caller will see empty data)",
            _DOWNLOAD_MAX_ATTEMPTS, file_id,
        )
        return None

    async def delete_file(self, file_id: str) -> dict[str, Any]:
        """Delete a file from storage. Returns {file_id, status}."""
        url = f"{self._base_url}{_API_PREFIX}/delete/{file_id}"
        try:
            response = await self._client.delete(url, timeout=_SHORT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("DELETE %s/delete/%s failed: %s", _API_PREFIX, file_id, exc)
            return {}

    async def head_file(self, file_id: str) -> Optional[dict[str, Any]]:
        """Size/metadata probe WITHOUT downloading the file.

        Used by the SA-1942 OOM guard (``DataStoreService.probe_size_bytes``) to
        decide whether a value is too large to materialize. Hits the file
        service's ``GET {prefix}/files/{id}/exists`` route — the only endpoint
        that reports ``size_bytes`` without streaming the bytes (there is NO
        ``/head`` route; an earlier version queried that non-existent path and so
        always returned None in production, silently disabling the pre-check).
        Returns the metadata dict (``{file_id, exists, size_bytes, …}``) on
        success, or ``None`` when the file is missing / size can't be determined
        (the guard then falls back to the post-download byte backstop).
        """
        url = f"{self._base_url}{_API_PREFIX}/files/{file_id}/exists"
        try:
            response = await self._client.get(url, timeout=_SHORT_TIMEOUT)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            # Unwrap the ResponseWrapperMiddleware envelope ({"data": {...}}) if present.
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                data = data["data"]
            if not isinstance(data, dict) or data.get("exists") is False:
                return None
            return data
        except Exception as exc:
            logger.error("GET %s/files/%s/exists failed: %s", _API_PREFIX, file_id, exc)
            return None

    async def get_presigned_url(self, file_id: str, operation: str = "download", expires_in: int = 3600) -> dict[str, Any]:
        """Get a presigned URL for direct download/upload. Returns {file_id, url, expires_in, operation}."""
        url = f"{self._base_url}{_API_PREFIX}/presigned-url/{file_id}"
        try:
            response = await self._client.get(
                url,
                params={"operation": operation, "expires_in": expires_in},
                timeout=_SHORT_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("GET %s/presigned-url/%s failed: %s", _API_PREFIX, file_id, exc)
            return {}

    async def query_file_data(self, file_id: str, query_params: dict[str, Any]) -> dict[str, Any]:
        """OData query over CSV file data (read-back path for GET /data previews)."""
        url = f"{self._base_url}{_API_PREFIX}/odata/{file_id}/data"
        logger.debug(
            "file-service odata READ START file_id=%s params=%s", file_id, query_params,
        )
        try:
            response = await self._client.get(url, params=query_params, timeout=_SHORT_TIMEOUT)
            response.raise_for_status()
            body = response.json()
            rows = body.get("data") if isinstance(body, dict) else None
            logger.debug(
                "file-service odata READ OK file_id=%s status=%s rows=%s total_matches=%s",
                file_id, response.status_code,
                len(rows) if isinstance(rows, list) else "?",
                (body.get("total_matches") if isinstance(body, dict) else "?"),
            )
            return body
        except httpx.HTTPStatusError as exc:
            resp = exc.response
            logger.error(
                "file-service odata READ HTTP ERROR file_id=%s status=%s reason=%s "
                "body=%s -- returning empty (GET /data preview will be blank)",
                file_id, resp.status_code, resp.reason_phrase, _truncate(resp.text),
            )
            return {"file_id": file_id, "total_matches": 0, "data": []}
        except Exception as exc:
            logger.error(
                "file-service odata READ CONNECTION ERROR file_id=%s error_type=%s "
                "error=%s -- returning empty (GET /data preview will be blank)",
                file_id, type(exc).__name__, exc,
            )
            return {"file_id": file_id, "total_matches": 0, "data": []}

    async def get_columns_via_odata(self, file_id: str) -> list[str]:
        if not file_id:
            return []
        resp = await self.query_file_data(file_id, {"$top": "1"})
        rows = resp.get("data", [])
        if not rows:
            return []
        return [k for k in rows[0].keys() if k and not k.startswith("_")]

    async def patch_file_data(self, file_id: str, patch_payload: dict[str, Any]) -> dict[str, Any]:
        """Patch CSV data: download → edit in memory → re-upload as new file.

        The external file service has no PATCH endpoint, so this is implemented
        as download + local edit + upload.
        """
        raw = await self.download_file(file_id)
        if raw is None:
            return {}

        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        columns = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

        edits = patch_payload.get("edits", [])
        new_rows = patch_payload.get("new_rows", [])
        delete_indices = set(patch_payload.get("delete_row_indices", []))

        # Apply edits on original rows, then delete
        if edits:
            for edit in edits:
                idx = edit.get("row_index", -1)
                col = edit.get("column", "")
                val = edit.get("value", "")
                if 0 <= idx < len(rows) and col in rows[idx]:
                    rows[idx][col] = val

        if delete_indices:
            rows = [r for i, r in enumerate(rows) if i not in delete_indices]

        for nr in new_rows:
            rows.append({c: nr.get(c, "") for c in columns})

        # Write back to CSV and re-upload
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")

        result = await self.upload_file(csv_bytes, f"patched-{file_id}.csv", "text/csv")
        return {
            "new_file_id": result.get("file_id", ""),
            "rows_modified": len(edits),
            "rows_added": len(new_rows),
            "rows_deleted": len(delete_indices),
        }
