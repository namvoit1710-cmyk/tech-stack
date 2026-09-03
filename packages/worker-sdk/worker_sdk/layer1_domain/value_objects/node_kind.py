"""Functional-kind vocabulary for the worker-sdk's own domain (SA-1734/SA-1735).

The worker-sdk is where a worker's `kind` is BORN — every worker (this repo's
and third-party) constructs its `NodeTypeDefinition`/`WorkerRegistration` here
before anything is ever sent over the wire to the executor or control plane.
It cannot import either of those services' own `NodeKind` copies (separate
deployables, no shared Python package boundary) — `kind` is modelled as a
per-layer copy (decision Q1), mirroring the executor's and control-plane's own
`layer1_domain/value_objects/node_kind.py`. A parity test
(`test_kind_vocab_parity.py`) guards drift across the four copies
(worker-sdk / executor / control-plane / FE).

``NodeKind`` subclasses ``str`` so it is wire-compatible: the HTTP JSON
payload and the protobuf ``kind`` field both carry the plain string value
(``"read"``), never an enum repr — existing ``== "action"``-style comparisons
and JSON/proto serialisation keep working untouched. A plain string a worker
passes today (``kind="read"``) still constructs via ``NodeKind("read")``;
only an invented/invalid value (e.g. the ``"write"`` that actually shipped
before this fix) raises ``ValueError``.
"""
from enum import Enum


class NodeKind(str, Enum):
    TRIGGER = "trigger"
    ACTION = "action"
    READ = "read"
    LOGIC = "logic"
    HUMAN = "human"
    TRANSFORM = "transform"
    UTIL = "util"
