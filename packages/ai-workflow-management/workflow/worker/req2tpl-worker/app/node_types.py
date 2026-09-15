"""Multi-type node registry for the req2tpl worker.

Nine nodes over one local Rust CLI, registered individually via worker-sdk
``node_types=[...]`` (the SA-1078 / SA-1549 pattern jira-worker follows). The
node names mirror the CLI's own subcommands so the manual reads as the node
documentation:

    req2tpl_fetch_inputs    Jira issue  -> workbooks on this machine's disk
    req2tpl_survey          what is REALLY in a workbook (run before profiling)
    req2tpl_init            auto-detect a profile stub
    req2tpl_write_profile   commit a confirmed profile to disk
    req2tpl_map             resolve requirement -> FS; mapping-table.md + gaps.md
    req2tpl_build           write the .xlsx and run the gates
    req2tpl_verify          re-run the gates against an existing .xlsx
    req2tpl_upload_payload  the exact upload body, offline
    req2tpl_attach_result   local artefact -> back onto the ticket

``upload send`` is deliberately NOT a node. It writes to a live landscape with
no undo, and the tool's own manual records that it has never run with valid
credentials — whether the route accepts a bearer, whether the interface key is
required, and what ``isOverwrite: true`` does are all unknown. That step stays a
typed command with ``--yes``.

Handler contract (worker-sdk routes): ``(inputs, parameters) -> dict``, and
**raise on failure** — a raise maps to TaskStatus "error", a return to "success"
(the load-bearing SA-1078 strings).

Design-time ``functions`` are empty on purpose: the FE invokes those over the
worker's HTTP endpoint, and a PULL worker registers without one.
"""

from __future__ import annotations

import hashlib
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from worker_sdk import NodeKind
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    switch_field,
    text_field,
    textarea_field,
)
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth

from app.layer2_application.services import classify as classify_svc
from app.layer2_application.services import profile_doc, req2tpl_cli
from app.layer2_application.services.jira_files import JiraFilesClient
from app.layer2_application.services.workspace import (
    WorkspaceError,
    rel_to_run,
    resolve_existing,
    resolve_in,
    resolve_run_dir,
    safe_filename,
    workspace_root,
)
from app.layer4_frameworks.config import settings

_TAGS = ["req2tpl", "smdg", "template", "xlsx"]

_DEFAULT_PROFILE = "profiles/profile.toml"
_DEFAULT_TEMPLATE = "out/template.xlsx"


# -- input / value helpers ----------------------------------------------------


def _req() -> List[ValidationRule]:
    return [ValidationRule(method="required")]


def _merge(inputs: Optional[Dict[str, Any]], parameters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge parameters + inputs (inputs win), matching the fleet convention."""
    return {**(parameters or {}), **(inputs or {})}


def _require(merged: Dict[str, Any], key: str, label: Optional[str] = None) -> Any:
    value = merged.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"VALIDATION_ERROR: '{label or key}' is required")
    return value.strip() if isinstance(value, str) else value


def _opt(merged: Dict[str, Any], key: str, default: Optional[str] = None) -> Optional[str]:
    value = merged.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return value


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _csv(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _slug(text: str) -> str:
    """Reduce free text to a run_dir slug (used to default one from an issue key)."""
    keep = [c if (c.isalnum() or c in "._-") else "-" for c in str(text).strip()]
    slug = "".join(keep).strip("-.") or "run"
    return slug[:128]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ok(**outputs: Any) -> Dict[str, Any]:
    return {"success": True, "error": None, "error_code": None, **outputs}


def _kv(mapping: Optional[Dict[str, Any]]) -> str:
    """Render a small dict as one scalar line.

    Measured on a live run: a node's ARRAY or DICT output is claim-checked to a
    CSV file and is only reachable as ``{{$artifacts.<field>}}`` — a file_ref an
    expression cannot read. Only scalars stay inline. So every structured field
    a downstream node needs also ships as a plain string beside it.
    """
    if not mapping:
        return ""
    return ", ".join(f"{k}={v}" for k, v in mapping.items())


def _lines(items: Optional[List[Any]]) -> str:
    """Same reason as ``_kv``: a list a downstream node must read, as one string."""
    return "\n".join(str(i) for i in (items or []))


def _client(merged: Dict[str, Any]) -> JiraFilesClient:
    """Resolve the vault credential (fail closed) and build a Jira client.

    The credential decides both the site and the secret. Nothing is read from
    this machine's disk or environment — a missing credential is an error, never
    a fallback.
    """
    cred = resolve_effective_auth(merged, require=True)
    return JiraFilesClient.from_credential(cred)



def _decisions_arg(merged: Dict[str, Any], run: Path) -> List[str]:
    """Resolve the operator's decisions into a ``--decisions`` argument.

    Two ways in, because a decision is a *judgement* and the person making it is
    not necessarily sitting at the worker machine:

    * ``decisions`` — a path to a file already in the run directory;
    * ``decisions_toml`` — the rulings as text, written to that path here.

    Text wins when both are given. Decisions are what clear a blocking uncovered
    FS primary key, and that exact class caused three live import rejections, so
    the alternative (``allow_open_gaps``) is never chosen on the operator's
    behalf.
    """
    rel = _opt(merged, "decisions", "profiles/decisions.toml")
    text = _opt(merged, "decisions_toml")
    if text and not text.lstrip().startswith("{{"):
        path = resolve_in(run, rel, label="decisions")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        return ["--decisions", str(path)]
    if _opt(merged, "decisions"):
        return ["--decisions", str(resolve_existing(run, rel, label="decisions"))]
    return []


def _fan_out(fn: Any, items: List[Any]) -> List[Any]:
    """Run ``fn`` over ``items`` concurrently, preserving INPUT order.

    Used for the Jira file transfers, which are independent round trips to one
    host. Order is restored afterwards so a node's output never depends on which
    transfer happened to finish first — a run that reports its files in a
    different order each time is not reproducible evidence.

    The first exception is re-raised: a partial upload or download must fail the
    node, not be quietly reported as a shorter list.
    """
    if not items:
        return []
    width = max(1, min(int(settings.REQ2TPL_JIRA_CONCURRENCY or 1), len(items)))
    if width == 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=width, thread_name_prefix="req2tpl-jira") as pool:
        return list(pool.map(fn, items))


def _run_and_report(action: str, args: List[str], run: Path) -> req2tpl_cli.CliResult:
    result = req2tpl_cli.run(args, cwd=run)
    return result.raise_for_status(action)


# -- output-schema field helpers ----------------------------------------------


def _o_bool(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _o_text(key: str, label: str) -> AutoFormField:
    return text_field(key, label, readonly=True)


def _o_number(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.NUMBER,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"type": "number", "readonly": True},
        ),
    )


def _o_array(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _o_object(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.OBJECT,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _outputs(*fields: AutoFormField) -> List[AutoFormField]:
    return [
        _o_bool("success", "Success"),
        *fields,
        _o_text("error", "Error"),
        _o_text("error_code", "Error Code"),
    ]


def _cli_outputs(*fields: AutoFormField) -> List[AutoFormField]:
    """Outputs every CLI-backed node shares."""
    return _outputs(
        *fields,
        _o_text("run_dir", "Run Directory"),
        _o_number("exit_code", "Exit Code"),
        _o_text("stdout", "CLI Output"),
    )


# -- shared inputs ------------------------------------------------------------


def _in_run_dir(required: bool = True) -> AutoFormField:
    return text_field(
        "run_dir",
        "Run Directory",
        placeholder="a single folder name under the worker workspace, e.g. SA-2243",
        rules=_req() if required else None,
    )


def _in_issue_key() -> AutoFormField:
    return text_field("issue_key", "Issue Key", placeholder="e.g. SA-2243", rules=_req())


def _in_profile() -> AutoFormField:
    return text_field("profile", "Profile (relative path)", default=_DEFAULT_PROFILE, rules=_req())


def _in_req_fs() -> List[AutoFormField]:
    return [
        text_field("req_file", "Requirement workbook", placeholder="PIR_requirement.xlsx", rules=_req()),
        text_field("fs_file", "Functional-spec workbook", placeholder="PIR_fs.xlsx", rules=_req()),
    ]


def _in_decisions() -> List[AutoFormField]:
    """A decisions file already on disk, or the rulings as text.

    Text is the one a workflow form can actually supply — the person making the
    ruling is usually not sitting at the worker machine.
    """
    return [
        text_field("decisions", "Decisions file",
                   placeholder="profiles/decisions.toml — operator rulings",
                   default="profiles/decisions.toml"),
        textarea_field("decisions_toml", "Decisions TOML (optional, written to that path)",
                       rows=8, growing=True),
    ]


# -- node factory -------------------------------------------------------------


def _node(
    worker_type: str,
    name: str,
    description: str,
    icon: str,
    inputs: List[AutoFormField],
    outputs: List[AutoFormField],
    handler: Any,
    kind: NodeKind = NodeKind.ACTION,
) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        worker_type=worker_type,
        name=name,
        description=description,
        version=settings.WORKER_VERSION,
        node_class=settings.WORKER_NODE_CLASS,
        icon=icon,
        color=settings.WORKER_COLOR,
        tags=_TAGS,
        input_schema=[f.to_dict() for f in inputs],
        output_schema=[f.to_dict() for f in outputs],
        functions=[],  # design-time invoke needs an HTTP endpoint; PULL has none
        handler=handler,
        kind=kind,
    )


# ============================================================================
# 1. fetch inputs — Jira attachments -> this machine's disk
# ============================================================================


def _fetch_inputs_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    issue_key = str(_require(merged, "issue_key", "Issue Key"))
    run = resolve_run_dir(_opt(merged, "run_dir") or _slug(issue_key))
    wanted = [n.lower() for n in _csv(merged.get("include"))]
    overwrite = _bool(merged.get("overwrite"))

    client = _client(merged)
    attachments = client.list_attachments(issue_key)
    if not attachments:
        raise ValueError(
            f"VALIDATION_ERROR: issue {issue_key} has no attachments — req2tpl needs the "
            "requirement and FS workbooks (and, to be trusted, a referral template)"
        )

    # Decide what to fetch first, then fetch it CONCURRENTLY. The transfers are
    # independent, and doing them one at a time cost 8.2s of a 35.6s pipeline on
    # the first live run. Order of the reported list is kept stable (the plan's
    # order), so the output does not depend on which download finished first.
    plan: List[tuple] = []
    skipped: List[Dict[str, Any]] = []
    for att in attachments:
        filename = att.get("filename") or ""
        if wanted and filename.lower() not in wanted:
            skipped.append({"filename": filename, "reason": "not in 'include'"})
            continue
        leaf = safe_filename(filename)
        dest = resolve_in(run, leaf, label="attachment")
        if dest.exists() and not overwrite:
            skipped.append({"filename": filename, "reason": "already present (overwrite is off)"})
            continue
        plan.append((att, leaf, dest))

    def _download(item: tuple) -> Dict[str, Any]:
        att_, leaf_, dest_ = item
        meta = client.download_to(
            att_["content_url"], dest_, max_bytes=settings.REQ2TPL_MAX_ATTACHMENT_BYTES
        )
        return {
            "filename": leaf_,
            "path": rel_to_run(run, dest_),
            "bytes": meta["bytes"],
            "sha256": meta["sha256"],
            "mime_type": att_.get("mime_type") or "",
            "attachment_id": att_.get("id") or "",
        }

    downloaded: List[Dict[str, Any]] = _fan_out(_download, plan)

    if not downloaded and not skipped:
        raise ValueError(f"VALIDATION_ERROR: nothing matched 'include' on issue {issue_key}")

    # Classify over everything now sitting in the run directory, not just what
    # this call downloaded — a re-run with overwrite off downloads nothing and
    # must still report which file is which.
    on_disk = sorted(p.name for p in run.iterdir() if p.is_file())
    picked = classify_svc.classify(on_disk)
    classify_svc.require(picked, on_disk)

    return _ok(
        run_dir=run.name,
        run_path=str(run),
        issue_key=issue_key,
        issue_url=client.browse_url(issue_key),
        files=downloaded,
        count=len(downloaded),
        skipped=skipped,
        attachments_seen=len(attachments),
        # Scalars the next node can actually read (see _kv).
        req_file=picked["req_file"],
        fs_file=picked["fs_file"],
        referral=picked["referral"],
        has_referral=picked["has_referral"],
        filenames=", ".join(on_disk),
    )


# ============================================================================
# 2. survey — what is really in these workbooks
# ============================================================================


def _survey_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    names = _csv(_require(merged, "files", "Workbooks"))
    paths = [str(resolve_existing(run, n, label="files")) for n in names]

    result = _run_and_report("survey", ["survey", *paths], run)
    return _ok(run_dir=run.name, exit_code=result.exit_code, stdout=result.stdout,
               files=names, summary=result.summary)


# ============================================================================
# 3. init — auto-detect a profile stub
# ============================================================================


def _init_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    req_file = resolve_existing(run, _require(merged, "req_file", "Requirement workbook"), label="req_file")
    fs_file = resolve_existing(run, _require(merged, "fs_file", "Functional-spec workbook"), label="fs_file")
    out_rel = _opt(merged, "profile_out", _DEFAULT_PROFILE)
    out_path = resolve_in(run, out_rel, label="profile_out")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = _run_and_report(
        "init",
        ["init", "--req", str(req_file), "--fs", str(fs_file), "-o", str(out_path)],
        run,
    )
    if not out_path.exists():
        raise req2tpl_cli.Req2tplError(
            f"req2tpl init reported success but wrote no profile at {out_rel}",
            error_code="REQ2TPL_ERROR",
        )

    toml_text = out_path.read_text(encoding="utf-8", errors="replace")
    summary = profile_doc.summarize(toml_text)
    ready, problems = profile_doc.readiness(toml_text)
    return _ok(
        run_dir=run.name,
        exit_code=result.exit_code,
        stdout=result.stdout,
        profile_path=rel_to_run(run, out_path),
        profile_toml=toml_text,
        ready=ready,
        problems=problems,
        problems_text=_lines(problems),
        verify_lines_text=_lines(summary.get("verify_lines")),
        **summary,
    )


# ============================================================================
# 4. write profile — commit a confirmed profile
# ============================================================================


def _write_profile_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"))
    rel = _opt(merged, "profile_path", _DEFAULT_PROFILE)
    toml_text = str(_require(merged, "profile_toml", "Profile TOML"))
    enforce = merged.get("enforce_ready")
    enforce = True if enforce is None else _bool(enforce)

    summary = profile_doc.summarize(toml_text)  # raises on malformed TOML
    ready, problems = profile_doc.readiness(toml_text)
    if enforce and not ready:
        raise ValueError(
            "VALIDATION_ERROR: profile is not ready to build from — "
            + "; ".join(problems)
            + ". Settle these, or switch off 'Enforce ready' to write it anyway."
        )

    path = resolve_in(run, rel, label="profile_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written LF-only: the file is an input to a tool whose whole promise is
    # byte-identical output, and mixed line endings parse fine either way.
    path.write_text(toml_text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")

    return _ok(
        run_dir=run.name,
        profile_path=rel_to_run(run, path),
        bytes=path.stat().st_size,
        sha256=_sha256(path),
        ready=ready,
        problems=problems,
        problems_text=_lines(problems),
        verify_lines_text=_lines(summary.get("verify_lines")),
        **summary,
    )


# ============================================================================
# 5. map — resolve requirement -> FS, no .xlsx
# ============================================================================


def _map_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    profile = resolve_existing(run, _opt(merged, "profile", _DEFAULT_PROFILE), label="profile")
    req_file = resolve_existing(run, _require(merged, "req_file", "Requirement workbook"), label="req_file")
    fs_file = resolve_existing(run, _require(merged, "fs_file", "Functional-spec workbook"), label="fs_file")
    out_dir = resolve_in(run, _opt(merged, "out_dir", "out"), label="out_dir")
    out_dir.mkdir(parents=True, exist_ok=True)

    args = ["map", "-p", str(profile), "--req", str(req_file), "--fs", str(fs_file), "-o", str(out_dir)]
    args += _decisions_arg(merged, run)
    if _bool(merged.get("allow_open_gaps")):
        args.append("--allow-open-gaps")

    result = _run_and_report("map", args, run)
    return _ok(
        run_dir=run.name,
        exit_code=result.exit_code,
        stdout=result.stdout,
        out_dir=rel_to_run(run, out_dir),
        summary=result.summary,
        summary_text=_kv(result.summary),
        mapping_table_md=req2tpl_cli.read_text_if_exists(out_dir / "mapping-table.md"),
        gaps_md=req2tpl_cli.read_text_if_exists(out_dir / "gaps.md"),
        resolution_path=rel_to_run(run, out_dir / "resolution.json"),
    )


# ============================================================================
# 6. build — write the .xlsx and gate it
# ============================================================================


def _build_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    profile = resolve_existing(run, _opt(merged, "profile", _DEFAULT_PROFILE), label="profile")
    req_file = resolve_existing(run, _require(merged, "req_file", "Requirement workbook"), label="req_file")
    fs_file = resolve_existing(run, _require(merged, "fs_file", "Functional-spec workbook"), label="fs_file")
    out_rel = _opt(merged, "template_out", _DEFAULT_TEMPLATE)
    out_path = resolve_in(run, out_rel, label="template_out")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    args = ["build", "-p", str(profile), "--req", str(req_file), "--fs", str(fs_file), "-o", str(out_path)]

    referral = _opt(merged, "referral")
    if referral:
        args += ["--referral", str(resolve_existing(run, referral, label="referral"))]
    args += _decisions_arg(merged, run)
    reports_rel = _opt(merged, "reports_dir")
    reports_dir = out_path.parent
    if reports_rel:
        reports_dir = resolve_in(run, reports_rel, label="reports_dir")
        reports_dir.mkdir(parents=True, exist_ok=True)
        args += ["--reports", str(reports_dir)]
    if _bool(merged.get("allow_open_gaps")):
        args.append("--allow-open-gaps")
    if _bool(merged.get("strict_coverage")):
        args.append("--strict-coverage")

    result = _run_and_report("build", args, run)
    if not out_path.exists():
        raise req2tpl_cli.Req2tplError(
            f"req2tpl build reported success but wrote no file at {out_rel}",
            error_code="REQ2TPL_ERROR",
        )

    return _ok(
        run_dir=run.name,
        exit_code=result.exit_code,
        stdout=result.stdout,
        template_path=rel_to_run(run, out_path),
        template_bytes=out_path.stat().st_size,
        template_sha256=_sha256(out_path),
        referral_used=bool(referral),
        summary=result.summary,
        summary_text=_kv(result.summary),
        mapping_table_md=req2tpl_cli.read_text_if_exists(reports_dir / "mapping-table.md"),
        gaps_md=req2tpl_cli.read_text_if_exists(reports_dir / "gaps.md"),
        **req2tpl_cli.gates_verdict(result.gates),
    )


# ============================================================================
# 7. verify — gates against a file on disk
# ============================================================================


def _verify_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    template = resolve_existing(run, _opt(merged, "template", _DEFAULT_TEMPLATE), label="template")

    result = _run_and_report("verify", ["verify", str(template)], run)
    return _ok(
        run_dir=run.name,
        exit_code=result.exit_code,
        stdout=result.stdout,
        template_path=rel_to_run(run, template),
        template_sha256=_sha256(template),
        **req2tpl_cli.gates_verdict(result.gates),
    )


# ============================================================================
# 8. upload payload — the exact request body, offline
# ============================================================================


def _upload_payload_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    template = resolve_existing(run, _opt(merged, "template", _DEFAULT_TEMPLATE), label="template")
    body_path = resolve_in(run, _opt(merged, "body_out", "out/body.json"), label="body_out")
    body_path.parent.mkdir(parents=True, exist_ok=True)

    args = ["upload", "payload", "--file", str(template), "-o", str(body_path)]
    array_rel = _opt(merged, "array_out")
    array_path = None
    if array_rel:
        array_path = resolve_in(run, array_rel, label="array_out")
        array_path.parent.mkdir(parents=True, exist_ok=True)
        args += ["--array", str(array_path)]
    expect = _opt(merged, "expect")
    if expect:
        args += ["--expect", str(resolve_existing(run, expect, label="expect"))]
        if _bool(merged.get("expect_partial")):
            args.append("--expect-partial")
    created_by = _opt(merged, "created_by")
    if created_by:
        args += ["--created-by", created_by]
    transport = _opt(merged, "transport")
    if transport:
        args += ["--transport", transport]
    if _bool(merged.get("overwrite")):
        args.append("--overwrite")

    result = _run_and_report("upload payload", args, run)
    return _ok(
        run_dir=run.name,
        exit_code=result.exit_code,
        stdout=result.stdout,
        body_path=rel_to_run(run, body_path),
        body_sha256=_sha256(body_path) if body_path.exists() else None,
        array_path=rel_to_run(run, array_path) if array_path else None,
        summary=result.summary,
        **req2tpl_cli.parse_payload(result.stdout),
    )


# ============================================================================
# 9. attach result — local artefact -> back onto the ticket
# ============================================================================


def _attach_result_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(inputs, parameters)
    issue_key = str(_require(merged, "issue_key", "Issue Key"))
    run = resolve_run_dir(_require(merged, "run_dir", "Run Directory"), create=False)
    names = _csv(_require(merged, "files", "Files"))
    comment = _opt(merged, "comment")

    client = _client(merged)

    # Resolve every path BEFORE uploading anything: a missing file should fail
    # the node with nothing sent, not leave the ticket half-updated.
    paths = [resolve_existing(run, name, label="files") for name in names]

    def _upload(path: Path) -> Dict[str, Any]:
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        result = client.add_attachment(issue_key, path.name, content, mime)
        first = result[0] if isinstance(result, list) and result else {}
        return {
            "filename": path.name,
            "path": rel_to_run(run, path),
            "bytes": len(content),
            "sha256": _sha256(path),
            "attachment_id": str((first or {}).get("id") or ""),
        }

    # Concurrent for the same reason as the download side: three sequential
    # uploads cost 8.3s on the first live run.
    attached: List[Dict[str, Any]] = _fan_out(_upload, paths)

    comment_added = False
    if comment:
        client.add_comment(issue_key, comment)
        comment_added = True

    return _ok(
        run_dir=run.name,
        issue_key=issue_key,
        issue_url=client.browse_url(issue_key),
        attached=attached,
        count=len(attached),
        comment_added=comment_added,
    )


# ============================================================================
# Registry
# ============================================================================


NODE_TYPES: List[NodeTypeDefinition] = [
    _node(
        "req2tpl_fetch_inputs",
        "req2tpl: Fetch Inputs from Jira",
        "Downloads a ticket's attachments onto the machine running this worker, into a "
        "run directory under the worker workspace. The file bytes never enter the "
        "workflow — downstream nodes get the path, size and sha256. Needs a Jira API "
        "Token credential.",
        "Download",
        [
            _in_issue_key(),
            _in_run_dir(required=False),
            text_field("include", "Only these filenames (optional)",
                       placeholder="PIR_requirement.xlsx, PIR_fs.xlsx, PIR_referral.xlsx"),
            switch_field("overwrite", "Overwrite files already downloaded", default=False),
        ],
        _outputs(
            _o_text("run_dir", "Run Directory"),
            _o_text("run_path", "Run Path (absolute)"),
            _o_text("issue_key", "Issue Key"),
            _o_text("issue_url", "Issue URL"),
            _o_array("files", "Files Downloaded"),
            _o_number("count", "Downloaded"),
            _o_array("skipped", "Skipped"),
            _o_number("attachments_seen", "Attachments on Issue"),
            _o_text("req_file", "Requirement workbook"),
            _o_text("fs_file", "FS workbook"),
            _o_text("referral", "Referral template"),
            _o_bool("has_referral", "Referral Present"),
            _o_text("filenames", "Files in the run directory"),
        ),
        _fetch_inputs_handler,
    ),
    _node(
        "req2tpl_survey",
        "req2tpl: Survey Workbooks",
        "Prints what is really in the workbooks — sheets, true row counts, hidden rows, "
        "header layout, duplicate labels, the Data Type vocabulary. Run this before "
        "writing a profile: formatted-but-empty rows make a sheet look 40x bigger than "
        "it is.",
        "Search",
        [
            _in_run_dir(),
            text_field("files", "Workbooks (comma-separated)",
                       placeholder="PIR_requirement.xlsx, PIR_fs.xlsx", rules=_req()),
        ],
        _cli_outputs(_o_array("files", "Workbooks"), _o_object("summary", "Counts")),
        _survey_handler,
    ),
    _node(
        "req2tpl_init",
        "req2tpl: Init Profile",
        "Auto-detects everything derivable from a requirement + FS pair and writes a "
        "profile stub. Two things are left for a human: template_id (never invented) and "
        "structure_map (not derivable — the heuristic is confidently wrong about 1 line "
        "in 6, and writes low-confidence lines commented out as '# VERIFY:'). Those "
        "lines come back on 'verify_lines'.",
        "FilePlus",
        [
            _in_run_dir(),
            *_in_req_fs(),
            text_field("profile_out", "Write profile to", default=_DEFAULT_PROFILE),
        ],
        _cli_outputs(
            _o_text("profile_path", "Profile Path"),
            _o_text("profile_toml", "Profile TOML"),
            _o_text("object_id", "Object Id"),
            _o_text("template_id", "Template Id"),
            _o_text("model_ns", "Model Namespace"),
            _o_object("structure_map", "Structure Map"),
            _o_number("structure_map_count", "Structure Map Lines"),
            _o_array("verify_lines", "Lines Awaiting Confirmation"),
            _o_number("verify_pending", "Pending Confirmations"),
            _o_text("verify_lines_text", "Lines Awaiting Confirmation (text)"),
            _o_bool("ready", "Ready to Build"),
            _o_array("problems", "Blocking Problems"),
            _o_text("problems_text", "Blocking Problems (text)"),
        ),
        _init_handler,
    ),
    _node(
        "req2tpl_write_profile",
        "req2tpl: Write Profile",
        "Writes a confirmed profile.toml to the run directory. Refuses by default unless "
        "template_id is set and structure_map is non-empty — the two things req2tpl "
        "cannot derive. Outstanding '# VERIFY:' lines are reported, not blocked: only "
        "the operator can say which are irrelevant to this object.",
        "FileCheck",
        [
            _in_run_dir(),
            text_field("profile_path", "Write to", default=_DEFAULT_PROFILE),
            textarea_field("profile_toml", "Profile TOML", rows=16, growing=True, rules=_req()),
            switch_field("enforce_ready", "Enforce ready (template_id + structure_map)", default=True),
        ],
        _outputs(
            _o_text("run_dir", "Run Directory"),
            _o_text("profile_path", "Profile Path"),
            _o_number("bytes", "Bytes"),
            _o_text("sha256", "SHA-256"),
            _o_text("object_id", "Object Id"),
            _o_text("template_id", "Template Id"),
            _o_text("model_ns", "Model Namespace"),
            _o_object("structure_map", "Structure Map"),
            _o_number("structure_map_count", "Structure Map Lines"),
            _o_array("verify_lines", "Lines Awaiting Confirmation"),
            _o_number("verify_pending", "Pending Confirmations"),
            _o_text("verify_lines_text", "Lines Awaiting Confirmation (text)"),
            _o_bool("ready", "Ready to Build"),
            _o_array("problems", "Blocking Problems"),
            _o_text("problems_text", "Blocking Problems (text)"),
        ),
        _write_profile_handler,
    ),
    _node(
        "req2tpl_map",
        "req2tpl: Map (review before build)",
        "Resolves requirement -> FS and writes mapping-table.md, gaps.md and "
        "resolution.json. No .xlsx is produced. Blocks on an uncovered FS primary key — "
        "that exact class caused three live rejections; clear it with a decision rather "
        "than by switching the check off.",
        "GitCompare",
        [
            _in_run_dir(),
            _in_profile(),
            *_in_req_fs(),
            *_in_decisions(),
            text_field("out_dir", "Report directory", default="out"),
            switch_field("allow_open_gaps", "Continue despite uncovered FS primary keys", default=False),
        ],
        _cli_outputs(
            _o_text("out_dir", "Report Directory"),
            _o_object("summary", "Counts"),
            _o_text("summary_text", "Counts (text)"),
            _o_text("mapping_table_md", "mapping-table.md"),
            _o_text("gaps_md", "gaps.md"),
            _o_text("resolution_path", "resolution.json"),
        ),
        _map_handler,
    ),
    _node(
        "req2tpl_build",
        "req2tpl: Build Template",
        "Everything Map does, then writes the .xlsx and runs the gates against the "
        "written file, re-unzipped. Supply a referral (a template that already imported) "
        "— without one, sheet scope falls back to 4 core sheets and column labels are "
        "unknown. Determinism is measured, not asserted: two builds are byte-compared.",
        "FileSpreadsheet",
        [
            _in_run_dir(),
            _in_profile(),
            *_in_req_fs(),
            text_field("referral", "Referral template (strongly recommended)",
                       placeholder="a template that already imported successfully"),
            *_in_decisions(),
            text_field("template_out", "Write template to", default=_DEFAULT_TEMPLATE),
            text_field("reports_dir", "Report directory (optional)",
                       placeholder="defaults to beside the .xlsx"),
            switch_field("allow_open_gaps", "Continue despite uncovered FS primary keys", default=False),
            switch_field("strict_coverage", "Block on thin per-table coverage", default=False),
        ],
        _cli_outputs(
            _o_text("template_path", "Template Path"),
            _o_number("template_bytes", "Template Bytes"),
            _o_text("template_sha256", "Template SHA-256"),
            _o_bool("referral_used", "Referral Used"),
            _o_array("gates", "Gates"),
            _o_number("gates_total", "Gates Run"),
            _o_array("gates_failed", "Gates Failed"),
            _o_text("gates_failed_text", "Gates Failed (text)"),
            _o_bool("all_gates_green", "All Gates Green"),
            _o_object("summary", "Counts"),
            _o_text("summary_text", "Counts (text)"),
            _o_text("mapping_table_md", "mapping-table.md"),
            _o_text("gaps_md", "gaps.md"),
        ),
        _build_handler,
    ),
    _node(
        "req2tpl_verify",
        "req2tpl: Verify Template",
        "Runs the gates against an existing .xlsx, always re-unzipping the file rather "
        "than reading any in-memory model. Use it on a file someone emailed you, or to "
        "confirm a file on disk is still the one that was gated. The referral-dependent "
        "gates only run inside Build.",
        "ShieldCheck",
        [
            _in_run_dir(),
            text_field("template", "Template (relative path)", default=_DEFAULT_TEMPLATE, rules=_req()),
        ],
        _cli_outputs(
            _o_text("template_path", "Template Path"),
            _o_text("template_sha256", "Template SHA-256"),
            _o_array("gates", "Gates"),
            _o_number("gates_total", "Gates Run"),
            _o_array("gates_failed", "Gates Failed"),
            _o_text("gates_failed_text", "Gates Failed (text)"),
            _o_bool("all_gates_green", "All Gates Green"),
        ),
        _verify_handler,
    ),
    _node(
        "req2tpl_upload_payload",
        "req2tpl: Upload Payload (offline)",
        "Turns the template into the exact uploadTransportData request body, reproducing "
        "the admin UI's in-browser parse from raw OOXML. No network path at all. Point "
        "'expect' at a request body captured from the app's own devtools for a real "
        "external check.",
        "FileJson",
        [
            _in_run_dir(),
            text_field("template", "Template (relative path)", default=_DEFAULT_TEMPLATE, rules=_req()),
            text_field("body_out", "Write request body to", default="out/body.json"),
            text_field("array_out", "Write readable array to (optional)", placeholder="out/array.json"),
            text_field("expect", "Diff against a known-good payload (optional)"),
            switch_field("expect_partial", "Treat 'expect' as a subset", default=False),
            text_field("created_by", "createdBy (optional)",
                       placeholder="the admin UI stamps the logged-in user; this tool has no session"),
            text_field("transport", "transport (optional)", placeholder="TP"),
            switch_field("overwrite", "isOverwrite", default=False),
        ],
        _cli_outputs(
            _o_text("body_path", "Body Path"),
            _o_text("body_sha256", "Body SHA-256"),
            _o_text("array_path", "Array Path"),
            _o_text("temp_id", "tempID"),
            _o_object("sheet_rows", "Rows per Sheet"),
            _o_object("summary", "Counts"),
        ),
        _upload_payload_handler,
    ),
    _node(
        "req2tpl_attach_result",
        "req2tpl: Attach Result to Jira",
        "Uploads artefacts from the run directory (the template, gaps.md, the payload) "
        "back onto the ticket, optionally with a comment. This is how a locally produced "
        "file gets back to the team without the bytes ever riding the workflow. Needs a "
        "Jira API Token credential.",
        "Upload",
        [
            _in_issue_key(),
            _in_run_dir(),
            text_field("files", "Files (comma-separated, relative)",
                       placeholder="out/template.xlsx, out/gaps.md", rules=_req()),
            textarea_field("comment", "Comment (optional)", rows=4, growing=True),
        ],
        _outputs(
            _o_text("run_dir", "Run Directory"),
            _o_text("issue_key", "Issue Key"),
            _o_text("issue_url", "Issue URL"),
            _o_array("attached", "Attached"),
            _o_number("count", "Attached Count"),
            _o_bool("comment_added", "Comment Added"),
        ),
        _attach_result_handler,
    ),
]


__all__ = ["NODE_TYPES", "workspace_root", "WorkspaceError"]
