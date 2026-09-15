# req2tpl Worker (`req2tpl_*`)

A **local, PULL-mode** worker that drives the [`req2tpl`](https://github.com/) Rust
CLI — the deterministic *requirement + functional spec → SimpleMDG import template*
builder — and moves its inputs and outputs to and from a Jira ticket.

It is local by necessity, not by preference: the CLI is a per-machine `cargo`
build, and the workbooks it reads are files on that machine's disk.

## The nine nodes

Named after the CLI's own subcommands, so [`USAGE.md`](../../../../../../) in the
tool's repo reads as the node documentation.

| Node | Does | Credential |
|---|---|---|
| `req2tpl_fetch_inputs` | ticket attachments → the run directory on this machine | Jira API Token |
| `req2tpl_survey` | what is *really* in a workbook — sheets, true row counts, hidden rows | — |
| `req2tpl_init` | auto-detect a profile stub; surface the lines it will not stand behind | — |
| `req2tpl_write_profile` | commit a confirmed `profile.toml` | — |
| `req2tpl_map` | resolve requirement → FS; `mapping-table.md` + `gaps.md`. No `.xlsx` | — |
| `req2tpl_build` | write the `.xlsx` and run the gates against the written file | — |
| `req2tpl_verify` | re-run the gates against an existing `.xlsx` | — |
| `req2tpl_upload_payload` | the exact `uploadTransportData` body, offline | — |
| `req2tpl_attach_result` | run-directory artefacts → back onto the ticket | Jira API Token |

## The shape of a run

```
jira_get_issue / trigger
   └─ req2tpl_fetch_inputs      requirement.xlsx + fs.xlsx + referral.xlsx onto disk
        └─ req2tpl_survey       measure the workbooks before profiling them
             └─ req2tpl_init    profile stub + the `# VERIFY:` lines
                  └─ claude_*   propose structure_map + template_id  ─┐
                       └─ jira_add_comment  ── human confirms ────────┘
                            └─ req2tpl_write_profile
                                 └─ req2tpl_map      review gaps
                                      └─ req2tpl_build     .xlsx + 9 gates
                                           └─ req2tpl_verify
                                                └─ req2tpl_attach_result
```

The Claude step is where the pipeline is honestly not automatic. `template_id` is
never invented by the tool, and `structure_map` is *not derivable* — on the
reference object `init`'s heuristic gets 3 of 6 right, ties on one, and is
**confidently wrong** on one. So `init` writes low-confidence lines commented out
as `# VERIFY:`, this worker reports them on `verify_lines`, and a human confirms
before `write_profile` will accept the profile.

## What is deliberately NOT here

**`upload send`** — the step that writes to a live landscape. It has no undo, and
the tool's own manual records that it has never run with valid credentials:
whether the route accepts a bearer, whether `application-interface-key` is
required, and what `isOverwrite: true` does are all unknown. It stays a typed
command with `--yes`. `req2tpl_upload_payload` gives you the exact bytes that
*would* be sent, offline, so the review can still happen in the workflow.

## Two design decisions worth knowing

**The file bytes never enter the workflow.** An `.xlsx` referral is ~180 KB; base64
alongside the other two workbooks is well past the 256 KB TASK-input ceiling, and a
split input is not re-joined today. So `fetch_inputs` writes to local disk and
passes on a *path*, a size and a sha256. That also removes any need for the
file-service on this path.

**Every path is confined.** This worker runs on a laptop and leases tasks from a
shared tenant, so a path a workflow supplies is untrusted input. `run_dir` is a
flat slug; everything else resolves under it and is re-checked *after*
normalisation. Absolute paths and `..` escapes are refused, and an attachment
filename is reduced to a safe leaf. The Jira attachment `content` URL is checked
to be on the credential's own host before any `Authorization` header is attached —
otherwise a crafted issue could aim this worker's Jira secret at a host of the
attacker's choosing.

## Running it

```bash
cd .../workflow/worker/req2tpl-worker
pip install -r requirements.txt          # from INSIDE this dir: `-e ../worker-sdk` resolves against CWD
cp .env.example .env                     # set REQ2TPL_BIN
python main.py                           # -> "Mode: PULL (leasing ... )"
```

Build the tool first:

```bash
cd .../requirement-to-template/req2tpl && cargo build --release
```

No secret belongs in `.env`. The Jira credential is resolved from the vault at run
time via the node's `credential_ref` — never from disk or environment.

### Settings

| Env | Meaning |
|---|---|
| `APP_MODE=PULL` | lease from the executor rather than serving HTTP |
| `REGISTRY_URL` | the Worker Executor |
| `REQ2TPL_BIN` | absolute path to the built binary; blank = `req2tpl` on PATH |
| `REQ2TPL_WORKSPACE` | the one directory this worker may touch (default `~/.req2tpl-worker/runs`) |
| `REQ2TPL_TIMEOUT_SECONDS` | per-invocation runaway guard (default 300) |
| `REQ2TPL_MAX_ATTACHMENT_BYTES` | download refusal ceiling (default 64 MiB) |

`LEASE_RENEW_ENABLED` is forced **on** here. The SDK default is off, and a task
outliving the 60 s lease TTL without renewal is re-leased by another instance —
double execution, and the original's callback rejected on a dead token.

## Tests

```bash
PYTHONPATH="../worker-sdk;." python -m pytest        # 110 passed
```

The `PYTHONPATH` is there to beat a stale `worker_sdk` in site-packages.

End-to-end against the real binary and the PIR samples (survey → init →
write_profile → map → build ×2 → verify → upload payload) was run and is the basis
for the claims above: 9 gates green, and two separate node invocations produced a
byte-identical template (`sha256 35106a72…`). Jira was not exercised — that needs a
real vault credential and a real ticket.

## Base branch

This worker uses the SDK's multi-type PULL support from **PR #1901**
(`feat/pull-multi-node-sdk`), which is open, not merged. That is why there is no
private lease loop here: claude-worker's `app/pull_multi.py` fork is deleted on
that branch, and copying it a third time would have meant copying three known
defects (heartbeat only while idle, per-task rather than per-batch renewal, a
permanently failed boot registration).

## Control-plane change

`worker_node_provider` seeds `req2tpl_fetch_inputs` and `req2tpl_attach_result`
with `API_KEY` so their credential picker renders, and groups the family under a
`req2tpl` palette collection. The other seven nodes take no credential and are
deliberately left unseeded.
