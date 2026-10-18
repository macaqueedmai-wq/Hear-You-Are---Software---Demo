"""Trigger narrow rules from precise source quotes, never withdrawn/foreign text."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from hya import engine as module
from hya.engine import Engine, HK


NOW = datetime(2026, 10, 18, 16, 0, tzinfo=HK)
NEUTRAL = "The speaker still feels anxious about the presentation and would like some reassurance."


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: NOW)
    return Engine(tmp_path, model_mode=True)


def add(engine, text, quote, *, content=NEUTRAL, confirmed=True):
    recording = engine.create_recording("invented.wav", None, "source-grounded-synthetic", NOW)
    engine.ingest_segments(recording["id"], [{"start": 1, "end": 8, "text": text,
                                            "speaker": "speaker_0", "words": []}],
                           extract=False, subject_confirmed=confirmed)
    snapshot = engine.agent_snapshot(recording["id"])
    segment = snapshot["segments"][0]
    memories = engine.prepare_agent_memories(snapshot, [{
        "source_segment_id": segment["id"], "content": content, "quote": quote,
        "category": "mood", "memory_type": "episodic", "scope": "recorded_conversation",
    }])
    result = engine.apply_agent_memories(recording["id"], memories, snapshot["revision"], {
        "status": "completed", "transcript_groups": [{"id": "source-topic", "title": "A request",
            "summary": "An unverified description.", "source_segment_ids": [segment["id"]]}],
    })
    return result, engine.get_source(memories[0]["id"])["memory"]


@pytest.mark.parametrize("text,quote,expected", [
    ("I would like some reassurance.", "I would like some reassurance.", True),
    ("I feel anxious, but I would like some reassurance.", "I would like some reassurance.", True),
    ("I would like some reassurance. If it rains, I will stay home.", "I would like some reassurance.", True),
    ("I would like some reassurance. If it rains, I will stay home.", "I would like some reassurance", True),
    ('My friend said, "I would like some reassurance."', "I would like some reassurance.", False),
    ("My friend said I would like some reassurance.", "I would like some reassurance.", False),
    ("My friend said, I need emotional support.", "I need emotional support.", False),
    ("An example is 'I need emotional support'.", "I need emotional support", False),
    ("If I need emotional support, I will ask.", "I need emotional support", False),
    ("I would like reassurance if I had a problem.", "I would like reassurance", False),
    ("If I have a problem, I would like reassurance.", "I would like reassurance.", False),
    ("I would like reassurance, if I had a problem.", "I would like reassurance", False),
    ("I would like reassurance, if I had a problem.", "I would like reassurance,", False),
    ("I do not think I need emotional support.", "I need emotional support.", False),
    ("I never said I need emotional support.", "I need emotional support.", False),
    ("I do not want reassurance.", "I do not want reassurance.", False),
])
def test_validated_quote_requires_direct_nonhypothetical_self_request(engine, text, quote, expected):
    recording, memory = add(engine, text, quote)
    original = deepcopy(recording["segments"])
    result = engine.evaluate()
    assert result["assessment"]["types"]["comfort"]["condition_met"] is expected
    assert bool(result["cards"]) is expected
    assert result["assessment"]["types"]["reminder"]["reason_code"] == "no_allowed_follow_up"
    assert result["assessment"]["types"]["suggestion"]["reason_code"] == "no_explicit_actionable_request"
    assert engine.get_recording(recording["id"])["segments"] == original
    assert engine.get_source(memory["id"])["memory"]["reviewed"] is False


@pytest.mark.parametrize("protected", ["user_corrected", "reviewed"])
def test_current_correction_is_authoritative_over_original_request(engine, protected):
    recording, memory = add(engine, "I would like some reassurance.", "I would like some reassurance.")
    with engine._connect() as db:
        engine._save_memory(db, {**memory, "content": "I do not want reassurance.", protected: True})
    assert engine.evaluate()["cards"] == []
    assert engine.get_recording(recording["id"])["segments"][0]["text"] == "I would like some reassurance."


def test_unrelated_atom_cannot_borrow_full_segment_request(engine):
    text = "I feel anxious about the presentation. I would like some reassurance."
    _, memory = add(engine, text, "I feel anxious about the presentation.",
                    content="The speaker feels anxious about a presentation.")
    assert engine.evaluate()["cards"] == []
    with engine._connect() as db:
        engine._save_memory(db, {**memory, "source_quote": "a fabricated request"})
    assert engine.evaluate()["cards"] == []


def test_disabled_preference_and_multiple_matches_keep_honest_per_type_results(engine):
    add(engine, "I need help preparing my presentation. I would like some reassurance.",
        "I need help preparing my presentation. I would like some reassurance.")
    result = engine.evaluate()
    assert result["cards"] == [] and "Several types" in result["assessment"]["reason"]
    assert result["assessment"]["types"]["suggestion"]["condition_met"] is True
    assert result["assessment"]["types"]["comfort"]["condition_met"] is True
    engine.settings_patch({"preferences": {"suggestion": False}})
    result = engine.evaluate()
    assert result["cards"][0]["type"] == "comfort"
    assert result["assessment"]["types"]["suggestion"]["reason_code"] == "preference_disabled"


def test_reminder_diagnostics_distinguish_permission_due_time_and_future_due(engine):
    _, memory = add(engine, "I finished my presentation.", "I finished my presentation.", content="The presentation was completed.")
    assert engine.evaluate()["assessment"]["types"]["reminder"]["reason_code"] == "no_allowed_follow_up"
    current = engine.revise_memory(memory["id"], memory["content"], memory["version"], follow_up={
        "allowed": True, "completed": False, "due_at": None})
    assert engine.evaluate()["assessment"]["types"]["reminder"]["reason_code"] == "no_due_time"
    engine.revise_memory(current["id"], current["content"], current["version"], follow_up={
        "allowed": True, "completed": False, "due_at": (NOW + timedelta(hours=1)).isoformat()})
    assert engine.evaluate()["assessment"]["types"]["reminder"]["reason_code"] == "not_due"
