"""Durable topic corrections and upload chronology never replace raw evidence."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from hya import engine as module
from hya.engine import Engine, HK, NotFoundError, VersionConflict
from hya.memory_agents import AgentError
from hya.pipeline import organize_recording, understand_recording
from test_memory_pipeline import FakeAgents


NOW = datetime(2026, 10, 18, 14, 0, tzinfo=HK)
TEXTS = ["I need help preparing my presentation.", "My friend helped me rehearse.", "We went for a walk."]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: NOW)
    return Engine(tmp_path / "isolated", model_mode=True)


def add(engine, *, suffix="one", recorded_at=NOW, confirmed=False):
    audio = engine.audio_dir / f"{suffix}.wav"
    audio.write_bytes(b"invented-source-audio")
    recording = engine.create_recording(audio.name, audio, f"hash-{suffix}", recorded_at)
    engine.ingest_segments(recording["id"], [
        {"start": index * 5, "end": index * 5 + 4, "text": text, "speaker": f"speaker_{index % 2}", "words": []}
        for index, text in enumerate(TEXTS)
    ], subject_confirmed=confirmed, extract=False)
    snapshot = engine.agent_snapshot(recording["id"])
    sources = snapshot["segments"]
    groups = [
        {"id": "model-topic-a", "title": "Presentation preparation", "summary": "Practice and support.",
         "source_segment_ids": [source["id"] for source in sources[:2]]},
        {"id": "model-topic-b", "title": "After rehearsal", "summary": "A walk was described.",
         "source_segment_ids": [sources[2]["id"]]},
    ]
    proposals = [{"source_segment_id": source["id"], "quote": source["text"], "content": source["text"],
                  "category": "moments", "memory_type": "episodic", "scope": "recorded_conversation"}
                 for source in sources]
    memories = engine.prepare_agent_memories(snapshot, proposals)
    recording = engine.apply_agent_memories(recording["id"], memories, snapshot["revision"],
        {"status": "completed", "advisory_only": True, "transcript_groups": groups})
    return recording, groups


def test_new_upload_uses_one_server_timestamp_and_duplicate_keeps_original(engine, monkeypatch):
    calls = []
    def tick():
        calls.append(True)
        return NOW + timedelta(seconds=len(calls))
    monkeypatch.setattr(module, "_now", tick)
    created = engine.create_uploaded_recording("new.wav", None, "new-upload")
    assert len(calls) == 1
    assert created["recorded_at"] == created["created_at"] == (NOW + timedelta(seconds=1)).isoformat()
    duplicate = engine.create_uploaded_recording("renamed.wav", None, "new-upload")
    assert duplicate["duplicate"] and duplicate["id"] == created["id"]
    assert duplicate["created_at"] == duplicate["recorded_at"] == created["created_at"]


def test_successful_groups_materialize_numbered_topics_without_identity_or_review(engine):
    recording, groups = add(engine)
    assert [topic["number"] for topic in recording["topics"]] == [1, 2]
    assert [topic["source_segment_ids"] for topic in recording["topics"]] == [group["source_segment_ids"] for group in groups]
    assert [len(topic["memory_ids"]) for topic in recording["topics"]] == [2, 1]
    assert all(topic["version"] == 1 and not topic["edited"] for topic in recording["topics"])
    assert all(not source["confirmed"] and source["subject_id"] == "unknown" for source in recording["segments"])
    assert all(not memory["reviewed"] and memory["status"] == "pending" for memory in engine.state()["memories"])
    assert engine.list_recordings()[0]["topics"] == recording["topics"]
    assert engine.state()["recordings"][0]["topics"] == recording["topics"]


def test_topic_and_explicit_fact_corrections_before_identity_are_atomic_and_protected(engine):
    recording, _ = add(engine)
    sources = deepcopy(recording["segments"])
    topic = recording["topics"][0]
    memory = engine.get_source(topic["memory_ids"][0])["memory"]
    with engine._connect() as db:
        permitted = {**memory, "follow_up": {"allowed": True, "completed": False, "due_at": NOW.isoformat()}}
        engine._save_memory(db, permitted)
    edited = engine.update_topic(recording["id"], topic["id"], topic["version"], "My corrected title", "My corrected account.",
                                 [{"id": memory["id"], "version": memory["version"], "content": "I was prepared and calm."}])
    assert edited["segments"] == sources
    assert edited["topics"][0]["version"] == 2 and edited["topics"][0]["edited"] is True
    current = engine.get_source(memory["id"])["memory"]
    assert current["content"] == "I was prepared and calm." and current["version"] == memory["version"] + 1
    assert current["subject_id"] == "unknown" and not current["confirmed"] and not current["reviewed"]
    assert current["user_corrected"] is True and current["follow_up"] is None
    retry = understand_recording(engine, FakeAgents(content="A model must not overwrite this correction."), recording["id"])
    assert retry["topics"] == edited["topics"]  # Model proposed one group; edited partition remains two.
    assert engine.get_source(memory["id"])["memory"] == current
    assert len(engine.state()["memories"]) == 3
    assert retry["segments"] == sources


def test_summary_edit_does_not_rewrite_facts_and_stale_topic_or_fact_cas_cannot_partially_save(engine):
    recording, _ = add(engine)
    topic = recording["topics"][0]
    before = deepcopy(engine.state()["memories"])
    edited = engine.update_topic(recording["id"], topic["id"], 1, "Edited topic", "Only this summary changes.")
    assert engine.state()["memories"] == before
    with pytest.raises(VersionConflict):
        Engine(engine.data_dir, model_mode=True).update_topic(recording["id"], topic["id"], 1, "Stale", "Stale")
    first, second = [engine.get_source(ident)["memory"] for ident in topic["memory_ids"]]
    with pytest.raises(VersionConflict):
        engine.update_topic(recording["id"], topic["id"], 2, "Must roll back", "Must roll back", [
            {"id": first["id"], "version": first["version"], "content": "A valid edit must not partly save."},
            {"id": second["id"], "version": second["version"] + 1, "content": "A stale edit."},
        ])
    assert engine.get_recording(recording["id"])["topics"] == edited["topics"]
    assert engine.state()["memories"] == before


@pytest.mark.parametrize("mutation", ["identity", "date", "memory", "preferences", "failed_pipeline"])
def test_topics_survive_non_source_invalidations(engine, mutation):
    recording, _ = add(engine)
    original = deepcopy(recording["topics"])
    if mutation == "identity":
        engine.confirm_recording(recording["id"])
    elif mutation == "date":
        engine.update_recording_time(recording["id"], NOW - timedelta(days=1), NOW)
    elif mutation == "memory":
        memory = engine.get_source(original[0]["memory_ids"][0])["memory"]
        engine.revise_memory(memory["id"], "A corrected fact.", memory["version"], confirmed=False)
    elif mutation == "preferences":
        engine.settings_patch({"quiet_mode": True})
    else:
        with pytest.raises(AgentError):
            understand_recording(engine, FakeAgents(fail="transcript_groups"), recording["id"])
    assert engine.get_recording(recording["id"])["topics"] == original


def test_topic_organize_never_extracts_or_duplicates_facts_and_rejects_inflight_edits(engine):
    recording, _ = add(engine)
    before = deepcopy(engine.state()["memories"])
    agents = FakeAgents()
    organized = organize_recording(engine, agents, recording["id"])
    assert agents.calls == ["transcript_groups"]
    assert organized["topic_pipeline"]["status"] == "completed"
    assert engine.state()["memories"] == before
    topic = organized["topics"][0]
    def edit_during_request(stage, *_):
        engine.update_topic(recording["id"], topic["id"], topic["version"], "Keep my edit", "An edited account.")
    with pytest.raises(VersionConflict):
        organize_recording(engine, FakeAgents(hook=edit_during_request), recording["id"])
    final = engine.get_recording(recording["id"])
    assert final["topics"][0]["title"] == "Keep my edit"
    assert final["topic_pipeline"]["status"] == "failed"
    assert engine.state()["memories"] == before


def test_full_extraction_snapshot_is_cancelled_by_concurrent_topic_correction(engine):
    recording, _ = add(engine)
    topic = recording["topics"][0]
    before = deepcopy(engine.state()["memories"])
    def edit_during_request(stage, *_):
        if stage == "governance":
            engine.update_topic(recording["id"], topic["id"], 1, "User title", "User account.")
    with pytest.raises(VersionConflict):
        understand_recording(engine, FakeAgents(hook=edit_during_request), recording["id"])
    assert engine.state()["memories"] == before
    assert engine.get_recording(recording["id"])["topics"][0]["title"] == "User title"


def test_raw_source_changes_invalidate_topics_and_recording_delete_removes_topic_history(engine):
    recording, _ = add(engine)
    engine.ingest_segments(recording["id"], [{"start": 20, "end": 24, "text": "An additional source."}], extract=False)
    assert engine.get_recording(recording["id"])["topics"] == []
    assert engine.list_recordings()[0]["topics"] == []
    engine.delete_recording(recording["id"])
    with engine._connect() as db:
        assert db.execute("SELECT count(*) FROM topic_versions").fetchone()[0] == 0


def test_raw_text_fingerprint_prevents_display_or_edit_after_source_mutation(engine):
    recording, _ = add(engine)
    source = deepcopy(recording["segments"][0])
    source["text"] = "The raw text was changed outside the normal API."
    with engine._connect() as db:
        db.execute("UPDATE segments SET payload=? WHERE id=?", (json.dumps(source), source["id"]))
    assert engine.get_recording(recording["id"])["topics"] == []
    with pytest.raises(NotFoundError):
        engine.update_topic(recording["id"], recording["topics"][0]["id"], 1, "Cannot", "Cannot")


def test_explicit_upload_alignment_versions_live_facts_preserves_metadata_and_is_idempotent(engine):
    first, _ = add(engine, recorded_at=None)
    second, _ = add(engine, suffix="second", recorded_at=NOW - timedelta(days=7), confirmed=True)
    topic = first["topics"][0]
    corrected = engine.get_source(topic["memory_ids"][0])["memory"]
    engine.update_topic(first["id"], topic["id"], 1, "User edited topic", "Keep this correction", [
        {"id": corrected["id"], "version": corrected["version"], "content": "User corrected atomic content."}])
    doomed = engine.get_source(first["topics"][1]["memory_ids"][0])["memory"]
    engine.delete_memory(doomed["id"])
    sources = {record["id"]: deepcopy(engine.get_recording(record["id"])["segments"]) for record in (first, second)}
    topics = {record["id"]: deepcopy(engine.get_recording(record["id"])["topics"]) for record in (first, second)}
    before = {memory["id"]: memory for memory in engine.state()["memories"]}
    audio = {record["id"]: open(record["file_path"], "rb").read() for record in (first, second)}
    result = engine.align_upload_dates()
    assert result["updated_recordings"] == 2 and result["updated_memories"] == 5
    for record in (first, second):
        current = engine.get_recording(record["id"])
        assert current["recorded_at"] == current["created_at"] == NOW.isoformat()
        assert current["segments"] == sources[record["id"]] and current["topics"] == topics[record["id"]]
        assert current["status"] == record["status"] and current["sha256"] == record["sha256"]
        assert current["recorded_at_history"][-1]["previous_recorded_at"] == record["recorded_at"]
        assert open(record["file_path"], "rb").read() == audio[record["id"]]
    after = {memory["id"]: memory for memory in engine.state()["memories"]}
    for ident, old in before.items():
        assert after[ident]["event_time"] == NOW.isoformat() and after[ident]["version"] == old["version"] + 1
        assert after[ident]["content"] == old["content"] and after[ident]["confirmed"] == old["confirmed"]
        assert after[ident]["subject_id"] == old["subject_id"] and after[ident].get("user_corrected") == old.get("user_corrected")
    with engine._connect() as db:
        assert engine._get(db, "memories", doomed["id"], deleted=True)["status"] == "deleted"
    versions = {ident: item["version"] for ident, item in after.items()}
    second_run = engine.align_upload_dates()
    assert second_run["updated_recordings"] == second_run["updated_memories"] == 0
    assert {item["id"]: item["version"] for item in engine.state()["memories"]} == versions


def test_confirmed_unreviewed_sources_trigger_and_schedule_without_auto_review(engine):
    recording, _ = add(engine, confirmed=True)
    memories = engine.state()["memories"]
    assert all(not memory["reviewed"] for memory in memories)
    assert engine.evaluate()["cards"][0]["type"] == "suggestion"
    assert all(not memory["reviewed"] for memory in engine.state()["memories"])


def test_alignment_is_one_atomic_transaction_across_recordings(engine, monkeypatch):
    add(engine, recorded_at=None)
    add(engine, suffix="second", recorded_at=NOW - timedelta(days=1))
    with engine._connect() as db:
        before = {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                  for table in ("recordings", "memories", "memory_versions", "topic_versions")}
    original = engine._new_version
    calls = []
    def fail_after_first(db, memory, replacement):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("Invented migration failure")
        return original(db, memory, replacement)
    monkeypatch.setattr(engine, "_new_version", fail_after_first)
    with pytest.raises(RuntimeError):
        engine.align_upload_dates()
    with engine._connect() as db:
        after = {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in before}
    assert before == after


def test_topic_fact_batch_unexpected_failure_rolls_back_all_corrections(engine, monkeypatch):
    recording, _ = add(engine)
    topic = recording["topics"][0]
    before = deepcopy(engine.state()["memories"])
    original = engine._new_version
    calls = []
    def fail_after_first(db, memory, replacement):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("Invented correction failure")
        return original(db, memory, replacement)
    monkeypatch.setattr(engine, "_new_version", fail_after_first)
    related = [engine.get_source(ident)["memory"] for ident in topic["memory_ids"]]
    with pytest.raises(RuntimeError):
        engine.update_topic(recording["id"], topic["id"], 1, "New", "New", [
            {"id": item["id"], "version": item["version"], "content": "User correction"} for item in related])
    assert engine.get_recording(recording["id"])["topics"] == recording["topics"]
    assert engine.state()["memories"] == before
