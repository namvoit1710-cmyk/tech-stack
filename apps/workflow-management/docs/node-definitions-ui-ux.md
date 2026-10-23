# Node Definitions — UI/UX Guide & Research

App: **workflow-management** (`@ldc/workflow-management`)
Feature: `src/features/node-definitions/` · Pages: `src/pages/node-definitions.tsx`,
`src/pages/node-definition-builder.tsx`

Audience for this doc: anyone designing or extending the node-definition UI. The goal is the
same as the feature's: let a **non-technical builder** turn a raw worker (e.g. an HTTP call)
into a clean, safe, reusable node — and **test it before** putting it in a workflow.

---

## 1. Who uses this and what they're trying to do

| Persona | Goal | Pain if we get it wrong |
|---|---|---|
| **Business builder** (non-tech) | "Make a *Google Search* node my team can drop on the canvas." | Forced to hand-write JSON schemas / mapping expressions. |
| **Integration owner** (semi-tech) | Wrap an internal API, lock the auth, expose 2 fields. | Can't tell which worker field a form field maps to. |
| **QA / the builder again** | Confirm the node actually works before shipping. | Has to build a whole workflow just to try it once. |

The feature succeeds when the first persona never has to read the backend mapping guide.

---

## 2. What exists today (two entry points)

There are **two** create/edit surfaces in the codebase. They differ sharply in friendliness —
this is the single most important UX fact about the feature.

### A. The visual Builder page — the friendly path ✅ (primary)

`src/pages/node-definition-builder.tsx` → route `node-definitions/:definitionId`
(`:definitionId = "new"` for create).

A full-page, **tabbed** editor backed by purpose-built visual sub-editors:

| Tab | Component | What the user does | Non-tech friendly? |
|---|---|---|---|
| **General** | `@ldc/autoform` `Builder` | name, description, base worker (dropdown), icon, color, tags | ✅ |
| **Locked Defaults** | `locked-defaults-editor.tsx` | toggles a 🔒 per worker field, sets a fixed value (typed input/select/JSON) | ✅ derived from the worker's own schema |
| **Input Schema** | `schema-field-list.tsx` + `schema-field-editor.tsx` + `schema-preview.tsx` | adds form fields with plain-language types ("Text", "Yes/No", "List"), reorders, **live preview** | ✅ |
| **Mappings** | `input-mapping-editor.tsx` + `key-value-editor.tsx` | picks a worker field, fills value via expression / key-value rows / JSON template; click a chip to insert `{{$custom.field}}` | ⚠️ better than raw JSON, still exposes `{{…}}` |
| **Test** (edit only) | `node-definition-test-tab.tsx` | auto-renders the input form from `input_schema`, runs the node, shows resolved input + outputs side-by-side | ✅ |

Why it's friendly:
- **Type names are humanized** — `schema-field-editor.tsx:29` maps `boolean → "Yes/No"`,
  `array → "List"`, control types to "Dropdown Select", "Toggle Switch", etc.
- **Locked defaults are discovered, not typed** — the editor reads the selected worker's
  `input_schema` and renders one row per real parameter, with the correct control
  (boolean→select, enum→select, object→JSON editor). The user only flips a lock and picks a
  value (`locked-defaults-editor.tsx:152`).
- **Live preview** — the Input Schema tab renders the actual form the end user will see
  (`schema-preview.tsx`), so the builder sees their work immediately.
- **Insertion chips** — in mappings, every custom field is a one-click chip that inserts the
  correct `{{$custom.key}}` expression; the JSON-template editor has an **Auto-generate**
  button that builds the template from the input fields (`input-mapping-editor.tsx:226`).

### B. The form modal — the raw-JSON path ⚠️ (legacy / power-user)

`src/features/node-definitions/components/node-definitions/node-definition-form-modal.tsx`
(driven by a zustand store `…/workflows/stores/node-definition-stores`).

A single dialog where `locked_defaults`, `input_schema`, `output_schema`, `input_mapping`,
`output_mapping` are **raw JSON textareas** parsed with `JSON.parse` in a `try/catch` that
silently discards invalid JSON (`node-definition-form-modal.tsx:85`).

> **UX risk:** a non-tech user pasting slightly-wrong JSON gets it **silently dropped** — no
> error, the field just saves empty. This path should be treated as power-user-only (or
> retired in favour of the Builder). If kept, it needs inline JSON validation + a visible
> error, not a swallowed `catch`.

**Recommendation:** funnel all users to the Builder page (path **A**). The list page's
"Create" button already does this (`navigate("/node-definitions/new")`).

---

## 3. The list / discovery surface

`src/pages/node-definitions.tsx` + `…/components/node-definitions/node-definition-list.tsx`

- A `@ldc/data-table` with columns: **Name** (icon swatch + description, links to the
  builder), **Base worker**, **Tags**, and a row delete action.
- **Client-side search** over name/description/tags (`node-definition-list.tsx:53`).
- Delete is guarded by a confirm message box and shows toast feedback; on success it
  invalidates both the definitions query and the **node-palette** query so the canvas palette
  updates immediately (`node-definition-list.tsx:30`).
- Loading/fetching/deleting states dim the table and show a top loader bar.

Good as-is for small lists. **Scales poorly** because list + search + paging are all
client-side and the backend caps `$top` at 100 — see §6.

---

## 4. The testing experience (the differentiator)

`node-definition-test-tab.tsx` is the feature's strongest non-tech affordance: it lets a
builder validate a node **without building a workflow**.

- The left panel **auto-builds the test input form from `input_schema`** (the same fields
  the end user will see), so testing == filling the real form.
- A **Run** button (with spinner / disabled state) calls
  `POST /node-definitions/{id}/test` with a generous 120s timeout
  (`hooks/apis/node-definitions.ts:106`).
- The right panel shows a **status badge** (green for success/completed, destructive for
  failed/error), execution time, the **resolved input** (what actually got sent after
  mapping + locked defaults), and the **outputs**, both as collapsible JSON
  (`@uiw/react-json-view`).
- Backend nuance the UI already respects: a *failed worker run* comes back **HTTP 200** with
  `status: "FAILED"` (a valid test result), while *not-testable* node types come back **422**.
  The tab renders both — the 422 surfaces via the error branch / toast.

### Testing gaps to close
- **Empty `input_schema`** → "no input fields" message and an empty `{}` test; fine, but a
  hint ("this node takes no user input") reads better than a dead end.
- **422 not-testable** types (INPUT/HUMAN_ACTION/LOOP/WORKFLOW/…) should show a friendly
  "This node can only be tested on the workflow canvas" with a link, instead of a generic
  toast. The backend already returns that exact message + `error_details.reason`.
- The Test tab only renders for **saved** definitions (`!isNew`) — correct, since test needs
  an id; make the "save first to test" state explicit for new definitions.

---

## 5. UX principles to keep (and the rationale)

1. **Progressive disclosure, never raw config first.** Default everyone to the Builder's
   guided tabs. Raw JSON is an escape hatch, not the front door.
2. **Derive, don't ask.** Locked-defaults and mapping target fields come from the *worker's*
   schema — the user recognizes fields instead of inventing them. Keep new editors
   worker-schema-driven.
3. **Show the result of abstraction.** Live preview (input schema) and resolved-input (test)
   let a builder see exactly what end users and workers will see. Preserve both.
4. **Humanize vocabulary.** "Yes/No" not `boolean`, "List" not `array`, "Fixed value" not
   `locked_defaults`. Continue mapping technical terms in the UI layer + i18n.
5. **Fail loudly at trust boundaries.** Any JSON/expression a user types must validate
   visibly. The silent `catch` in the modal is the one place this is violated today.
6. **Keep palette + list in sync.** Always invalidate `nodepalleteKey` after create/update/
   delete so the canvas reflects changes (already done in all three mutations).

---

## 6. Concrete improvement backlog (prioritized, lazy-first)

| # | Improvement | Why | Effort |
|---|---|---|---|
| 1 | Retire or hard-gate the raw-JSON **form modal**; make Builder the only path | removes the silent-data-loss trap for non-tech users | S (delete code) |
| 2 | Friendly **422 "test on canvas"** panel in the Test tab | turn a confusing toast into guidance | S |
| 3 | Inline **validation** on any remaining JSON/expression inputs | no silent drops | S |
| 4 | Show **`base_worker_id` is required & immutable** affordance; today it's only editable on create | matches backend (worker fixed after create) | S |
| 5 | **Output Schema** tab is built but commented out in the page (`node-definition-builder.tsx:394`) — finish or remove | dead tab = confusion | S |
| 6 | Server-side **search + paging** for the list once tenants have >100 defs | backend caps `$top` at 100; current search is client-side | M (needs backend filter) |
| 7 | A short **inline "what is this?"** helper per tab (1 line each) | first-timers don't read external guides | S |
| 8 | **Worker-field metadata beyond HTTP** — `worker-field-metadata.ts` only knows `http-request-worker`; other workers fall back to plain expression mode | mapping UX degrades for non-HTTP workers | M |
| 9 | **Duplicate / clone** a definition (esp. for inheritance children) | common builder action; not in UI | S–M |

> Items 1–5, 7 are mostly UI-only and cheap. 6 and 8 need backend/registry work.

---

## 7. Accessibility / interaction notes

- Custom `<select>` and native checkboxes are used in `schema-field-editor.tsx`; ensure they
  keep visible focus rings and labels (they currently have `<label>`s — keep them associated).
- The mappings "Add" menu is a hand-rolled dropdown with an outside-click handler
  (`input-mapping-editor.tsx:339`) — verify keyboard (Esc to close, arrow nav) before relying
  on it for accessibility.
- Drag handle in `schema-field-editor.tsx` is decorative (`GripVerticalIcon`); actual
  reordering is via up/down buttons — keep the buttons (keyboard-usable) as the source of truth.
- All user-facing strings go through `useLanguage()` `t(...)` / `@ldc/i18n`
  (`src/locales/en/workflow-management.json`) — no hard-coded copy in new UI.
