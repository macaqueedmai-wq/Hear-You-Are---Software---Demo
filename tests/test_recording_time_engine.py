"""Recording-time corrections version chronology without reinterpreting evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading

import pytest

from hya import engine as engine_module
from hya.engine import Engine, HK, NotFoundError, VersionConflict


NOW = datetime(2026, 10, 18, 16, 0, tzinfo=HK)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "_now", lambda: NOW)
    return Engine(tmp_path / "data")


def add_recording(engine, *, recorded_at=NOW, confirmed=True, extract=True, suffix="main"):
    audio = engine.audio_dir / f"synthetic-{suffix}.wav"
    audio.write_bytes(b"fictional original recording bytes")
    recording = engine.create_recording(audio.name, audio, f"synthetic-hash-{suffix}", recorded_at, True)
    engine.ingest_segments(recording["id"], [
        {"start": 1.25, "end": 4.5, "text": "I need help preparing my presentation.", "speaker": "speaker_0", "words": []},
        {"start": 5, "end": 9.75, "text": "I talked with Alex after class.", "speaker": "speaker_1", "words": []},
    ], subject_confirmed=confirmed, extract=extract)
    return engine.get_recording(recording["id"]), audio


def database_snapshot(engine):
    with engine._connect() as db:
        return {table: sorted(tuple(row) for row in db.execute(f"SELECT * FROM {table}")) for table in (
            "recordings", "segments", "memories", "memory_versions", "card_summaries", "card_reviews", "proactive", "settings")}


def publish_groups(engine, recording_id):
    snapshot = engine.agent_snapshot(recording_id)
    return engine.apply_agent_memories(recording_id, [], snapshot["revision"], {
        "status": "completed", "stage": "completed", "advisory_only": True,
        "transcript_groups": [{"id": "group-1", "title": "Fictional discussion", "summary": "A synthetic conversation.",
                               "source_segment_ids": [source["id"] for source in snapshot["segments"]]}],
    })


def test_undated_correction_preserves_source_corrections_identity_and_tombstones(engine):
    recording, audio = add_recording(engine, recorded_at=None)
    memories = engine.state()["memories"]
    corrected = engine.revise_memory(memories[0]["id"], "I talked with Alex about my presentation.", memories[0]["version"],
                                     follow_up={"due_at": (NOW + timedelta(hours=1)).isoformat(), "allowed": True, "completed": False})
    removed = next(memory for memory in memories if memory["id"] != corrected["id"])
    engine.delete_memory(removed["id"])
    engine.set_recording_status(recording["id"], "completed", metrics={"provider": "synthetic-only", "elapsed_seconds": 1})
    publish_groups(engine, recording["id"])
    before_recording = engine.get_recording(recording["id"])
    before_memory = engine.get_source(corrected["id"])["memory"]
    before_database = database_snapshot(engine)
    with engine._connect() as db:
        tombstone = deepcopy(engine._get(db, "memories", removed["id"], deleted=True))
    result = engine.update_recording_time(recording["id"], "2026-10-18T08:00:00Z", None)
    assert result["recorded_at"] == NOW.isoformat()
    assert result["segments"] == before_recording["segments"]
    assert audio.read_bytes() == b"fictional original recording bytes"
    for field in ("created_at", "status", "error", "metrics", "filename", "file_path", "sha256", "language", "is_demo", "extraction_mode"):
        assert result[field] == before_recording[field]
    assert result["memory_pipeline"]["status"] == "stale"
    assert "transcript_groups" not in result["memory_pipeline"]
    after = engine.get_source(corrected["id"])
    current = after["memory"]
    assert current["event_time"] == NOW.isoformat()
    assert current["version"] == before_memory["version"] + 1
    assert current["reviewed"] is False and current.get("reviewed_at") is None
    assert "reviewed_via" not in current
    changed_fields = {"event_time", "version", "updated_at", "reviewed", "reviewed_at", "reviewed_via"}
    assert {key: value for key, value in current.items() if key not in changed_fields} == {
        key: value for key, value in before_memory.items() if key not in changed_fields}
    assert after["versions"][-2]["event_time"] is None
    assert after["versions"][-2]["status"] == "superseded"
    with engine._connect() as db:
        assert engine._get(db, "memories", removed["id"], deleted=True) == tombstone
        assert db.execute("SELECT COUNT(*) FROM memory_versions WHERE memory_id=?", (removed["id"],)).fetchone()[0] == 0
    assert len(database_snapshot(engine)["memory_versions"]) == len(before_database["memory_versions"]) + 1


@pytest.mark.parametrize("confirmed", [False, True])
def test_all_live_memories_are_versioned_while_identity_and_raw_sources_stay_unchanged(engine, confirmed):
    recording, _ = add_recording(engine, confirmed=confirmed)
    previous = {memory["id"]: memory for memory in engine.state()["memories"]}
    corrected = NOW - timedelta(days=1)
    result = engine.update_recording_time(recording["id"], corrected, NOW)
    assert result["segments"] == recording["segments"]
    for memory in engine.state()["memories"]:
        assert memory["event_time"] == corrected.isoformat()
        assert memory["version"] == previous[memory["id"]]["version"] + 1
        assert memory["confirmed"] is confirmed
        assert memory["subject_id"] == ("user" if confirmed else "unknown")
        assert memory["status"] == ("active" if confirmed else "pending")
        assert memory["content"] == previous[memory["id"]]["content"]
        assert memory["reviewed"] is False


def test_correction_can_remove_recording_time_without_removing_evidence(engine):
    recording, _ = add_recording(engine)
    result = engine.update_recording_time(recording["id"], None, NOW.isoformat())
    state = engine.state()
    assert result["recorded_at"] is None
    assert result["segments"] == recording["segments"]
    assert all(memory["event_time"] is None and memory["version"] == 2 for memory in state["memories"])
    assert state["cards"] == state["proactive"] == []
    assert state["coverage"]["undated_memories"] == 2


@pytest.mark.parametrize("current,new,expected", [
    (NOW, "2026-10-18T08:00:00Z", "2026-10-18T16:00:00+08:00"),
    (NOW, "2026-10-18T16:00:00", "2026-10-18T08:00:00+00:00"),
    (None, None, None),
])
def test_equivalent_timestamp_noop_leaves_entire_database_unchanged(engine, current, new, expected):
    recording, _ = add_recording(engine, recorded_at=current)
    publish_groups(engine, recording["id"])
    before = database_snapshot(engine)
    result = engine.update_recording_time(recording["id"], new, expected)
    assert result["recorded_at"] == (current.isoformat() if current else None)
    assert database_snapshot(engine) == before


def test_stale_expected_time_rejects_even_when_requested_time_is_current(engine):
    recording, _ = add_recording(engine)
    before = database_snapshot(engine)
    with pytest.raises(VersionConflict, match="Recording time changed; refresh the recording before saving again"):
        engine.update_recording_time(recording["id"], NOW, None)
    assert database_snapshot(engine) == before


def test_missing_or_deleted_recording_is_not_found(engine):
    with pytest.raises(NotFoundError):
        engine.update_recording_time("missing", NOW, None)
    recording, _ = add_recording(engine)
    engine.delete_recording(recording["id"])
    with pytest.raises(NotFoundError):
        engine.update_recording_time(recording["id"], NOW, None)


@pytest.mark.parametrize("invalid", ["", "not-a-date", "2026-13-01T00:00:00", datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))])
@pytest.mark.parametrize("field", ["recorded_at", "expected_recorded_at"])
def test_invalid_or_overflowing_timestamps_fail_atomically(engine, invalid, field):
    recording, _ = add_recording(engine)
    before = database_snapshot(engine)
    values = {"recorded_at": NOW - timedelta(days=1), "expected_recorded_at": NOW}
    values[field] = invalid
    with pytest.raises(ValueError, match="Recording time must be a valid ISO date and time"):
        engine.update_recording_time(recording["id"], **values)
    assert database_snapshot(engine) == before


def test_memory_version_failure_rolls_back_recording_and_every_derived_change(engine, monkeypatch):
    recording, _ = add_recording(engine)
    publish_groups(engine, recording["id"])
    before = database_snapshot(engine)
    original = engine._new_version
    calls = []

    def fail_second_memory(db, current, replacement):
        calls.append(current["id"])
        if len(calls) == 2:
            raise RuntimeError("Synthetic transaction failure")
        return original(db, current, replacement)

    monkeypatch.setattr(engine, "_new_version", fail_second_memory)
    with pytest.raises(RuntimeError, match="Synthetic transaction failure"):
        engine.update_recording_time(recording["id"], None, NOW)
    assert len(calls) == 2
    assert database_snapshot(engine) == before


def test_concurrent_corrections_with_same_expected_time_have_only_one_winner(engine):
    recording, _ = add_recording(engine)
    engines = [Engine(engine.data_dir), Engine(engine.data_dir)]
    barrier = threading.Barrier(2)
    candidates = [NOW - timedelta(days=1), NOW - timedelta(days=2)]

    def correct(index):
        barrier.wait(timeout=5)
        try:
            result = engines[index].update_recording_time(recording["id"], candidates[index], NOW)
            return result["recorded_at"]
        except VersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, range(2)))
    assert results.count("conflict") == 1
    assert engine.get_recording(recording["id"])["recorded_at"] in results
    assert all(memory["version"] == 2 for memory in engine.state()["memories"])


def test_correction_clears_both_days_cache_reviews_and_dependent_care(engine):
    recording, _ = add_recording(engine)
    for memory in engine.state()["memories"]:
        engine.revise_memory(memory["id"], memory["content"], memory["version"])
    active = engine.evaluate()["cards"][0]
    old_date, new_date, unrelated_date = NOW.date().isoformat(), (NOW - timedelta(days=1)).date().isoformat(), "2026-10-15"
    with engine._connect() as db:
        for date in (old_date, new_date, unrelated_date):
            db.execute("INSERT INTO card_summaries VALUES (?,?,?)", (date, "mood", "{}"))
            db.execute("INSERT INTO card_reviews VALUES (?,?)", (f"daily-{date}-mood", "{}"))
    result = engine.update_recording_time(recording["id"], NOW - timedelta(days=1), NOW)
    assert result["recorded_at"].startswith(new_date)
    with engine._connect() as db:
        assert [row[0] for row in db.execute("SELECT date FROM card_summaries")] == [unrelated_date]
        assert [row[0] for row in db.execute("SELECT id FROM card_reviews")] == [f"daily-{unrelated_date}-mood"]
        invalidated = engine._get(db, "proactive", active["id"])
    assert invalidated["status"] == "invalidated"
    assert invalidated["summary"] == "" and invalidated["source_refs"] == []
    assert engine.state()["proactive"] == []


def test_zero_memory_time_correction_stales_groups_clears_days_and_invalidates_job_revision(engine):
    recording, _ = add_recording(engine, extract=False)
    publish_groups(engine, recording["id"])
    snapshot = engine.agent_snapshot(recording["id"])
    old_date, new_date = NOW.date().isoformat(), (NOW - timedelta(days=1)).date().isoformat()
    with engine._connect() as db:
        for date in (old_date, new_date):
            db.execute("INSERT INTO card_summaries VALUES (?,?,?)", (date, "mood", "{}"))
            db.execute("INSERT INTO card_reviews VALUES (?,?)", (f"daily-{date}-mood", "{}"))
    result = engine.update_recording_time(recording["id"], NOW - timedelta(days=1), NOW)
    assert result["memory_pipeline"]["status"] == "stale"
    assert "transcript_groups" not in result["memory_pipeline"]
    assert result["segments"] == recording["segments"]
    assert engine.agent_snapshot(recording["id"])["revision"] != snapshot["revision"]
    with pytest.raises(VersionConflict):
        engine.apply_agent_memories(recording["id"], [], snapshot["revision"], {"status": "completed"})
    with engine._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM card_summaries").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM card_reviews").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] == 0


def test_confirmed_undated_evidence_explains_missing_date_without_matching(engine):
    recording, _ = add_recording(engine, recorded_at=None)
    state, evaluation = engine.state(), engine.evaluate()
    for result in (state, evaluation):
        assert "no recording time" in result["assessment"]["reason"]
        assert "Set the recording time" in result["assessment"]["reason"]
        assert result["assessment"]["matched_types"] == []
        assert result["cards"] == []
    engine.update_recording_time(recording["id"], NOW, None)
    assert engine.state()["assessment"]["matched_types"] == ["suggestion"]
    assert all(not memory["reviewed"] for memory in engine.state()["memories"])
    assert engine.evaluate()["cards"][0]["type"] == "suggestion"


def test_unknown_undated_sources_do_not_claim_confirmed_evidence(engine):
    add_recording(engine, recorded_at=None, confirmed=False)
    assert "confirmed memories have no recording time" not in engine.state()["assessment"]["reason"]
    assert "confirmed memories have no recording time" not in engine.evaluate()["assessment"]["reason"]


def test_undated_message_does_not_replace_quiet_mode_or_current_identity_based_support(engine):
    add_recording(engine, recorded_at=None)
    engine.settings_patch({"quiet_mode": True})
    assert "Quiet mode" in engine.state()["assessment"]["reason"]
    assert "Quiet mode" in engine.evaluate()["assessment"]["reason"]
    engine.settings_patch({"quiet_mode": False})
    add_recording(engine, suffix="dated")
    assert engine.state()["assessment"]["matched_types"] == ["suggestion"]
    assert engine.evaluate()["cards"][0]["type"] == "suggestion"
