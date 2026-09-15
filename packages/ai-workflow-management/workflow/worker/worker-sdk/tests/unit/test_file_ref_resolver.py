import pytest
from unittest.mock import MagicMock, patch

from worker_sdk.layer1_domain.value_objects.file_reference import is_file_ref, FILE_REF_MARKER
from worker_sdk.layer1_domain.exceptions import FileRefResolutionError
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    HttpFileRefResolver,
    _unwrap,
)


# ---------------------------------------------------------------------------
# is_file_ref
# ---------------------------------------------------------------------------

def test_is_file_ref_true():
    assert is_file_ref({"__file_ref": True, "file_id": "abc"}) is True


def test_is_file_ref_false_missing_marker():
    assert is_file_ref({"file_id": "abc"}) is False


def test_is_file_ref_false_marker_not_true():
    assert is_file_ref({"__file_ref": False, "file_id": "abc"}) is False


def test_is_file_ref_false_non_dict():
    assert is_file_ref("string") is False
    assert is_file_ref(42) is False
    assert is_file_ref(None) is False
    assert is_file_ref([1, 2]) is False


# ---------------------------------------------------------------------------
# _unwrap helper
# ---------------------------------------------------------------------------

def test_unwrap_with_envelope():
    assert _unwrap({"data": {"file_id": "x"}}) == {"file_id": "x"}


def test_unwrap_without_envelope():
    assert _unwrap([{"a": 1}]) == [{"a": 1}]


# ---------------------------------------------------------------------------
# HttpFileRefResolver
# ---------------------------------------------------------------------------

def _make_resolver():
    return HttpFileRefResolver("http://file-service:8000")


def _mock_response(data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


def test_resolve_inputs_no_file_refs():
    resolver = _make_resolver()
    inputs = {"url": "https://example.com", "method": "GET"}
    result = resolver.resolve_inputs(inputs)
    assert result == inputs


def test_resolve_inputs_does_not_mutate_original():
    resolver = _make_resolver()
    original = {"nested": {"key": "value"}}
    result = resolver.resolve_inputs(original)
    assert result == original
    # Mutating result should not affect original
    result["nested"]["key"] = "changed"
    assert original["nested"]["key"] == "value"


def test_resolve_inputs_top_level_file_ref():
    resolver = _make_resolver()
    file_ref = {"__file_ref": True, "file_id": "f-123", "row_count": 2}
    odata_response = {
        "data": {
            "file_id": "f-123",
            "total_matches": 2,
            "data": [{"name": "Alice"}, {"name": "Bob"}],
        }
    }

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    result = resolver.resolve_inputs({"source_data": file_ref, "mode": "strict"})

    assert result["mode"] == "strict"
    assert result["source_data"] == [{"name": "Alice"}, {"name": "Bob"}]
    # A short first page (2 < page size) ends the paged fetch after one call.
    resolver._client.get.assert_called_once_with(
        "http://file-service:8000/api/v1/odata/f-123/data",
        params={"$top": "10000", "$skip": "0"},
    )


def test_fetch_file_data_pages_until_short_page():
    # SA-1905: a large collection is fetched in bounded pages ($top/$skip),
    # accumulating until a short page signals the end.
    from worker_sdk.layer4_frameworks.providers.file_service import (
        http_file_ref_resolver as mod,
    )
    resolver = _make_resolver()
    full = [{"i": i} for i in range(mod._FETCH_PAGE_SIZE)]   # one full page
    tail = [{"i": mod._FETCH_PAGE_SIZE}]                      # short second page
    resolver._client = MagicMock()
    resolver._client.get.side_effect = [
        _mock_response({"data": {"data": full}}),
        _mock_response({"data": {"data": tail}}),
    ]

    rows = resolver._fetch_file_data("f-big")

    assert rows == full + tail
    assert resolver._client.get.call_count == 2
    # second call skips past the first page
    assert resolver._client.get.call_args_list[1].kwargs["params"]["$skip"] == str(
        mod._FETCH_PAGE_SIZE
    )


def _resp(data, content_len=0):
    resp = _mock_response(data)
    resp.content = b"x" * content_len
    return resp


class TestInputSizeGuard:
    """SA-1943 — cap the cumulative bytes a file-backed INPUT collection loads
    into (small) worker RAM; raise instead of OOMing."""

    def test_over_byte_cap_raises_on_first_page(self):
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=100)
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp({"data": {"data": [{"i": 1}]}}, content_len=500)
        with pytest.raises(FileRefResolutionError, match="OOM guard|worker input cap"):
            resolver._fetch_file_data("f-big")

    def test_cumulative_over_two_pages_raises(self):
        from worker_sdk.layer4_frameworks.providers.file_service import (
            http_file_ref_resolver as mod,
        )
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=100)
        full = [{"i": i} for i in range(mod._FETCH_PAGE_SIZE)]   # full page → keep paging
        resolver._client = MagicMock()
        resolver._client.get.side_effect = [
            _resp({"data": {"data": full}}, content_len=60),        # page 1: 60 ≤ 100
            _resp({"data": {"data": [{"i": 999}]}}, content_len=60),  # page 2: 120 > 100 → raise
        ]
        with pytest.raises(FileRefResolutionError, match="OOM guard|worker input cap"):
            resolver._fetch_file_data("f-big")

    def test_under_cap_materializes(self):
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=10_000_000)
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp(
            {"data": {"data": [{"i": 1}, {"i": 2}]}}, content_len=50,
        )
        assert resolver._fetch_file_data("f-small") == [{"i": 1}, {"i": 2}]

    def test_cap_disabled_allows_large(self):
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=0)
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp(
            {"data": {"data": [{"i": 1}]}}, content_len=10_000_000,
        )
        assert resolver._fetch_file_data("f") == [{"i": 1}]

    def test_boundary_content_equals_cap_ok(self):
        """Strict ``>``: cumulative bytes EXACTLY at the cap must NOT raise."""
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=100)
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp({"data": {"data": [{"i": 1}]}}, content_len=100)
        assert resolver._fetch_file_data("f") == [{"i": 1}]

    def test_verbatim_body_over_cap_raises(self):
        """A huge single JSON verbatim (non-list) body is capped BEFORE the
        non-list return path / json parse, not passed through."""
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=100)
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp({"huge": "blob"}, content_len=500)
        with pytest.raises(FileRefResolutionError, match="OOM guard|worker input cap"):
            resolver._fetch_file_data("f")

    def test_negative_cap_clamps_to_disabled(self):
        """A misconfigured negative cap disables the guard (does NOT reject
        every input via ``fetched > -1``)."""
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=-1)
        assert resolver._max_input_bytes == 0
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp(
            {"data": {"data": [{"i": 1}]}}, content_len=10_000_000,
        )
        assert resolver._fetch_file_data("f") == [{"i": 1}]

    def test_production_default_is_on_and_caps(self):
        """The wired-in production default is ON (>0) and actually caps — pins
        the constructor-off (0) vs settings-on divergence."""
        from worker_sdk.layer4_frameworks.config.app_config import settings
        assert settings.RESOLVE_MAX_INPUT_BYTES > 0
        resolver = HttpFileRefResolver(
            "http://fs:8000", max_input_bytes=settings.RESOLVE_MAX_INPUT_BYTES,
        )
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp(
            {"data": {"data": [{"i": 1}]}},
            content_len=settings.RESOLVE_MAX_INPUT_BYTES + 1,
        )
        with pytest.raises(FileRefResolutionError, match="OOM guard|worker input cap"):
            resolver._fetch_file_data("f")

    def test_row_cap_raises_loudly_not_silent_truncate(self, monkeypatch):
        from worker_sdk.layer4_frameworks.providers.file_service import (
            http_file_ref_resolver as mod,
        )
        # Shrink the row cap so one full page trips it (byte cap off).
        monkeypatch.setattr(mod, "_FETCH_MAX_ROWS", mod._FETCH_PAGE_SIZE)
        resolver = HttpFileRefResolver("http://fs:8000", max_input_bytes=0)
        full = [{"i": i} for i in range(mod._FETCH_PAGE_SIZE)]
        resolver._client = MagicMock()
        resolver._client.get.return_value = _resp({"data": {"data": full}}, content_len=1)
        with pytest.raises(FileRefResolutionError, match="row"):
            resolver._fetch_file_data("f")


def test_resolve_inputs_nested_file_ref():
    resolver = _make_resolver()
    file_ref = {"__file_ref": True, "file_id": "f-456"}
    odata_response = {"data": {"data": [{"x": 1}]}}

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    inputs = {"outer": {"inner": file_ref}}
    result = resolver.resolve_inputs(inputs)

    assert result["outer"]["inner"] == [{"x": 1}]


def test_resolve_inputs_file_ref_in_list():
    resolver = _make_resolver()
    file_ref = {"__file_ref": True, "file_id": "f-789"}
    odata_response = {"data": {"data": [{"v": "ok"}]}}

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    inputs = {"items": [file_ref, "plain_value"]}
    result = resolver.resolve_inputs(inputs)

    assert result["items"][0] == [{"v": "ok"}]
    assert result["items"][1] == "plain_value"


def test_resolve_inputs_multiple_file_refs():
    resolver = _make_resolver()

    call_count = 0

    def mock_get(url, params=None):
        nonlocal call_count
        call_count += 1
        return _mock_response({
            "data": {"data": [{"row": call_count}]}
        })

    resolver._client = MagicMock()
    resolver._client.get.side_effect = mock_get

    inputs = {
        "a": {"__file_ref": True, "file_id": "f-1"},
        "b": {"__file_ref": True, "file_id": "f-2"},
    }
    result = resolver.resolve_inputs(inputs)

    assert result["a"] == [{"row": 1}]
    assert result["b"] == [{"row": 2}]
    assert resolver._client.get.call_count == 2


def test_resolve_inputs_unwrapped_odata_response():
    """Handle responses that are already unwrapped (no outer envelope)."""
    resolver = _make_resolver()
    file_ref = {"__file_ref": True, "file_id": "f-plain"}
    # Response has no outer "data" envelope, just the OData body directly
    odata_body = {"file_id": "f-plain", "total_matches": 1, "data": [{"k": "v"}]}

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_body)

    result = resolver.resolve_inputs({"ref": file_ref})
    assert result["ref"] == [{"k": "v"}]


def test_resolve_inputs_http_error_raises():
    resolver = _make_resolver()
    file_ref = {"__file_ref": True, "file_id": "f-404"}

    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=mock_resp
    )

    resolver._client = MagicMock()
    resolver._client.get.return_value = mock_resp

    with pytest.raises(FileRefResolutionError, match="404"):
        resolver.resolve_inputs({"data": file_ref})


def test_resolve_inputs_connection_error_raises():
    resolver = _make_resolver()
    file_ref = {"__file_ref": True, "file_id": "f-err"}

    resolver._client = MagicMock()
    resolver._client.get.side_effect = ConnectionError("refused")

    with pytest.raises(FileRefResolutionError, match="refused"):
        resolver.resolve_inputs({"data": file_ref})


def test_resolver_close():
    resolver = _make_resolver()
    resolver._client = MagicMock()
    resolver.close()
    resolver._client.close.assert_called_once()


# ---------------------------------------------------------------------------
# is_file_id_input
# ---------------------------------------------------------------------------

from worker_sdk.layer1_domain.value_objects.file_reference import is_file_id_input


def test_is_file_id_input_true():
    assert is_file_id_input({"file_id": "f-1", "data_mode": "csv_single"}) is True
    assert is_file_id_input({"file_id": "f-2", "data_mode": "csv_multi"}) is True


def test_is_file_id_input_false():
    assert is_file_id_input({"file_id": "f-1"}) is False  # no data_mode
    assert is_file_id_input({"data_mode": "csv_single"}) is False  # no file_id
    assert is_file_id_input({"file_id": "f-1", "data_mode": "json"}) is False
    assert is_file_id_input("string") is False
    assert is_file_id_input(None) is False


# ---------------------------------------------------------------------------
# Top-level file_id input resolution (csv_single)
# ---------------------------------------------------------------------------

def test_resolve_file_id_input_csv_single():
    resolver = _make_resolver()
    odata_response = {
        "data": {
            "data": [
                {"method": "GET", "url": "https://api.example.com", "_row_index": "0"}
            ]
        }
    }

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    inputs = {"file_id": "f-csv-1", "data_mode": "csv_single"}
    result = resolver.resolve_inputs(inputs)

    assert result["method"] == "GET"
    assert result["url"] == "https://api.example.com"
    assert "_row_index" not in result
    assert "file_id" not in result
    assert "data_mode" not in result


def test_resolve_file_id_input_csv_single_with_locked_defaults():
    """Extra keys (locked_defaults) are merged into the resolved inputs."""
    resolver = _make_resolver()
    odata_response = {
        "data": {"data": [{"method": "POST", "url": "https://example.com", "_row_index": "0"}]}
    }

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    inputs = {
        "file_id": "f-csv-2",
        "data_mode": "csv_single",
        "timeout_seconds": 60,  # locked default
    }
    result = resolver.resolve_inputs(inputs)

    assert result["method"] == "POST"
    assert result["url"] == "https://example.com"
    assert result["timeout_seconds"] == 60
    assert "file_id" not in result


def test_resolve_file_id_input_csv_single_empty_rows():
    resolver = _make_resolver()
    odata_response = {"data": {"data": []}}

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    inputs = {"file_id": "f-empty", "data_mode": "csv_single"}
    result = resolver.resolve_inputs(inputs)

    assert result == {}


def test_resolve_file_id_input_csv_multi():
    resolver = _make_resolver()
    odata_response = {
        "data": {
            "data": [
                {"name": "Alice", "_row_index": "0"},
                {"name": "Bob", "_row_index": "1"},
            ]
        }
    }

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    inputs = {"file_id": "f-multi", "data_mode": "csv_multi"}
    result = resolver.resolve_inputs(inputs)

    assert result["data"] == [{"name": "Alice"}, {"name": "Bob"}]


def test_resolve_file_id_input_not_triggered_for_plain_inputs():
    """Plain inputs with no file_id/data_mode pass through unchanged."""
    resolver = _make_resolver()
    resolver._client = MagicMock()

    inputs = {"method": "GET", "url": "https://example.com"}
    result = resolver.resolve_inputs(inputs)

    assert result == inputs
    resolver._client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Wire-thin contract: NodeDispatched.input now arrives without ``extracted``.
# The control-plane drops the in-process scalar cache before publishing so
# event payloads stay under broker per-message limits. The resolver MUST
# behave identically against thin (``extracted={}``) and fat
# (``extracted={...}``) file_refs — it has always materialised via
# ``file_id`` and never read ``extracted``, but this regression test pins
# that contract so a future "optimise: read from extracted when present"
# refactor can't silently break the wire-thin path.
# ---------------------------------------------------------------------------

def test_resolve_inputs_wire_thin_file_ref_uses_file_id():
    """A wire-thin file_ref (extracted={}) resolves via file_id download."""
    resolver = _make_resolver()
    thin_ref = {
        "__file_ref": True,
        "file_id": "f-thin",
        "content_type": "text/csv",
        "row_count": 1,
        "columns": ["url", "method"],
        "version": 1,
        "etag": "sha-abc",
        "artifacts": [],
        "extracted": {},  # ← stripped on the wire
    }
    odata_response = {"data": {"data": [{"url": "https://api/x", "method": "GET"}]}}

    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    result = resolver.resolve_inputs({"source": thin_ref})

    assert result["source"] == [{"url": "https://api/x", "method": "GET"}]
    resolver._client.get.assert_called_once_with(
        "http://file-service:8000/api/v1/odata/f-thin/data",
        params={"$top": "10000", "$skip": "0"},
    )


def test_resolve_inputs_thin_and_fat_produce_identical_results():
    """Resolver behaviour must not depend on whether ``extracted`` is
    populated — both paths materialise via file_id.
    """
    resolver = _make_resolver()
    odata_response = {"data": {"data": [{"x": 1, "y": 2}]}}
    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(odata_response)

    thin_ref = {
        "__file_ref": True, "file_id": "f-1",
        "columns": ["x", "y"], "extracted": {},
    }
    fat_ref = {
        "__file_ref": True, "file_id": "f-1",
        "columns": ["x", "y"],
        "extracted": {
            "x": {"value": 1},
            "y": {"value": 2},
        },
    }

    thin_result = resolver.resolve_inputs({"ref": thin_ref})
    fat_result = resolver.resolve_inputs({"ref": fat_ref})

    assert thin_result == fat_result
    assert thin_result["ref"] == [{"x": 1, "y": 2}]
    assert resolver._client.get.call_count == 2  # one per resolve_inputs call


def test_resolve_inputs_thin_file_ref_missing_extracted_key():
    """A wire-thin file_ref may omit ``extracted`` entirely (not even an
    empty dict). The resolver must still work — it only needs ``file_id``.
    """
    resolver = _make_resolver()
    # No ``extracted`` field at all.
    minimal_ref = {
        "__file_ref": True,
        "file_id": "f-minimal",
        "columns": ["a"],
    }
    resolver._client = MagicMock()
    resolver._client.get.return_value = _mock_response(
        {"data": {"data": [{"a": "ok"}]}}
    )

    result = resolver.resolve_inputs({"ref": minimal_ref})
    assert result["ref"] == [{"a": "ok"}]
