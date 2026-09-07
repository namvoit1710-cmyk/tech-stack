import polars as pl
import urllib.parse
import operator
import re
from typing import Dict, Any, Optional
from app.layer2_application.interfaces.odata_parser_interface import IODataParser


class PolarsODataProvider(IODataParser):
    def __init__(self):
        self.operator_map = {
            'eq': operator.eq, 'ne': operator.ne, 'gt': operator.gt,
            'ge': operator.ge, 'lt': operator.lt, 'le': operator.le,
            'and': operator.and_, 'or': operator.or_,
        }

    def parse_and_format(self, df: pl.DataFrame, odata_query: str) -> Dict[str, Any]:
        result_df = df
        params = self._parse_query_string(odata_query)

        total_count = len(df)

        # 1. Filter
        filter_str = params.get('$filter') or params.get('filter')
        if filter_str:
            filter_expr = self._parse_filter(filter_str, df)
            if filter_expr is not None:
                result_df = result_df.filter(filter_expr)
            elif self._references_missing_column(filter_str, df):
                result_df = df.head(0)

        filtered_count = len(result_df)

        # 2. OrderBy
        orderby_str = params.get('$orderby') or params.get('orderby')
        if orderby_str:
            columns, descending = self._parse_orderby(orderby_str, df)
            if columns:
                result_df = result_df.sort(columns, descending=descending)

        # 3. Skip & Top (Pagination)
        skip = int(params.get('$skip') or params.get('skip', 0))
        if skip > 0:
            result_df = result_df.slice(skip)

        top = int(params.get('$top') or params.get('top', 0))
        if top > 0:
            result_df = result_df.head(top)

        # 4. Select
        select_str = params.get('$select') or params.get('select')
        if select_str and select_str.strip() != "*":
            columns = [c.strip() for c in select_str.split(',') if c.strip() in result_df.columns]
            if columns:
                result_df = result_df.select(columns)

        # Handle $count parameter
        count_param = params.get('$count') or params.get('count')
        include_count = count_param and count_param.lower() == 'true'

        result = {
            "data": result_df.to_dicts(),
            "count": filtered_count,
            "total_count": filtered_count
        }

        if include_count:
            result['@odata.count'] = filtered_count

        return result

    def _parse_query_string(self, query_string: str) -> Dict[str, str]:
        params = {}
        if not query_string:
            return params
        try:
            query_string = urllib.parse.unquote_plus(query_string)
        except Exception:
            pass
        for part in query_string.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k.strip()] = v.strip()
        return params

    def _parse_filter(self, filter_str: str, df: pl.DataFrame) -> Optional[pl.Expr]:
        """Parse OData filter string into Polars expression, supporting OR conditions and function calls."""
        try:
            # Handle OR conditions first
            or_parts = self._split_by_operator(filter_str, ' or ')
            if len(or_parts) > 1:
                or_exprs = []
                for part in or_parts:
                    part_expr = self._parse_filter(part, df)
                    if part_expr is not None:
                        or_exprs.append(part_expr)
                if or_exprs:
                    result_expr = or_exprs[0]
                    for expr in or_exprs[1:]:
                        result_expr = result_expr | expr
                    return result_expr
                return None

            # Handle AND conditions
            and_parts = self._split_by_operator(filter_str, ' and ')
            if len(and_parts) > 1:
                and_exprs = []
                for part in and_parts:
                    part_expr = self._parse_filter(part, df)
                    if part_expr is not None:
                        and_exprs.append(part_expr)
                if and_exprs:
                    result_expr = and_exprs[0]
                    for expr in and_exprs[1:]:
                        result_expr = result_expr & expr
                    return result_expr
                return None

            # Handle function calls like contains(field, 'value')
            func_match = re.match(r'^(\w+)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*,\s*[\'"]([^\'"]*)[\'"]\s*\)$', filter_str.strip())
            if func_match:
                func_name = func_match.group(1).lower()
                field_name = func_match.group(2)
                search_value = func_match.group(3)

                if field_name not in df.columns:
                    return None

                if func_name == 'contains':
                    return pl.col(field_name).str.contains(search_value, literal=True)
                elif func_name == 'startswith':
                    return pl.col(field_name).str.starts_with(search_value)
                elif func_name == 'endswith':
                    return pl.col(field_name).str.ends_with(search_value)
                elif func_name == 'tolower':
                    return pl.col(field_name).str.to_lowercase().str.contains(search_value.lower(), literal=True)
                elif func_name == 'toupper':
                    return pl.col(field_name).str.to_uppercase().str.contains(search_value.upper(), literal=True)
                elif func_name == 'length':
                    return pl.col(field_name).str.len_chars() == int(search_value)

            # Handle simple field op value patterns
            for op_name, op_func in self.operator_map.items():
                if op_name in ['and', 'or']:
                    continue
                match = re.match(rf'^([\w\.]+)\s+{op_name}\s+(.+)$', filter_str.strip(), re.IGNORECASE)
                if match:
                    col, val = match.group(1).strip(), match.group(2).strip()
                    if col not in df.columns:
                        return None
                    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                        val = val[1:-1]
                    elif val.lower() == 'null':
                        val = None
                    else:
                        try:
                            val = float(val) if '.' in val else int(val)
                        except Exception:
                            pass

                    if val is None:
                        return pl.col(col).is_null() if op_name == 'eq' else pl.col(col).is_not_null()
                    return op_func(pl.col(col), val)

            return None
        except Exception:
            return None

    def _references_missing_column(self, filter_str: str, df: pl.DataFrame) -> bool:
        or_parts = self._split_by_operator(filter_str, ' or ')
        if len(or_parts) > 1:
            return any(self._references_missing_column(part, df) for part in or_parts)

        and_parts = self._split_by_operator(filter_str, ' and ')
        if len(and_parts) > 1:
            return any(self._references_missing_column(part, df) for part in and_parts)

        func_match = re.match(r'^(\w+)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*,\s*[\'"]([^\'"]*)[\'"]\s*\)$', filter_str.strip())
        if func_match:
            return func_match.group(2) not in df.columns

        for op_name in self.operator_map:
            if op_name in ['and', 'or']:
                continue
            match = re.match(rf'^([\w\.]+)\s+{op_name}\s+(.+)$', filter_str.strip(), re.IGNORECASE)
            if match:
                return match.group(1).strip() not in df.columns

        return False

    def _split_by_operator(self, expr: str, operator: str) -> list[str]:
        """Split expression by operator, respecting parentheses and quotes."""
        parts = []
        current = ''
        depth = 0
        in_quotes = False
        quote_char = ''

        i = 0
        while i < len(expr):
            char = expr[i]

            # Handle quotes
            if (char == '"' or char == "'") and (i == 0 or expr[i-1] != '\\'):
                if not in_quotes:
                    in_quotes = True
                    quote_char = char
                elif char == quote_char:
                    in_quotes = False
                    quote_char = ''
                current += char
                i += 1
                continue

            # Handle parentheses
            if char == '(' and not in_quotes:
                depth += 1
                current += char
                i += 1
                continue

            if char == ')' and not in_quotes:
                depth -= 1
                current += char
                i += 1
                continue

            # Check for operator
            if expr[i:i+len(operator)] == operator and depth == 0 and not in_quotes:
                parts.append(current.strip())
                current = ''
                i += len(operator)
                continue

            current += char
            i += 1

        if current.strip():
            parts.append(current.strip())

        return parts if parts else [expr]

    def _parse_orderby(self, orderby_str, df):
        cols, desc = [], []
        for part in orderby_str.split(','):
            p = part.strip()
            if not p:
                continue
            d = False
            if p.lower().endswith(' desc'):
                d, p = True, p[:-5].strip()
            elif p.lower().endswith(' asc'):
                p = p[:-4].strip()
            if p in df.columns:
                cols.append(p)
                desc.append(d)
        return cols, desc
