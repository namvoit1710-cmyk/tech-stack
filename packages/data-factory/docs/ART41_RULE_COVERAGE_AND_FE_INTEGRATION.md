# ART41.00 Rule Coverage and Frontend Integration

Audience: the frontend team integrating mass-upload validation.

This describes what the Data Factory returns for a mass-upload workbook, which
of the rules configured on ART41.00 it can run, and how to reproduce every
result yourself against the deployed tenant.

---

## 1. What changed, in one paragraph

A violation used to say only *which column* failed, named by its spreadsheet
label (`Base Unit (String)`). It now also names the **field path the template is
configured against** (`GENERALDATA.baseUnit`), which is what the rules are
written in and what errors should be keyed on. Filter Rules can now run at all,
and they report the source field that constrained the target. Nothing was
removed: every field that used to be in the response is still there.

---

## 2. The violation object

Each result row carries a `validation_errors` column holding a **JSON array**.
One entry per rule that the row broke.

### Every rule type

```json
{
  "rule":    "LENGTH_40",
  "field":   "Base Unit (String)",
  "path":    "GENERALDATA.baseUnit",
  "message": "The field length cannot exceed 40 characters",
  "value":   "PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPP"
}
```

| key | meaning |
|---|---|
| `rule` | the rule id as configured (`LENGTH_40`) |
| `field` | the column label exactly as it appears in the workbook |
| `path` | `SECTION.fieldName` — key your error rendering on this |
| `message` | the configured error message |
| `value` | what the cell actually held, as text |

### Filter Rules carry four more keys

```json
{
  "rule":        "FILTER_PURCHORG_SUPPLIER",
  "field":       "Supplier (String)",
  "path":        "PURCHASING.supplier",
  "message":     "Supplier is not valid for the selected purchasing organization",
  "value":       "SUPPLIER_INVALID",
  "ruleType":    "FILTER",
  "sourceField": "Purchasing Organization (String)",
  "sourcePath":  "PURCHASING.purchasingOrganization",
  "severity":    "ERROR"
}
```

`sourcePath` matters for the UI: an invalid supplier is only invalid **for the
chosen purchasing organisation**. An error that names only the target cannot
tell the user which of the two fields to change.

`severity` is `ERROR`, `WARNING`, or `INFO`. It is sent lower-case in the rule
config and always returned upper-case.

### Where `path` comes from, and when to distrust it

A workbook writes the section once across a merged range on the banner row, and
the column label underneath:

```
row 1:  ['3', 'Article', '', '', '', '', 'General data', '', ...]
row 2:  ['ItemID (Integer)', ...,                'Base Unit (String)']
```

Forward-filling row 1 gives the section; the label is stripped of its type hint
and camel-cased. `Base Unit (String)` becomes `GENERALDATA.baseUnit`.

**The camel-casing is a heuristic.** It is exact for `Base Unit`, `Article
Type`, `Merchandise Category`. It guesses for `Haz. Matl No.` (yields
`hazMatlNo`) and `X-Plant Status Valid From` (yields `xPlantStatusValidFrom`).
If you have the template field metadata, pass `field_path_overrides` — a map of
column label to path — and those win outright:

```json
"field_path_overrides": {
  "Haz. Matl No. (String)": "GENERALDATA.hazardousMaterialNumber"
}
```

**Put them on the table, not the bundle, whenever a label is ambiguous.** A
column label is not unique across a workbook: `Supplier (String)` is
`PURCHASING.supplier` on ARTMasterPurchasing and `SALESPRICE.supplier` on
ARTMasterSalesPrice. A bundle-wide map has one entry per label, so the last
table to mention it would silently win. Table-level overrides are merged over
the bundle-level ones:

```jsonc
{
  "field_path_overrides": { "Article (String)": "ARTICLE.article" },   // applies everywhere
  "tables": [
    { "name": "ARTMasterPurchasing",
      "field_path_overrides": { "Supplier (String)": "PURCHASING.supplier" } },
    { "name": "ARTMasterSalesPrice",
      "field_path_overrides": { "Supplier (String)": "SALESPRICE.supplier" } }
  ]
}
```

`field` is never a guess. If `path` looks wrong for a column, fall back to
`field` and send us the label.

---

## 3. Row identity — read this before wiring anything

Three different things in the result look like a row id. They are not
interchangeable.

| column | who produces it | use it for |
|---|---|---|
| `__row_id` | the File Service, on upload | **the system key.** Edits and mutations are keyed on this |
| `sourceRow` | `add_technical_fields`, during transform | pointing the user at a line in their spreadsheet |
| `ItemID (Integer)` | the workbook itself | business data — treat it as a normal column |

`sourceRow` honours `source_row_offset`, so it counts the banner rows and
matches what the user sees in Excel. For ART41.00 the header is row 3 and data
starts at row 4, so `source_row_offset: 4`.

> **Known sharp edge.** Calling `POST /api/v1/validation` directly on a raw
> `.xlsx` produces a `__row_id` numbered over the *filtered* result rather than
> the source. Use the bundle endpoint (below), which transforms to CSV first and
> produces correct ids. This is tracked separately.

---

## 4. Which rules run

18 rules are assigned to ART41.00. **16 run.**

| rule | type | sheet | status |
|---|---|---|---|
| `LENGTH_40` | length | ArticleMaster | runs |
| `FILTER_CHARC_VALUE` | filter | ARTMasterCharac | runs |
| `FILTER_CLASS_CHARC` | filter | ARTMasterCharac | runs |
| `FILTER_CLASSTYPE_CLASS` | filter | ARTMasterClass | runs |
| `FILTER_COUNTRY_REGION` | filter | ArticleMaster | runs |
| `FILTER_PURCHORG_SUPPLIER` | filter | ARTMasterPurchasing | runs |
| `FILTER_SALESPRICE_SUPPLIER` | filter | ARTMasterSalesPrice | runs |
| `ASSIGN_VALUATION_CLASS` | assignment | ArticleMaster | runs |
| `ASSIGN_VALUATION_CLASS_VAL` | assignment | ARTMasterValuation | runs (cross-table) |
| `ASSIGN_OVERDELIVERY` | assignment | ArticleMaster | runs |
| `ASSIGN_REMINDER_1/2/3` | assignment | ArticleMaster | runs |
| `ASSIGN_EFFECTIVE_PRICE` | assignment | ARTMasterPurchasing | runs |
| `ASSIGN_DIST_CHANNEL` | assignment | ARTMasterSD | runs |
| `ARTM_FOOD` | derive | ARTMasterPurchasing | runs (cross-table) |
| `ASSIGN_UNDERDELIVERY` | — | — | **cannot run**: the workbook has no underdelivery tolerance column |
| `INVISIBLE_BASICTEXT` | — | — | **cannot run**: section visibility is not a row rule |

### Filter and Assignment need a mapping file

The template screen shows *which* fields relate, never *which values* are
permitted. So the mapping arrives as data: a two-column CSV of
source value to target value, uploaded to the File Service like any other file,
and referenced by `mapping_source`.

- **`assignment`** with `"mode": "match"` — the target must equal the one value
  the mapping gives for that source.
- **`filter`** — the target must be *one of* the values the mapping lists for
  that source.

A source value the mapping has never seen is not judged: that is a
`reference_lookup` question about the source, and failing it in two places would
report one problem twice. A null target is never a violation either — absence is
what `required` is for.

---

## 5. Calling it

Use **`POST /api/v1/bundle/transform-validate`**. One call cuts the workbook
into its logical tables, transforms each, and validates each. Field paths are
derived automatically here.

```
https://smdg-ai-tenant-1-data-factory.cfapps.br10.hana.ondemand.com
```

```jsonc
{
  "source_file_id": "<file_id of the uploaded .xlsx>",
  "file_format": "xlsx",
  "output_format": "csv",
  "default_header_row": 2,          // 0-indexed: row 3 in Excel
  "tables": [
    {
      "name": "ArticleMaster",
      "sheet_names": ["ArticleMaster"],
      "header_row": 2,
      "rules": [
        { "type": "add_technical_fields",
          "params": { "object_id": "OBJ-ART41", "req_id": "CR-0007",
                      "task_id": "T-1", "status": "10", "modify_type": "C",
                      "table_name": "ArticleMaster",
                      "source_row": "sourceRow", "source_row_offset": 4 } }
      ],
      "validation_rules": [
        { "rule_name": "LENGTH_40", "type": "length",
          "error_message": "The field length cannot exceed 40 characters",
          "params": { "columns": ["Base Unit (String)"], "max": 40 } }
      ]
    },
    {
      "name": "ARTMasterPurchasing",
      "sheet_names": ["ARTMasterPurchasing"],
      "header_row": 2,
      "depends_on": ["ArticleMaster"],
      "rules": [
        // the ARTM_FOOD condition lives on ArticleMaster, so join it in...
        { "type": "join_reference",
          "params": { "table": "ArticleMaster", "file_format": "csv",
                      "on": ["ItemID (Integer)"],
                      "columns": ["Merchandise Category (String)"], "how": "left" } },
        { "type": "derive",
          "params": { "when": { "column": "Merchandise Category (String)",
                                "operator": "EQUAL", "value": "FOOD" },
                      "then": [ { "column": "Purchasing Info Record (String)",
                                  "value": "PC" } ] } },
        // ...and drop it again, so the child table does not gain a parent column
        { "type": "drop_columns",
          "params": { "columns": ["Merchandise Category (String)"] } }
      ],
      "validation_rules": [
        { "rule_name": "FILTER_PURCHORG_SUPPLIER", "type": "filter",
          "error_message": "Supplier is not valid for the selected purchasing organization",
          "params": { "source_column": "Purchasing Organization (String)",
                      "target_column": "Supplier (String)",
                      "mapping_source": "<file_id of the mapping csv>",
                      "mapping_format": "csv", "severity": "error" } }
      ]
    }
  ]
}
```

### Reading the response

```jsonc
{
  "tables": [
    {
      "name": "ArticleMaster",
      "success": true,
      "total_rows": 506,
      "output_file_id": "...",          // the transformed table
      "validation": {
        "total_rows": 506,
        "invalid_rows": 2,
        "result_file_id": "...",
        "result_file_url": "..."        // GET this, parse as CSV
      }
    }
  ]
}
```

> **HTTP 200 does not mean every table succeeded.** The bundle returns 200 with
> per-table `success` flags and only 400s when *nothing* ran. Check each
> `tables[].success`, not just the status code.

Download `validation.result_file_url` and parse it as CSV. In `errors_only`
mode (the default) it contains only rows that broke at least one rule.

### Cross-table rules

A rule whose condition lives on another sheet needs three transform rules in
order: `join_reference` to bring the column in, the rule itself, then
`drop_columns` to remove it. Name the earlier table with `"table":
"ArticleMaster"` rather than a file id — the bundle resolves it, because the id
does not exist until that table has run. List it in `depends_on` and put it
earlier in `tables`.

---

## 6. Testing it yourself

### Fixtures

Two workbooks, same 500 articles:

- `ART41.00_500_valid_rules.xlsx` — satisfies every rule
- `ART41.00_500_invalid_rules.xlsx` — the same data with deliberate violations

The valid one is also where the **value mappings come from**. Its distinct
(source, target) pairs are the permitted set by construction, so you can
generate every `mapping_source` file from it rather than sourcing them
elsewhere.

### The expected result, by ItemID

Assert on these, not on counts — a count can match by accident.

| rule | ItemIDs flagged |
|---|---|
| `LENGTH_40` | 7, 8 |
| `FILTER_CHARC_VALUE` | 19 |
| `FILTER_COUNTRY_REGION` | 9 |
| `FILTER_PURCHORG_SUPPLIER` | 10, 20 |
| `FILTER_SALESPRICE_SUPPLIER` | 10, 20 |
| `ASSIGN_OVERDELIVERY` | 13 |
| `ASSIGN_REMINDER_1 / 2 / 3` | 13 |
| `ASSIGN_EFFECTIVE_PRICE` | 7, 14, 17 |
| `ASSIGN_DIST_CHANNEL` | 15 |
| `ARTM_FOOD` | 7, 17 rewritten from `WRONG` to `PC` |
| `FILTER_CLASS_CHARC`, `FILTER_CLASSTYPE_CLASS`, `ASSIGN_VALUATION_CLASS`, `ASSIGN_VALUATION_CLASS_VAL` | none — these check for false positives |

ItemID 7 appears under three different rules, which is the useful case: one row
carrying several unrelated violations.

### Upload, run, clean up

```bash
FS=https://smdg-ai-tenant-1-file-service.cfapps.br10.hana.ondemand.com
DF=https://smdg-ai-tenant-1-data-factory.cfapps.br10.hana.ondemand.com

# 1. upload the workbook
curl -s -F "file=@ART41.00_500_invalid_rules.xlsx" $FS/api/v1/upload

# 2. upload a mapping (two columns: source label, target label)
printf 'Purchasing Organization (String),Supplier (String)\n0001,100008\n1000,109362\n2000,200001\n' > map.csv
curl -s -F "file=@map.csv" $FS/api/v1/upload

# 3. run the bundle with the payload from section 5
curl -s -X POST $DF/api/v1/bundle/transform-validate \
     -H 'Content-Type: application/json' -d @payload.json

# 4. fetch the result file named by validation.result_file_url, then tidy up
curl -s -X DELETE $FS/api/v1/files/<file_id>
```

Please delete what you upload — the tenant file store is shared.

### The whole thing, in one command

The harness that produced the table above ships with the repo:

```powershell
$env:ART41_WORKBOOK_DIR = "C:\path\holding\the\two\xlsx"
python tests/e2e/art41/run_all_rules.py
```

It mines the mappings, builds the bundle, scores every rule by ItemID, and
deletes everything it uploaded. Exit code 0 only when all 34 checks pass. See
[`tests/e2e/art41/README.md`](../tests/e2e/art41/README.md).

Point it elsewhere with `ART41_FILE_SERVICE` and `ART41_DATA_FACTORY`.

### Unit tests

```powershell
cd apps/backend/data-factory
python -m pytest tests/unit -q
```

`tests/unit/test_29_field_paths_filter_and_cross_table_derive.py` covers the
banner parsing, the Filter Rule shape, and cross-table derivation.

---

## 7. Caveats worth knowing

- **Numbers compare by value, codes compare as text.** A CSV drops the trailing
  zero of a whole float, so a mapping carrying `8.0` and a column carrying `8`
  are reconciled automatically. A value with a leading zero is deliberately
  *not* treated as a number — `0001` is a purchasing organisation, not the
  number one — so codes keep their exact spelling. The violation always reports
  the value as the file spelled it; only the comparison is normalised.
- **`ARTM_FOOD` uses a stand-in.** The rule is configured against
  `ArticleMaster.articleGroup`, which this workbook does not have. `Merchandise
  Category` is used instead — the same business concept, not literally the same
  field.
- **Section names come from the workbook, not the doc.** The banner says
  `Purchasing value`, so the path is `PURCHASINGVALUE.*` where the doc writes
  `ARTMASTERPURVALUE.*`. Use `field_path_overrides` if you need the doc spelling.
- **A long bundle is a long request.** 23 tables of ~500 rows takes about 90
  seconds in one call. Check your gateway timeout before running the full
  template.
