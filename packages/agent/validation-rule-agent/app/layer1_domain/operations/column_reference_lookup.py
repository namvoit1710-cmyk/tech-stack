OPERATION = {
  "scope": "column",
  "type": "reference_lookup",
  "description": (
    "Flag rows whose column value is present in (or absent from) a materialized "
    "reference key-set — a de-duplicated list of known values such as dangerous / "
    "prohibited / blacklisted substances. Use this for requests like 'no dangerous "
    "things allowed', 'no hazardous/prohibited/banned materials', 'value must not be "
    "in the blocklist', or the inverse 'value must exist in the allowed/reference list'. "
    "For dangerous-substance checks the reference is the PubChem GHS compound key-set "
    "registered in governance reference_data with purpose 'dangerously_validate'. "
    "Prefer this over a Polars 'expression' whenever the check is membership against an "
    "external/known list rather than an in-row pattern: it is a single vectorized hash "
    "membership (O(n + m)), not a per-cell scan. Set 'violate_when' to 'in_set' (default) "
    "to flag values found in the key-set (e.g. dangerous substances are NOT allowed), or "
    "'not_in_set' to flag values missing from an allow-list. Provide the reference via "
    "'reference_id' (a governance reference_data id) or 'reference_source' (a file "
    "id/path of the key-set parquet)."
  ),
  "example": {
    "rule_name": "No dangerous substances allowed",
    "type": "reference_lookup",
    "params": {
      "columns": ["Material"],
      "reference_id": "dangerously_validate",
      "reference_format": "parquet",
      "key_column": "key",
      "violate_when": "in_set"
    },
    "error_message": "Value is a known dangerous substance (PubChem GHS) and is not allowed"
  }
}
