import importlib.util
import os

# Loaded directly from its file path (rather than `from litellm.proxy import
# ui_static_sync`) so this test doesn't need the full `litellm`/`litellm.proxy`
# dependency graph (fastapi, boto3, mcp, ...) installed - `ui_static_sync` itself
# only needs the stdlib.
_spec = importlib.util.spec_from_file_location(
    "ui_static_sync", os.path.join(os.path.dirname(__file__), "ui_static_sync.py")
)
ui_static_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_static_sync)


def _make_ui_build(root: str, chunk_names: list) -> None:
    static_dir = os.path.join(root, "_next", "static", "chunks")
    os.makedirs(static_dir, exist_ok=True)
    for name in chunk_names:
        with open(os.path.join(static_dir, name), "w") as f:
            f.write("// stub chunk\n")


def test_next_static_chunk_names_lists_relative_paths(tmp_path):
    packaged = tmp_path / "packaged"
    _make_ui_build(str(packaged), ["a.js", "b.js"])

    names = ui_static_sync.next_static_chunk_names(str(packaged))

    assert names == {os.path.join("chunks", "a.js"), os.path.join("chunks", "b.js")}


def test_next_static_chunk_names_missing_dir_returns_empty_set(tmp_path):
    assert ui_static_sync.next_static_chunk_names(str(tmp_path / "nope")) == set()


def test_runtime_ui_is_stale_when_chunks_differ(tmp_path):
    packaged = tmp_path / "packaged"
    runtime = tmp_path / "runtime"
    _make_ui_build(str(packaged), ["new-teams-chunk.js", "shared.js"])
    _make_ui_build(str(runtime), ["old-teams-chunk.js", "shared.js"])

    assert ui_static_sync.runtime_ui_is_stale(str(packaged), str(runtime)) is True


def test_runtime_ui_is_not_stale_when_chunks_match(tmp_path):
    packaged = tmp_path / "packaged"
    runtime = tmp_path / "runtime"
    _make_ui_build(str(packaged), ["a.js", "b.js"])
    _make_ui_build(str(runtime), ["a.js", "b.js"])

    assert ui_static_sync.runtime_ui_is_stale(str(packaged), str(runtime)) is False


def test_runtime_ui_is_not_stale_when_packaged_build_is_empty(tmp_path):
    # Nothing to compare against - don't force an unwanted refresh.
    packaged = tmp_path / "packaged"
    runtime = tmp_path / "runtime"
    _make_ui_build(str(runtime), ["a.js"])

    assert ui_static_sync.runtime_ui_is_stale(str(packaged), str(runtime)) is False


def test_refresh_runtime_ui_replaces_stale_content(tmp_path):
    packaged = tmp_path / "packaged"
    runtime = tmp_path / "runtime"
    _make_ui_build(str(packaged), ["new-teams-chunk.js"])
    _make_ui_build(str(runtime), ["old-teams-chunk.js"])
    with open(os.path.join(str(runtime), "stale_marker.txt"), "w") as f:
        f.write("stale")

    success, error = ui_static_sync.refresh_runtime_ui(str(packaged), str(runtime))

    assert success is True
    assert error == ""
    assert not os.path.exists(os.path.join(str(runtime), "stale_marker.txt"))
    assert os.path.exists(os.path.join(str(runtime), "_next", "static", "chunks", "new-teams-chunk.js"))
    assert ui_static_sync.runtime_ui_is_stale(str(packaged), str(runtime)) is False


def test_refresh_runtime_ui_reports_failure_for_unwritable_target(tmp_path, monkeypatch):
    packaged = tmp_path / "packaged"
    runtime = tmp_path / "runtime"
    _make_ui_build(str(packaged), ["a.js"])

    def _boom(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(ui_static_sync.shutil, "copytree", _boom)

    success, error = ui_static_sync.refresh_runtime_ui(str(packaged), str(runtime))

    assert success is False
    assert "read-only filesystem" in error
