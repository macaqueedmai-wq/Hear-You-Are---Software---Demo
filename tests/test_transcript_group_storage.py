"""Transcript grouping is advisory and committed only against current evidence."""
from copy import deepcopy
from datetime import datetime
import json

import pytest

from hya.engine import Engine, HK, VersionConflict


@pytest.fixture
def case(tmp_path):
    engine = Engine(tmp_path / "isolated-data", model_mode=True)
    recording = engine.create_recording(
        "fictional.wav", None, "fictional-transcript-groups", datetime.now(HK), is_demo=True)
    # Deliberately ingest out of order: chronology comes from source timestamps.
    engine.ingest_segments(recording["id"], [
        {"start": 12, "end": 16, "text": "I will practice again tomorrow.", "speaker": "speaker_0", "words": []},
        {"start": 2, "end": 6, "text": "I felt nervous before the presentation.", "speaker": "speaker_0", "words": []},
        {"start": 7, "end": 11, "text": "I talked with Alex after class.", "speaker": "speaker_1", "words": []},
    ], extract=False)
    snapshot = engine.agent_snapshot(recording["id"])
    sources = snapshot["segments"]
    groups = [
        {"id": "group-1", "title": "Presentation and conversation", "summary": "The speakers discussed the presentation.",
         "source_segment_ids": [source["id"] for source in sources[:2]]},
        {"id": "group-2", "title": "Future practice", "summary": "A speaker mentioned practicing again.",
         "source_segment_ids": [sources[2]["id"]]},
    ]
    memories = engine.prepare_agent_memories(snapshot, [{
        "source_segment_id": sources[0]["id"], "quote": sources[0]["text"],
        "content": "A speaker described feeling nervous before a presentation.",
        "category": "mood", "memory_type": "episodic", "scope": "recorded_conversation",
    }])
    return engine, recording, snapshot, groups, memories


def commit(case, groups=None, *, memories=None):
    engine, recording, snapshot, original_groups, original_memories = case
    return engine.apply_agent_memories(
        recording["id"], original_memories if memories is None else memories, snapshot["revision"],
        {"status": "completed", "stage": "completed", "advisory_only": True,
         "transcript_groups": deepcopy(original_groups if groups is None else groups)})


def test_valid_groups_preserve_native_sources_and_require_both_reviews(case):
    engine, recording, snapshot, groups, _ = case
    committed = commit(case)
    assert committed["memory_pipeline"]["transcript_groups"] == groups
    assert committed["segments"] == snapshot["segments"]
    state = engine.state()
    assert len(state["memories"]) == 1
    memory = state["memories"][0]
    assert memory["subject_id"] == "unknown" and memory["confirmed"] is False
    assert memory["reviewed"] is False and memory["status"] == "pending"
    assert memory["evidence_kind"] == "ai_inference"
    assert memory["source_refs"][0]["text"] == snapshot["segments"][0]["text"]
    assert state["cards"] == state["proactive"] == []
    assert engine.get_recording(recording["id"])["segments"] == snapshot["segments"]


@pytest.mark.parametrize("invalid", [
    "unknown_source", "omitted_source", "duplicate_within_group", "duplicate_between_groups",
    "reversed_sources", "reversed_groups", "interleaved_groups", "empty_group", "empty_partition",
    "duplicate_group_id", "extra_identity_field", "missing_summary", "missing_group_id",
    "blank_title", "blank_summary", "non_string_source_id", "non_list_sources", "non_object_group",
])
def test_invalid_groups_cannot_partially_commit_memories_or_replace_sources(case, invalid):
    engine, recording, snapshot, original, _ = case
    groups = deepcopy(original)
    ids = [source["id"] for source in snapshot["segments"]]
    if invalid == "unknown_source":
        groups[1]["source_segment_ids"] = ["another-recordings-source"]
    elif invalid == "omitted_source":
        groups.pop()
    elif invalid == "duplicate_within_group":
        groups[0]["source_segment_ids"].append(ids[0])
    elif invalid == "duplicate_between_groups":
        groups[1]["source_segment_ids"].insert(0, ids[1])
    elif invalid == "reversed_sources":
        groups[0]["source_segment_ids"].reverse()
    elif invalid == "reversed_groups":
        groups.reverse()
    elif invalid == "interleaved_groups":
        groups[0]["source_segment_ids"], groups[1]["source_segment_ids"] = [ids[0], ids[2]], [ids[1]]
    elif invalid == "empty_group":
        groups[0]["source_segment_ids"] = []
    elif invalid == "empty_partition":
        groups = []
    elif invalid == "duplicate_group_id":
        groups[1]["id"] = groups[0]["id"]
    elif invalid == "extra_identity_field":
        groups[0]["subject_id"] = "user"
    elif invalid == "missing_summary":
        groups[0].pop("summary")
    elif invalid == "missing_group_id":
        groups[0].pop("id")
    elif invalid == "blank_title":
        groups[0]["title"] = " "
    elif invalid == "blank_summary":
        groups[0]["summary"] = " "
    elif invalid == "non_string_source_id":
        groups[0]["source_segment_ids"][0] = 1
    elif invalid == "non_list_sources":
        groups[0]["source_segment_ids"] = ids[0]
    else:
        groups[0] = "not a group"
    with pytest.raises(ValueError):
        commit(case, groups)
    assert engine.state()["memories"] == []
    current = engine.get_recording(recording["id"])
    assert current["segments"] == snapshot["segments"]
    assert "transcript_groups" not in current.get("memory_pipeline", {})


def test_source_revision_check_precedes_group_commit(case):
    engine, recording, _, groups, _ = case
    engine.ingest_segments(recording["id"], [
        {"start": 18, "end": 20, "text": "An additional original source."},
    ], extract=False)
    with pytest.raises(VersionConflict):
        commit(case, groups)
    assert engine.state()["memories"] == []
    assert "transcript_groups" not in engine.get_recording(recording["id"]).get("memory_pipeline", {})


def test_legacy_direct_success_without_groups_remains_supported(case):
    engine, recording, snapshot, _, memories = case
    result = engine.apply_agent_memories(recording["id"], memories, snapshot["revision"], {"status": "completed"})
    assert result["memory_pipeline"]["status"] == "completed"
    assert "transcript_groups" not in result["memory_pipeline"]
    assert len(engine.state()["memories"]) == 1


@pytest.mark.parametrize("status", ["pending", "processing", "failed", "stale", "completed"])
def test_progress_or_failure_update_cannot_publish_or_retain_groups(case, status):
    engine, recording, _, groups, _ = case
    commit(case)
    engine.update_pipeline(recording["id"], {"status": status, "stage": "atomic", "transcript_groups": groups})
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert report["status"] == status
    assert "transcript_groups" not in report


def test_non_successful_commit_cannot_publish_groups(case):
    engine, recording, snapshot, groups, memories = case
    with pytest.raises(ValueError):
        engine.apply_agent_memories(recording["id"], memories, snapshot["revision"],
                                    {"status": "failed", "transcript_groups": groups})
    assert engine.state()["memories"] == []


@pytest.mark.parametrize("mutation", ["confirm_identity", "append_source", "edit_memory", "delete_memory", "settings"])
def test_source_or_evidence_mutation_removes_completed_groups(case, mutation):
    engine, recording, _, _, _ = case
    commit(case)
    if mutation == "confirm_identity":
        engine.confirm_recording(recording["id"])
    elif mutation == "append_source":
        engine.ingest_segments(recording["id"], [{"start": 18, "end": 20, "text": "A new source."}], extract=False)
    elif mutation in {"edit_memory", "delete_memory"}:
        memory = engine.state()["memories"][0]
        if mutation == "edit_memory":
            engine.revise_memory(memory["id"], "A corrected interpretation.", memory["version"], confirmed=False)
        else:
            engine.delete_memory(memory["id"])
    else:
        engine.settings_patch({"quiet_mode": True})
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert report["status"] == "stale"
    assert "transcript_groups" not in report


def test_identity_confirmation_without_memories_still_invalidates_groups(case):
    engine, recording, snapshot, _, _ = case
    commit(case, memories=[])
    engine.confirm_recording(recording["id"], [snapshot["segments"][0]["id"]])
    result = engine.get_recording(recording["id"])
    assert result["memory_pipeline"]["status"] == "stale"
    assert "transcript_groups" not in result["memory_pipeline"]
    assert result["segments"][0]["confirmed"] is True
    assert result["segments"][1]["confirmed"] is False
    assert engine.state()["memories"] == []


def test_duplicate_source_ingest_keeps_current_partition(case):
    engine, recording, snapshot, groups, _ = case
    commit(case)
    result = engine.ingest_segments(recording["id"], snapshot["segments"], extract=False)
    assert result["added_segments"] == 0
    assert result["recording"]["memory_pipeline"]["transcript_groups"] == groups


@pytest.mark.parametrize("legacy_status", ["failed", "processing", "pending"])
def test_invalidation_cleans_legacy_groups_even_in_nonterminal_reports(case, legacy_status):
    engine, recording, _, groups, _ = case
    # Simulate a report persisted by an older backend that bypassed validation.
    current = engine.get_recording(recording["id"])
    current.pop("segments")
    current["memory_pipeline"] = {"status": legacy_status, "stage": "atomic", "transcript_groups": groups}
    with engine._connect() as db:
        db.execute("UPDATE recordings SET payload=? WHERE id=?", (json.dumps(current), recording["id"]))
    engine.settings_patch({"quiet_mode": True})
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert report["status"] == legacy_status
    assert "transcript_groups" not in report
