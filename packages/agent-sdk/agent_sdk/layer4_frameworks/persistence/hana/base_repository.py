"""Base HANA repository providing common SQL helpers.

Ported from orchestrator/executor ``BaseHanaRepository`` pattern so SDK
consumers can extend the same contract without duplicating boilerplate.
"""

import dataclasses
import json
from typing import Any, Dict, List, Protocol


class IDatabaseConnection(Protocol):
    def execute_query(self, sql: str, params: tuple = ()) -> List[Dict]: ...
    def execute_write(self, sql: str, params: tuple = ()) -> int: ...


class _DataclassEncoder(json.JSONEncoder):
    """JSON encoder that handles sets and other non-standard types."""

    def default(self, o: Any) -> Any:
        if isinstance(o, set):
            return list(o)
        return super().default(o)


class BaseHanaRepository:
    """Base class for all HANA repository adapters."""

    def __init__(self, db: IDatabaseConnection, table_name: str) -> None:
        self._db = db
        self._table = table_name

    def _execute_query(self, sql: str, params: tuple = ()) -> List[Dict]:
        return self._db.execute_query(sql, params)

    def _execute_write(self, sql: str, params: tuple = ()) -> int:
        return self._db.execute_write(sql, params)

    @staticmethod
    def _to_json(obj: Any) -> str:
        if obj is None:
            return "null"
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return json.dumps(dataclasses.asdict(obj), cls=_DataclassEncoder)
        if isinstance(obj, list):
            items = []
            for item in obj:
                if dataclasses.is_dataclass(item) and not isinstance(item, type):
                    items.append(dataclasses.asdict(item))
                else:
                    items.append(item)
            return json.dumps(items, cls=_DataclassEncoder)
        if isinstance(obj, dict):
            return json.dumps(obj, cls=_DataclassEncoder)
        return json.dumps(obj, cls=_DataclassEncoder)

    @staticmethod
    def _from_json(s: Any) -> Any:
        if s is None:
            return None
        if isinstance(s, str):
            try:
                return json.loads(s)
            except (json.JSONDecodeError, TypeError):
                return s
        return s

    def _upsert(
        self, columns: List[str], values: tuple, key_column: str = "ID"
    ) -> None:
        src_cols = ", ".join(f":p{i} AS {col}" for i, col in enumerate(columns))
        on_clause = f"target.{key_column} = src.{key_column}"
        update_sets = ", ".join(
            f"target.{col} = src.{col}" for col in columns if col != key_column
        )
        insert_cols = ", ".join(columns)
        insert_vals = ", ".join(f"src.{col}" for col in columns)

        sql = (
            f"MERGE INTO {self._table} AS target "
            f"USING (SELECT {src_cols} FROM DUMMY) AS src "
            f"ON {on_clause} "
            f"WHEN MATCHED THEN UPDATE SET {update_sets} "
            f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
        )
        self._execute_write(sql, values)
