"""Warehouse schemas: every column documented, partitioned on event_ts, no row-data columns."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMAS = Path(__file__).resolve().parents[2] / "infra" / "terraform" / "gcp" / "bigquery_schemas"
TYPES = {"STRING", "INTEGER", "NUMERIC", "BOOLEAN", "TIMESTAMP", "JSON"}
MODES = {"REQUIRED", "NULLABLE"}
# Invariant 4 / ADR-004: nothing in the warehouse may hold a user's row values.
ROW_DATA_WORDS = ("sample", "row_value", "row_data", "rows")


def _schema(table: str) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = json.loads((SCHEMAS / f"{table}.json").read_text("utf-8"))
    return fields


def test_every_declared_table_has_exactly_one_schema(terraform: Callable[[str], Any]) -> None:
    declared = set(terraform("infra/terraform/gcp").locals()["warehouse_tables"])
    on_disk = {path.stem for path in SCHEMAS.glob("*.json")}
    assert declared == on_disk


def test_every_column_is_documented_and_typed() -> None:
    for path in SCHEMAS.glob("*.json"):
        names = []
        for field in _schema(path.stem):
            names.append(field["name"])
            assert field["type"] in TYPES, f"{path.stem}.{field['name']}"
            assert field["mode"] in MODES, f"{path.stem}.{field['name']}"
            assert len(field["description"]) >= 15, f"{path.stem}.{field['name']} is undocumented"
        assert len(names) == len(set(names)), f"{path.stem} has duplicate columns"


def test_every_table_is_an_event_stream_partitioned_on_event_ts() -> None:
    for path in SCHEMAS.glob("*.json"):
        first_two = [(f["name"], f["type"], f["mode"]) for f in _schema(path.stem)[:2]]
        assert first_two == [
            ("event_id", "STRING", "REQUIRED"),
            ("event_ts", "TIMESTAMP", "REQUIRED"),
        ], path.stem


def test_clustering_columns_exist(terraform: Callable[[str], Any]) -> None:
    for table, spec in terraform("infra/terraform/gcp").locals()["warehouse_tables"].items():
        columns = {field["name"] for field in _schema(table)}
        assert set(spec["clustering"]) <= columns, table


def test_no_column_can_hold_user_row_data() -> None:
    for path in SCHEMAS.glob("*.json"):
        for field in _schema(path.stem):
            assert not any(word in field["name"] for word in ROW_DATA_WORDS), (
                f"{path.stem}.{field['name']}"
            )
