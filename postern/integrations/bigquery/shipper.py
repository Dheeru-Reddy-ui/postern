"""Ship warehouse events from Postgres's outbox to BigQuery (ADR-003).

Events are claimed with FOR UPDATE SKIP LOCKED, appended through the Storage Write API's
default stream (at-least-once), and marked shipped only after BigQuery confirms. A failure
leaves them for the next run; a duplicate delivery is removed by the *_dedup views.

Rows are encoded as protocol buffers built at runtime from the same schema files Terraform
uses (postern/integrations/bigquery/schemas), with Google's documented type mapping:
TIMESTAMP as int64 microseconds, JSON and NUMERIC as strings.
"""

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from importlib.resources import files
from typing import Any

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from psycopg import Connection

_TYPES = {
    "STRING": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "JSON": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "NUMERIC": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "INTEGER": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "TIMESTAMP": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "BOOLEAN": descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
}


def load_schema(table: str) -> list[dict[str, str]]:
    text = (
        files("postern.integrations.bigquery")
        .joinpath(f"schemas/{table}.json")
        .read_text(encoding="utf-8")
    )
    schema: list[dict[str, str]] = json.loads(text)
    return schema


def _encode(value: Any, bq_type: str) -> Any:
    if bq_type == "TIMESTAMP":
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return int(moment.timestamp() * 1_000_000)
    if bq_type == "JSON":
        return json.dumps(value, sort_keys=True)
    if bq_type == "NUMERIC":
        return str(value)
    if bq_type == "INTEGER":
        return int(value)
    if bq_type == "BOOLEAN":
        return bool(value)
    return str(value)


class TableEncoder:
    """A protobuf message type for one warehouse table."""

    def __init__(self, table: str) -> None:
        self.table = table
        self.fields = load_schema(table)
        file_proto = descriptor_pb2.FileDescriptorProto(
            name=f"postern_{table}.proto", package="postern.warehouse", syntax="proto2"
        )
        message = file_proto.message_type.add(name="Row")
        for number, field in enumerate(self.fields, start=1):
            message.field.add(
                name=field["name"],
                number=number,
                type=_TYPES[field["type"]],
                label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
            )
        pool = descriptor_pool.DescriptorPool()
        pool.Add(file_proto)
        self.descriptor_proto = message
        self._cls = message_factory.GetMessageClass(
            pool.FindMessageTypeByName("postern.warehouse.Row")
        )

    def encode(self, row: dict[str, Any]) -> bytes:
        message = self._cls()
        for field in self.fields:
            value = row.get(field["name"])
            if value is None:
                if field["mode"] == "REQUIRED":
                    raise ValueError(f"{self.table}.{field['name']} is required")
                continue
            setattr(message, field["name"], _encode(value, field["type"]))
        serialized: bytes = message.SerializeToString()
        return serialized


AppendFn = Callable[[str, TableEncoder, list[bytes]], None]


def storage_write_append(project: str, dataset: str) -> AppendFn:
    from google.cloud import bigquery_storage_v1
    from google.cloud.bigquery_storage_v1 import types, writer

    client = bigquery_storage_v1.BigQueryWriteClient()

    def append(table: str, encoder: TableEncoder, rows: list[bytes]) -> None:
        template = types.AppendRowsRequest(
            write_stream=f"{client.table_path(project, dataset, table)}/streams/_default",
            proto_rows=types.AppendRowsRequest.ProtoData(
                writer_schema=types.ProtoSchema(proto_descriptor=encoder.descriptor_proto)
            ),
        )
        stream = writer.AppendRowsStream(client, template)
        try:
            request = types.AppendRowsRequest(
                proto_rows=types.AppendRowsRequest.ProtoData(
                    rows=types.ProtoRows(serialized_rows=rows)
                )
            )
            stream.send(request).result(timeout=60)
        finally:
            stream.close()

    return append


def ship_outbox(conn: Connection[Any], append: AppendFn, batch_size: int = 500) -> int:
    """Ship one batch. Returns how many events were shipped."""
    rows = conn.execute(
        "select event_id::text, table_name, row from postern.claim_outbox(%s)", (batch_size,)
    ).fetchall()
    if not rows:
        conn.rollback()
        return 0
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for event_id, table, row in rows:
        grouped[str(table)].append((str(event_id), dict(row)))
    shipped: list[str] = []
    try:
        for table, events in grouped.items():
            encoder = TableEncoder(table)
            append(table, encoder, [encoder.encode(row) for _, row in events])
            shipped.extend(event_id for event_id, _ in events)
    except Exception:
        conn.rollback()
        raise
    conn.execute("select postern.mark_outbox_shipped(%s::uuid[])", (shipped,))
    conn.commit()
    return len(shipped)
