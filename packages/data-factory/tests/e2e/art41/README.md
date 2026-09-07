# ART41.00 live rule coverage

Runs every rule assigned to the ART41.00 template against a deployed tenant and
scores the result per rule.

```powershell
$env:ART41_WORKBOOK_DIR = "C:\path\holding\the\two\xlsx"
python tests/e2e/art41/run_all_rules.py
```

Optional: `ART41_FILE_SERVICE` and `ART41_DATA_FACTORY` to point somewhere other
than tenant-1.

Exit code is 0 only when every check passes.

## What it needs

Two workbooks, same 500 articles, in `ART41_WORKBOOK_DIR`:

| file | role |
|---|---|
| `ART41.00_500_valid_rules.xlsx` | satisfies every rule — the source of the value mappings |
| `ART41.00_500_invalid_rules.xlsx` | the same data with deliberate violations — the thing under test |

They are not in the repo: they are ~860KB each and carry customer-shaped data.

## Why two workbooks

The rule trace names *which* fields relate — article type decides valuation
class — but never *which values* are permitted. The template screen does not
show the table behind the relationship, and that table is what a Filter or
Assignment rule needs in order to run at all.

The valid workbook is that table. 500 rows that satisfy every assigned rule
means the distinct (source, target) pairs it contains are the permitted set by
construction. The runner mines them, uploads each as a mapping file, and points
the rules at it.

The check that this is the real mapping and not an artefact of the sample: apply
the mined pairs to the *invalid* workbook and see whether it flags the rows that
were deliberately broken. It does, and the runner asserts exactly which ones.

## Why it asserts ItemIDs and not counts

A count can match by accident. Twelve distinct ItemID sets cannot:

| rule | ItemIDs |
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
| `ARTM_FOOD` | 7, 17 rewritten `WRONG` → `PC` |

Four rules are clean in the invalid workbook — `FILTER_CLASS_CHARC`,
`FILTER_CLASSTYPE_CLASS`, `ASSIGN_VALUATION_CLASS`,
`ASSIGN_VALUATION_CLASS_VAL`. Asserting they flag *nothing* is the
false-positive half of the test, and is why they are still listed.

ItemID 7 appears under three unrelated rules, which is the case worth having:
one row carrying several different violations.

## The two that cannot run

Named in the output rather than skipped:

- `ASSIGN_UNDERDELIVERY` — the workbook has `Overdeliv. Tolerance` and
  `Unltd Overdelivery`, but no underdelivery tolerance column at all.
- `INVISIBLE_BASICTEXT` — section visibility is not a row rule, and `userID` is
  not in the workbook either.

## What it exercises

One `POST /api/v1/bundle/transform-validate` covering 7 sheets:

- **field paths** — every violation reports `SECTION.fieldName`, pinned per
  table via `field_path_overrides` where the banner spells a section
  differently from the template
- **filter rules** — flagged without dropping the row, naming the source that
  constrained the target
- **cross-table rules** — `ASSIGN_VALUATION_CLASS_VAL` and `ARTM_FOOD` both read
  a condition that lives on ArticleMaster, joined in on ItemID and dropped again
- **technical fields** — `objectID`, `sourceRow` offset past the banner

It deletes everything it uploads, including the files the service produced.
Please keep it that way: the tenant file store is shared.

## If it fails

The runner computes the expected result locally before calling the server, so a
failure is a difference between what the workbook says and what the service did
— not a flaky assertion. `rule_catalogue.py` holds the mapping from the trace's
field names to the workbook's column labels; if a template changes, that is the
one file to edit.
