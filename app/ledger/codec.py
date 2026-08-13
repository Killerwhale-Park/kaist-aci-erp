import base64
import json
import uuid
import zlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

CHUNK_SIZE = 1800


def encode_chunks(
    *, record_type: str, data: dict[str, Any], record_id: str | None = None
) -> list[dict[str, Any]]:
    encoded = base64.urlsafe_b64encode(
        zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    ).decode("ascii")
    chunks = [encoded[index : index + CHUNK_SIZE] for index in range(0, len(encoded), CHUNK_SIZE)]
    actual_id = record_id or str(uuid.uuid4())
    return [
        {
            "version": 1,
            "record_type": record_type,
            "record_id": actual_id,
            "chunk_index": index,
            "chunk_count": len(chunks),
            "data": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]


def decode_chunks(messages: list[dict], *, event_type: str) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for message in messages:
        metadata = message.get("metadata") or {}
        if metadata.get("event_type") != event_type:
            continue
        payload = metadata.get("event_payload") or {}
        record_id = payload.get("record_id")
        if isinstance(record_id, str):
            groups[record_id].append((message.get("ts", ""), payload))

    records: list[dict[str, Any]] = []
    for record_id, parts in groups.items():
        expected = int(parts[0][1].get("chunk_count", 0))
        by_index = {int(payload["chunk_index"]): payload for _, payload in parts}
        if expected < 1 or len(by_index) != expected or set(by_index) != set(range(expected)):
            continue
        encoded = "".join(by_index[index]["data"] for index in range(expected))
        try:
            decoded = json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded)).decode("utf-8"))
        except (ValueError, TypeError, zlib.error, json.JSONDecodeError):
            continue
        records.append(
            {
                "record_id": record_id,
                "record_type": parts[0][1]["record_type"],
                "ts": min(ts for ts, _ in parts),
                "data": decoded,
            }
        )
    return sorted(records, key=lambda item: item["ts"])


def event_record(kind: str, actor: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "actor": actor,
        "at": datetime.now(UTC).isoformat(),
        "data": data,
    }
