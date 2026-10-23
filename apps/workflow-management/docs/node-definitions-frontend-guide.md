# Node Definitions — Frontend Coding Style & Build Guide

App: **workflow-management** (`@ldc/workflow-management`)

This doc has two halves:

1. **Coding style** — the conventions of this app, learned from the existing code, so anything
   you add looks like it belongs.
2. **Build guide** — how the node-definitions feature is assembled, and the recipe to build or
   extend a feature the same way.

> The node-definitions feature **already exists** end-to-end (list, builder, CRUD+test API
> hooks, visual editors). This guide documents *how it was built* so you can extend it (new
> tab, new editor, new field) or replicate the pattern for another feature — not build it from
> scratch.

---

## Part 1 — Coding style & conventions

### 1.1 Stack

| Concern | Choice |
|---|---|
| Framework | **React 19** (`catalog:react19`) |
| Bundler / dev | **rsbuild** (`rsbuild dev` / `build`) + **Module Federation** (this app is a remote) |
| Routing | **react-router-dom** (`src/remote-app-routes.tsx`) |
| Server state | **TanStack Query** via `@ldc/tanstack-query` (re-exported `useQuery`/`useMutation`/`queryKeyFactory`) |
| Client/UI state | **zustand** (e.g. `…/workflows/stores/node-definition-stores.ts`) |
| HTTP | `@ldc/api-sdk` `APISdk` instance in `src/lib/api.ts` (axios-style; unwraps the response body) |
| UI kit | `@ldc/ui` (shadcn-style: `Button`, `Dialog`, `Tabs`, `Select`, `Resizable`, `Badge`, `Spinner`, `toast`) |
| Forms | `@ldc/autoform` `Builder` (schema-driven, AutoFormField) + `BuilderRef.onSubmit()` |
| Tables | `@ldc/data-table` `DataTable` + `ColumnDef` |
| Validation | **zod** (`catalog:`) |
| i18n | `@ldc/i18n` via `useLanguage()` → `t(key, params)`; strings in `src/locales/en/workflow-management.json` |
| Icons | `lucide-react`; node icons via `DynamicNodeIcon` from `@ldc/workflow-editor` |
| Lint/format | `@ldc/eslint-config`, `@ldc/prettier-config` (`"prettier": "@ldc/prettier-config"`) |
| Tests | **rstest** (`rstest run`) + `@testing-library/react` + `happy-dom` |

Cross-app shared code lives in workspace packages (`@ldc/*`); never reach into another app.

### 1.2 Feature folder layout

```
src/features/<feature>/
  components/<group>/<component>.tsx   # PascalCase component, kebab-case file
  hooks/apis/<resource>.ts             # TanStack Query hooks + queryKey factory
  types/<resource>.ts                  # I-prefixed interfaces
  stores/<resource>-stores.ts          # zustand (when cross-component UI state is needed)
src/pages/<page>.tsx                   # route-level page; composes feature components
src/remote-app-routes.tsx             # route table
```

Node-definitions concretely:

```
features/node-definitions/
  components/node-definitions/         # list + (legacy) form modal
    node-definition-list.tsx
    node-definition-form-modal.tsx
    node-definition-schema-editor.tsx
  components/node-definition-builder/  # the visual builder sub-editors
    builder-header.tsx
    schema-field-list.tsx  schema-field-editor.tsx  schema-preview.tsx
    locked-defaults-editor.tsx
    input-mapping-editor.tsx  key-value-editor.tsx
    node-definition-test-tab.tsx
    worker-field-metadata.ts
  hooks/apis/node-definitions.ts
  types/node-definition.ts
pages/node-definitions.tsx             # list page
pages/node-definition-builder.tsx      # builder page (orchestrates the sub-editors)
```

### 1.3 Conventions (with the actual patterns)

**Components** — function components, default-exported, hooks at the top. Props via a local
`interface XxxProps`. Example shapes: `node-definition-list.tsx`, `schema-field-editor.tsx`.

**Naming** — components `PascalCase`; files `kebab-case.tsx`; TS interfaces `I`-prefixed
(`INodeDefinition`, `ISchemaFieldItem`); query-key objects `<resource>Key`.

**API hooks** (`hooks/apis/node-definitions.ts`) — one file per resource, all hooks named
`use<Verb><Resource>`:
```ts
const _key = queryKeyFactory("node-definitions");
export const nodeDefinitionKey = {
  ..._key,
  getAll: (params?) => [..._key.lists(), params] as const,
  getById: (id) => [..._key.details(), id] as const,
};
export const useNodeDefinitionList = (params, options) =>
  useQuery({ queryKey: nodeDefinitionKey.getAll(params),
             queryFn: () => api.get("/node-definitions", { params }), ...options });
```
- `queryFn` calls the shared `api` (`@/lib/api`) and returns the **already-unwrapped** body.
- Mutations type the error as `AxiosError<IApiErrorBody>` and take `options` minus `mutationFn`.
- Mutations carry no side-effects themselves — the **caller** wires `onSuccess`/`onError`.

**Mutations at the call site** — toast + cache invalidation live in the component:
```ts
const { mutate } = useUpdateNodeDefinition({
  onSuccess: () => { toast.success(t("notification.success"), t("…updated_successfully"));
                     queryClient.invalidateQueries({ queryKey: nodeDefinitionKey.all });
                     queryClient.invalidateQueries({ queryKey: nodepalleteKey.getAllNodePalletes() }); },
  onError:  () => toast.error(t("notification.error"), t("…update_failed")),
});
```
> Always invalidate the **node-palette** query too, so the canvas palette reflects the change.

**Toasts** — `import { toast } from "@ldc/ui/blocks/toast/toast"`, `toast.success(title, msg)`.

**Confirmation** — `useMessageBox()` returns a promise (`node-definition-list.tsx:42`); await it
before destructive actions.

**i18n** — every visible string is `t("namespace.key")`; never hard-code. Add keys to
`src/locales/en/workflow-management.json` under `node_definitions.*` / `node_definition_builder.*`.

**Styling** — Tailwind utility classes inline; conditional classes via `cn(...)` from `@ldc/ui`.
Follow the existing scale (`text-sm`, `text-xs text-muted-foreground`, `border-gray-200`, etc.).

**Pages** — wrap content in the `Page` container (`@/components/containers/page`) with
`Page.Header title=… actions=…`. Routes are registered in `src/remote-app-routes.tsx`.

**Forms via `Builder`** — declare an AutoFormField `fields[]` array (key, `outputType`,
`fieldConfig.{fieldWrapper,wrapperProps,fieldControl,controlProps,rules}`), render
`<Builder ref={builderRef} schema={{fields}} defaultValues onSubmit onValuesChange />`, and
trigger submit imperatively with `builderRef.current?.onSubmit()` from your own footer button.
See `node-definition-form-modal.tsx` and `node-definition-builder.tsx`.

**Local-vs-server state in the builder** — visual editors keep their own React state
(`useState`) of a *friendly* shape (`ISchemaFieldItem`, `ILockedDefaultEntry`, `IMappingEntry`)
and convert to/from the backend's AutoFormField/dict shapes with pure helpers co-located in the
same file (`autoFormToSchemaFields`/`schemaFieldsToAutoForm`, `lockedDefaultsToEntries`/
`entriesToLockedDefaults`, `objectToMappingEntries`/`mappingEntriesToObject`). **This
conversion-helper pattern is the core of the feature** — keep new editors in the same mold.

### 1.4 Testing

- Runner **rstest** (`pnpm test` → `rstest run`), config `@ldc/rstest-config`, DOM `happy-dom`,
  `@testing-library/react` + `@testing-library/user-event`.
- Tests sit next to the code as `*.test.ts(x)` (e.g.
  `features/workflows/utils/workflow-save-mapper.test.ts`).
- The pure conversion helpers above are the highest-value, easiest things to unit-test
  (round-trip: `entries → object → entries`).

---

## Part 2 — How the feature is built / how to extend it

### 2.1 Data flow (one picture)

```
Backend  /api/v1/node-definitions            (workflow-control-plane-service)
   │  {status:"ok", data:{…}}  ← ResponseWrapperMiddleware
   ▼
api (@/lib/api, APISdk)  → unwraps body
   ▼
hooks/apis/node-definitions.ts   useNodeDefinitionList / …ById / Create / Update / Delete / Test
   ▼
types/node-definition.ts         I-typed request/response contracts
   ▼
pages/node-definition-builder.tsx (state owner)
   ├─ General tab     → Builder (AutoFormField)
   ├─ Locked Defaults → LockedDefaultsEditor   (entries ↔ locked_defaults dict)
   ├─ Input Schema    → SchemaFieldList + SchemaPreview (items ↔ input_schema[])
   ├─ Mappings        → InputMappingEditor + KeyValueEditor (entries ↔ *_mapping dict)
   └─ Test            → NodeDefinitionTestTab (POST /{id}/test)
   ▼
on save: helpers convert friendly state → backend shapes → create/update mutation
         → invalidate definitions + node-palette queries → toast → navigate
```

The builder page (`pages/node-definition-builder.tsx`) is the **state owner**: it holds the
friendly state for every tab, hydrates it from the loaded definition (render-time
`prevDefinition` guard pattern, `:77`), and assembles the payload in `handleFormSubmit` (`:211`).

### 2.2 Contracts (`types/node-definition.ts`)

`INodeDefinition` mirrors the backend entity. Note the response wrappers:
- List → `INodeDefinitionListResponse = { status, data: { items, total } }`
- Detail/Create/Update → `INodeDefinitionDetailResponse = { ok, data }`
- Test → `ITestNodeDefinitionDataResponse = { status, data: ITestNodeDefinitionResponse }`

Keep these in sync with the backend feature reference
(`workflow-control-plane-service/docs/node-definition-feature.md`).

### 2.3 Recipe — add a new builder tab (e.g. enable the Output Schema tab)

1. **State** — add `useState` for the tab's friendly model in `node-definition-builder.tsx`
   (Output Schema already has `outputSchemaFields` + a `SchemaFieldList` block, just commented
   out at `:394` and rendered at `:485`).
2. **Hydrate** — populate it in the `prevDefinition` block (`:77`) from `definition.<field>`.
3. **Editor** — reuse an existing editor or add one under `components/node-definition-builder/`
   with co-located `xToY` / `yToX` pure helpers.
4. **Register** — add `{ id, label: t("node_definition_builder.section_<x>") }` to `sections`
   (`:390`) and a `<TabsContent value="<x>">`.
5. **Save** — include the converted value in the payload in `handleFormSubmit` (`:219`).
6. **i18n** — add the label/help keys to `workflow-management.json`.
7. **Test** — unit-test the new conversion helper round-trip.

### 2.4 Recipe — support a new worker's input_mapping

`worker-field-metadata.ts` currently only describes `http-request-worker`
(`HTTP_WORKER_FIELDS`). To make the mapping UI smart for another worker, add that worker's
field metadata (each with a `mode`: `expression` / `key_value_array` / `json_template`) and
key it by worker type, then have `InputMappingEditor` pick the list by the selected
`base_worker_id` instead of the hard-coded `HTTP_WORKER_FIELDS`. (See UI/UX doc, backlog #8.)

### 2.5 Recipe — replicate the pattern for a brand-new feature

1. `features/<feature>/types/<resource>.ts` — `I`-typed request/response contracts.
2. `features/<feature>/hooks/apis/<resource>.ts` — `queryKeyFactory` + `use*` query/mutation
   hooks calling `@/lib/api`; no side-effects inside.
3. `features/<feature>/components/...` — function components, Tailwind + `@ldc/ui`, `t(...)`.
4. `pages/<page>.tsx` — `Page` container; compose components; wire mutations with toast +
   invalidation at the call site.
5. `src/remote-app-routes.tsx` — add the route(s).
6. `src/locales/en/workflow-management.json` — add strings.
7. `*.test.ts(x)` next to any non-trivial pure logic.

### 2.6 Gotchas (carried over from the existing code)

- The **form modal** parses JSON in a swallowed `try/catch` (`node-definition-form-modal.tsx:85`)
  — invalid JSON is silently dropped. Don't copy this; validate visibly. Prefer the Builder.
- `base_worker_id` / `base_type` are **create-only** in the UI (the update payload omits them) —
  matches the backend treating the worker as fixed after creation.
- The Test tab requires a saved id (`!isNew`); new definitions must be saved before testing.
- Always invalidate `nodepalleteKey.getAllNodePalletes()` alongside `nodeDefinitionKey.all` on
  any write, or the canvas palette goes stale.
- Response shape comes from the backend `ResponseWrapperMiddleware` (`{status, data}`) — the
  `api` SDK returns the body; read `resp.data.items` etc. accordingly.
