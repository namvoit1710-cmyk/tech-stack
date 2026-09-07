import requests
import tempfile
import os
import time
from typing import Any, Dict, Optional, List
import concurrent.futures


from app.layer2_application.interfaces.storage_interface import (
    IFileStorageProvider,
    StoredFileInfo,
    FileVersionConflictError,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.providers.formats.registry import (
    SourceReaderRegistry,
    default_source_reader_registry,
)

_BACKOFF_SLEEP = time.sleep


class NodeFileStorageProvider(IFileStorageProvider):
    def __init__(
        self,
        node_url: str,
        logger: ILogger,
        source_readers: Optional[SourceReaderRegistry] = None,
    ):
        self.node_url = self._normalize_base_url(node_url)
        self.logger = logger
        # Format read/write is delegated to injectable per-format strategies.
        # Defaults to the shipped formats; pass a custom registry to add sources
        # (e.g. a SAP export reader) without touching this provider.
        self._source_readers = source_readers or default_source_reader_registry()

    def generate_presigned_url(
        self,
        file_id: str,
        operation: str = "download",
        version_id: Optional[str] = None,
    ) -> str:
        if file_id.startswith("http"):
            return file_id
        if self._is_local_file(file_id):
            return file_id
        if operation != "download":
            return f"{self.node_url}/upload"
        if version_id:
            return f"{self.node_url}/files/versions/{version_id}/download"
        return f"{self.node_url}/download/{file_id}"

    def download_and_read(
        self,
        file_path: str,
        file_format: str,
        version_id: Optional[str] = None,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any:
        normalized_format = file_format.lstrip(".").lower()

        if self._is_local_file(file_path):
            self.logger.info(f"Reading local file from: {file_path}")
            return self._read_dataframe(
                file_path,
                normalized_format,
                sheet_names=sheet_names,
                merge_sheets=merge_sheets,
                add_sheet_name_column=add_sheet_name_column,
                header_row=header_row,
            )

        temp_path = self.download_to_temp_file(
            file_path,
            normalized_format,
            version_id=version_id,
        )

        try:
            return self._read_dataframe(
                temp_path,
                normalized_format,
                sheet_names=sheet_names,
                merge_sheets=merge_sheets,
                add_sheet_name_column=add_sheet_name_column,
                header_row=header_row,
            )
        except Exception as e:
            self.logger.error(f"Download/Read error: {e}")
            raise e
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def download_to_temp_file(
        self,
        file_path: str,
        file_format: str,
        version_id: Optional[str] = None,
    ) -> str:
        normalized_format = file_format.lstrip(".").lower()

        if self._is_local_file(file_path):
            return file_path

        url = self._resolve_download_url(file_path, version_id=version_id)
        self.logger.info(f"Downloading file from: {url}")

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=f".{normalized_format}"
        ) as tf:
            temp_path = tf.name

        max_retries = max(1, settings.FILE_SERVICE_MAX_RETRIES)
        download_timeout = (
            settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
            settings.FILE_SERVICE_READ_TIMEOUT_SEC,
        )
        download_started_at = time.perf_counter()
        for attempt in range(max_retries):
            try:
                with requests.get(url, stream=True, headers={"Connection": "close"}, timeout=download_timeout) as r:
                    r.raise_for_status()
                    content_length = int(r.headers.get("content-length", 0))

                if content_length > 16 * 1024 * 1024:
                    import concurrent.futures
                    import threading
                    # NB: no `import os` here. `os` is imported at module level,
                    # and a local import inside this branch makes the name local
                    # to the whole function — so the error handler below, which
                    # runs only for files that never enter this branch, raised
                    # UnboundLocalError and masked every real download failure.

                    cpu_cores = os.cpu_count() or 4

                    try:
                        import psutil
                        # Check live CPU usage (blocks for 0.1s to get accurate reading)
                        cpu_usage = psutil.cpu_percent(interval=0.1)
                        free_cpu_ratio = max(0.1, 1.0 - (cpu_usage / 100.0))
                        
                        # Calculate effective "free" cores based on current load
                        effective_free_cores = max(1.0, cpu_cores * free_cpu_ratio)
                        max_allowed_workers = min(32, int(effective_free_cores * 4))

                        # Safe to use up to 15% of available RAM for the download buffer
                        available_mem_mb = psutil.virtual_memory().available / (1024 * 1024)
                        safe_buffer_mb = available_mem_mb * 0.15
                    except ImportError:
                        effective_free_cores = cpu_cores
                        max_allowed_workers = min(32, cpu_cores * 4)
                        safe_buffer_mb = 256.0  # Fallback to a safe 256MB total buffer

                    # Distribute the safe memory buffer across the workers
                    # S3 is most efficient with 8MB to 32MB chunks
                    dynamic_chunk_mb = max(8, min(32, int(safe_buffer_mb / max_allowed_workers)))
                    chunk_size = dynamic_chunk_mb * 1024 * 1024
                    
                    max_workers = min(max_allowed_workers, max(4, content_length // chunk_size))
                    
                    self.logger.info(
                        f"Dynamic Parallel Download: Allocated {max_workers} streams "
                        f"with {dynamic_chunk_mb}MB chunks "
                        f"(Based on {effective_free_cores:.1f} Free CPUs out of {cpu_cores} total, and {safe_buffer_mb:.0f}MB safe buffer)"
                    )
                    
                    with open(temp_path, "wb") as f:
                        f.truncate(content_length)
                        
                    ranges = [(start, min(start + chunk_size - 1, content_length - 1)) 
                              for start in range(0, content_length, chunk_size)]
                              
                    file_lock = threading.Lock()
                    
                    def _download_range(r_tuple):
                        start, end = r_tuple
                        headers = {"Range": f"bytes={start}-{end}", "Connection": "close"}
                        with requests.get(url, headers=headers, stream=True, timeout=download_timeout) as resp:
                            resp.raise_for_status()
                            chunk_data = resp.content
                            with file_lock:
                                with open(temp_path, "r+b") as out_f:
                                    out_f.seek(start)
                                    out_f.write(chunk_data)

                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                        list(executor.map(_download_range, ranges))

                else:
                    with requests.get(
                        url,
                        stream=True,
                        timeout=download_timeout,
                        headers={"Connection": "close", "User-Agent": "DataFactory/1.0"},
                    ) as r:
                        r.raise_for_status()
                        import shutil
                        import functools
                        r.raw.read = functools.partial(r.raw.read, decode_content=True)
                        with open(temp_path, "wb") as f:
                            shutil.copyfileobj(r.raw, f, length=8 * 1024 * 1024)
                return temp_path
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                elapsed = time.perf_counter() - download_started_at

                if settings.FILE_SERVICE_FAIL_FAST_ON_TIMEOUT and isinstance(
                    e,
                    (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout),
                ):
                    if isinstance(e, requests.exceptions.ReadTimeout):
                        raise TimeoutError(
                            "FILE_SERVICE_DOWNLOAD_TIMEOUT: "
                            f"file_id='{file_path}', format='{normalized_format}', "
                            f"elapsed={elapsed:.2f}s, timeout={download_timeout}, retries_skipped=true"
                        )
                    raise TimeoutError(
                        "FILE_SERVICE_DOWNLOAD_CONNECT_TIMEOUT: "
                        f"file_id='{file_path}', format='{normalized_format}', "
                        f"elapsed={elapsed:.2f}s, timeout={download_timeout}, retries_skipped=true"
                    )

                if attempt == max_retries - 1:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    if isinstance(e, requests.exceptions.ReadTimeout):
                        raise TimeoutError(
                            "FILE_SERVICE_DOWNLOAD_TIMEOUT: "
                            f"file_id='{file_path}', format='{normalized_format}', "
                            f"elapsed={elapsed:.2f}s, timeout={download_timeout}"
                        )
                    if isinstance(e, requests.exceptions.ConnectTimeout):
                        raise TimeoutError(
                            "FILE_SERVICE_DOWNLOAD_CONNECT_TIMEOUT: "
                            f"file_id='{file_path}', format='{normalized_format}', "
                            f"elapsed={elapsed:.2f}s, timeout={download_timeout}"
                        )
                    if isinstance(e, ConnectionAbortedError) or "Connection aborted" in str(e):
                        raise Exception(
                            "Failed to download file: connection aborted by server after retries. "
                            "Please check if the file exists and the file service is accessible."
                        )
                    raise Exception(
                        "FILE_SERVICE_DOWNLOAD_ERROR: "
                        f"file_id='{file_path}', format='{normalized_format}', "
                        f"elapsed={elapsed:.2f}s, error={e}"
                    ) from e
                backoff_seconds = settings.FILE_SERVICE_RETRY_BACKOFF_SEC * (attempt + 1)
                self.logger.info(
                    f"Retrying file download in {backoff_seconds:.1f}s "
                    f"(attempt {attempt + 2}/{max_retries})"
                )
                _BACKOFF_SLEEP(backoff_seconds)

        return temp_path

    def _resolve_download_url(
        self,
        file_id: str,
        version_id: Optional[str] = None,
    ) -> str:
        direct_url = self.generate_presigned_url(file_id, "download", version_id=version_id)
        
        if not settings.FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE:
            return direct_url

        presigned_url = self._get_download_presigned_url(file_id, version_id)
        if presigned_url:
            self.logger.info(
                f"Using file service presigned download URL (file_id={file_id}, version_id={version_id})"
            )
            return presigned_url

        self.logger.warning(
            f"Failed to resolve presigned download URL. Falling back to download API (file_id={file_id})"
        )
        return direct_url

    def _get_download_presigned_url(self, file_id: str, version_id: Optional[str] = None) -> Optional[str]:
        try:
            payload = {
                "file_id": file_id,
                "operation": "download",
                "expires_in": settings.FILE_SERVICE_PRESIGNED_DOWNLOAD_EXPIRES_SEC,
            }
            if version_id:
                payload["version_id"] = version_id
                
            response = requests.post(
                f"{self.node_url}/presigned-url",
                json=payload,
                timeout=(
                    settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                    settings.FILE_SERVICE_READ_TIMEOUT_SEC,
                ),
                headers={"Connection": "close", "User-Agent": "DataFactory/1.0"},
            )
            response.raise_for_status()
            payload = response.json() or {}
            url = payload.get("url")
            if isinstance(url, str) and url.strip():
                return url
            self.logger.warning(
                f"Presigned download response missing 'url' for file '{file_id}'."
            )
            return None
        except Exception as exc:
            self.logger.warning(
                f"Failed to fetch presigned download URL for file '{file_id}': {exc}"
            )
            return None

    def _get_file_size_bytes(self, file_id: str) -> Optional[int]:
        metadata = self._get_file_metadata(file_id)
        if not metadata:
            return None

        for key in ("size", "file_size", "size_bytes", "content_length"):
            value = metadata.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue

        return None

    def upload_file(
        self,
        local_path: str,
        file_format: str = "csv",
        file_id: Optional[str] = None,
        version_id: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> StoredFileInfo:
        content_type = "text/csv" if file_format == "csv" else "application/octet-stream"
        upload_started_at = time.perf_counter()
        try:
            self.logger.info("Uploading result to file service...")
            resolved_filename = filename or self._resolve_upload_filename(local_path, file_id, file_format)

            if getattr(settings, "FILE_SERVICE_USE_PRESIGNED_UPLOAD_FOR_LARGE", False):
                file_size_bytes = os.path.getsize(local_path)
                threshold_bytes = max(1, getattr(settings, "FILE_SERVICE_PRESIGNED_UPLOAD_MIN_SIZE_MB", 25)) * 1024 * 1024
                if file_size_bytes >= threshold_bytes:
                    return self._upload_file_multipart_presigned(
                        local_path,
                        file_id,
                        version_id,
                        resolved_filename,
                        content_type,
                        file_size_bytes
                    )

            upload_params = self._build_upload_params(file_id, version_id)
            upload_mode = (settings.FILE_SERVICE_UPLOAD_MODE or "raw").strip().lower()

            def _log_upload_attempt(mode: str, params: dict):
                self.logger.info(
                    "File service upload request prepared: "
                    f"mode={mode}, content_type={content_type}, "
                    f"param_keys={sorted(params.keys())}, file_id={file_id}, filename={resolved_filename}"
                )

            if upload_mode not in {"raw", "multipart", "auto"}:
                raise ValueError(
                    "FILE_SERVICE_UPLOAD_MODE must be one of: raw, multipart, auto"
                )

            if upload_mode in {"multipart", "auto"}:
                _log_upload_attempt("multipart", upload_params)
                with open(local_path, "rb") as f:
                    r = requests.post(
                        f"{self.node_url}/upload",
                        params=upload_params,
                        files={"file": (resolved_filename, f, content_type)},
                        headers={
                            "Connection": "close",
                            "User-Agent": "DataFactory/1.0",
                        },
                        timeout=(
                            settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                            settings.FILE_SERVICE_UPLOAD_TIMEOUT_SEC,
                        ),
                    )
                    self.logger.info(
                        f"File service upload mode=multipart status={r.status_code}"
                    )

                if upload_mode == "auto" and r.status_code == 422:
                    self.logger.warning(
                        "Multipart upload returned 422. "
                        "Retrying with raw-body + filename query for compatibility."
                    )
                    raw_params = dict(upload_params)
                    raw_params["filename"] = resolved_filename
                    _log_upload_attempt("raw", raw_params)
                    with open(local_path, "rb") as raw_stream:
                        r = requests.post(
                            f"{self.node_url}/upload",
                            params=raw_params,
                            data=raw_stream,
                            headers={
                                "Connection": "close",
                                "User-Agent": "DataFactory/1.0",
                                "Content-Type": content_type,
                            },
                            timeout=(
                                settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                                settings.FILE_SERVICE_UPLOAD_TIMEOUT_SEC,
                            ),
                        )
                        self.logger.info(
                            f"File service upload mode=raw status={r.status_code}"
                        )
            else:
                raw_params = dict(upload_params)
                raw_params["filename"] = resolved_filename
                _log_upload_attempt("raw", raw_params)
                with open(local_path, "rb") as raw_stream:
                    r = requests.post(
                        f"{self.node_url}/upload",
                        params=raw_params,
                        data=raw_stream,
                        headers={
                            "Connection": "close",
                            "User-Agent": "DataFactory/1.0",
                            "Content-Type": content_type,
                        },
                        timeout=(
                            settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                            settings.FILE_SERVICE_UPLOAD_TIMEOUT_SEC,
                        ),
                    )
                    self.logger.info(
                        f"File service upload mode=raw status={r.status_code}"
                    )

            if r.status_code in {400, 409} and file_id and version_id:
                current_version_id = self._get_current_version_id(file_id)
                if current_version_id and current_version_id != version_id:
                    raise FileVersionConflictError(
                        file_id=file_id,
                        requested_version_id=version_id,
                        current_version_id=current_version_id,
                    )

            r.raise_for_status()
            response_data = r.json()
            uploaded_file_id = response_data["file_id"]
            uploaded_version_id = response_data.get("version_id", "")
            download_url = self.generate_presigned_url(
                uploaded_file_id,
                "download",
                version_id=uploaded_version_id or None,
            )
            self.logger.info(
                f"Upload successful via file service. Result URL: {download_url}"
            )
            return StoredFileInfo(
                file_id=uploaded_file_id,
                version_id=uploaded_version_id,
                download_url=download_url,
                filename=response_data.get("filename", resolved_filename),
                status=response_data.get("status", ""),
            )
        except FileVersionConflictError:
            raise
        except requests.exceptions.ReadTimeout as e:
            elapsed = time.perf_counter() - upload_started_at
            self.logger.error(f"Upload timed out after {elapsed:.2f}s: {e}")
            raise TimeoutError(
                "FILE_SERVICE_UPLOAD_TIMEOUT: "
                f"file_id='{file_id}', format='{file_format}', elapsed={elapsed:.2f}s"
            ) from e
        except requests.exceptions.ConnectTimeout as e:
            elapsed = time.perf_counter() - upload_started_at
            self.logger.error(f"Upload connection timed out after {elapsed:.2f}s: {e}")
            raise TimeoutError(
                "FILE_SERVICE_UPLOAD_CONNECT_TIMEOUT: "
                f"file_id='{file_id}', format='{file_format}', elapsed={elapsed:.2f}s"
            ) from e
        except requests.exceptions.HTTPError as e:
            response = locals().get("r")
            elapsed = time.perf_counter() - upload_started_at
            if response is not None and response.status_code == 422:
                body = ""
                try:
                    body = response.text[:1000]
                except Exception:
                    body = ""
                raise Exception(
                    "FILE_SERVICE_UPLOAD_CONTRACT_MISMATCH: "
                    f"file_id='{file_id}', format='{file_format}', elapsed={elapsed:.2f}s, "
                    f"status=422, body={body}"
                ) from e
            raise
        except Exception as e:
            response = locals().get("r")
            response_details = ""
            if response is not None:
                try:
                    response_details = f" | status={response.status_code} body={response.text[:1000]}"
                except Exception:
                    response_details = ""
            elapsed = time.perf_counter() - upload_started_at
            self.logger.error(
                f"Upload failed against file service after {elapsed:.2f}s: {e}{response_details}"
            )
            raise

    def _upload_file_multipart_presigned(
        self,
        local_path: str,
        file_id: Optional[str],
        version_id: Optional[str],
        resolved_filename: str,
        content_type: str,
        file_size_bytes: int,
    ) -> StoredFileInfo:
        self.logger.info(
            f"Initiating Presigned Multipart Upload for huge file "
            f"(size={file_size_bytes} bytes, filename={resolved_filename})"
        )

        init_payload = {
            "filename": resolved_filename,
            "file_size": file_size_bytes,
            "content_type": content_type
        }
        if file_id:
            init_payload["file_id"] = file_id
        if version_id:
            init_payload["previous_version_id"] = version_id

        r_init = requests.post(
            f"{self.node_url}/presigned-multipart-upload/initiate",
            json=init_payload,
            headers={"Connection": "close", "User-Agent": "DataFactory/1.0"},
            timeout=(
                settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
            ),
        )
        r_init.raise_for_status()
        init_data = r_init.json()

        upload_id = init_data["upload_id"]
        parts = init_data.get("parts", [])

        part_size = init_data.get("part_size")
        if not part_size and parts:
            part_size = (file_size_bytes // len(parts)) + 1
        completed_parts = []
        futures = []
        
        with requests.Session() as session:
            # Ensure the session has a large connection pool
            adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
            session.mount('https://', adapter)
            session.mount('http://', adapter)

            def _upload_part(part, chunk):
                part_number = part.get("part_number")
                url = part["url"]
                self.logger.debug(f"Uploading part {part_number}/{len(parts)} ({len(chunk)} bytes)...")
                # We explicitly do NOT use a strict timeout here because parallel S3 uploads 
                # can take a while on slower connections since they share bandwidth.
                r_chunk = session.put(
                    url,
                    data=chunk,
                    timeout=None
                )
                r_chunk.raise_for_status()
                etag = r_chunk.headers.get("ETag") or r_chunk.headers.get("etag")
                if not etag:
                    self.logger.warning(f"No ETag returned for part {part_number}!")
                    etag = '""'
                return {"part_number": part_number, "etag": etag}

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(parts))) as executor:
                with open(local_path, "rb") as f:
                    for i, part in enumerate(parts):
                        part["part_number"] = part.get("part_number", i + 1)
                        chunk = f.read(part_size)
                        if not chunk:
                            break
                        futures.append(executor.submit(_upload_part, part, chunk))
                
                for future in concurrent.futures.as_completed(futures):
                    completed_parts.append(future.result())

        self.logger.info("Completing Presigned Multipart Upload...")
        complete_payload = {
            "upload_id": upload_id,
            "is_preprocessed": True,
            "parts": completed_parts
        }

        r_complete = requests.post(
            f"{self.node_url}/presigned-multipart-upload/complete",
            json=complete_payload,
            headers={"Connection": "close", "User-Agent": "DataFactory/1.0"},
            timeout=(
                settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                settings.FILE_SERVICE_UPLOAD_TIMEOUT_SEC,
            ),
        )
        r_complete.raise_for_status()
        response_data = r_complete.json()

        uploaded_file_id = response_data.get("file_id") or file_id or ""
        uploaded_version_id = response_data.get("version_id", "")
        download_url = self.generate_presigned_url(
            uploaded_file_id,
            "download",
            version_id=uploaded_version_id or None,
        )
        self.logger.info(f"Presigned Multipart Upload successful. Result URL: {download_url}")
        return StoredFileInfo(
            file_id=uploaded_file_id,
            version_id=uploaded_version_id,
            download_url=download_url,
            filename=response_data.get("filename", resolved_filename),
            status=response_data.get("status", ""),
        )

    def upload_dataframe(
        self,
        df: Any,
        file_format: str = "csv",
        file_id: Optional[str] = None,
        version_id: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> StoredFileInfo:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_format}") as tf:
            temp_path = tf.name
        try:
            self._source_readers.for_format(file_format).write(df, temp_path)

            return self.upload_file(
                temp_path,
                file_format,
                file_id=file_id,
                version_id=version_id,
                filename=filename,
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def download_to_local(self, file_path: str, file_format: str = "csv") -> str:
        """
        Stream-download a file to a local temp path and return it WITHOUT parsing or
        deleting it. Unlike ``download_and_read`` (which loads an eager DataFrame),
        this lets callers ``pl.scan_*`` the file lazily — essential for streaming a
        very large source (e.g. a 500MB+ reference CSV) into a parquet key-set
        without loading it all into memory. Caller is responsible for cleanup.
        """
        normalized_format = file_format.lstrip(".").lower()
        if self._is_local_file(file_path):
            return file_path

        url = self.generate_presigned_url(file_path, "download")
        self.logger.info(f"Streaming file to local temp from: {url}")
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{normalized_format}") as tf:
            temp_path = tf.name

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with requests.get(
                    url,
                    stream=True,
                    timeout=120,
                    headers={"Connection": "close", "User-Agent": "DataFactory/1.0"},
                ) as r:
                    r.raise_for_status()
                    with open(temp_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                return temp_path
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"download_to_local attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise

    def _is_local_file(self, file_path: str) -> bool:
        return bool(file_path) and os.path.exists(file_path)

    def _read_dataframe(
        self,
        path: str,
        file_format: str,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
    ) -> Any:
        # Dispatch to the injected per-format reader strategy. Falls back to
        # detecting the format from the path extension when none is supplied.
        normalized_format = (file_format or "").lstrip(".").lower() or self._source_readers.detect(path)
        reader = self._source_readers.for_format(normalized_format)
        return reader.read(
            path,
            sheet_names=sheet_names,
            merge_sheets=merge_sheets,
            add_sheet_name_column=add_sheet_name_column,
            header_row=header_row,
        )

    def download_and_read_with_paths(
        self,
        file_path: str,
        file_format: str,
        version_id: Optional[str] = None,
        sheet_names: Optional[List[str]] = None,
        merge_sheets: bool = False,
        add_sheet_name_column: bool = False,
        header_row: Optional[int] = None,
        field_path_overrides: Optional[Dict[str, str]] = None,
    ) -> tuple:
        """Read the frame and, from the same download, its ``SECTION.field`` map.

        Deliberately one download. Asking for the banner separately would mean
        fetching the workbook twice per table, and a bundle run over a 23-sheet
        template already reads it once per table -- doubling that to decorate
        error messages would be a poor trade.
        """
        from app.layer4_frameworks.providers.formats.field_paths import build_field_paths

        normalized_format = file_format.lstrip(".").lower()
        local = self._is_local_file(file_path)
        temp_path = file_path if local else self.download_to_temp_file(
            file_path, normalized_format, version_id=version_id
        )
        try:
            frame = self._read_dataframe(
                temp_path,
                normalized_format,
                sheet_names=sheet_names,
                merge_sheets=merge_sheets,
                add_sheet_name_column=add_sheet_name_column,
                header_row=header_row,
            )
            sections, labels = self.read_banner_rows(
                temp_path, normalized_format, header_row or 0, sheet_names=sheet_names
            )
            paths = build_field_paths(
                labels or list(frame.columns),
                sections or None,
                overrides=field_path_overrides,
            )
            return frame, paths
        finally:
            if not local and os.path.exists(temp_path):
                os.remove(temp_path)

    def read_banner_rows(
        self,
        path: str,
        file_format: str,
        header_row: int,
        sheet_names: Optional[List[str]] = None,
    ) -> tuple:
        """Return ``(section_values, label_values)`` from a workbook's banner.

        A mass-upload template writes the section name once across a merged
        range on the row above the headers, so the labels alone cannot say which
        section a column belongs to. Reading those two rows is what lets a
        column label become the ``SECTION.field`` path the rules are written in.

        Only worth asking of a workbook, and only when the header is not the
        first row; anything else has no banner and returns empty. Never raises --
        paths are an enrichment, and a template with an unusual banner should
        degrade to bare labels rather than fail the transform.
        """
        normalized = (file_format or "").lstrip(".").lower()
        if normalized not in {"xlsx", "xls", "xlsm", "excel"} or not header_row:
            return [], []

        try:
            import polars as pl

            sheet = (sheet_names or [None])[0]
            kwargs = {"read_options": {"header_row": None, "n_rows": header_row + 1}}
            if sheet is not None:
                kwargs["sheet_name"] = sheet
            frame = pl.read_excel(path, **kwargs)
            if frame.height <= header_row:
                return [], []
            as_text = lambda row: [None if v is None else str(v) for v in row]
            return as_text(frame.row(header_row - 1)), as_text(frame.row(header_row))
        except Exception as exc:
            self.logger.warning(
                f"Could not read banner rows for field paths ({exc}). "
                "Falling back to column labels."
            )
            return [], []

    def _build_upload_params(
        self,
        file_id: Optional[str],
        version_id: Optional[str],
    ) -> dict:
        params = {}
        if file_id:
            params["file_id"] = file_id
        if version_id:
            params["previous_version_id"] = version_id
        return params

    def _resolve_upload_filename(
        self,
        local_path: str,
        file_id: Optional[str] = None,
        file_format: Optional[str] = None,
    ) -> str:
        source_filename = None
        if file_id and not self._is_local_file(file_id):
            metadata = self._get_file_metadata(file_id)
            source_filename = self._extract_source_filename(metadata)

        if source_filename:
            return self._build_edited_filename(source_filename, file_format)

        return os.path.basename(local_path)

    def _extract_source_filename(self, metadata: Optional[dict]) -> Optional[str]:
        if not metadata:
            return None

        for key in ("filename", "file_name", "name", "original_filename"):
            value = metadata.get(key)
            if value:
                return os.path.basename(str(value))

        return None

    def _build_edited_filename(
        self,
        source_filename: str,
        file_format: Optional[str] = None,
    ) -> str:
        base_name = os.path.basename(source_filename)
        stem, extension = os.path.splitext(base_name)

        if stem.endswith("_edited_by_data-factory"):
            suffix_stem = stem
        else:
            suffix_stem = f"{stem}_edited_by_data-factory"

        if not extension and file_format:
            extension = f".{file_format.lstrip('.')}"

        return f"{suffix_stem}{extension}"

    def _get_file_metadata(self, file_id: str) -> Optional[dict]:
        try:
            response = requests.get(
                f"{self.node_url}/files/{file_id}",
                timeout=(
                    settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                    settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC,
                ),
                headers={"Connection": "close", "User-Agent": "DataFactory/1.0"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            self.logger.warning(
                f"Failed to fetch metadata for file '{file_id}': {exc}"
            )
            return None

    def _get_current_version_id(self, file_id: str) -> Optional[str]:
        metadata = self._get_file_metadata(file_id)
        if not metadata:
            return None
        return metadata.get("current_version_id")

    def _normalize_base_url(self, node_url: str) -> str:
        normalized = node_url.strip().rstrip("/")
        if "/docs" in normalized:
            normalized = normalized.split("/docs", 1)[0]
        if normalized.endswith("/#"):
            normalized = normalized[:-2]
        if normalized.endswith("#"):
            normalized = normalized[:-1]
        if not normalized.endswith("/api/v1"):
            normalized = f"{normalized}/api/v1"
        return normalized.rstrip("/")
