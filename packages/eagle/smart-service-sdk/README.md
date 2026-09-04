# AI Eagle API

This service exposes a FastAPI backend for duplicate detection, search, similarity retrieval, and a pair of lightweight stub workflows for cleansing enrichment and rule suggestion.

The current API supports:

- duplicate checking with request-driven exact and fuzzy rules
- optional LLM term expansion
- vector similarity ranking
- graph-style related entity evidence
- search and similarity runtime config endpoints
- default tenant fallback when `tenant_id` is omitted
- request-driven duplicate index import jobs backed by shared `AE_*` tables
- stubbed `cleansing-enrichment` and `rule-suggestions` flows

## Run

Install dependencies and start the API:

```powershell
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8088
```

The API starts on:

```text
http://127.0.0.1:8088
```

Swagger UI is available at:

```text
http://127.0.0.1:8088/docs
```

The lightweight in-app API console is available at:

```text
http://127.0.0.1:8088/ui
```

## Current Endpoints

- `GET /api/v1/health`
- `POST /api/v1/duplicate-check`
- `POST /api/v1/import-jobs`
- `GET /api/v1/import-jobs/{job_id}`
- `POST /api/v1/cleansing-enrichment/new-record`
- `POST /api/v1/rule-suggestions`
- `POST /api/v1/search`
- `GET /api/v1/search/config`
- `PUT /api/v1/search/config`
- `POST /api/v1/material-sds-analysis`
- `GET /api/v1/material-sds-analysis/config`
- `PUT /api/v1/material-sds-analysis/config`
- `POST /api/v1/similarity`
- `GET /api/v1/similarity/config`
- `PUT /api/v1/similarity/config`
- `GET /api/v1/ui`
- `GET /api/v1/ui/search`
- `GET /api/v1/ui/similarity`
- `GET /api/v1/ui/config`
- `GET /api/v1/ui/material-sds-analysis`

## Duplicate Check

Method and path:

```text
POST /api/v1/duplicate-check
```

### Request payload

```json
{
  "record_id": "REQ-10001",
  "tenant_id": "default-tenant",
  "fields": {
    "tax_number": "TH1234567890",
    "website_url": "https://acme-industrial.com",
    "organization_name_1": "Acme Industrial Co.",
    "street": "88 Rama IV Road",
    "city": "Bangkok",
    "postal_code": "10500",
    "country": "TH",
    "first_name": "Somchai",
    "last_name": "Prasert"
  },
  "rules": [
    { "field": "tax_number", "match_type": "exact" },
    { "field": "website_url", "match_type": "exact" },
    { "field": "organization_name_1", "match_type": "fuzzy", "threshold": 0.8 },
    { "field": "street", "match_type": "fuzzy", "threshold": 0.8 }
  ]
}
```

### Request field guide

- `record_id`: your request or source record identifier
- `tenant_id`: optional; if omitted the service uses `DEFAULT_TENANT_ID`
- `fields`: dynamic field payload used to build the duplicate probe
- `rules`: request-driven exact and fuzzy match rules

### Example cURL

```bash
curl -X POST "http://127.0.0.1:8088/api/v1/duplicate-check" \
  -H "Content-Type: application/json" \
  -d '{
    "record_id": "REQ-10001",
    "fields": {
      "Material Name": "Finished Product: Wireless Earbuds",
      "Material Description": "Wireless Earbuds"
    },
    "rules": [
      { "field": "Material Name", "match_type": "exact" },
      { "field": "Material Description", "match_type": "fuzzy", "threshold": 0.8 }
    ]
  }'
```

### Example response

```json
{
  "result_id": "7f3bc95b-8e36-4b47-8a48-2e4d2481f4f6",
  "record_id": "REQ-10001",
  "tenant_id": "default-tenant",
  "score": 0.5825,
  "candidates": [
    {
      "record_id": "CR-1001",
      "tenant_id": "default-tenant",
      "source_type": "approved_file",
      "source_file_id": "019e33a5-2309-7de4-ab41-f17351df3a47_3ef5c78f-07b8-4ebc-a0a1-7c75510a62df-table_data.csv",
      "source_row_key": "2",
      "score": 0.5825,
      "exact_matches": [
        "tax_number",
        "website_url",
        "city",
        "postal_code",
        "country"
      ],
      "fuzzy_matches": {
        "organization_name_1": 0.9143,
        "street": 0.8387
      },
      "vector_score": 0.9634,
      "graph_score": 0.625,
      "matched_terms": [
        "Acme Industrial Co."
      ],
      "graph_evidence": {
        "matched_entities": [
          "acme industrial co",
          "somchai",
          "prasert"
        ],
        "relation_paths": []
      }
    }
  ]
}
```

## Search

Method and path:

```text
POST /api/v1/search
```

### Sample payload

```json
[
  { "key": "Material Name", "value": "Finished Product: Wireless Earbuds", "rule": "exact" },
  { "key": "Material Description", "value": "Wireless Earbuds", "rule": "fuzzy" }
]
```

This route performs rule-based exact and fuzzy matching over indexed structured row chunks.
Structured row chunks are searched through `AE_RAG_CHUNKS.CONTENT_JSON`, which stores both
raw `fields` and `normalized_fields`. Existing structured-row data must be reindexed after
deploying this change before `POST /api/v1/search` will return complete results.

### Search config

- `GET /api/v1/search/config`
- `PUT /api/v1/search/config`

Sample update payload:

```json
{
  "fuzzy_threshold": 0.8,
  "max_results": 10,
  "expand_terms_enabled": true
}
```

The browser demo page for this flow is available at:

```text
GET /api/v1/ui/search
```

## Similarity

Method and path:

```text
POST /api/v1/similarity
```

### Sample payload

```json
{
  "query_text": "Acme industrial supplier in Bangkok",
  "mode": "ALL",
  "tenant_id": "default-tenant",
  "top_k": 5,
  "filters": {}
}
```

### Similarity config

- `GET /api/v1/similarity/config`
- `PUT /api/v1/similarity/config`

Sample update payload:

```json
{
  "vector_top_k": 10,
  "vector_min_score": 0.45,
  "graph_seed_top_k": 8,
  "graph_neighbor_cap_per_seed": 6,
  "graph_max_relation_candidates": 24,
  "graph_max_graph_chunk_candidates": 16,
  "max_results": 10
}
```

The browser demo page for this flow is available at:

```text
GET /api/v1/ui/similarity
```

## Material SDS Analysis

Method and path:

```text
POST /api/v1/material-sds-analysis
```

Decides, per material, whether a Safety Data Sheet (SDS) is required before the
material is created. Each result carries a `decision`
(`required` / `not_required` / `needs_review`), `is_sds_required`, the matched
`hazardous_categories`, a `confidence` score, human-readable `reasons`, supporting
`evidence`, and a `method` (see below).

### Inputs

| Field | Notes |
|-------|-------|
| `material_name`, `material_type`, `material_group` | core SAP material attributes |
| `material_description`, `free_text_description` | free text scanned for hazard indicators |
| `unspsc_code`, `classification` | UNSPSC / classification code |
| `manufacturer_name`, `manufacturer_description` | manufacturer-provided details |
| `options.enable_online_search` | when `true`, the model may use an online web search |

At least one of `material_name`, `material_description`, `free_text_description`,
`manufacturer_name`, or `manufacturer_description` must be provided; otherwise the
item returns `needs_review` with `method: "insufficient_input"`.

### How a decision is made (`method`)

1. **`rules`** — a deterministic hazardous-category pre-screen (Software 5.0
   recipe) scans the combined material text for well-known hazard terms
   (solvents, fuels, corrosives, batteries, compressed gases, …). A confident hit
   returns `required` **without an LLM call**, citing the matched rule in
   `evidence`. This is skipped when `enable_online_search` is `true`.
2. **`llm`** — ambiguous materials (or any request with `enable_online_search`)
   are screened by the model. A deterministic rule hit acts as a safety floor: the
   model can never downgrade a rule-matched material to a false `not_required` —
   it is forced to `needs_review` instead.
3. **`insufficient_input`** / **`error`** — not enough text to analyze, or the
   model call failed; both return `needs_review`.

### Sample payload

```json
{
  "materials": [
    {
      "material_name": "Industrial Solvent",
      "material_type": "raw_material",
      "material_group": "cleaning_agents",
      "material_description": "Solvent based cleaning fluid",
      "free_text_description": "Highly flammable liquid used for degreasing.",
      "unspsc_code": "47131800",
      "manufacturer_name": "Acme Chemicals",
      "manufacturer_description": "Aliphatic solvent blend",
      "options": {
        "enable_online_search": false
      }
    }
  ]
}
```

### Sample response

```json
{
  "results": [
    {
      "material_index": 0,
      "decision": "required",
      "is_sds_required": true,
      "hazardous_categories": ["solvents", "cleaning_agents"],
      "confidence": 0.9,
      "reasons": ["SDS required by deterministic hazardous-category rule pre-screen."],
      "evidence": ["Deterministic rule match: hazardous category 'solvents' (matched term: 'solvent')."],
      "web_search_used": false,
      "web_search_status": "not_requested",
      "method": "rules"
    }
  ]
}
```

The browser demo page for this flow is available at:

```text
GET /api/v1/ui/material-sds-analysis
```

### Configurable SDS sources (SA-1474)

The material SDS requirement check can be pointed at a curated set of trusted
sources instead of the open web. The configuration has two list-valued fields:

- `resource_urls` — authoritative reference URLs (must be `https`). These are
  surfaced to the model as authoritative references in the prompt and are cited
  in the response `evidence` when configured.
- `allowed_domains` — an allow-list of bare hostnames (e.g. `osha.gov`). When a
  request sets `options.enable_online_search = true`, the online `web_search`
  lookup is restricted to these domains (passed as `filters.allowed_domains`),
  and the applied restriction is reflected in the response `evidence`.

Load the current config (defaults are returned when nothing has been saved):

```text
GET /api/v1/material-sds-analysis/config
```

```json
{
  "resource_urls": ["https://pubchem.ncbi.nlm.nih.gov", "https://echa.europa.eu", "https://www.osha.gov"],
  "allowed_domains": ["pubchem.ncbi.nlm.nih.gov", "echa.europa.eu", "osha.gov"]
}
```

Update the config:

```text
PUT /api/v1/material-sds-analysis/config
```

```json
{
  "resource_urls": ["https://echa.europa.eu"],
  "allowed_domains": ["echa.europa.eu"]
}
```

**Validation.** URLs must be well-formed and `https`; domains must be bare hosts
(no scheme/path). Both lists are de-duplicated (domains are lower-cased) and
bounded (max 50 each). Malformed input is rejected with HTTP `422` and a clear
message. The update takes effect immediately for subsequent SDS checks.

**Scope.** The SDS source config is **global** (a single, service-wide setting),
persisted as JSON-encoded lists in the runtime-settings key-value store
(`MATERIAL_SDS_RESOURCE_URLS`, `MATERIAL_SDS_ALLOWED_DOMAINS`). It is not
per-tenant; per-tenant scoping would require extending the runtime-settings
substrate with a tenant dimension.

## API Console

The in-app showcase is split into four lightweight HTML pages:

- `GET /api/v1/ui` for the landing page
- `GET /api/v1/ui/search` for the search request demo
- `GET /api/v1/ui/similarity` for the similarity request demo
- `GET /api/v1/ui/config` for search and similarity config load/update flows
- `GET /api/v1/ui/material-sds-analysis` for batch SDS screening requests

The config page calls these runtime config endpoints directly:

- `GET /api/v1/search/config`
- `PUT /api/v1/search/config`
- `GET /api/v1/similarity/config`
- `PUT /api/v1/similarity/config`

## Cleansing Enrichment

Method and path:

```text
POST /api/v1/cleansing-enrichment/new-record
```

This is currently a stub flow that adds an LLM-generated `enrichment_note` and persists the result.

### Sample payload

```json
{
  "record_id": "REQ-10001",
  "values": {
    "organization_name_1": "Acme Industrial Co."
  }
}
```

## Rule Suggestions

Method and path:

```text
POST /api/v1/rule-suggestions
```

This is currently a stub flow that returns canned suggestions plus an LLM-generated rationale.

### Sample payload

```json
{
  "request_id": "RULE-001",
  "change_summary": "Require tax number and reject exact duplicate website URL"
}
```

## Health Check

You can verify the app is alive with:

```bash
curl "http://127.0.0.1:8088/api/v1/health"
```
```
{"status":"ok","service":"ai-eagle"}
```

## Matching Rules

- Exact match fields are normalized with trim and lowercase and must match character-for-character after normalization.
- Fuzzy match fields are treated as duplicates only when the fuzzy score is strictly greater than the configured threshold.
- Candidates are returned when at least one exact or fuzzy rule passes.
- Vector and graph signals improve ranking, not eligibility.

## Data Source Modes

### Local seeded mode

This is the easiest way to try the API.

- the service uses built-in sample duplicate data
- no HANA connection is required

### HANA mode

Use this when you want duplicate-check and retrieval data stored in SAP HANA.

- provide `HANA_HOST`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD`, `HANA_SCHEMA`
- optionally set `AUTO_CREATE_SCHEMA=true`
- optionally set `AUTO_SEED_DATA=true` to run the sample seed SQL on startup
- schema DDL is loaded from `deployment/sql/*.sql`
- optional sample seed SQL is loaded from `deployment/seed/*.sql`

## Important Environment Variables

- `DEFAULT_TENANT_ID`: fallback tenant when request payload omits `tenant_id`
- `AUTO_CREATE_SCHEMA`: auto-create HANA schema tables on startup
- `AUTO_SEED_DATA`: run optional sample HANA seed SQL on startup
- `OPENAI_API_KEY`: optional; if missing the app uses the local stub LLM client
- `OPENAI_MODEL`: OpenAI model name
- `FILE_SERVICE_BASE_URL`: file-service base URL used by duplicate index imports
- `HANA_HOST`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD`, `HANA_SCHEMA`: HANA connection settings

## Configurable ingest fields (SA-1451)

By default every column of an imported row feeds both the vector embedding and
the graph-entity extractor. SA-1451 lets an operator narrow that per tenant:

- **Embedding fields** — the ordered subset of columns composed into the text
  that gets embedded. Fewer, more-signal fields → tighter similarity/dedup.
- **Graph-entity fields** — the subset of columns fed to the graph extractor,
  independent of the embedding selection.

An **empty** selection means "use every field" — the pre-SA-1451 behavior, so
existing tenants are unaffected until they configure a selection.

### API

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/ingest-fields/config` | Read the current selection |
| `PUT`  | `/api/v1/ingest-fields/config` | Set `embedding_fields` / `graph_entity_fields` (optionally validate against `available_fields`) |
| `POST` | `/api/v1/ingest-fields/reindex` | Rebuild the given `file_ids` under the new selection |

```jsonc
// PUT /api/v1/ingest-fields/config
{
  "embedding_fields": ["Material Name", "Material Description"],
  "graph_entity_fields": ["Manufacturer", "Material Group"],
  "available_fields": ["Material Name", "Material Description", "Manufacturer", "Material Group"]
}
```

Unknown fields (not in `available_fields`, when supplied) are rejected with
`422`. A `PUT` invalidates the config cache so the **next** ingest uses the new
selection immediately; already-indexed rows keep their old vectors until you
call `reindex`. The selection is also editable from the UI console at
`/ui/config` ("Ingest field selection").

### Where it plugs in

- `RuntimeConfigurationService.get_field_config` / `update_field_config`
  (persisted as JSON arrays in runtime settings).
- `field_configuration/field_selection.py` — the single pure helper both ingest
  paths share (`BackgroundJobRunner` and the governance `UploadImportFileUseCase`)
  so embedding/graph composition can never drift between them.

## Testing

```bash
pytest
```
