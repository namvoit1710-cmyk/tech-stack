"""Subprocess wrapper around the ``req2tpl`` Rust CLI.

The CLI is the source of truth; this module adds nothing to it. It resolves the
binary, runs one argv (never a shell string), and turns the documented stdout
shapes into structured fields so a workflow can branch on them.

Exit codes, per the tool's manual: ``0`` = success / all gates green, ``1`` =
anything else with a message on stderr naming the cause and the fix. There is no
partial success.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.layer4_frameworks.config import settings


class Req2tplError(Exception):
    """A CLI or configuration failure. ``error_code`` drives the node's error_code."""

    def __init__(self, message: str, error_code: str = "REQ2TPL_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass
class CliResult:
    """One invocation. ``ok`` is exit code 0 and nothing else."""

    argv: List[str]
    exit_code: int
    stdout: str
    stderr: str
    gates: List[Dict[str, str]] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def raise_for_status(self, action: str) -> "CliResult":
        """Fail the node on a non-zero exit, carrying the CLI's own message.

        The tool's stderr names the cause and the fix (e.g. "BLOCKING - N FS
        primary key(s) have no template row"), so it is reproduced verbatim
        rather than summarised — a paraphrase would lose the fix.
        """
        if self.ok:
            return self
        detail = (self.stderr or "").strip() or (self.stdout or "").strip() or "no output"
        failed = [g["name"] for g in self.gates if g.get("status") == "FAIL"]
        gate_note = f" (failing gates: {', '.join(failed)})" if failed else ""
        raise Req2tplError(
            f"req2tpl {action} failed with exit code {self.exit_code}{gate_note}: {detail}",
            error_code="GATE_FAILED" if failed else "REQ2TPL_ERROR",
        )


# ``  [PASS] trap-bool          1260 boolean cells, all t="b"``
_GATE_RE = re.compile(r"^\s*\[(PASS|FAIL)\]\s+(\S+)\s*(.*?)\s*$")
# ``  requirement rows  : 99``  /  ``  fields    : 82``
_SUMMARY_RE = re.compile(r"^\s{2,}([A-Za-z][A-Za-z0-9 _/-]*?)\s*:\s*(\d+)\s*$")


def binary_path() -> Path:
    """Locate the req2tpl binary, or fail with the exact thing to fix."""
    configured = (settings.REQ2TPL_BIN or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.exists():
            raise Req2tplError(
                f"REQ2TPL_BIN points at a file that does not exist: {path}. "
                "Build it with `cargo build --release` and point REQ2TPL_BIN at "
                "target/release/req2tpl(.exe).",
                error_code="CONFIG_ERROR",
            )
        return path.resolve()
    found = shutil.which("req2tpl")
    if not found:
        raise Req2tplError(
            "req2tpl was not found: set REQ2TPL_BIN to the built binary, or put "
            "`req2tpl` on PATH.",
            error_code="CONFIG_ERROR",
        )
    return Path(found).resolve()


def run(args: List[str], *, cwd: Path, timeout: Optional[float] = None) -> CliResult:
    """Run one req2tpl invocation and parse what it printed.

    ``args`` excludes the program name. No shell is involved, so a path with a
    space or an ampersand is data, never syntax.
    """
    argv = [str(binary_path()), *[str(a) for a in args]]
    limit = timeout if timeout is not None else settings.REQ2TPL_TIMEOUT_SECONDS
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell, binary is operator-configured
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise Req2tplError(
            f"req2tpl timed out after {limit:.0f}s: {' '.join(argv[1:])}",
            error_code="TIMEOUT",
        ) from exc
    except OSError as exc:
        raise Req2tplError(f"could not run req2tpl: {exc}", error_code="CONFIG_ERROR") from exc

    stdout = proc.stdout or ""
    return CliResult(
        argv=argv[1:],
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=proc.stderr or "",
        gates=parse_gates(stdout),
        summary=parse_summary(stdout),
    )


def parse_gates(stdout: str) -> List[Dict[str, str]]:
    """Extract the ``[PASS]/[FAIL] <gate> <detail>`` block."""
    gates: List[Dict[str, str]] = []
    for line in stdout.splitlines():
        m = _GATE_RE.match(line)
        if m:
            gates.append({"status": m.group(1), "name": m.group(2), "detail": m.group(3)})
    return gates


def parse_summary(stdout: str) -> Dict[str, int]:
    """Extract the ``label : count`` block (requirement rows, gaps, fields, ...).

    Best-effort by design: these are human-facing lines, not a contract. A caller
    that needs a number the CLI did not print gets nothing rather than a guess.
    """
    out: Dict[str, int] = {}
    for line in stdout.splitlines():
        m = _SUMMARY_RE.match(line)
        if m:
            key = re.sub(r"[ /-]+", "_", m.group(1).strip().lower())
            out[key] = int(m.group(2))
    return out


# ``  tempID      : PIR_IMPORT_TEMPLATE``
_TEMP_ID_RE = re.compile(r"^\s+tempID\s*:\s*(\S+)\s*$")
# ``    TemplateSectionField               94`` — the per-sheet row counts, which
# carry no colon and so are invisible to _SUMMARY_RE.
_SHEET_ROWS_RE = re.compile(r"^\s{4,}([A-Za-z][A-Za-z0-9_]*)\s{2,}(\d+)\s*$")


def parse_payload(stdout: str) -> Dict[str, Any]:
    """Pull the identity and per-sheet row counts out of ``upload payload``.

    Its stdout is shaped differently from the other subcommands: the sheet lines
    have no colon, and ``tempID`` has a colon but a non-numeric value, so neither
    is visible to the generic summary parser. ``tempID`` is worth surfacing on
    its own — it is the identity the live system would receive.
    """
    out: Dict[str, Any] = {"temp_id": None, "sheet_rows": {}}
    for line in stdout.splitlines():
        m = _TEMP_ID_RE.match(line)
        if m:
            out["temp_id"] = m.group(1)
            continue
        m = _SHEET_ROWS_RE.match(line)
        if m:
            out["sheet_rows"][m.group(1)] = int(m.group(2))
    return out


def read_text_if_exists(path: Path, *, max_chars: int = 200_000) -> Optional[str]:
    """Read a report the CLI wrote, or ``None`` if it did not write one.

    Truncated at ``max_chars`` because these land in node output, which rides the
    run envelope — a gaps report on a wide object is not worth a broker cap.
    """
    if not path.exists() or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n... [truncated at {max_chars} chars; full file at {path.name}]"
    return text


def gates_verdict(gates: List[Dict[str, str]]) -> Dict[str, Any]:
    """Roll the gate list up into the fields a CONDITION node wants.

    ``gates_failed_text`` duplicates ``gates_failed`` as a scalar because a list
    output is claim-checked to a file the expression layer cannot read — only
    scalars stay inline.
    """
    failed = [g["name"] for g in gates if g.get("status") == "FAIL"]
    return {
        "gates": gates,
        "gates_total": len(gates),
        "gates_failed": failed,
        "gates_failed_text": ", ".join(failed),
        "all_gates_green": bool(gates) and not failed,
    }
