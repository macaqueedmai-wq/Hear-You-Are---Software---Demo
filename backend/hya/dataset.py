"""Quarantined local professional-dataset intake, never runtime/training input.

Original JSONL bytes and normalized entries share one SQLite transaction. Intake
permission and a named reviewer are declarations, not evidence of approval.
There is intentionally no API here that enables training or model retrieval.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import uuid

MAX_BYTES = 5 * 1024 * 1024
MAX_ITEMS = 1000
KINDS = frozenset({"knowledge", "counselling_case", "guidance"})
PURPOSE = "Local intake for later professional review and post-training planning; it plays no part in models, proactive care or training today."
HK = timezone(timedelta(hours=8))


class DatasetNotFoundError(LookupError):
    """Dataset does not exist or was deleted (map to HTTP 404)."""


def _strict_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("JSON 物件包含重複欄位，請移除有歧義的資料。")
        result[name] = value
    return result


def _reject_nonfinite(value):
    raise ValueError("JSON 不允許 NaN 或 Infinity，請提供有效數值。")


def _strict_float(value):
    number = float(value)
    if not math.isfinite(number):
        return _reject_nonfinite(value)
    return number


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _safe_filename(filename: str) -> str:
    # The source filename is display metadata only; it is never joined to a
    # filesystem path. Strip both Windows and POSIX path components explicitly.
    if not isinstance(filename, str):
        raise ValueError("資料集檔名格式不正確。")
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    basename = re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]", "", basename)
    suffix = Path(basename).suffix.lower()
    if suffix not in {".jsonl", ".ndjson"}:
        raise ValueError("請選擇 JSONL（.jsonl 或 .ndjson）資料集檔案。")
    if len(basename) > 160:
        basename = basename[:160 - len(suffix)] + suffix
    return basename


def _validate_item(value: Any, line_number: int) -> dict[str, Any]:
    prefix = f"第 {line_number} 行："
    if not isinstance(value, dict):
        raise ValueError(prefix + "每行必須是一個 JSON 物件。")
    required = ("kind", "title", "content", "source", "license", "deidentified", "consent_to_use")
    missing = [name for name in required if name not in value]
    if missing:
        raise ValueError(prefix + "缺少必填欄位：" + "、".join(missing) + "。")
    if not isinstance(value["kind"], str) or value["kind"] not in KINDS:
        raise ValueError(prefix + "kind 須為 knowledge、counselling_case 或 guidance。")
    result = {"kind": value["kind"]}
    for name, maximum in (("title", 256), ("content", 100_000), ("source", 2000), ("license", 1000)):
        text = value[name]
        if not isinstance(text, str) or not text.strip():
            raise ValueError(prefix + f"{name} 須為非空白文字。")
        if len(text) > maximum:
            raise ValueError(prefix + f"{name} 超過 {maximum} 字的限制。")
        result[name] = text.strip()
    # bool is required: integers 1, strings 'true', null and false are not consent.
    for name, description in (("deidentified", "去識別"), ("consent_to_use", "使用授權")):
        if type(value[name]) is not bool or value[name] is not True:
            raise ValueError(prefix + f"必須明確聲明 {description}（{name}: true），未符合的資料不予匯入。")
        result[name] = True
    reviewer = value.get("professional_reviewer")
    if reviewer is not None:
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
            raise ValueError(prefix + "professional_reviewer 須為 1 至 256 字的非空白文字。")
        result["professional_reviewer"] = reviewer.strip()
    # Whitelist fields. Imported claims such as approved/runtime_enabled cannot
    # change the trusted intake state, even when a reviewer is named.
    result.update(status="pending_review", training_enabled=False, runtime_enabled=False)
    return result


def _parse_jsonl(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        raise ValueError("資料集檔案是空的。")
    if len(raw) > MAX_BYTES:
        raise ValueError("資料集超過 5 MB，請先分拆檔案。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("資料集須使用 UTF-8 編碼。") from error
    entries = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(entries) >= MAX_ITEMS:
            raise ValueError("資料集超過 1000 項，請先分拆檔案。")
        try:
            item = json.loads(line, parse_constant=_reject_nonfinite, parse_float=_strict_float, object_pairs_hook=_strict_object)
        except json.JSONDecodeError as error:
            raise ValueError(f"第 {number} 行不是有效 JSON；每項資料須放在同一行。") from error
        except ValueError as error:
            raise ValueError(f"第 {number} 行：{error}") from error
        entries.append(_validate_item(item, number))
    if not entries:
        raise ValueError("資料集沒有可匯入的項目。")
    return entries


class DatasetStore:
    """Thread-safe local storage; caller must place data_dir in ignored data/."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.dataset_dir = self.data_dir / "datasets"
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dataset_dir / "datasets.sqlite3"
        self._lock = threading.RLock()
        with self._connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    metadata TEXT NOT NULL,
                    original_jsonl BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_entries (
                    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(dataset_id, position)
                );
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA secure_delete=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _metadata(database, dataset_id):
        row = database.execute("SELECT metadata FROM datasets WHERE id=?", (str(dataset_id),)).fetchone()
        if row is None:
            raise DatasetNotFoundError("找不到這個資料集，或資料集已被刪除。")
        return json.loads(row[0])

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as database:
            rows = database.execute("SELECT metadata FROM datasets").fetchall()
            return sorted((json.loads(row[0]) for row in rows), key=lambda item: item["created_at"], reverse=True)

    @staticmethod
    def _write_import(database, summary, entries, raw):
        database.execute("INSERT INTO datasets VALUES(?,?,?,?)", (
            summary["id"], summary["sha256"], _encode(summary), sqlite3.Binary(raw),
        ))
        database.executemany("INSERT INTO dataset_entries VALUES(?,?,?)", [
            (summary["id"], index, _encode(item)) for index, item in enumerate(entries)
        ])

    def import_file(self, filename: str, rawbytes: bytes) -> dict[str, Any]:
        safe_name = _safe_filename(filename)
        if not isinstance(rawbytes, (bytes, bytearray)):
            raise ValueError("資料集內容須為檔案位元組。")
        raw = bytes(rawbytes)
        entries = _parse_jsonl(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        summary = {
            "id": str(uuid.uuid4()),
            "filename": safe_name,
            "sha256": checksum,
            "item_count": len(entries),
            "kind_counts": dict(Counter(item["kind"] for item in entries)),
            "created_at": datetime.now(HK).isoformat(),
            "status": "pending_review",
            "training_enabled": False,
            "runtime_enabled": False,
            "purpose": PURPOSE,
        }
        with self._lock, self._connect() as database:
            # IMMEDIATE transaction plus UNIQUE hash also prevents duplicate
            # imports from separate DatasetStore instances in the same process.
            database.execute("BEGIN IMMEDIATE")
            duplicate = database.execute("SELECT metadata FROM datasets WHERE sha256=?", (checksum,)).fetchone()
            if duplicate:
                return {**json.loads(duplicate[0]), "duplicate": True}
            self._write_import(database, summary, entries, raw)
        return {**summary, "duplicate": False}

    def get(self, dataset_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as database:
            summary = self._metadata(database, dataset_id)
            items = [json.loads(row[0]) for row in database.execute(
                "SELECT payload FROM dataset_entries WHERE dataset_id=? ORDER BY position", (str(dataset_id),)
            )]
            return {**summary, "items": items}

    def delete(self, dataset_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            summary = self._metadata(database, dataset_id)
            database.execute("DELETE FROM datasets WHERE id=?", (str(dataset_id),))
        return {**summary, "deleted": True, "status": "deleted"}
