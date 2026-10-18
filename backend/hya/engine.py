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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .transcript_groups import validate_transcript_groups
from .topics import merge_topics, source_fingerprint, topic_view
from .warning_signals import direct_distress_context, distress_topics, strong_distress

HK = timezone(timedelta(hours=8))
KINDS = ("reminder", "suggestion", "comfort", "warning")
CATEGORIES = ("mood", "social", "moments")
UNDATED_RECORDING_REASON = ("Some confirmed memories have no recording time. Set the recording time so they can appear "
                            "in daily cards and be checked for proactive support.")
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
    def __init__(self, data_dir: str | Path, model_mode: bool = False):
        self.model_mode = model_mode
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
                CREATE TABLE IF NOT EXISTS support_display_times (
                    type TEXT PRIMARY KEY, display_at TEXT, revision INTEGER NOT NULL,
                    fulfilled_revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS card_summaries (
                    date TEXT NOT NULL, category TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(date, category));
                CREATE TABLE IF NOT EXISTS card_reviews (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS topic_versions (
                    topic_id TEXT NOT NULL, version INTEGER NOT NULL, recording_id TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(topic_id, version));
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (_dump(DEFAULT_SETTINGS),))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

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

    @staticmethod
    def _display_time(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError
            return stamp.astimezone(HK).isoformat()
        except (ValueError, TypeError, AttributeError, OverflowError) as error:
            raise ValueError("Display time must be an ISO date and time with a timezone, or null.") from error

    @staticmethod
    def _display_schedule(db, kind):
        row = db.execute("SELECT * FROM support_display_times WHERE type=?", (kind,)).fetchone()
        return dict(row) if row else {"type": kind, "display_at": None, "revision": 0, "fulfilled_revision": 0}

    def _schedule_pending(self, db, kind, now):
        schedule = self._display_schedule(db, kind)
        return bool(schedule["display_at"] and _time(schedule["display_at"]) <= now
                    and schedule["fulfilled_revision"] != schedule["revision"])

    def _schedule_block(self, db, kind, now):
        stamp = self._display_schedule(db, kind)["display_at"]
        return "The scheduled display time has not arrived." if stamp and _time(stamp) > now else None

    def _fulfill_schedule(self, db, kind, now):
        if self._schedule_pending(db, kind, now):
            db.execute("UPDATE support_display_times SET fulfilled_revision=revision WHERE type=?", (kind,))

    def update_display_time(self, kind: str, display_at: str | None, expected_display_at: str | None) -> dict:
        if kind not in KINDS:
            raise ValueError("Unknown proactive support type.")
        stamp, expected = self._display_time(display_at), self._display_time(expected_display_at)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._display_schedule(db, kind)
            if previous["display_at"] != expected:
                raise VersionConflict("Display time changed. Refresh the saved time before editing again.")
            if previous["display_at"] != stamp:
                revision = previous["revision"] + 1
                db.execute("INSERT INTO support_display_times VALUES (?,?,?,?) ON CONFLICT(type) DO UPDATE SET "
                           "display_at=excluded.display_at,revision=excluded.revision,fulfilled_revision=excluded.fulfilled_revision",
                           (kind, stamp, revision, 0 if stamp else revision))
                # A new explicit time supersedes an earlier direct display. Keep
                # safe generated wording dormant instead of faking a dismissal.
                for card in self._all(db, "proactive"):
                    if card["type"] == kind and card["status"] in {"active", "scheduled"}:
                        receipt = self._automatic_receipt(card)
                        if receipt:
                            card["automatic_receipt_at"] = receipt.isoformat()
                        card.update(status="scheduled", delivery_mode="scheduled" if stamp else "automatic",
                                    display_schedule_revision=revision)
                        self._save_proactive(db, card)
                self._revalidate(db, _now())
        return self.state()

    def create_recording(self, filename: str, file_path: str | Path | None, sha256: str,
                         recorded_at: str | datetime | None, is_demo: bool = False, *, _upload_time: bool = False) -> dict:
        created = _now()
        stamp = created if _upload_time else _time(recorded_at, nullable=True)
        if not str(sha256).strip():
            raise ValueError("錄音缺少檔案雜湊。")
        with self._lock, self._connect() as db:
            found = db.execute("SELECT payload FROM recordings WHERE sha256=?", (sha256,)).fetchone()
            if found:
                return {**json.loads(found[0]), "duplicate": True}
            value = {
                "id": _uid(), "filename": Path(filename).name, "file_path": str(file_path) if file_path else None,
                "sha256": sha256, "recorded_at": stamp.isoformat() if stamp else None,
                "created_at": created.isoformat(), "status": "queued", "error": None,
                "metrics": {}, "is_demo": bool(is_demo), "duplicate": False,
                "language": "en", "extraction_mode": "local_rules", "has_audio": bool(file_path),
            }
            db.execute("INSERT INTO recordings VALUES (?,?,?)", (value["id"], sha256, _dump(value)))
            return value

    def create_uploaded_recording(self, filename: str, file_path: str | Path | None,
                                  sha256: str, is_demo: bool = False) -> dict:
        """Use one persisted server upload timestamp for new HTTP recordings."""
        return self.create_recording(filename, file_path, sha256, None, is_demo, _upload_time=True)

    def _sources(self, db: sqlite3.Connection, recording_id: str) -> list[dict]:
        return sorted([json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM segments WHERE recording_id=?", (recording_id,))], key=lambda source: source["start"])

    def _save_topics(self, db, recording: dict, groups: list[dict], sources: list[dict]) -> None:
        topics = merge_topics(recording, sources, groups, self._all(db, "memories"))
        recording.update(topics=topics, topics_source_fingerprint=source_fingerprint(sources),
                         topic_pipeline={"status": "completed", "stage": "completed", "error": None})
        for topic in topics:
            db.execute("INSERT OR IGNORE INTO topic_versions VALUES (?,?,?,?)",
                       (topic["id"], topic["version"], recording["id"], _dump(topic)))

    def _preserve_legacy_topics(self, db, recording: dict) -> None:
        """Promote successful legacy groups before transient reports are cleared."""
        report = recording.get("memory_pipeline", {})
        if "topics" not in recording and report.get("status") == "completed" and report.get("transcript_groups"):
            try:
                self._save_topics(db, recording, report["transcript_groups"], self._sources(db, recording["id"]))
                self._save_recording(db, recording)
            except ValueError:
                return

    def update_topic_pipeline(self, recording_id: str, report: dict) -> None:
        with self._lock, self._connect() as db:
            recording = self._get(db, "recordings", recording_id)
            recording["topic_pipeline"] = dict(report)
            self._save_recording(db, recording)

    def commit_topic_groups(self, recording_id: str, groups: list[dict], expected_revision: str,
                            report: dict | None = None) -> dict:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._agent_revision(db, recording_id) != expected_revision:
                raise VersionConflict("Sources, topics or memories changed during topic organization; please retry.")
            recording = self._get(db, "recordings", recording_id)
            self._save_topics(db, recording, groups, self._sources(db, recording_id))
            recording["topic_pipeline"] = {**(report or {}), "status": "completed", "stage": "completed"}
            self._save_recording(db, recording)
        return self.get_recording(recording_id)

    def update_topic(self, recording_id: str, topic_id: str, version: int, title: str, summary: str,
                     memories: list[dict] | None = None) -> dict:
        if (type(version) is not int or version < 1 or not isinstance(title, str) or not title.strip()
                or len(title) > 120 or not isinstance(summary, str) or not summary.strip() or len(summary) > 2000):
            raise ValueError("Topic title, summary or version is invalid.")
        if memories is not None and (not isinstance(memories, list) or len(memories) > 100):
            raise ValueError("Topic corrections must contain at most 100 memory edits.")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recording = self._get(db, "recordings", recording_id)
            self._preserve_legacy_topics(db, recording)
            sources, rows = self._sources(db, recording_id), self._all(db, "memories")
            topics = topic_view(recording, sources, rows)
            topic = next((item for item in topics if item["id"] == topic_id), None)
            if topic is None:
                raise NotFoundError("The topic does not exist or its original sources changed.")
            if topic["version"] != version:
                raise VersionConflict("The topic changed; refresh it before saving again.")
            allowed = {item["id"]: item for item in rows if item["id"] in topic["memory_ids"]}
            edits, seen = [], set()
            for raw in memories or []:
                if (not isinstance(raw, dict) or set(raw) != {"id", "version", "content"}
                        or not isinstance(raw["id"], str) or raw["id"] in seen
                        or type(raw["version"]) is not int or raw["version"] < 1
                        or not isinstance(raw["content"], str) or not raw["content"].strip() or len(raw["content"]) > 4000):
                    raise ValueError("A topic memory correction has an invalid structure.")
                memory = allowed.get(raw["id"])
                if memory is None:
                    raise ValueError("A corrected memory must belong to this topic's live original sources.")
                if memory["version"] != raw["version"]:
                    raise VersionConflict("A topic memory changed; refresh it before saving again.")
                seen.add(raw["id"])
                if raw["content"].strip() != memory["content"]:
                    edits.append((memory, raw["content"].strip()))
            title, summary = title.strip(), summary.strip()
            if not edits and (title, summary) == (topic["title"], topic["summary"]):
                return self.get_recording(recording_id)
            # Validate every expected version before modifying any source-derived state.
            for memory, content in edits:
                replacement = {**memory, "content": content, "user_corrected": True,
                               "reviewed": False, "reviewed_at": None, "follow_up": None}
                replacement.pop("reviewed_via", None)
                self._new_version(db, memory, replacement)
            self._clear_agent_reports(db)
            recording = self._get(db, "recordings", recording_id)
            updated = {**topic, "title": title, "summary": summary, "version": topic["version"] + 1, "edited": True}
            recording.update(topics=[updated if item["id"] == topic_id else item for item in topics],
                             topics_source_fingerprint=source_fingerprint(sources))
            db.execute("INSERT INTO topic_versions VALUES (?,?,?,?)",
                       (topic_id, updated["version"], recording_id, _dump(updated)))
            self._save_recording(db, recording)
        return self.get_recording(recording_id)

    def update_recording_time(self, recording_id: str, recorded_at: str | datetime | None,
                              expected_recorded_at: str | datetime | None) -> dict:
        """Correct source chronology without re-extracting or approving evidence.

        The expected timestamp is a nullable compare-and-swap token. Equivalent
        timezone representations are compared after normalization to Hong Kong.
        A real change versions live memories and requires semantic review again.
        """
        def normalized(value):
            try:
                stamp = _time(value, nullable=True)
                return stamp.isoformat() if stamp else None
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Recording time must be a valid ISO date and time.") from None

        replacement_time = normalized(recorded_at)
        expected_time = normalized(expected_recorded_at)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT payload FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if row is None:
                raise NotFoundError("找不到這項資料，或資料已刪除。")
            recording = json.loads(row[0])
            current_time = normalized(recording.get("recorded_at"))
            if expected_time != current_time:
                raise VersionConflict("Recording time changed; refresh the recording before saving again.")
            if replacement_time == current_time:
                return self.get_recording(recording_id)
            recording.setdefault("recorded_at_history", []).append({
                "previous_recorded_at": recording.get("recorded_at"), "recorded_at": replacement_time,
                "changed_at": _now().isoformat(), "reason": "explicit_recording_time_correction"})
            recording["recorded_at"] = replacement_time
            # The payload predicate also protects against another Engine/process
            # writing between this connection's read and its first write.
            changed = db.execute("UPDATE recordings SET payload=? WHERE id=? AND payload=?",
                                 (_dump(recording), recording_id, row[0]))
            if changed.rowcount != 1:
                raise VersionConflict("Recording time changed; refresh the recording before saving again.")
            for memory in self._all(db, "memories"):
                if memory["recording_id"] != recording_id or memory["status"] == "deleted":
                    continue
                replacement = {**memory, "event_time": replacement_time, "reviewed": False, "reviewed_at": None}
                replacement.pop("reviewed_via", None)
                self._new_version(db, memory, replacement)
            # Date-wide summaries/reviews also depend on category membership:
            # moving evidence changes both days, including an empty source set.
            dates = {_time(value).date().isoformat() for value in (current_time, replacement_time) if value is not None}
            for date in dates:
                db.execute("DELETE FROM card_summaries WHERE date=?", (date,))
                db.execute("DELETE FROM card_reviews WHERE id LIKE ?", (f"daily-{date}-%",))
            # Even an empty extraction may have source groups or an active job.
            # recorded_at is part of the in-flight pipeline revision token.
            self._clear_agent_reports(db)
        return self.get_recording(recording_id)

    def align_upload_dates(self) -> dict:
        """Explicit, idempotent migration to persisted server upload chronology.

        Never called implicitly on import or HTTP reads. Retention created_at,
        source evidence, identity, user corrections and deletion tombstones stay
        intact. Every changed live memory receives a new historical version.
        """
        changed_recordings = changed_memories = unchanged = 0
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in self._all(db, "recordings"):
                recording = self._get(db, "recordings", item["id"])
                target = recording["created_at"]
                _time(target)  # Reject malformed persisted chronology atomically.
                if recording.get("recorded_at") == target:
                    unchanged += 1
                    continue
                self._preserve_legacy_topics(db, recording)
                previous = recording.get("recorded_at")
                recording.setdefault("recorded_at_history", []).append({
                    "previous_recorded_at": previous, "recorded_at": target,
                    "changed_at": _now().isoformat(), "reason": "align_to_server_upload_date"})
                recording["recorded_at"] = target
                self._save_recording(db, recording)
                for memory in self._all(db, "memories"):
                    if memory["recording_id"] != recording["id"] or memory["status"] == "deleted":
                        continue
                    replacement = {**memory, "event_time": target, "reviewed": False, "reviewed_at": None}
                    replacement.pop("reviewed_via", None)
                    self._new_version(db, memory, replacement)
                    changed_memories += 1
                for date in {_time(value).date().isoformat() for value in (previous, target) if value is not None}:
                    db.execute("DELETE FROM card_summaries WHERE date=?", (date,))
                    db.execute("DELETE FROM card_reviews WHERE id LIKE ?", (f"daily-{date}-%",))
                changed_recordings += 1
            if changed_recordings:
                self._clear_agent_reports(db)
        return {"updated_recordings": changed_recordings, "updated_memories": changed_memories,
                "unchanged_recordings": unchanged, "policy": "server_upload_date"}

    def get_recording(self, ident: str) -> dict:
        with self._lock, self._connect() as db:
            value = self._get(db, "recordings", ident)
            self._preserve_legacy_topics(db, value)
            value["segments"] = self._sources(db, ident)
            value["topics"] = topic_view(value, value["segments"], self._all(db, "memories"))
            return value

    def list_recordings(self) -> list[dict]:
        with self._connect() as db:
            recordings = self._all(db, "recordings")
            for value in recordings:
                self._preserve_legacy_topics(db, value)
                rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM segments WHERE recording_id=?", (value["id"],))]
                value["segment_count"] = len(rows)
                value["confirmed_segment_count"] = sum(s["subject_id"] == "user" for s in rows)
                value["segments"] = sorted(rows, key=lambda s: s["start"])
                value["topics"] = topic_view(value, value["segments"], self._all(db, "memories"))
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
                        subject_confirmed: bool = False, extract: bool = True) -> dict:
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
                    "speaker_label": str(raw.get("speaker", "unknown"))[:100],
                    "subject_id": "user" if subject_confirmed else "unknown",
                    "confirmed": bool(subject_confirmed), "words": raw.get("words", []),
                    "fingerprint": fingerprint,
                }
                db.execute("INSERT INTO segments VALUES (?,?,?)", (segment["id"], recording_id, _dump(segment)))
                if extract:
                    self._extract_segment(db, recording, segment)
                existing.add(fingerprint)
                added += 1
            recording.update(status="ready", language=language, error=None,
                             extraction_mode="local_rules" if extract else "deepseek_pending")
            if added and recording.get("topics_source_fingerprint") != source_fingerprint(self._sources(db, recording_id)):
                recording["topics"] = []
                recording.pop("topics_source_fingerprint", None)
            self._save_recording(db, recording)
            if added and "transcript_groups" in recording.get("memory_pipeline", {}):
                # A partition of the previous sources is no longer exhaustive.
                self._clear_agent_reports(db)
        return {"recording": self.get_recording(recording_id), "added_segments": added,
                "extraction_mode": "local_rules" if extract else "deepseek_pending", "requires_confirmation": not subject_confirmed}

    def update_pipeline(self, recording_id: str, pipeline: dict) -> None:
        with self._lock, self._connect() as db:
            recording = self._get(db, "recordings", recording_id)
            # Progress/failure updates cannot publish unchecked source groups.
            # Only the successful revision-checked commit below may store them.
            recording["memory_pipeline"] = {key: value for key, value in pipeline.items()
                                            if key != "transcript_groups"}
            self._save_recording(db, recording)

    def _agent_revision(self, db, recording_id: str) -> str:
        recording = self._get(db, "recordings", recording_id)
        segments = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM segments WHERE recording_id=? ORDER BY id", (recording_id,))]
        # Card review changes semantic approval without bumping a memory version.
        # Include the complete payload so that approval/permission races cancel.
        memories = sorted(self._all(db, "memories"), key=lambda memory: memory["id"])
        snapshot = {"recorded_at": recording.get("recorded_at"), "segments": segments,
                    "topics": recording.get("topics", []), "topics_source_fingerprint": recording.get("topics_source_fingerprint"),
                    "memories": memories, "settings": self._settings(db)}
        return hashlib.sha256(_dump(snapshot).encode()).hexdigest()

    def agent_snapshot(self, recording_id: str) -> dict:
        with self._lock, self._connect() as db:
            recording = self._get(db, "recordings", recording_id)
            self._preserve_legacy_topics(db, recording)
            segments = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM segments WHERE recording_id=?", (recording_id,))]
            return {"recording": recording, "segments": sorted(segments, key=lambda s: s["start"]),
                    "memories": [m for m in self._all(db, "memories") if m["status"] != "deleted"][-100:],
                    "settings": self._settings(db), "revision": self._agent_revision(db, recording_id)}

    def prepare_agent_memories(self, snapshot: dict, proposals: list[dict]) -> list[dict]:
        """Assign source, time, identity and version fields outside the model's control."""
        recording = snapshot["recording"]
        sources = {segment["id"]: segment for segment in snapshot["segments"]}
        with self._connect() as db:
            existing = self._all(db, "memories")
        existing_ids = {memory["id"] for memory in existing}
        protected_segments = {memory["segment_id"] for memory in existing
                              if memory.get("reviewed") or memory.get("user_corrected") or memory["status"] == "deleted"}
        if not isinstance(proposals, list) or len(proposals) > 100:
            raise ValueError("DeepSeek memory output must be a list of at most 100 items.")
        output = {}
        for raw in proposals:
            segment = sources.get(raw.get("source_segment_id"))
            quote, content = raw.get("quote"), raw.get("content")
            if not segment or not isinstance(quote, str) or not quote.strip() or quote not in segment["text"]:
                raise ValueError("DeepSeek cited an unknown source or a quote not present in the transcript.")
            if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                raise ValueError("DeepSeek returned invalid memory content.")
            if raw.get("category") not in CATEGORIES or raw.get("memory_type") not in {"episodic", "semantic"}:
                raise ValueError("DeepSeek returned an invalid memory category/type.")
            if raw.get("scope") not in {"recorded_conversation", "stated_preference", "current_context"}:
                raise ValueError("DeepSeek returned an invalid memory scope.")
            ident = str(uuid.uuid5(uuid.NAMESPACE_URL, _dump([recording["id"], segment["id"], quote, content])))
            if ident in existing_ids or segment["id"] in protected_segments:
                continue
            identity_confirmed = segment["subject_id"] == "user"
            output[ident] = {
                "id": ident, "recording_id": recording["id"], "segment_id": segment["id"],
                "content": content.strip(), "category": raw["category"], "memory_type": raw["memory_type"],
                "scope": raw["scope"], "event_time": recording.get("recorded_at"),
                "subject_id": "user" if identity_confirmed else "unknown",
                "confirmed": identity_confirmed, "reviewed": False, "evidence_kind": "ai_inference",
                "status": "active" if identity_confirmed else "pending", "version": 1, "follow_up": None,
                "is_demo": recording["is_demo"], "extraction_mode": "deepseek", "source_quote": quote,
                "source_refs": [{"recording_id": recording["id"], "segment_id": segment["id"],
                                 "start": segment["start"], "end": segment["end"], "text": segment["text"],
                                 "is_demo": recording["is_demo"]}],
                "created_at": _now().isoformat(), "updated_at": _now().isoformat(),
            }
        return list(output.values())

    def apply_agent_memories(self, recording_id: str, memories: list[dict], expected_revision: str, report: dict) -> dict:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._agent_revision(db, recording_id) != expected_revision:
                raise VersionConflict("Sources, memories or preferences changed during DeepSeek processing; please retry.")
            recording = self._get(db, "recordings", recording_id)
            report = dict(report)
            if "transcript_groups" in report:
                if report.get("status") != "completed":
                    raise ValueError("Transcript groups require a successfully completed memory pipeline.")
                sources = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM segments WHERE recording_id=?", (recording_id,))]
                report["transcript_groups"] = validate_transcript_groups(
                    report["transcript_groups"], sorted(sources, key=lambda source: source["start"]))
            for memory in memories:
                # A retry cannot resurrect a tombstone or overwrite a user's correction.
                if db.execute("SELECT 1 FROM memories WHERE id=?", (memory["id"],)).fetchone():
                    continue
                if any(m["segment_id"] == memory["segment_id"] and (m.get("reviewed") or m.get("user_corrected"))
                       for m in self._all(db, "memories")):
                    continue
                db.execute("INSERT INTO memories VALUES (?,?,?,?,?)", (memory["id"], recording_id,
                           memory["segment_id"], memory["status"], _dump(memory)))
                db.execute("INSERT INTO memory_versions VALUES (?,?,?)", (memory["id"], 1, _dump(memory)))
            report = {**report, "memory_ids": [m["id"] for m in self._all(db, "memories")
                                               if m["recording_id"] == recording_id and m["status"] != "deleted"
                                               and m.get("extraction_mode") == "deepseek"]}
            if "transcript_groups" in report:
                self._save_topics(db, recording, report["transcript_groups"], self._sources(db, recording_id))
            recording.update(extraction_mode="deepseek", memory_pipeline=report)
            self._save_recording(db, recording)
        return self.get_recording(recording_id)

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
            identity_changed = False
            for segment in rows:
                if segment["id"] not in requested:
                    continue
                identity_changed |= (segment["subject_id"] != "user" or not segment["confirmed"]
                                     or segment["speaker"] != "user")
                segment.update(subject_id="user", speaker="user", confirmed=True)
                db.execute("UPDATE segments SET payload=? WHERE id=?", (_dump(segment), segment["id"]))
                for memory in self._all(db, "memories"):
                    if memory["segment_id"] == segment["id"] and memory["status"] == "pending":
                        self._new_version(db, memory, {**memory, "subject_id": "user", "confirmed": True,
                                                     "evidence_kind": "ai_inference" if memory.get("extraction_mode") == "deepseek" else "user_confirmed",
                                                     "status": "active"})
            if identity_changed:
                # A recording may have transcript groups but no extracted facts.
                self._clear_agent_reports(db)
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
                           "user_corrected": True,
                           "subject_id": "user" if confirmed else "unknown",
                           "status": "active" if confirmed else "pending", "category": self._category(content),
                           "evidence_kind": "user_confirmed" if confirmed else "direct_expression",
                           "reviewed": bool(confirmed), "reviewed_at": _now().isoformat() if confirmed else None,
                           # A changed statement must not inherit an old permission/task
                           # accidentally. An explicitly resubmitted follow-up is required.
                           "follow_up": follow_up if follow_up is not None else (
                               current.get("follow_up") if content == current["content"] else None)}
            return self._new_version(db, current, replacement)

    def _clear_agent_reports(self, db: sqlite3.Connection) -> None:
        for recording in self._all(db, "recordings"):
            self._preserve_legacy_topics(db, recording)
            report = recording.get("memory_pipeline")
            if report and report.get("status") in {"completed", "stale"}:
                recording["memory_pipeline"] = {"status": "stale", "stage": "pending", "error": None,
                                                "model": report.get("model"), "stages": []}
                self._save_recording(db, recording)
            elif report and "transcript_groups" in report:
                # Remove legacy/interrupted groups even outside terminal states.
                recording["memory_pipeline"] = {key: value for key, value in report.items()
                                                if key != "transcript_groups"}
                self._save_recording(db, recording)

    def _invalidate(self, db: sqlite3.Connection, memory_ids: set[str], reason: str) -> None:
        self._clear_agent_reports(db)
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
                receipt = self._automatic_receipt(card)
                card.update(status="invalidated", title="Cancelled suggestion", summary="", action_text="", source_refs=[],
                            condition_reason=reason, versions={}, invalidated_at=_now().isoformat())
                if receipt:
                    card["automatic_receipt_at"] = receipt.isoformat()
                card.pop("warning_trigger", None)
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
        db.execute("DELETE FROM topic_versions WHERE recording_id=?", (recording["id"],))
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
            self._clear_agent_reports(db)
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
        return Engine._direct_request(text, r"\b(please comfort me|I (?:would like|need|want) (?:some )?reassurance|I need emotional support)\b")

    @staticmethod
    def _direct_context(text: str, start: int, end: int | None = None) -> bool:
        """Conservative English guards; reported/conditional words are not requests."""
        before = text[:start]
        # Apostrophes inside words (don't, I'm) are not quotation delimiters.
        if (before.count('"') % 2 or before.count("“") > before.count("”")
                or before.count("‘") > before.count("’")
                or len(re.findall(r"(?<!\w)'|'(?!\w)", before)) % 2):
            return False
        sentence = re.split(r"[.!?;\n]", before)[-1]
        if re.search(
            r"\b(if|unless|whether|suppose|imagine|example|said|says|asked|asks|told|quoted|wrote|writes)\b", sentence, re.I):
            return False
        clause = re.split(r",|\b(?:but|however)\b", sentence, flags=re.I)[-1]
        if re.search(r"\b(never|not|no longer|don't|didn't|doesn't|wouldn't|can't|cannot)\b", clause, re.I):
            return False
        if end is not None and not re.search(r"[.!?;\n]\s*$", text[start:end]):
            suffix = re.split(r"[.!?;\n]", text[end:], maxsplit=1)[0]
            if re.search(r"\b(if|unless|whether)\b", suffix, re.I):
                return False
        return True

    @staticmethod
    def _direct_request(text: str, pattern: str) -> bool:
        for match in re.finditer(pattern, text, re.I):
            if Engine._direct_context(text, match.start(), match.end()):
                return True
        return False

    def _condition_texts(self, db: sqlite3.Connection, memory: dict) -> list[str]:
        """Use an exact current quote only while no user correction supersedes it.

        Never use a whole-segment fallback: another atom in the same speech
        window may describe an unrelated event or somebody else's request.
        """
        content = memory["content"]
        if memory.get("user_corrected") or memory.get("reviewed"):
            return [content]
        quote = memory.get("source_quote")
        if not isinstance(quote, str) or not quote.strip():
            return [content]
        try:
            source = self._get(db, "segments", memory["segment_id"])
            recording = self._get(db, "recordings", memory["recording_id"])
        except NotFoundError:
            return [content]
        if (source.get("recording_id") != recording["id"] or source.get("subject_id") != "user"
                or source.get("confirmed") is not True or recording.get("status") not in {"ready", "completed"}
                or not any(ref.get("recording_id") == recording["id"] and ref.get("segment_id") == source["id"]
                           for ref in memory.get("source_refs", []))):
            return [content]
        positions = [match.start() for match in re.finditer(re.escape(quote), source["text"])]
        if not any(self._direct_context(source["text"], start, start + len(quote)) for start in positions):
            return [content]
        return list(dict.fromkeys([content, quote]))

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
            return f"You mentioned sleeping only {hours.group(1)} hours and said it was not enough." if hours else "You explicitly mentioned not getting enough sleep."
        if re.search(r"\b(talked|conversation)\b", value) and re.search(r"\b(friend|friends)\b", value):
            if re.search(r"\b(feel|felt|feeling) (?:a little |much )?better\b", value):
                return "You mentioned feeling better after talking with a friend."
            friend = re.search(r"\bmy friend ([A-Z][a-z]+)\b", text)
            who = f"your friend {friend.group(1)}" if friend else "a friend"
            topic = " about your group project" if "group project" in value else ""
            return f"You mentioned talking with {who}{topic}."
        if Engine._lonely_signal(text):
            return "You mentioned feeling lonely or finding it hard to talk with people."
        if "presentation" in value and re.search(r"\b(finished|completed|sent)\b", value):
            if "draft" in value:
                return "You mentioned finishing the presentation draft." if "sent" not in value else "You mentioned sending the presentation draft."
            return "You mentioned finishing the presentation."
        if "presentation" in value and re.search(r"\b(send|share)\b", value):
            return "You mentioned needing to send or share the presentation draft."
        slides = re.search(r"\btomorrow i need to review my slides at (\d{1,2}) in the morning\b", value)
        if slides and 1 <= int(slides.group(1)) <= 12:
            return f"You mentioned needing to review your slides at {int(slides.group(1))} tomorrow morning."
        if re.search(r"\bplease remind me to prepare before the meeting\b", value):
            return "You expressed a wish to prepare before the meeting and be reminded; the exact time and reminder settings are still unconfirmed."
        if Engine._suggestion(text):
            return "You explicitly asked for help, including something specific that can be acted on."
        if "presentation" in value and "nervous" in value and "not prepared" in value:
            return "You mentioned feeling nervous before the presentation because the content was not ready."
        mood_words = {"nervous": "nervous", "anxious": "anxious", "worried": "worried", "sad": "sad", "happy": "happy", "calm": "calm", "excited": "excited"}
        for word, label in mood_words.items():
            if re.search(rf"\b(?:i (?:feel|felt|am|was)|feeling) (?:a little |very )?{word}\b", value):
                return f"You mentioned feeling {label}."
        if Engine._stress_signal(text):
            return "You explicitly mentioned feeling stressed recently."
        if Engine._comfort_signal(text):
            return "You explicitly said you wanted comfort or emotional support."
        if "fictional warning demonstration" in value:
            return "This is a clearly labelled fictional warning-flow demonstration."
        if "i prefer" in value and ("quiet" in value or "calm" in value):
            return "You expressed a preference for quieter settings."
        return None

    def _daily_cards(self, memories: list[dict], date: str) -> list[dict]:
        titles = {"mood": "Today's mood", "social": "Today's social side", "moments": "Worth remembering"}
        cards = []
        for category in CATEGORIES:
            rows = [m for m in memories if self._eligible(m) and m["category"] == category]
            if not rows:
                continue
            # Same utterance does not inflate a narrative, although all independent sources remain traceable.
            paraphrases = [self._local_understanding(m["content"]) for m in rows]
            texts = list(dict.fromkeys(filter(None, paraphrases)))
            summary = "\n".join(texts[:5]) if texts else "The local rules could not reliably summarise this yet; view the original words and correct the understanding."
            unknown_count = sum(text is None for text in paraphrases)
            if texts and unknown_count:
                summary += f"\nAnother {unknown_count} memor{'y' if unknown_count == 1 else 'ies'} could not be reliably summarised; view the original words and correct the understanding."
            if len(texts) > 5:
                summary += f"\nThere are {len(texts) - 5} more recognised items; view the sources for the full content."
            if self.model_mode:
                summary = "Daily summary has not yet been generated for these sources."
            refs = {r["segment_id"]: r for m in rows for r in m["source_refs"]}
            cards.append({"id": f"daily-{date}-{category}", "category": category, "title": titles[category],
                          "summary": summary,
                          "date": date, "memory_ids": [m["id"] for m in rows], "source_refs": list(refs.values()),
                          "versions": {m["id"]: m["version"] for m in rows},
                          "recording_count": len({m["recording_id"] for m in rows}), "status": "active",
                          "is_demo": any(m["is_demo"] for m in rows),
                          "demo_only": all(m["is_demo"] for m in rows),
                          "provisional": any(m["evidence_kind"] == "ai_inference" for m in rows),
                          "extraction_mode": "deepseek" if any(m.get("extraction_mode") == "deepseek" for m in rows) else "local_rules",
                          "analysis_mode": "pending" if self.model_mode else "local_rules",
                          "summary_mode": "pending" if self.model_mode else "local_rule_summary"})
        return cards

    def invalidate_card_summaries_for_recording(self, recording_id: str) -> None:
        """A topic correction can invalidate prose without changing atomic versions."""
        with self._lock, self._connect() as db:
            self._get(db, "recordings", recording_id)
            dates = {_time(memory["event_time"]).date().isoformat() for memory in self._all(db, "memories")
                     if memory["recording_id"] == recording_id and memory["status"] != "deleted"
                     and memory.get("event_time")}
            for date in dates:
                db.execute("DELETE FROM card_summaries WHERE date=?", (date,))
                db.execute("DELETE FROM card_reviews WHERE id LIKE ?", (f"daily-{date}-%",))

    def apply_card_summaries(self, date: str, summaries: list[dict], expected_versions: dict[str, int], *,
                             expected_categories: dict[str, str] | None = None) -> dict:
        """Cache optional model prose only while every evidence dependency is current.

        expected_versions is the whole snapshot given to the model. Per-category
        summaries must reference the complete category evidence, so a newly added
        memory also invalidates the cached narrative at display time.
        """
        selected = _date(date)
        if not isinstance(summaries, list) or not isinstance(expected_versions, dict):
            raise ValueError("模型摘要格式不正確。")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = {m["id"]: m for m in self._memories_for_date(db, selected) if self._eligible(m)}
            if expected_categories is not None and (
                    set(active) != set(expected_versions) or set(active) != set(expected_categories)
                    or any(memory["category"] != expected_categories[ident] for ident, memory in active.items())):
                raise VersionConflict("Daily-summary source membership or categories changed during generation.")
            for ident, version in expected_versions.items():
                if ident not in active or active[ident]["version"] != version:
                    raise VersionConflict("模型處理期間來源已更新，請重新生成摘要。")
                segment = self._get(db, "segments", active[ident]["segment_id"])
                if segment["subject_id"] != "user":
                    raise VersionConflict("模型摘要依據尚未確認主體。")
                if expected_categories is not None:
                    recording = self._get(db, "recordings", active[ident]["recording_id"])
                    if (segment.get("confirmed") is not True or segment.get("recording_id") != recording["id"]
                            or recording.get("status") not in {"ready", "completed"}):
                        raise VersionConflict("Daily-summary identity or source status changed during generation.")
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
                        review_prompt="Did these things happen? Did I understand you correctly? What needs to change?",
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
            if self.model_mode and card.get("analysis_mode") != "llm":
                raise ValueError("Generate the DeepSeek card before confirming its understanding.")
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
            self._clear_agent_reports(db)
            self._revalidate(db, _now())
        return self.state(selected)

    @staticmethod
    def _suggestion(text: str) -> str | None:
        patterns = [
            (r"\b(I need help|help me|I want advice).*(presentation|presenting)\b", "Write down the three key points of the presentation, then practise the opening for one minute."),
            (r"\b(I need help|help me|I want advice).*(study|studying|exam)\b", "Pick the topic that most needs revision and set aside a short practice block."),
            (r"\b(I need help|help me|I want advice).*(friend|conversation|talking|social)\b", "Choose a friend you know well and arrange a short conversation."),
        ]
        return next((action for pattern, action in patterns if Engine._direct_request(text, pattern)), None)

    def _support_eligible(self, db: sqlite3.Connection, memory: dict, now: datetime) -> bool:
        if not self._eligible(memory) or not memory.get("event_time"):
            return False
        if _time(memory["event_time"]) > now:
            return False
        try:
            recording = self._get(db, "recordings", memory["recording_id"])
            source = self._get(db, "segments", memory["segment_id"])
        except NotFoundError:
            return False
        return source.get("subject_id") == "user" and recording["status"] in {"ready", "completed"}

    def _warning_texts(self, db: sqlite3.Connection, memory: dict) -> list[str]:
        """Warning needs the current correction or an exact, attributed quote.

        A model paraphrase cannot establish distress. In particular, do not
        borrow another atom's words from its larger transcript segment.
        """
        try:
            source = self._get(db, "segments", memory["segment_id"])
            recording = self._get(db, "recordings", memory["recording_id"])
        except NotFoundError:
            return []
        if (memory.get("is_demo") or recording.get("is_demo")
                or source.get("recording_id") != recording["id"]
                or source.get("subject_id") != "user" or source.get("confirmed") is not True
                or not any(ref.get("recording_id") == recording["id"] and ref.get("segment_id") == source["id"]
                           for ref in memory.get("source_refs", []))):
            return []
        if memory.get("user_corrected") or memory.get("reviewed"):
            return [memory["content"]]
        quote = memory.get("source_quote")
        if not quote and memory.get("extraction_mode") == "local_rules":
            quote = memory["content"]
        if not isinstance(quote, str) or not quote.strip():
            return []
        positions = [match.start() for match in re.finditer(re.escape(quote), source["text"])]
        if not any(direct_distress_context(source["text"], start, start + len(quote)) for start in positions):
            return []
        return [quote]

    def _real_warning_candidate(self, db, all_rows, now):
        grounded = {memory["id"]: self._warning_texts(db, memory) for memory in all_rows}
        current = [memory for memory in all_rows if _time(memory["event_time"]).date() == now.date()]
        acute = [memory for memory in current if any(strong_distress(text) for text in grounded[memory["id"]])]
        basis, rows, topics = "acute_distress", acute, set()
        if acute:
            topics = {topic for memory in acute for text in grounded[memory["id"]] for topic in distress_topics(text)}
        else:
            by_topic = {}
            for memory in all_rows:
                day = _time(memory["event_time"]).date()
                if not now.date() - timedelta(days=6) <= day <= now.date():
                    continue
                for topic in {topic for text in grounded[memory["id"]] for topic in distress_topics(text)}:
                    by_topic.setdefault(topic, []).append(memory)
            matched = [(topic, memories) for topic, memories in by_topic.items()
                       if len({_time(memory["event_time"]).date() for memory in memories}) >= 3
                       and any(_time(memory["event_time"]).date() == now.date() for memory in memories)]
            if not matched:
                return None
            topic, rows = sorted(matched, key=lambda pair: (-len({_time(m["event_time"]).date() for m in pair[1]}), pair[0]))[0]
            basis, topics = "repeated_distress", {topic}
        rows = list({memory["id"]: memory for memory in rows}.values())
        dates = sorted({_time(memory["event_time"]).date().isoformat() for memory in rows})
        evidence = [{"memory_id": memory["id"], "text": text}
                    for memory in rows for text in grounded[memory["id"]]
                    if (strong_distress(text) if basis == "acute_distress" else topics.intersection(distress_topics(text)))]
        return {"rows": rows, "support_basis": basis, "warning_trigger": {
            "basis": basis, "topics": sorted(topics), "dates": dates, "day_count": len(dates),
            "window_days": 1 if basis == "acute_distress" else 7, "evidence_quotes": evidence}}

    def _support_candidates(self, db: sqlite3.Connection, date: str, now: datetime) -> dict:
        settings = self._settings(db)
        candidates = {kind: {"rows": [], "support_basis": "current_request"} for kind in KINDS}
        # Historical cards remain readable; proactive support always concerns now.
        if date != now.date().isoformat():
            return candidates
        all_rows = [m for m in self._all(db, "memories") if self._support_eligible(db, m, now)]
        texts = {memory["id"]: self._condition_texts(db, memory) for memory in all_rows}
        def signal(memory, predicate):
            return any(predicate(text) for text in texts[memory["id"]])
        current = [m for m in all_rows if _time(m["event_time"]).date() == now.date()]
        history = [m for m in all_rows if _time(m["event_time"]).date() < now.date()]
        for memory in all_rows:
            follow = memory.get("follow_up")
            if (settings["preferences"]["reminder"] and follow and follow.get("allowed") is True
                    and follow.get("completed") is False and follow.get("due_at") and _time(follow["due_at"]) <= now):
                candidates["reminder"]["rows"].append(memory)
        for memory in current:
            if settings["preferences"]["suggestion"] and signal(memory, self._suggestion):
                candidates["suggestion"]["rows"].append(memory)
            if settings["preferences"]["comfort"] and signal(memory, self._comfort_signal):
                candidates["comfort"]["rows"].append(memory)
            if (settings["preferences"]["warning"] and settings["warning_demo_enabled"] and memory["is_demo"]
                    and "fictional warning demonstration" in memory["content"].lower()):
                candidates["warning"]["rows"].append(memory)
                candidates["warning"]["support_basis"] = "fictional_warning"
        if settings["preferences"]["warning"]:
            real_warning = self._real_warning_candidate(db, all_rows, now)
            if real_warning:
                candidates["warning"] = real_warning
        recent_sleep = [m for m in all_rows if signal(m, self._sleep_signal)
                        and now.date() - timedelta(days=6) <= _time(m["event_time"]).date() <= now.date()]
        sleep_dates = {_time(m["event_time"]).date() for m in recent_sleep}
        scheduled = any(m.get("follow_up", {}) and m["follow_up"].get("allowed")
                        and not m["follow_up"].get("completed") for m in recent_sleep)
        if settings["preferences"]["suggestion"] and len(sleep_dates) >= 3 and now.date() in sleep_dates and not scheduled:
            candidates["suggestion"] = {"rows": recent_sleep, "support_basis": "sleep_pattern"}
        lonely_history = [m for m in history if signal(m, self._lonely_signal)]
        def social_improvement(text):
            return (not self._negated(text, r"talk(?:ed)?|conversation|better")
                    and re.search(r"\bI\b.*\b(talked|conversation)\b.*\b(friend|friends)\b", text, re.I)
                    and re.search(r"\b(feel|felt|feeling) (?:a little |much )?better\b", text, re.I))
        improved_social = [m for m in current if signal(m, social_improvement)]
        past_stress = [m for m in history if signal(m, self._stress_signal)]
        present_stress = [m for m in current if signal(m, self._stress_signal)]
        request = [m for m in current if signal(m, self._comfort_signal)]
        if settings["preferences"]["comfort"] and lonely_history and improved_social:
            candidates["comfort"] = {"rows": lonely_history + improved_social, "support_basis": "social_follow_up"}
        elif settings["preferences"]["comfort"] and past_stress and present_stress and request:
            candidates["comfort"] = {"rows": past_stress + present_stress + request, "support_basis": "emotional_follow_up"}
        for candidate in candidates.values():
            candidate["rows"] = list({m["id"]: m for m in candidate["rows"]}.values())
        return candidates

    def _matches(self, db: sqlite3.Connection, date: str, now: datetime) -> dict[str, list[dict]]:
        return {kind: candidate["rows"] for kind, candidate in self._support_candidates(db, date, now).items()}

    def _automatic_matches(self, db, matches, now):
        available = {kind: [] if self._schedule_block(db, kind, now) else rows for kind, rows in matches.items()}
        # Explicit real distress has a defined priority. A future Warning time
        # does not suppress other support, and fictional fixtures add no priority.
        if available["warning"] and any(not memory.get("is_demo") for memory in available["warning"]):
            return {kind: rows if kind == "warning" else [] for kind, rows in available.items()}
        return available

    def _type_conditions(self, db, date: str, now: datetime, matches: dict[str, list[dict]]) -> dict:
        settings = self._settings(db)
        eligible = [memory for memory in self._all(db, "memories") if self._support_eligible(db, memory, now)]
        current = [memory for memory in eligible if _time(memory["event_time"]).date() == now.date()]
        existing = self._all(db, "proactive")
        automatic_times = [receipt for card in existing if (receipt := self._automatic_receipt(card))]
        automatic_matches = self._automatic_matches(db, matches, now)
        warning_priority = bool(automatic_matches["warning"] and any(
            not memory.get("is_demo") for memory in automatic_matches["warning"]))
        warning_candidate = self._support_candidates(db, date, now)["warning"]
        result = {}
        for kind in KINDS:
            enabled, matched = settings["preferences"][kind], bool(matches[kind])
            code, reason = "conditions_met", "The source conditions for this support type are met."
            if not enabled:
                code, reason = "preference_disabled", "This support type is turned off in your preferences."
            elif date != now.date().isoformat():
                code, reason = "historical_view", "Proactive support is checked for today in Hong Kong; this view is another date."
            elif not matched:
                if not (eligible if kind == "reminder" else current):
                    code, reason = "no_eligible_sources", "No current, identity-confirmed source with a known time is eligible for this support type."
                elif kind == "reminder":
                    allowed = [memory["follow_up"] for memory in eligible if memory.get("follow_up", {})
                               and memory["follow_up"].get("allowed") is True
                               and memory["follow_up"].get("completed") is False]
                    if not allowed:
                        code, reason = "no_allowed_follow_up", "No unfinished follow-up has your explicit permission. A reminder needs permission and a due time."
                    elif not any(follow.get("due_at") for follow in allowed):
                        code, reason = "no_due_time", "An allowed follow-up needs a due time before a reminder can be shown."
                    else:
                        code, reason = "not_due", "Your allowed, unfinished follow-ups are not due yet."
                elif kind == "suggestion":
                    code, reason = "no_explicit_actionable_request", "No explicit request for practical help with a presentation, study/exams or social conversation was found, and no supported sleep pattern matched."
                elif kind == "comfort":
                    code, reason = "no_explicit_support_request", "No explicit reassurance/support request or supported follow-up pattern was found."
                else:
                    code, reason = "no_distress_warning_condition", (
                        "Warning needs your confirmed words describing strong distress today, or the same distress topic "
                        "on at least three distinct days in the last seven, including today.")
            elif kind == "warning":
                basis = warning_candidate["support_basis"]
                if basis == "acute_distress":
                    reason = "Your confirmed words explicitly describe strong distress today. This is a support prompt, not a diagnosis."
                elif basis == "repeated_distress":
                    count = warning_candidate["warning_trigger"]["day_count"]
                    reason = f"Your confirmed words describe the same distress topic on {count} distinct days in the last seven, including today."
                else:
                    reason = "A clearly labelled fictional Warning example is enabled by both demo permissions."
            result[kind] = {"enabled": enabled, "condition_met": matched, "matched_memory_count": len(matches[kind]),
                            "reason_code": code, "reason": reason}
            if kind == "warning" and matched:
                result[kind]["support_basis"] = warning_candidate["support_basis"]
                if warning_candidate.get("warning_trigger"):
                    result[kind]["warning_trigger"] = warning_candidate["warning_trigger"]
            status, delivery_reason = ("not_matched", reason) if enabled else ("disabled", reason)
            pending_schedule = self._schedule_pending(db, kind, now)
            schedule = self._display_schedule(db, kind)
            scheduled_authorization = bool(schedule["display_at"] and schedule["revision"] != schedule["fulfilled_revision"])
            feedback = False
            ambiguous = len([rows for rows in automatic_matches.values() if rows]) > 1
            priority_blocked = warning_priority and kind != "warning"
            if matched:
                versions = {memory["id"]: memory["version"] for memory in matches[kind]}
                same = [card for card in existing if card["date"] == date and card["type"] == kind
                        and card["versions"] == versions]
                active = next((card for card in same if card["status"] == "active"), None)
                visible = active and (not self.model_mode or active.get("generation_mode") == "llm")
                feedback = not scheduled_authorization and any(card["status"] in {"dismissed", "done"} or
                    card["status"] == "snoozed" and self._requested_support(card)
                               for card in same)
                blocked = self._common_block(settings, now) or self._schedule_block(db, kind, now)
                if visible:
                    status, delivery_reason = "shown", "The current support card is available."
                    if self._requested_support(active):
                        delivery_reason = "Your requested support is shown. Automatic delivery settings are unchanged."
                        if blocked:
                            delivery_reason += " " + blocked
                elif active and self._requested_support(active):
                    status, delivery_reason = "eligible", "Your requested support is being generated."
                elif blocked:
                    status, delivery_reason = "paused", blocked
                elif priority_blocked:
                    status, delivery_reason = "paused", "A current Warning takes priority for automatic display. You can still request this support manually."
                elif ambiguous:
                    status, delivery_reason = "paused", "Several types match; automatic delivery has no priority rule."
                elif feedback:
                    status, delivery_reason = "paused", "This support was dismissed, snoozed or done; automatic delivery will not repeat it."
                elif not pending_schedule and automatic_times and now < max(automatic_times) + timedelta(minutes=settings["frequency_minutes"]):
                    status, delivery_reason = "paused", "The minimum interval before the next automatic support has not passed."
                else:
                    status, delivery_reason = "eligible", "The source conditions are met; support can be generated."
            result[kind]["delivery"] = {"status": status, "reason": delivery_reason}
            result[kind]["request_allowed"] = enabled and matched and date == now.date().isoformat()
            displayed = [stamp for card in existing if card["type"] == kind and (stamp := self._displayed_at(card))]
            effective = None
            time_reason = delivery_reason
            if enabled and matched and not settings["quiet_mode"] and not ambiguous and not feedback and not priority_blocked:
                bounds = [now]
                if schedule["display_at"]:
                    bounds.append(_time(schedule["display_at"]))
                if settings.get("snoozed_until"):
                    bounds.append(_time(settings["snoozed_until"]))
                if not scheduled_authorization and automatic_times:
                    bounds.append(max(automatic_times) + timedelta(minutes=settings["frequency_minutes"]))
                effective = max(bounds).isoformat()
                time_reason = "Earliest permitted automatic display, subject to current sources, generation and the next app visit or poll."
            elif enabled and matched:
                if settings["quiet_mode"]:
                    time_reason = self._common_block(settings, now)
                elif priority_blocked:
                    time_reason = "Automatic display waits while the current Warning has priority."
                elif ambiguous:
                    time_reason = "Several support types match; an automatic display time cannot be determined."
                else:
                    time_reason = "Previous feedback prevents automatic repeat until new evidence or an explicit new display time."
            result[kind]["display_time"] = {"scheduled_at": schedule["display_at"], "effective_at": effective,
                                            "displayed_at": max(displayed, key=_time) if displayed else None,
                                            "reason": time_reason}
        return result

    @staticmethod
    def _requested_support(card: dict) -> bool:
        return card["type"] in KINDS and card.get("delivery_mode") == "requested"

    @staticmethod
    def _displayed_at(card: dict) -> str | None:
        # Legacy ready LLM cards predate explicit release timestamps. Never use
        # candidate creation time as evidence of a display.
        if "displayed_at" in card:
            return card["displayed_at"]
        return card.get("generated_at") if card.get("generation_mode") == "llm" else None

    @staticmethod
    def _automatic_receipt(card: dict) -> datetime | None:
        # Upgrading an already delivered automatic card must retain its original
        # interval, while direct requests and their feedback add no new interval.
        if card.get("automatic_receipt_at"):
            return _time(card["automatic_receipt_at"])
        if card.get("delivery_mode") != "requested" and card["status"] in {"active", "helpful", "dismissed", "snoozed", "done"}:
            if "displayed_at" in card:
                return _time(card.get("feedback_at") or card["displayed_at"]) if card["displayed_at"] else None
            return _time(card.get("feedback_at", card["created_at"]))
        return None

    def _common_block(self, settings: dict, now: datetime) -> str | None:
        if settings["quiet_mode"]:
            return "Quiet mode is on, so proactive care is paused."
        if settings.get("snoozed_until") and _time(settings["snoozed_until"]) > now:
            return "Proactive care is still snoozed."
        return None

    def _revalidate(self, db: sqlite3.Connection, now: datetime) -> None:
        settings = self._settings(db)
        all_memories = {m["id"]: m for m in self._all(db, "memories")}
        for card in self._all(db, "proactive"):
            if card["status"] not in {"active", "scheduled"}:
                continue
            requested = self._requested_support(card)
            dormant = card["status"] == "scheduled"
            if not requested and self._schedule_block(db, card["type"], now):
                card.update(status="scheduled", delivery_mode="scheduled")
                self._save_proactive(db, card)
                dormant = True
            reason = None if requested or dormant else self._common_block(settings, now)
            if not settings["preferences"].get(card["type"], False):
                reason = "This kind of care is turned off."
            if (card["type"] == "warning" and card.get("demo_only", card.get("is_demo"))
                    and not settings["warning_demo_enabled"]):
                reason = "Fictional warning demos are turned off."
            matches = self._matches(db, card["date"], now)
            matched_types = [k for k, rows in self._automatic_matches(db, matches, now).items() if rows]
            if (not matches[card["type"]] if requested or dormant else matched_types != [card["type"]]):
                reason = "Source conditions changed, or several types match with no priority rule; the suggestion was cancelled."
                if (not requested and not dormant and card["type"] != "warning" and matched_types == ["warning"]
                        and any(not memory.get("is_demo") for memory in matches["warning"])):
                    reason = "A current Warning has priority for automatic display; this automatic suggestion was cancelled."
            candidate_versions = {m["id"]: m["version"] for m in matches[card["type"]]}
            if candidate_versions != card["versions"]:
                reason = "The source set or version behind this suggestion changed; the old suggestion was cancelled."
            for ident, version in card["versions"].items():
                current = all_memories.get(ident)
                if not current or not self._support_eligible(db, current, now) or current["version"] != version:
                    reason = "The source version is no longer valid; the old suggestion was cancelled."
                    break
                if (requested or dormant) and self._get(db, "segments", current["segment_id"]).get("confirmed") is not True:
                    reason = "The speaker behind this support is no longer confirmed."
                    break
            if reason:
                receipt = self._automatic_receipt(card)
                card.update(status="invalidated", title="Cancelled suggestion", summary="", action_text="", source_refs=[], versions={}, condition_reason=reason)
                if receipt:
                    card["automatic_receipt_at"] = receipt.isoformat()
                card.pop("warning_trigger", None)
                self._save_proactive(db, card)

    def _new_support_card(self, db, selected, kind, rows, support_basis, stamp, warning_trigger=None):
        versions = {memory["id"]: memory["version"] for memory in rows}
        titles = {"reminder": "A follow-up you allowed", "suggestion": "A small step to try", "comfort": "A little support for you", "warning": "Fictional warning scenario demo"}
        reasons = {
            "reminder": "Confirmed as your own item, reminders allowed, now due, and not yet done.",
            "suggestion": "You explicitly asked for help, and your words support one concrete, workable suggestion.",
            "comfort": "You explicitly said you wanted comfort or support, and you allowed this kind of care.",
            "warning": "The source is explicitly labelled as a fictional warning demo, and both warning switches are on.",
        }
        texts = {
            "reminder": "You have a follow-up that is now due. You can look at the original words and decide whether to handle it now.",
            "suggestion": next((action for text in self._condition_texts(db, rows[0])
                                if (action := self._suggestion(text))), "Start with one small step."),
            "comfort": "You have said you want support. Take a pause, give yourself a little space, then move at your own pace.",
            "warning": "This is a fictional demo for testing suggestion conditions and feedback; it makes no illness or risk judgement.",
        }
        if kind == "reminder" and any(m.get("bedtime_reminder") for m in rows):
            titles[kind] = "Your bedtime reminder"
            texts[kind] = "Your bedtime reminder is due. You can get ready to rest whenever suits you."
        if support_basis == "sleep_pattern":
            titles[kind] = "Leave some time to rest tonight"
            reasons[kind] = "Demo rule: verified words on three different days in the last seven (including today) explicitly mention not getting enough sleep; this is not a diagnosis threshold."
            texts[kind] = "On several different days in the last week, including today, you explicitly mentioned not getting enough sleep. Leave some time to rest tonight. Would you like to set a bedtime reminder?"
        elif support_basis == "social_follow_up":
            titles[kind] = "That conversation is worth acknowledging"
            reasons[kind] = "Verified past words mention loneliness or social difficulty, and today's verified words say you felt better after talking with a friend."
            texts[kind] = "You mentioned feeling lonely or finding it hard to talk, and today you mentioned feeling better after chatting with a friend. That conversation is worth acknowledging; continue at a pace that feels comfortable."
        elif support_basis == "emotional_follow_up":
            titles[kind] = "Looking back on that stress with you"
            reasons[kind] = "Verified words from both the past and today explicitly describe stress, and today you clearly asked for support."
            texts[kind] = "You mentioned feeling stressed both before and today, and said you wanted support. Take a pause, pick one small thing you can handle right now, or talk with someone you trust."
        elif support_basis == "acute_distress":
            titles[kind] = "Please pause and reach out for support"
            reasons[kind] = "Your current, identity-confirmed words explicitly describe strong distress today. This prompt follows what you said; it is not a diagnosis or a prediction."
            texts[kind] = "Your words describe things feeling especially difficult right now. You deserve support with this. If you can, pause nonessential tasks and reach out to someone you trust to talk or stay with you. You can also seek professional support if you need more help."
        elif support_basis == "repeated_distress":
            count = warning_trigger["day_count"] if warning_trigger else len({_time(m["event_time"]).date() for m in rows})
            titles[kind] = "This has been difficult on several days"
            reasons[kind] = f"Your current, identity-confirmed words describe the same distress topic on {count} distinct days in the last seven, including today. This transparent product rule is not a clinical threshold."
            texts[kind] = f"You have described the same kind of difficulty on {count} different days this week, including today. It may help to lighten one demand and talk with someone you trust about what has been happening. If it keeps affecting your day, consider asking a qualified professional for support."
        refs = {r["segment_id"]: r for m in rows for r in m["source_refs"]}
        card = {"id": _uid(), "category": "proactive", "type": kind, "title": titles[kind],
                "summary": texts[kind], "action_text": "View sources and follow-up" if kind == "reminder" else "View the words behind this suggestion",
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
        if kind == "warning" and warning_trigger:
            card["warning_trigger"] = warning_trigger
        card["displayed_at"] = None
        return card

    def request_comfort(self, date: str | None = None) -> dict:
        return self.request_support("comfort", date)

    def request_support(self, kind: str, date: str | None = None) -> dict:
        """A direct selection overrides timing, never source or preference gates."""
        if kind not in KINDS:
            raise ValueError("Unknown proactive support type.")
        stamp = _now()
        selected = _date(date or stamp.date().isoformat())
        with self._lock, self._connect() as db:
            self._revalidate(db, stamp)
            candidate = self._support_candidates(db, selected, stamp)[kind]
            rows = candidate["rows"]
            if not rows or any(self._get(db, "segments", memory["segment_id"]).get("confirmed") is not True
                               for memory in rows):
                raise VersionConflict("Display now needs current, identity-confirmed matching sources and this support type enabled.")
            versions = {memory["id"]: memory["version"] for memory in rows}
            existing = next((card for card in self._all(db, "proactive") if card["date"] == selected
                             and card["type"] == kind and card["status"] in {"active", "scheduled"}
                             and card["versions"] == versions), None)
            if existing:
                if not self._requested_support(existing):
                    receipt = self._automatic_receipt(existing)
                    existing.update(status="active", delivery_mode="requested", requested_at=stamp.isoformat())
                    if receipt:
                        existing["automatic_receipt_at"] = receipt.isoformat()
                    if existing.get("generation_mode") == "llm" or not self.model_mode:
                        existing["displayed_at"] = stamp.isoformat()
                    self._save_proactive(db, existing)
                return existing
            card = self._new_support_card(db, selected, kind, rows, candidate["support_basis"], stamp,
                                          candidate.get("warning_trigger"))
            card.update(delivery_mode="requested", requested_at=stamp.isoformat())
            if not self.model_mode:
                card["displayed_at"] = stamp.isoformat()
            self._save_proactive(db, card)
            return card

    def evaluate(self, date: str | None = None, now: str | datetime | None = None) -> dict:
        stamp = _time(now)
        selected = _date(date or stamp.date().isoformat())
        with self._lock, self._connect() as db:
            self._revalidate(db, stamp)
            settings = self._settings(db)
            matches = self._matches(db, selected, stamp)
            matched = [kind for kind, rows in matches.items() if rows]
            assessment = {"reason": "No conditions are met; nothing is shown.", "matched_types": matched,
                          "extraction_mode": "local_rules", "types": self._type_conditions(db, selected, stamp, matches)}
            requested = [card for card in self._all(db, "proactive") if card["date"] == selected
                         and card["status"] == "active" and self._requested_support(card)]
            automatic_matches = self._automatic_matches(db, matches, stamp)
            real_warning_ready = bool(automatic_matches["warning"] and any(
                not memory.get("is_demo") for memory in automatic_matches["warning"]))

            def outcome(cards):
                # A direct support card remains available, while a newly
                # eligible real Warning can still enter its automatic flow.
                combined = {card["id"]: card for card in [*requested, *cards]}
                return {"cards": list(combined.values()), "assessment": assessment}

            if requested:
                for card in requested:
                    if self._displayed_at(card):
                        self._fulfill_schedule(db, card["type"], stamp)
                if not real_warning_ready or any(card["type"] == "warning" for card in requested):
                    assessment["reason"] = "Support was requested directly; automatic delivery settings are unchanged."
                    assessment["types"] = self._type_conditions(db, selected, stamp, matches)
                    return outcome([])
            blocked = self._common_block(settings, stamp)
            if blocked:
                assessment["reason"] = blocked
                return outcome([])
            automatic_types = [kind for kind, rows in automatic_matches.items() if rows]
            if len(automatic_types) > 1:
                assessment["reason"] = "Several types match at once and no priority rule is set; nothing is shown."
                return outcome([])
            if not automatic_types:
                if matched:
                    assessment["reason"] = "The scheduled display time has not arrived for the matching support."
                    return outcome([])
                memories = self._memories_for_date(db, selected)
                if any(m["status"] == "pending" for m in memories):
                    assessment["reason"] = "Some segments still have unconfirmed speakers; those segments are not used for proactive suggestions."
                elif any(m.get("is_demo") and "fictional warning demonstration" in m.get("content", "").lower() for m in memories):
                    assessment["reason"] = "Fictional warning demos need both the \"Warning\" preference and \"Warning demos\" turned on."
                elif any(self._eligible(m) and not m.get("event_time") for m in self._all(db, "memories")):
                    assessment["reason"] = UNDATED_RECORDING_REASON
                return outcome([])
            kind = automatic_types[0]
            rows = automatic_matches[kind]
            candidate = self._support_candidates(db, selected, stamp)[kind]
            support_basis = candidate["support_basis"]
            versions = {m["id"]: m["version"] for m in rows}
            existing = self._all(db, "proactive")
            pending_schedule = self._schedule_pending(db, kind, stamp)
            schedule = self._display_schedule(db, kind)
            reusable = next((card for card in reversed(existing) if card["date"] == selected and card["type"] == kind
                             and card["versions"] == versions and (card["status"] == "scheduled" or pending_schedule
                             and card["status"] in {"dismissed", "snoozed"})), None)
            # Polling/evaluation is idempotent and does not create duplicate outputs.
            for card in existing:
                if card["date"] == selected and card["type"] == kind and card["versions"] == versions:
                    if card["status"] == "active":
                        assessment["reason"] = "Conditions are met, and the source and preference checks passed before display."
                        return outcome([card])
                    if not pending_schedule and (card["status"] in {"dismissed", "done"} or (
                            card["status"] == "snoozed" and (self._requested_support(card) or
                            settings.get("snoozed_until") and _time(settings["snoozed_until"]) > stamp))):
                        assessment["reason"] = "This suggestion was dismissed, snoozed or done; it is not shown again without new evidence."
                        return outcome([])
            latest = [receipt for card in existing if (receipt := self._automatic_receipt(card))]
            if not pending_schedule and latest and stamp < max(latest) + timedelta(minutes=settings["frequency_minutes"]):
                assessment["reason"] = "The minimum interval before the next proactive suggestion has not passed."
                return outcome([])
            card = reusable or self._new_support_card(db, selected, kind, rows, support_basis, stamp,
                                                       candidate.get("warning_trigger"))
            card.update(status="active", delivery_mode="scheduled" if pending_schedule else "automatic",
                        display_schedule_revision=schedule["revision"])
            if card.get("generation_mode") == "llm" or not self.model_mode:
                card["displayed_at"] = stamp.isoformat()
                card.pop("automatic_receipt_at", None)
                self._fulfill_schedule(db, kind, stamp)
            self._save_proactive(db, card)
            # Recheck after generation, before returning it for presentation.
            self._revalidate(db, stamp)
            card = self._get(db, "proactive", card["id"])
            assessment["reason"] = "Conditions are met, and the source and preference checks passed before display."
            assessment["types"] = self._type_conditions(db, selected, stamp, matches)
            return outcome([card] if card["status"] == "active" else [])

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
            # A ready direct display may have crossed its saved time while the
            # browser was idle. Consume that due release before recording new
            # feedback, so the same one-off time cannot undo the dismissal.
            if self._displayed_at(card):
                self._fulfill_schedule(db, card["type"], _now())
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

    def proactive_text_snapshot(self, card_id: str, expected_versions: dict[str, int], display_revision: int) -> dict:
        """Recheck a queued job before network work, without holding a lock for HTTP."""
        with self._lock, self._connect() as db:
            self._revalidate(db, _now())
            card = self._get(db, "proactive", card_id)
            if (card["status"] != "active" or card["versions"] != expected_versions
                    or card.get("display_schedule_revision", 0) != display_revision):
                raise VersionConflict("Support sources, conditions or display time changed before generation.")
            return {"card": card, "memories": [self._get(db, "memories", ident) for ident in card["memory_ids"]]}

    def apply_proactive_text(self, card_id: str, title: str, summary: str,
                             expected_versions: dict[str, int], *, expected_display_revision: int | None = None) -> dict:
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
            if (card["status"] != "active" or card["versions"] != expected_versions
                    or expected_display_revision is not None
                    and card.get("display_schedule_revision", 0) != expected_display_revision):
                raise VersionConflict("關懷條件或來源版本已改變，請重新評估。")
            released = _now().isoformat()
            card.update(title=title, summary=summary, generation_mode="llm", provisional=True,
                        generated_at=released, displayed_at=released)
            if not self._requested_support(card):
                card.pop("automatic_receipt_at", None)
            self._save_proactive(db, card)
            self._revalidate(db, _now())
            current = self._get(db, "proactive", card_id)
            if current["status"] != "active":
                raise VersionConflict("展示前條件已改變，關懷文案已取消。")
            self._fulfill_schedule(db, current["type"], _now())
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
            proactives = [c for c in self._all(db, "proactive") if c["status"] == "active" and c["date"] == selected
                          and (not self.model_mode or c.get("generation_mode") == "llm")]
            settings = self._settings(db)
            matches = self._matches(db, selected, _now())
            type_conditions = self._type_conditions(db, selected, _now(), matches)
            matched = [kind for kind, rows in matches.items() if rows]
            automatic_types = [kind for kind, rows in self._automatic_matches(db, matches, _now()).items() if rows]
            reason = self._common_block(settings, _now()) or (
                "Several types match at once and no priority rule is set; nothing is shown." if len(automatic_types) > 1 else
                "Warning has priority for automatic display because current confirmed words meet a real distress condition."
                if automatic_types == ["warning"] and any(not memory.get("is_demo") for memory in matches["warning"]) else
                "Conditions are met; evaluation is pending or complete." if matched else "No conditions are met; nothing is shown.")
            if not matched and any(m["status"] == "pending" for m in daily):
                reason = "Some segments still have unconfirmed speakers; those segments are not used for proactive suggestions."
            if not matched and any(m.get("is_demo") and "fictional warning demonstration" in m.get("content", "").lower() for m in daily):
                reason = "Fictional warning demos need both the \"Warning\" preference and \"Warning demos\" turned on."
            elif not matched and reason == "No conditions are met; nothing is shown." and any(
                    self._eligible(m) and not m.get("event_time") for m in memories):
                reason = UNDATED_RECORDING_REASON
            cards = self._render_cards(db, daily, selected)
            coverage = {"active_memories": sum(self._eligible(m) for m in daily),
                        "pending_memories": sum(m["status"] == "pending" for m in daily),
                        "reviewed_memories": sum(m.get("reviewed") is True for m in daily),
                        "pending_review_memories": sum(self._eligible(m) and not m.get("reviewed") for m in daily),
                        "undated_memories": sum(not m.get("event_time") for m in memories),
                        "recording_count": len({m["recording_id"] for m in daily}),
                        "extraction_mode": "deepseek" if self.model_mode else "local_rules",
                        "summary_mode": "deepseek_automatic" if self.model_mode else "local_rule_summary",
                        "note": "Covers only imported, confirmed segments; it does not represent the whole day or a complete psychological state."}
        return {"date": selected, "dates": dates, "recordings": self.list_recordings(),
                "memories": sorted(memories, key=lambda m: m["created_at"], reverse=True),
                "cards": cards, "proactive": proactives,
                "settings": settings, "coverage": coverage,
                "assessment": {"reason": reason, "matched_types": matched,
                               "types": type_conditions,
                               "extraction_mode": "deepseek" if self.model_mode else "local_rules",
                               "gates": "deterministic"}}

    def seed_demo(self) -> dict:
        # Deterministic hash makes repeated loading a no-op; no private/real audio is supplied.
        today = _now().replace(hour=10, minute=0, second=0, microsecond=0)
        recording = self.create_recording("Fictional example: fragments of one day (transcript only)", None,
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
        scenario_name = {"reminder": "Reminder", "suggestion": "Suggestion", "comfort": "Comfort", "warning": "Warning", "none": "No type matches", "multiple": "Multiple types match",
                         "sleep": "Sleep history support", "social_history": "Social history support", "emotional_history": "Emotional history support"}[kind]
        offsets = [-2, -1, 0] if kind == "sleep" else [-1, 0] if kind in {"social_history", "emotional_history"} else [0]
        for offset in offsets:
            event_time = today + timedelta(days=offset)
            text = samples[kind]
            if offset < 0 and kind == "social_history":
                text = "I felt lonely and found it difficult to talk to people."
            elif offset < 0 and kind == "emotional_history":
                text = "I felt stressed about my work."
            recording = self.create_recording(f"Fictional scenario: {scenario_name} · {event_time.date()} (transcript only)", None,
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
