"""Five-layer, source-backed memory and condition-gated care for the local MVP.

The extractor is intentionally a small English rule extractor, not an LLM or a
clinical assessment. Original utterances never become model diagnoses. SQLite is
the source of truth; every operation opens its own connection and mutations are
serialised so the ASR worker and the web server can safely share one Engine.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HK = timezone(timedelta(hours=8))
KINDS = ("reminder", "suggestion", "comfort", "warning")
CATEGORIES = ("mood", "social", "moments")
DEFAULT_SETTINGS = {
    "preferences": {"reminder": True, "suggestion": True, "comfort": True, "warning": False},
    "quiet_mode": False,
    "frequency_minutes": 60,
    "snoozed_until": None,
    "warning_demo_enabled": False,
    "retention_days": 30,
}


class VersionConflict(ValueError):
    """The caller edited an older memory version (HTTP 409)."""


class NotFoundError(ValueError):
    """A record does not exist or has been deleted (HTTP 404)."""


def _now() -> datetime:
    return datetime.now(HK)


def _time(value: str | datetime | None, *, nullable: bool = False) -> datetime | None:
    if value is None:
        return None if nullable else _now()
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("時間格式不正確，請使用 ISO8601。") from exc
    # A datetime-local input is explicitly interpreted in the product's Hong Kong zone.
    return stamp.replace(tzinfo=HK) if stamp.tzinfo is None else stamp.astimezone(HK)


def _date(value: str | None) -> str:
    value = value or _now().date().isoformat()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("日期格式須為 YYYY-MM-DD。") from exc
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _uid() -> str:
    return str(uuid.uuid4())


class Engine:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.data_dir / "audio"
        self.audio_dir.mkdir(exist_ok=True)
        self.db_path = self.data_dir / "memory.sqlite3"
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS segments_recording ON segments(recording_id);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS memories_recording ON memories(recording_id);
                CREATE TABLE IF NOT EXISTS memory_versions (
                    memory_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(memory_id, version));
                CREATE TABLE IF NOT EXISTS proactive (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS card_summaries (
                    date TEXT NOT NULL, category TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(date, category));
                CREATE TABLE IF NOT EXISTS card_reviews (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (_dump(DEFAULT_SETTINGS),))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _get(self, db: sqlite3.Connection, table: str, ident: str, *, deleted: bool = False) -> dict:
        row = db.execute(f"SELECT payload FROM {table} WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise NotFoundError("找不到這項資料，或資料已刪除。")
        value = json.loads(row[0])
        if table == "memories" and value.get("status") != "deleted":
            value.setdefault("reviewed", False)
        if value.get("status") == "deleted" and not deleted:
            raise NotFoundError("這項資料已刪除。")
        return value

    def _all(self, db: sqlite3.Connection, table: str) -> list[dict]:
        values = [json.loads(row[0]) for row in db.execute(f"SELECT payload FROM {table}")]
        if table == "memories":
            for value in values:
                if value.get("status") != "deleted":
                    value.setdefault("reviewed", False)
        return values

    def _save_recording(self, db: sqlite3.Connection, value: dict) -> None:
        db.execute("UPDATE recordings SET payload=? WHERE id=?", (_dump(value), value["id"]))

    def _save_memory(self, db: sqlite3.Connection, value: dict) -> None:
        db.execute("UPDATE memories SET status=?,payload=? WHERE id=?", (value["status"], _dump(value), value["id"]))

    def _save_proactive(self, db: sqlite3.Connection, value: dict) -> None:
        db.execute("INSERT INTO proactive VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (value["id"], _dump(value)))

    def _settings(self, db: sqlite3.Connection) -> dict:
        return json.loads(db.execute("SELECT payload FROM settings WHERE id=1").fetchone()[0])

    def create_recording(self, filename: str, file_path: str | Path | None, sha256: str,
                         recorded_at: str | datetime | None, is_demo: bool = False) -> dict:
        stamp = _time(recorded_at, nullable=True)
        if not str(sha256).strip():
            raise ValueError("錄音缺少檔案雜湊。")
        with self._lock, self._connect() as db:
            found = db.execute("SELECT payload FROM recordings WHERE sha256=?", (sha256,)).fetchone()
            if found:
                return {**json.loads(found[0]), "duplicate": True}
            value = {
                "id": _uid(), "filename": Path(filename).name, "file_path": str(file_path) if file_path else None,
                "sha256": sha256, "recorded_at": stamp.isoformat() if stamp else None,
                "created_at": _now().isoformat(), "status": "queued", "error": None,
                "metrics": {}, "is_demo": bool(is_demo), "duplicate": False,
                "language": "en", "extraction_mode": "local_rules", "has_audio": bool(file_path),
            }
            db.execute("INSERT INTO recordings VALUES (?,?,?)", (value["id"], sha256, _dump(value)))
            return value

    def get_recording(self, ident: str) -> dict:
        with self._connect() as db:
            value = self._get(db, "recordings", ident)
            value["segments"] = [json.loads(row[0]) for row in db.execute("SELECT payload FROM segments WHERE recording_id=?", (ident,))]
            value["segments"].sort(key=lambda s: s["start"])
            return value

    def list_recordings(self) -> list[dict]:
        with self._connect() as db:
            recordings = self._all(db, "recordings")
            for value in recordings:
                rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM segments WHERE recording_id=?", (value["id"],))]
                value["segment_count"] = len(rows)
                value["confirmed_segment_count"] = sum(s["subject_id"] == "user" for s in rows)
                value["segments"] = sorted(rows, key=lambda s: s["start"])
            return sorted(recordings, key=lambda r: r["created_at"], reverse=True)

    def set_recording_status(self, ident: str, status: str, error: str | None = None,
                             metrics: dict | None = None) -> dict:
        if status not in {"queued", "processing", "transcribing", "ready", "completed", "failed", "error"}:
            raise ValueError("不支援的錄音處理狀態。")
        with self._lock, self._connect() as db:
            value = self._get(db, "recordings", ident)
            value.update(status=status, error=error)
            if metrics is not None:
                value["metrics"] = dict(metrics)
            self._save_recording(db, value)
            return value

    @staticmethod
    def _category(text: str) -> str:
        # An explicitly described conversation stays in the social lens even
        # when the same sentence also mentions how that conversation felt.
        if re.search(r"\b(talked|conversation|met|social|colleague|colleagues)\b", text, re.I):
            return "social"
        if re.search(r"\b(feel|felt|happy|sad|worried|anxious|nervous|excited|lonely|reassurance|comfort|overwhelmed|calm)\b", text, re.I):
            return "mood"
        if re.search(r"\b(friend|friends|classmate|classmates|talked|conversation|met|social|colleague|colleagues)\b", text, re.I):
            return "social"
        return "moments"

    def _extract_segment(self, db: sqlite3.Connection, recording: dict, segment: dict) -> list[dict]:
        # Retries cannot re-create removed or corrected memories for the same source segment.
        if db.execute("SELECT 1 FROM memories WHERE segment_id=? LIMIT 1", (segment["id"],)).fetchone():
            return []
        clauses = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", segment["text"]) if p.strip()]
        output = []
        for text in dict.fromkeys(clauses):
            confirmed = segment["subject_id"] == "user"
            preference = bool(re.search(r"\b(I prefer|I usually|I like to|my preference)\b", text, re.I))
            value = {
                "id": _uid(), "recording_id": recording["id"], "segment_id": segment["id"],
                "content": text, "subject_id": "user" if confirmed else "unknown",
                "memory_type": "semantic" if preference else "episodic",
                "evidence_kind": "user_confirmed" if confirmed else "direct_expression",
                "scope": "stated_preference" if preference else "recorded_conversation",
                "event_time": recording["recorded_at"], "source_refs": [{
                    "recording_id": recording["id"], "segment_id": segment["id"],
                    "start": segment["start"], "end": segment["end"], "text": segment["text"],
                    "is_demo": recording["is_demo"],
                }], "category": self._category(text), "status": "active" if confirmed else "pending",
                "version": 1, "confirmed": confirmed, "reviewed": False, "follow_up": None,
                "is_demo": recording["is_demo"], "extraction_mode": "local_rules",
                "created_at": _now().isoformat(), "updated_at": _now().isoformat(),
            }
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?)", (value["id"], recording["id"], segment["id"], value["status"], _dump(value)))
            db.execute("INSERT INTO memory_versions VALUES (?,?,?)", (value["id"], 1, _dump(value)))
            output.append(value)
        return output

    def ingest_segments(self, recording_id: str, segments: list[dict], language: str = "en",
                        subject_confirmed: bool = False) -> dict:
        if not isinstance(segments, list):
            raise ValueError("逐字稿片段格式不正確。")
        with self._lock, self._connect() as db:
            recording = self._get(db, "recordings", recording_id)
            existing = {s["fingerprint"] for s in self._all(db, "segments") if s["recording_id"] == recording_id}
            added = 0
            for raw in segments:
                text = str(raw.get("text", "")).strip()
                if not text:
                    continue
                start, end = float(raw.get("start", 0)), float(raw.get("end", 0))
                if start < 0 or end < start or end > 24 * 3600:
                    raise ValueError("逐字稿時間戳不正確。")
                fingerprint = hashlib.sha256(_dump([round(start, 3), round(end, 3), text]).encode()).hexdigest()
                if fingerprint in existing:
                    continue
                segment = {
                    "id": _uid(), "recording_id": recording_id, "start": start, "end": end,
                    "text": text, "speaker": "user" if subject_confirmed else "unknown",
                    "subject_id": "user" if subject_confirmed else "unknown",
                    "confirmed": bool(subject_confirmed), "words": raw.get("words", []),
                    "fingerprint": fingerprint,
                }
                db.execute("INSERT INTO segments VALUES (?,?,?)", (segment["id"], recording_id, _dump(segment)))
                self._extract_segment(db, recording, segment)
                existing.add(fingerprint)
                added += 1
            recording.update(status="ready", language=language, error=None)
            self._save_recording(db, recording)
        return {"recording": self.get_recording(recording_id), "added_segments": added,
                "extraction_mode": "local_rules", "requires_confirmation": not subject_confirmed}

    def _new_version(self, db: sqlite3.Connection, current: dict, replacement: dict) -> dict:
        old = {**current, "status": "superseded"}
        db.execute("UPDATE memory_versions SET payload=? WHERE memory_id=? AND version=?", (_dump(old), current["id"], current["version"]))
        replacement = {**replacement, "version": current["version"] + 1, "updated_at": _now().isoformat()}
        self._save_memory(db, replacement)
        db.execute("INSERT INTO memory_versions VALUES (?,?,?)", (current["id"], replacement["version"], _dump(replacement)))
        self._invalidate(db, {current["id"]}, "記憶已更新，舊提示已取消。")
        return replacement

    def confirm_recording(self, recording_id: str, segment_ids: list[str] | None = None) -> dict:
        with self._lock, self._connect() as db:
            self._get(db, "recordings", recording_id)
            rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM segments WHERE recording_id=?", (recording_id,))]
            requested = set(segment_ids) if segment_ids is not None else {s["id"] for s in rows}
            if requested - {s["id"] for s in rows}:
                raise ValueError("選取的片段不屬於這段錄音。")
            if not requested:
                raise ValueError("請至少選擇一段屬於自己的片段。")
            for segment in rows:
                if segment["id"] not in requested:
                    continue
                segment.update(subject_id="user", speaker="user", confirmed=True)
                db.execute("UPDATE segments SET payload=? WHERE id=?", (_dump(segment), segment["id"]))
                for memory in self._all(db, "memories"):
                    if memory["segment_id"] == segment["id"] and memory["status"] == "pending":
                        self._new_version(db, memory, {**memory, "subject_id": "user", "confirmed": True,
                                                     "evidence_kind": "user_confirmed", "status": "active"})
        return self.get_recording(recording_id)

    def get_source(self, memory_id: str) -> dict:
        with self._connect() as db:
            memory = self._get(db, "memories", memory_id)
            recording = self._get(db, "recordings", memory["recording_id"])
            segments = [self._get(db, "segments", ref["segment_id"]) for ref in memory["source_refs"]]
            versions = [json.loads(row[0]) for row in db.execute("SELECT payload FROM memory_versions WHERE memory_id=? ORDER BY version", (memory_id,))]
            return {"memory": memory, "recording": recording, "segments": segments, "versions": versions}

    def revise_memory(self, ident: str, content: str, version: int, confirmed: bool = True,
                      follow_up: dict | None = None) -> dict:
        content = str(content).strip()
        if not content or len(content) > 10000:
            raise ValueError("記憶內容須為 1 至 10,000 字。")
        with self._lock, self._connect() as db:
            current = self._get(db, "memories", ident)
            if version != current["version"]:
                raise VersionConflict("記憶已被更新，請重新載入最新版本再修改。")
            if confirmed and current.get("subject_id") != "user":
                raise ValueError("請先確認原話屬於本人，再核實或修正理解。")
            if follow_up is not None:
                if not isinstance(follow_up, dict):
                    raise ValueError("跟進事項格式不正確。")
                due = _time(follow_up.get("due_at"), nullable=True)
                if not isinstance(follow_up.get("allowed", False), bool) or not isinstance(follow_up.get("completed", False), bool):
                    raise ValueError("跟進事項的許可及完成狀態須為布林值。")
                follow_up = {"due_at": due.isoformat() if due else None, "allowed": follow_up.get("allowed", False),
                             "completed": follow_up.get("completed", False)}
            replacement = {**current, "content": content, "confirmed": bool(confirmed),
                           "subject_id": "user" if confirmed else "unknown",
                           "status": "active" if confirmed else "pending", "category": self._category(content),
                           "evidence_kind": "user_confirmed" if confirmed else "direct_expression",
                           "reviewed": bool(confirmed), "reviewed_at": _now().isoformat() if confirmed else None,
                           # A changed statement must not inherit an old permission/task
                           # accidentally. An explicitly resubmitted follow-up is required.
                           "follow_up": follow_up if follow_up is not None else (
                               current.get("follow_up") if content == current["content"] else None)}
            return self._new_version(db, current, replacement)

    def _invalidate(self, db: sqlite3.Connection, memory_ids: set[str], reason: str) -> None:
        for row in db.execute("SELECT id,payload FROM card_reviews").fetchall():
            review = json.loads(row["payload"])
            if memory_ids.intersection(review.get("memory_ids", [])):
                db.execute("DELETE FROM card_reviews WHERE id=?", (row["id"],))
        for row in db.execute("SELECT date,category,payload FROM card_summaries").fetchall():
            cache = json.loads(row["payload"])
            if memory_ids.intersection(cache.get("memory_ids", [])):
                db.execute("DELETE FROM card_summaries WHERE date=? AND category=?", (row["date"], row["category"]))
        for card in self._all(db, "proactive"):
            if memory_ids.intersection(card.get("memory_ids", [])):
                # Derived content must disappear as soon as evidence is edited/deleted.
                card.update(status="invalidated", title="已取消的提示", summary="", action_text="", source_refs=[],
                            condition_reason=reason, versions={}, invalidated_at=_now().isoformat())
                self._save_proactive(db, card)

    def _delete_memory(self, db: sqlite3.Connection, memory: dict) -> None:
        self._invalidate(db, {memory["id"]}, "來源記憶已刪除，提示已取消。")
        db.execute("DELETE FROM memory_versions WHERE memory_id=?", (memory["id"],))
        tombstone = {"id": memory["id"], "recording_id": memory["recording_id"],
                     "segment_id": memory["segment_id"], "status": "deleted",
                     "version": memory["version"] + 1, "deleted_at": _now().isoformat()}
        self._save_memory(db, tombstone)

    def delete_memory(self, ident: str) -> dict:
        with self._lock, self._connect() as db:
            memory = self._get(db, "memories", ident)
            self._delete_memory(db, memory)
        return {"id": ident, "deleted": True}

    def _delete_recording(self, db: sqlite3.Connection, recording: dict) -> None:
        for memory in self._all(db, "memories"):
            if memory["recording_id"] == recording["id"] and memory["status"] != "deleted":
                self._delete_memory(db, memory)
        # Recording deletion removes tombstones/links too; no source text survives.
        ids = [row[0] for row in db.execute("SELECT id FROM memories WHERE recording_id=?", (recording["id"],))]
        for ident in ids:
            db.execute("DELETE FROM memory_versions WHERE memory_id=?", (ident,))
        db.execute("DELETE FROM memories WHERE recording_id=?", (recording["id"],))
        db.execute("DELETE FROM segments WHERE recording_id=?", (recording["id"],))
        db.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
        if recording.get("file_path"):
            path = Path(recording["file_path"]).resolve()
            # Only remove audio managed by this data directory, never arbitrary user originals.
            if path.is_relative_to(self.data_dir) and path.is_file():
                path.unlink()

    def delete_recording(self, ident: str) -> dict:
        with self._lock, self._connect() as db:
            self._delete_recording(db, self._get(db, "recordings", ident))
        return {"id": ident, "deleted": True}

    def cleanup_retention(self, now: str | datetime | None = None) -> dict:
        stamp = _time(now)
        with self._lock, self._connect() as db:
            days = self._settings(db)["retention_days"]
            cutoff = stamp - timedelta(days=days)
            expired = [r for r in self._all(db, "recordings") if _time(r["created_at"]) < cutoff]
            for recording in expired:
                self._delete_recording(db, recording)
            return {"deleted_recordings": len(expired), "retention_days": days}

    def settings_patch(self, patch: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS)
        if set(patch) - allowed:
            raise ValueError("偏好中包含不支援的欄位。")
        with self._lock, self._connect() as db:
            settings = self._settings(db)
            for name, value in patch.items():
                if name == "preferences":
                    if not isinstance(value, dict) or set(value) - set(KINDS) or any(not isinstance(v, bool) for v in value.values()):
                        raise ValueError("關懷類型偏好須為布林值。")
                    settings[name].update(value)
                elif name in {"quiet_mode", "warning_demo_enabled"}:
                    if not isinstance(value, bool):
                        raise ValueError("開關須為布林值。")
                    settings[name] = value
                elif name in {"frequency_minutes", "retention_days"}:
                    lower, upper = (0, 1440) if name == "frequency_minutes" else (1, 3650)
                    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                        raise ValueError("頻率須為 0–1440 分鐘；保留期限須為 1–3650 日。")
                    settings[name] = value
                elif name == "snoozed_until":
                    stamp = _time(value, nullable=True)
                    settings[name] = stamp.isoformat() if stamp else None
            db.execute("UPDATE settings SET payload=? WHERE id=1", (_dump(settings),))
            self._revalidate(db, _now())
            return settings

    @staticmethod
    def _eligible(memory: dict) -> bool:
        return (memory.get("status") == "active" and memory.get("subject_id") == "user"
                and memory.get("confirmed") is True and bool(memory.get("source_refs")))

    def _memories_for_date(self, db: sqlite3.Connection, date: str) -> list[dict]:
        return [m for m in self._all(db, "memories") if m["status"] != "deleted"
                and m.get("event_time") and _time(m["event_time"]).date().isoformat() == date]

    @staticmethod
    def _sleep_signal(text: str) -> bool:
        # Hours alone are not an individual's statement of insufficient sleep.
        # Keep explicit "not enough sleep" as a positive insufficiency signal.
        if Engine._negated(text, r"sleep deprived"):
            return False
        return bool(re.search(r"\bI\b.*\b(did not get enough sleep|didn't get enough sleep|have not had enough sleep|haven't had enough sleep|am sleep deprived)\b", text, re.I))

    @staticmethod
    def _negated(text: str, target: str) -> bool:
        """Conservative guards for the narrow rules, not a semantic parser."""
        return bool(re.search(rf"(?:\bnot\b|\bnever\b|\bno longer\b|n't\b).{{0,45}}\b(?:{target})\b", text, re.I))

    @staticmethod
    def _lonely_signal(text: str) -> bool:
        if Engine._negated(text, r"lonely|difficult to (?:talk|socialize)"):
            return False
        return bool(re.search(r"\bI\b.*\b(feel lonely|felt lonely|am lonely|was lonely|find it difficult to (?:talk|socialize)|found it difficult to (?:talk|socialize))\b", text, re.I))

    @staticmethod
    def _stress_signal(text: str) -> bool:
        if Engine._negated(text, r"stressed|under pressure|overwhelmed"):
            return False
        return bool(re.search(r"\bI\b.*\b(feel stressed|felt stressed|am stressed|was stressed|feel under pressure|felt under pressure|feel overwhelmed|felt overwhelmed|am under pressure)\b", text, re.I))

    @staticmethod
    def _comfort_signal(text: str) -> bool:
        return bool(re.search(r"\b(please comfort me|I (?:would like|need|want) (?:some )?reassurance|I need emotional support)\b", text, re.I))

    @staticmethod
    def _local_understanding(text: str) -> str | None:
        """Narrow, visible paraphrase rules. Unknown sentences are not guessed."""
        value = text.casefold()
        if Engine._negated(text, r"finished|completed|sent|talk(?:ed)?|conversation|stressed|lonely|under pressure|overwhelmed"):
            return None
        if not re.search(r"\b(i|my|me)\b", value) and "fictional warning demonstration" not in value:
            return None
        if Engine._sleep_signal(text):
            hours = re.search(r"(?:only )?slept (\d+(?:\.\d+)?) hours?", value)
            return f"你提到只睡了 {hours.group(1)} 小時，並表示睡眠不足。" if hours else "你明確提到睡眠不足。"
        if re.search(r"\b(talked|conversation)\b", value) and re.search(r"\b(friend|friends)\b", value):
            if re.search(r"\b(feel|felt|feeling) (?:a little |much )?better\b", value):
                return "你提到與朋友交談後，感覺好些。"
            friend = re.search(r"\bmy friend ([A-Z][a-z]+)\b", text)
            who = f"朋友 {friend.group(1)}" if friend else "朋友"
            topic = "，內容與小組項目有關" if "group project" in value else ""
            return f"你提到與{who}交談{topic}。"
        if Engine._lonely_signal(text):
            return "你提到感到孤單或與人交談有困難。"
        if "presentation" in value and re.search(r"\b(finished|completed|sent)\b", value):
            if "draft" in value:
                return "你提到已完成簡報草稿。" if "sent" not in value else "你提到已交出簡報草稿。"
            return "你提到已完成簡報。"
        if "presentation" in value and re.search(r"\b(send|share)\b", value):
            return "你提到需要交出或分享簡報草稿。"
        slides = re.search(r"\btomorrow i need to review my slides at (\d{1,2}) in the morning\b", value)
        if slides and 1 <= int(slides.group(1)) <= 12:
            return f"你提到明天早上 {int(slides.group(1))} 時需要檢查簡報內容。"
        if re.search(r"\bplease remind me to prepare before the meeting\b", value):
            return "你表達了希望在會議前準備並收到提醒的意願，具體時間及提醒設定仍待確認。"
        if Engine._suggestion(text):
            return "你明確表示需要協助，當中有可以進一步處理的具體事項。"
        if "presentation" in value and "nervous" in value and "not prepared" in value:
            return "你提到簡報前感到緊張，因為內容尚未準備好。"
        mood_words = {"nervous": "緊張", "anxious": "焦慮", "worried": "擔心", "sad": "難過", "happy": "開心", "calm": "平靜", "excited": "興奮"}
        for word, label in mood_words.items():
            if re.search(rf"\b(?:i (?:feel|felt|am|was)|feeling) (?:a little |very )?{word}\b", value):
                return f"你提到感到{label}。"
        if Engine._stress_signal(text):
            return "你明確提到近期感到壓力。"
        if Engine._comfort_signal(text):
            return "你明確表示希望得到安慰或情緒支持。"
        if "fictional warning demonstration" in value:
            return "這是一段清楚標示的虛構警示流程演示。"
        if "i prefer" in value and ("quiet" in value or "calm" in value):
            return "你表達了較喜歡安靜場合的偏好。"
        return None

    def _daily_cards(self, memories: list[dict], date: str) -> list[dict]:
        titles = {"mood": "今天的心情", "social": "今天的社交", "moments": "值得記住的事"}
        cards = []
        for category in CATEGORIES:
            rows = [m for m in memories if self._eligible(m) and m["category"] == category]
            if not rows:
                continue
            # Same utterance does not inflate a narrative, although all independent sources remain traceable.
            paraphrases = [self._local_understanding(m["content"]) for m in rows]
            texts = list(dict.fromkeys(filter(None, paraphrases)))
            summary = "\n".join(texts[:5]) if texts else "目前沒有可由本機規則可靠歸納的內容，請查看原話並修正理解。"
            unknown_count = sum(text is None for text in paraphrases)
            if texts and unknown_count:
                summary += f"\n另外有 {unknown_count} 則記憶未能可靠歸納，請查看原話並修正理解。"
            if len(texts) > 5:
                summary += f"\n另有 {len(texts) - 5} 項已辨識內容，請在來源查看完整內容。"
            refs = {r["segment_id"]: r for m in rows for r in m["source_refs"]}
            cards.append({"id": f"daily-{date}-{category}", "category": category, "title": titles[category],
                          "summary": summary,
                          "date": date, "memory_ids": [m["id"] for m in rows], "source_refs": list(refs.values()),
                          "versions": {m["id"]: m["version"] for m in rows},
                          "recording_count": len({m["recording_id"] for m in rows}), "status": "active",
                          "is_demo": any(m["is_demo"] for m in rows),
                          "demo_only": all(m["is_demo"] for m in rows),
                          "provisional": any(m["evidence_kind"] == "ai_inference" for m in rows),
                          "extraction_mode": "local_rules", "analysis_mode": "local_rules",
                          "summary_mode": "local_rule_summary"})
        return cards

    def apply_card_summaries(self, date: str, summaries: list[dict], expected_versions: dict[str, int]) -> dict:
        """Cache optional model prose only while every evidence dependency is current.

        expected_versions is the whole snapshot given to the model. Per-category
        summaries must reference the complete category evidence, so a newly added
        memory also invalidates the cached narrative at display time.
        """
        selected = _date(date)
        if not isinstance(summaries, list) or not isinstance(expected_versions, dict):
            raise ValueError("模型摘要格式不正確。")
        with self._lock, self._connect() as db:
            active = {m["id"]: m for m in self._memories_for_date(db, selected) if self._eligible(m)}
            for ident, version in expected_versions.items():
                if ident not in active or active[ident]["version"] != version:
                    raise VersionConflict("模型處理期間來源已更新，請重新生成摘要。")
                segment = self._get(db, "segments", active[ident]["segment_id"])
                if segment["subject_id"] != "user":
                    raise VersionConflict("模型摘要依據尚未確認主體。")
            values = []
            for raw in summaries:
                category = raw.get("category")
                ids = raw.get("memory_ids")
                title, summary = str(raw.get("title", "")).strip(), str(raw.get("summary", "")).strip()
                if category not in CATEGORIES or not isinstance(ids, list) or not ids or not summary or len(summary) > 5000 or len(title) > 100:
                    raise ValueError("模型摘要的類型、內容或來源格式不正確。")
                expected_ids = {ident for ident, m in active.items() if m["category"] == category}
                if set(ids) != expected_ids or any(ident not in expected_versions for ident in ids):
                    raise VersionConflict("模型摘要的來源範圍已改變，請重新生成。")
                cache = {"category": category, "title": title, "summary": summary,
                         "memory_ids": list(dict.fromkeys(ids)),
                         "versions": {ident: expected_versions[ident] for ident in ids},
                         "analysis_mode": "llm", "generated_at": _now().isoformat()}
                values.append(cache)
            for cache in values:
                db.execute("INSERT INTO card_summaries VALUES (?,?,?) ON CONFLICT(date,category) DO UPDATE SET payload=excluded.payload",
                           (selected, cache["category"], _dump(cache)))
        return self.state(selected)

    def _render_cards(self, db: sqlite3.Connection, memories: list[dict], date: str) -> list[dict]:
        cards = self._daily_cards(memories, date)
        cache = {row["category"]: json.loads(row["payload"]) for row in db.execute("SELECT category,payload FROM card_summaries WHERE date=?", (date,))}
        for card in cards:
            item = cache.get(card["category"])
            if item and item["versions"] == card["versions"]:
                card.update(title=item["title"] or card["title"], summary=item["summary"],
                            analysis_mode="llm", summary_mode="llm_summary", provisional=True,
                            generated_at=item["generated_at"])
            token = hashlib.sha256(_dump({"id": card["id"], "title": card["title"], "summary": card["summary"],
                                         "versions": dict(sorted(card["versions"].items()))}).encode()).hexdigest()
            row = db.execute("SELECT payload FROM card_reviews WHERE id=?", (card["id"],)).fetchone()
            confirmed = row is not None and json.loads(row[0])["review_token"] == token
            card.update(review_token=token, review_status="confirmed" if confirmed else "pending",
                        review_prompt="這些事情有發生嗎？我有理解對你嗎？需要怎樣修改？",
                        provisional=not confirmed or card["analysis_mode"] == "llm")
        return cards

    def confirm_card(self, card_id: str, expected_versions: dict[str, int], review_token: str) -> dict:
        match = re.fullmatch(r"daily-(\d{4}-\d{2}-\d{2})-(mood|social|moments)", card_id)
        if not match:
            raise NotFoundError("找不到這張每日卡片。")
        selected = _date(match.group(1))
        with self._lock, self._connect() as db:
            cards = self._render_cards(db, self._memories_for_date(db, selected), selected)
            card = next((item for item in cards if item["id"] == card_id), None)
            if not card or card["versions"] != expected_versions or card["review_token"] != review_token:
                raise VersionConflict("卡片摘要或來源已更新，請重新查看再核實。")
            for ident in card["memory_ids"]:
                memory = self._get(db, "memories", ident)
                source = self._get(db, "segments", memory["segment_id"])
                if not self._eligible(memory) or source["subject_id"] != "user":
                    raise VersionConflict("仍有來源未確認屬於本人，無法核實卡片。")
                memory.update(reviewed=True, reviewed_at=_now().isoformat(), reviewed_via="daily_card")
                self._save_memory(db, memory)
                db.execute("UPDATE memory_versions SET payload=? WHERE memory_id=? AND version=?", (_dump(memory), ident, memory["version"]))
            review = {"review_token": review_token, "memory_ids": card["memory_ids"],
                      "versions": expected_versions, "reviewed_at": _now().isoformat()}
            db.execute("INSERT INTO card_reviews VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (card_id, _dump(review)))
            self._revalidate(db, _now())
        return self.state(selected)

    @staticmethod
    def _suggestion(text: str) -> str | None:
        patterns = [
            (r"\b(I need help|help me|I want advice).*(presentation|presenting)\b", "先寫下簡報的三個重點，再用一分鐘練習開場。"),
            (r"\b(I need help|help me|I want advice).*(study|studying|exam)\b", "選一個最需要複習的主題，安排一段短時間練習。"),
            (r"\b(I need help|help me|I want advice).*(friend|conversation|talking|social)\b", "可先選一位熟悉的朋友，約一次短時間交談。"),
        ]
        return next((action for pattern, action in patterns if re.search(pattern, text, re.I)), None)

    def _support_eligible(self, db: sqlite3.Connection, memory: dict, now: datetime) -> bool:
        if not self._eligible(memory) or memory.get("reviewed") is not True or not memory.get("event_time"):
            return False
        if _time(memory["event_time"]) > now:
            return False
        try:
            recording = self._get(db, "recordings", memory["recording_id"])
            source = self._get(db, "segments", memory["segment_id"])
        except NotFoundError:
            return False
        return source.get("subject_id") == "user" and recording["status"] in {"ready", "completed"}

    def _support_candidates(self, db: sqlite3.Connection, date: str, now: datetime) -> dict:
        settings = self._settings(db)
        candidates = {kind: {"rows": [], "support_basis": "current_request"} for kind in KINDS}
        # Historical cards remain readable; proactive support always concerns now.
        if date != now.date().isoformat():
            return candidates
        all_rows = [m for m in self._all(db, "memories") if self._support_eligible(db, m, now)]
        current = [m for m in all_rows if _time(m["event_time"]).date() == now.date()]
        history = [m for m in all_rows if _time(m["event_time"]).date() < now.date()]
        for memory in all_rows:
            follow = memory.get("follow_up")
            if (settings["preferences"]["reminder"] and follow and follow.get("allowed") is True
                    and follow.get("completed") is False and follow.get("due_at") and _time(follow["due_at"]) <= now):
                candidates["reminder"]["rows"].append(memory)
        for memory in current:
            if settings["preferences"]["suggestion"] and self._suggestion(memory["content"]):
                candidates["suggestion"]["rows"].append(memory)
            if settings["preferences"]["comfort"] and self._comfort_signal(memory["content"]):
                candidates["comfort"]["rows"].append(memory)
            if (settings["preferences"]["warning"] and settings["warning_demo_enabled"] and memory["is_demo"]
                    and "fictional warning demonstration" in memory["content"].lower()):
                candidates["warning"]["rows"].append(memory)
        recent_sleep = [m for m in all_rows if self._sleep_signal(m["content"])
                        and now.date() - timedelta(days=6) <= _time(m["event_time"]).date() <= now.date()]
        sleep_dates = {_time(m["event_time"]).date() for m in recent_sleep}
        scheduled = any(m.get("follow_up", {}) and m["follow_up"].get("allowed")
                        and not m["follow_up"].get("completed") for m in recent_sleep)
        if settings["preferences"]["suggestion"] and len(sleep_dates) >= 3 and now.date() in sleep_dates and not scheduled:
            candidates["suggestion"] = {"rows": recent_sleep, "support_basis": "sleep_pattern"}
        lonely_history = [m for m in history if self._lonely_signal(m["content"])]
        improved_social = [m for m in current if not self._negated(m["content"], r"talk(?:ed)?|conversation|better")
                           and re.search(r"\bI\b.*\b(talked|conversation)\b.*\b(friend|friends)\b", m["content"], re.I)
                           and re.search(r"\b(feel|felt|feeling) (?:a little |much )?better\b", m["content"], re.I)]
        past_stress = [m for m in history if self._stress_signal(m["content"])]
        present_stress = [m for m in current if self._stress_signal(m["content"])]
        request = [m for m in current if self._comfort_signal(m["content"])]
        if settings["preferences"]["comfort"] and lonely_history and improved_social:
            candidates["comfort"] = {"rows": lonely_history + improved_social, "support_basis": "social_follow_up"}
        elif settings["preferences"]["comfort"] and past_stress and present_stress and request:
            candidates["comfort"] = {"rows": past_stress + present_stress + request, "support_basis": "emotional_follow_up"}
        for candidate in candidates.values():
            candidate["rows"] = list({m["id"]: m for m in candidate["rows"]}.values())
        return candidates

    def _matches(self, db: sqlite3.Connection, date: str, now: datetime) -> dict[str, list[dict]]:
        return {kind: candidate["rows"] for kind, candidate in self._support_candidates(db, date, now).items()}

    def _common_block(self, settings: dict, now: datetime) -> str | None:
        if settings["quiet_mode"]:
            return "免打擾已開啟，暫不展示主動關懷。"
        if settings.get("snoozed_until") and _time(settings["snoozed_until"]) > now:
            return "主動關懷仍在稍後提醒期間。"
        return None

    def _revalidate(self, db: sqlite3.Connection, now: datetime) -> None:
        settings = self._settings(db)
        all_memories = {m["id"]: m for m in self._all(db, "memories")}
        for card in self._all(db, "proactive"):
            if card["status"] != "active":
                continue
            reason = self._common_block(settings, now)
            if not settings["preferences"].get(card["type"], False):
                reason = "這類關懷已關閉。"
            if card["type"] == "warning" and not settings["warning_demo_enabled"]:
                reason = "虛構警示演示已關閉。"
            matches = self._matches(db, card["date"], now)
            matched_types = [k for k, rows in matches.items() if rows]
            if matched_types != [card["type"]]:
                reason = "來源條件已改變，或多類選擇規則未定；提示已取消。"
            candidate_versions = {m["id"]: m["version"] for m in matches[card["type"]]}
            if candidate_versions != card["versions"]:
                reason = "支持這項提示的來源集合或版本已改變，舊提示已取消。"
            for ident, version in card["versions"].items():
                current = all_memories.get(ident)
                if not current or not self._support_eligible(db, current, now) or current["version"] != version:
                    reason = "來源版本已失效，舊提示已取消。"
                    break
            if reason:
                card.update(status="invalidated", title="已取消的提示", summary="", action_text="", source_refs=[], versions={}, condition_reason=reason)
                self._save_proactive(db, card)

    def evaluate(self, date: str | None = None, now: str | datetime | None = None) -> dict:
        stamp = _time(now)
        selected = _date(date or stamp.date().isoformat())
        with self._lock, self._connect() as db:
            self._revalidate(db, stamp)
            settings = self._settings(db)
            matches = self._matches(db, selected, stamp)
            matched = [kind for kind, rows in matches.items() if rows]
            assessment = {"reason": "沒有符合的前置條件；不輸出。", "matched_types": matched,
                          "extraction_mode": "local_rules"}
            blocked = self._common_block(settings, stamp)
            if blocked:
                assessment["reason"] = blocked
                return {"cards": [], "assessment": assessment}
            if len(matched) > 1:
                assessment["reason"] = "多類條件同時符合，選擇規則未定；不輸出。"
                return {"cards": [], "assessment": assessment}
            if not matched:
                memories = self._memories_for_date(db, selected)
                if any(m["status"] == "pending" for m in memories):
                    assessment["reason"] = "仍有片段未確認主體；這些片段不作主動提示的依據。"
                elif any(self._eligible(m) and not m.get("reviewed") for m in memories):
                    assessment["reason"] = "已確認原話屬於本人，理解仍待核實；請先確認每日卡片。"
                elif any(m.get("is_demo") and "fictional warning demonstration" in m.get("content", "").lower() for m in memories):
                    assessment["reason"] = "虛構警示演示需要同時開啟「警示」偏好及「警示演示」。"
                return {"cards": [], "assessment": assessment}
            kind = matched[0]
            rows = matches[kind]
            support_basis = self._support_candidates(db, selected, stamp)[kind]["support_basis"]
            versions = {m["id"]: m["version"] for m in rows}
            existing = self._all(db, "proactive")
            # Polling/evaluation is idempotent and does not create duplicate outputs.
            for card in existing:
                if card["date"] == selected and card["type"] == kind and card["versions"] == versions:
                    if card["status"] == "active":
                        assessment["reason"] = "前置條件符合，並已通過展示前來源及偏好檢查。"
                        return {"cards": [card], "assessment": assessment}
                    if card["status"] in {"dismissed", "done"} or (
                            card["status"] == "snoozed" and settings.get("snoozed_until") and _time(settings["snoozed_until"]) > stamp):
                        assessment["reason"] = "這項提示已略過、延後或完成；沒有新依據便不重複展示。"
                        return {"cards": [], "assessment": assessment}
            latest = [_time(c.get("feedback_at", c["created_at"])) for c in existing if c["status"] in {"active", "helpful", "dismissed", "snoozed", "done"}]
            if latest and stamp < max(latest) + timedelta(minutes=settings["frequency_minutes"]):
                assessment["reason"] = "尚未到達下一次主動關懷的頻率間隔。"
                return {"cards": [], "assessment": assessment}
            titles = {"reminder": "你允許的跟進事項", "suggestion": "一個可以嘗試的小步驟", "comfort": "給你一點支持", "warning": "虛構警示情境演示"}
            reasons = {
                "reminder": "已確認為本人事項、允許提醒、已到時間，而且未完成。",
                "suggestion": "本人明確提出需要，原話支持一個具體可行的建議。",
                "comfort": "本人明確表示希望獲得安慰或支持，而且已允許這類關懷。",
                "warning": "來源明確標示為虛構警示演示，且用戶已開啟警示及演示開關。",
            }
            texts = {
                "reminder": "你有一項已到時間的跟進事項，可查看原話後決定是否現在處理。",
                "suggestion": self._suggestion(rows[0]["content"]) or "可先選一個小步驟。",
                "comfort": "你已經說出希望得到支持。可以先停一停，給自己一點空間，再按自己的步伐處理。",
                "warning": "這是用來驗證提示條件與回饋的虛構演示，沒有作出疾病或風險判斷。",
            }
            if kind == "reminder" and any(m.get("bedtime_reminder") for m in rows):
                titles[kind] = "你設定的睡覺提醒"
                texts[kind] = "你設定的睡覺提醒已到時間。現在可以按自己的安排準備休息。"
            if support_basis == "sleep_pattern":
                titles[kind] = "今晚留一點時間休息"
                reasons[kind] = "產品示範規則：最近七天三個不同日期（含今天）的已核實原話明確表述睡眠不足；這不是疾病判斷門檻。"
                texts[kind] = "你在最近七天的幾個不同日子，包括今天，都明確提到睡眠不足。今晚可以留一點時間休息；你想設定睡覺提醒嗎？"
            elif support_basis == "social_follow_up":
                titles[kind] = "這次交談值得肯定"
                reasons[kind] = "過往已核實的原話提到孤單或社交困難；今天已核實的原話明確提到與朋友交談後感覺好些。"
                texts[kind] = "你之前提到感到孤單或交談有困難，今天則提到與朋友聊天後感覺好些。這次交談值得肯定，可以按自己舒服的步伐繼續。"
            elif support_basis == "emotional_follow_up":
                titles[kind] = "陪你回看這段壓力"
                reasons[kind] = "過往及今天已核實的原話都明確表述壓力，而且今天明確希望得到支持。"
                texts[kind] = "你之前和今天都提到感到壓力，也說出希望得到支持。可以先停一停，選一件目前能處理的小事，或找信任的人談談。"
            refs = {r["segment_id"]: r for m in rows for r in m["source_refs"]}
            card = {"id": _uid(), "category": "proactive", "type": kind, "title": titles[kind],
                    "summary": texts[kind], "action_text": "查看來源與跟進事項" if kind == "reminder" else "查看支持這項提示的原話",
                    "condition_reason": reasons[kind], "date": selected, "memory_ids": list(versions),
                    "source_refs": list(refs.values()), "versions": versions,
                    "recording_count": len({m["recording_id"] for m in rows}), "status": "active",
                    "is_demo": any(m["is_demo"] for m in rows),
                    "demo_only": all(m["is_demo"] for m in rows), "provisional": True,
                    "extraction_mode": "local_rules", "generation_mode": "local_templates",
                    "created_at": stamp.isoformat()}
            card.update(support_basis=support_basis,
                        historical_memory_ids=[m["id"] for m in rows if _time(m["event_time"]).date() < stamp.date()],
                        current_memory_ids=[m["id"] for m in rows if _time(m["event_time"]).date() == stamp.date()],
                        can_schedule_bedtime=support_basis == "sleep_pattern")
            self._save_proactive(db, card)
            # Recheck after generation, before returning it for presentation.
            self._revalidate(db, stamp)
            card = self._get(db, "proactive", card["id"])
            assessment["reason"] = "前置條件符合，並已通過展示前來源及偏好檢查。"
            return {"cards": [card] if card["status"] == "active" else [], "assessment": assessment}

    def schedule_bedtime(self, card_id: str, due_at: str | datetime, expected_versions: dict[str, int]) -> dict:
        due = _time(due_at)
        if due <= _now():
            raise ValueError("請選擇未來的香港時間設定睡覺提醒。")
        with self._lock, self._connect() as db:
            self._revalidate(db, _now())
            card = self._get(db, "proactive", card_id)
            if card["status"] != "active" or card["versions"] != expected_versions:
                raise VersionConflict("這項休息建議或來源已更新，請重新評估。")
            if card.get("support_basis") != "sleep_pattern" or not card.get("can_schedule_bedtime"):
                raise ValueError("只有有效的睡眠模式支持卡可以設定睡覺提醒。")
            if not card.get("current_memory_ids"):
                raise VersionConflict("缺少今天已核實的來源，不能設定提醒。")
            memory = self._get(db, "memories", card["current_memory_ids"][0])
            self._new_version(db, memory, {**memory, "follow_up": {
                "due_at": due.isoformat(), "allowed": True, "completed": False}, "bedtime_reminder": True})
        return self.state(card["date"])

    def feedback(self, card_id: str, action: str) -> dict:
        if action not in {"snooze", "dismiss", "done", "helpful"}:
            raise ValueError("不支援的回饋動作。")
        with self._lock, self._connect() as db:
            self._revalidate(db, _now())
            card = self._get(db, "proactive", card_id)
            if card["status"] != "active":
                raise VersionConflict("這項提示已失效或已處理，請重新載入。")
            if action == "done":
                if card["type"] != "reminder":
                    raise ValueError("只有跟進事項可以標示完成。")
                for ident in card["memory_ids"]:
                    memory = self._get(db, "memories", ident)
                    self._new_version(db, memory, {**memory, "follow_up": {**memory["follow_up"], "completed": True}})
                card.update(status="done", summary="", action_text="", source_refs=[], versions={})
            elif action == "snooze":
                settings = self._settings(db)
                settings["snoozed_until"] = (_now() + timedelta(minutes=30)).isoformat()
                db.execute("UPDATE settings SET payload=? WHERE id=1", (_dump(settings),))
                card["status"] = "snoozed"
            elif action == "dismiss":
                card["status"] = "dismissed"
            else:
                # Helpful feedback is a reception signal, never a new personal fact.
                card["helpful"] = True
            card["feedback_at"] = _now().isoformat()
            self._save_proactive(db, card)
            return card

    def apply_proactive_text(self, card_id: str, title: str, summary: str,
                             expected_versions: dict[str, int]) -> dict:
        """Apply optional wording after, never instead of, deterministic gates.

        The caller may generate text outside the engine lock. It must present the
        exact evidence snapshot used; type, conditions, dependencies and actions
        remain those of the already-approved candidate.
        """
        title, summary = str(title).strip(), str(summary).strip()
        if not title or len(title) > 100 or not summary or len(summary) > 2500:
            raise ValueError("主動關懷文案長度或格式不正確。")
        if not isinstance(expected_versions, dict):
            raise ValueError("主動關懷來源版本格式不正確。")
        with self._lock, self._connect() as db:
            self._revalidate(db, _now())
            card = self._get(db, "proactive", card_id)
            if card["status"] != "active" or card["versions"] != expected_versions:
                raise VersionConflict("關懷條件或來源版本已改變，請重新評估。")
            card.update(title=title, summary=summary, generation_mode="llm", provisional=True,
                        generated_at=_now().isoformat())
            self._save_proactive(db, card)
            self._revalidate(db, _now())
            current = self._get(db, "proactive", card_id)
            if current["status"] != "active":
                raise VersionConflict("展示前條件已改變，關懷文案已取消。")
            return current

    def retrieve(self, query: str = "", date: str | None = None, person: str | None = None) -> list[dict]:
        selected = _date(date) if date else None
        with self._connect() as db:
            rows = self._all(db, "memories")
        terms = [word.casefold() for word in query.split() if word]
        return [m for m in rows if self._eligible(m)
                and (selected is None or (m.get("event_time") and _time(m["event_time"]).date().isoformat() == selected))
                and (not person or person.casefold() in m["content"].casefold())
                and all(term in m["content"].casefold() for term in terms)]

    def state(self, date: str | None = None) -> dict:
        self.cleanup_retention()
        selected = _date(date)
        with self._lock, self._connect() as db:
            self._revalidate(db, _now())
            memories = [m for m in self._all(db, "memories") if m["status"] != "deleted"]
            dates = sorted({_time(m["event_time"]).date().isoformat() for m in memories if m.get("event_time")}, reverse=True)
            daily = self._memories_for_date(db, selected)
            proactives = [c for c in self._all(db, "proactive") if c["status"] == "active" and c["date"] == selected]
            settings = self._settings(db)
            matches = self._matches(db, selected, _now())
            matched = [kind for kind, rows in matches.items() if rows]
            reason = self._common_block(settings, _now()) or (
                "多類條件同時符合，選擇規則未定；不輸出。" if len(matched) > 1 else
                "前置條件符合，等待或已完成評估。" if matched else "沒有符合的前置條件；不輸出。")
            if not matched and any(m["status"] == "pending" for m in daily):
                reason = "仍有片段未確認主體；這些片段不作主動提示的依據。"
            elif not matched and any(self._eligible(m) and not m.get("reviewed") for m in daily):
                reason = "已確認原話屬於本人，理解仍待核實；請先確認每日卡片。"
            if not matched and any(m.get("is_demo") and "fictional warning demonstration" in m.get("content", "").lower() for m in daily):
                reason = "虛構警示演示需要同時開啟「警示」偏好及「警示演示」。"
            cards = self._render_cards(db, daily, selected)
            coverage = {"active_memories": sum(self._eligible(m) for m in daily),
                        "pending_memories": sum(m["status"] == "pending" for m in daily),
                        "reviewed_memories": sum(m.get("reviewed") is True for m in daily),
                        "pending_review_memories": sum(self._eligible(m) and not m.get("reviewed") for m in daily),
                        "undated_memories": sum(not m.get("event_time") for m in memories),
                        "recording_count": len({m["recording_id"] for m in daily}),
                        "extraction_mode": "local_rules", "summary_mode": "local_rule_summary",
                        "note": "只涵蓋已匯入並確認的片段，不代表整天或完整心理狀態。"}
        return {"date": selected, "dates": dates, "recordings": self.list_recordings(),
                "memories": sorted(memories, key=lambda m: m["created_at"], reverse=True),
                "cards": cards, "proactive": proactives,
                "settings": settings, "coverage": coverage,
                "assessment": {"reason": reason, "matched_types": matched, "extraction_mode": "local_rules"}}

    def seed_demo(self) -> dict:
        # Deterministic hash makes repeated loading a no-op; no private/real audio is supplied.
        today = _now().replace(hour=10, minute=0, second=0, microsecond=0)
        recording = self.create_recording("虛構示例：一天的片段（只有逐字稿）", None,
                                          f"hya-demo-baseline-{today.date()}", today.isoformat(), True)
        self.ingest_segments(recording["id"], [
            {"start": 0, "end": 6, "text": "I felt nervous before the presentation because I had not prepared the opening."},
            {"start": 6, "end": 12, "text": "I talked with my friend Alex about our group project."},
            {"start": 12, "end": 18, "text": "I finished the first draft of my presentation today."},
        ], subject_confirmed=True)
        return self.state(today.date().isoformat())

    def set_demo_scenario(self, kind: str) -> dict:
        if kind not in {*KINDS, "none", "multiple", "sleep", "social_history", "emotional_history"}:
            raise ValueError("不支援的演示情境。")
        today = _now()
        with self._lock, self._connect() as db:
            for recording in self._all(db, "recordings"):
                if recording["is_demo"] and recording["sha256"].startswith("hya-demo-scenario-"):
                    self._delete_recording(db, recording)
        samples = {
            "reminder": "I need to send the presentation draft to Alex.",
            "suggestion": "I need help preparing my presentation.",
            "comfort": "I would like some reassurance before my presentation.",
            "warning": "This is a fictional warning demonstration. Please show the demo warning card.",
            "none": "I completed my presentation and had a quiet afternoon.",
            "multiple": "I need help preparing my presentation. Please comfort me before the presentation.",
            "sleep": "I did not get enough sleep last night.",
            "social_history": "I talked with my friend Alex and felt better after our conversation.",
            "emotional_history": "I feel stressed about my work. I need emotional support.",
        }
        scenario_name = {"reminder": "提醒", "suggestion": "建議", "comfort": "安慰", "warning": "預警", "none": "零類符合", "multiple": "多類符合",
                         "sleep": "睡眠歷史支持", "social_history": "社交歷史支持", "emotional_history": "情緒歷史支持"}[kind]
        offsets = [-2, -1, 0] if kind == "sleep" else [-1, 0] if kind in {"social_history", "emotional_history"} else [0]
        for offset in offsets:
            event_time = today + timedelta(days=offset)
            text = samples[kind]
            if offset < 0 and kind == "social_history":
                text = "I felt lonely and found it difficult to talk to people."
            elif offset < 0 and kind == "emotional_history":
                text = "I felt stressed about my work."
            recording = self.create_recording(f"虛構情境：{scenario_name}・{event_time.date()}（只有逐字稿）", None,
                                              f"hya-demo-scenario-{kind}-{event_time.date()}", event_time.isoformat(), True)
            self.ingest_segments(recording["id"], [{"start": 0, "end": 8, "text": text}], subject_confirmed=True)
            # Selecting a clearly fictional scenario intentionally loads its
            # pre-reviewed fixture. Baseline/new real recordings remain unreviewed.
            with self._lock, self._connect() as db:
                for memory in self._all(db, "memories"):
                    if memory["recording_id"] == recording["id"] and memory["status"] == "active":
                        memory.update(reviewed=True, reviewed_via="fictional_scenario", reviewed_at=_now().isoformat())
                        self._save_memory(db, memory)
                        db.execute("UPDATE memory_versions SET payload=? WHERE memory_id=? AND version=?", (_dump(memory), memory["id"], memory["version"]))
        if kind == "reminder":
            memory = next(m for m in self.state()["memories"] if m["recording_id"] == recording["id"])
            self.revise_memory(memory["id"], memory["content"], memory["version"],
                               follow_up={"due_at": (today - timedelta(minutes=1)).isoformat(), "allowed": True, "completed": False})
        evaluation = self.evaluate(today.date().isoformat())
        state = self.state(today.date().isoformat())
        state["assessment"] = evaluation["assessment"]
        state["demo_scenario"] = kind
        return state
