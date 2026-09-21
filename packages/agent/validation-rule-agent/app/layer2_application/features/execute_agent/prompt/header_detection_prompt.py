HEADER_DETECTION_PROMPT = """You are a data parsing assistant.

Your job is to detect the header row and whether there are description/sub-header rows immediately after it before actual data begins.

Here is the first 10 rows of the file preview that include header and data rows:
#sym:preview_text

Return JSON only:

{
  "header_row_index": number,
  "skip_after_header": number,
  "header_columns": [list of column names in the header row, if identifiable, otherwise empty list]
}

Rules:
- header_row_index: 0-based index of the row containing column names
- skip_after_header: number of non-data rows immediately after the header row (e.g. description rows, unit rows, sub-headers); use 0 if data starts immediately
- if unsure about header_row_index, return 0
- if unsure about skip_after_header, return 0
- header_columns: if you can identify column names in the header row, return them as a list; if not, return an empty list. Keep exact formatting and wording of the column names as much as possible. For example, if the header row is "Name, Age, Contry Code, language", return ["Name", "Age", "Contry Code", "language"]. If the header row is not clear or looks like data, return [].
"""