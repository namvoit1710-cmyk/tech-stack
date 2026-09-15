"""Expose generated protobuf stubs under ``workflow_proto.v1``.

Eagerly imports all ``*_pb2`` and ``*_pb2_grpc`` modules from the
generated directory so that normal import statements like
``from workflow_proto.v1 import push_gateway_pb2`` just work.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

_GENERATED_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "workflow" / "proto" / "generated" / "workflow" / "v1"
)


def _import_stub(file_stem: str):
    """Import a single generated module and bind it to this package."""
    fqn = f"workflow_proto.v1.{file_stem}"
    if fqn in sys.modules:
        return sys.modules[fqn]

    path = _GENERATED_DIR / f"{file_stem}.py"
    spec = importlib.util.spec_from_file_location(fqn, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fqn] = mod
    spec.loader.exec_module(mod)
    return mod


# Auto-import all generated stubs so ``from workflow_proto.v1 import X`` works.
if _GENERATED_DIR.is_dir():
    for _f in sorted(_GENERATED_DIR.glob("*_pb2*.py")):
        _mod = _import_stub(_f.stem)
        if _mod is not None:
            globals()[_f.stem] = _mod
