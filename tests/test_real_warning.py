"""Real Warning uses grounded, current words and preserves care governance."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from hya import engine as module
from hya.engine import Engine, HK, VersionConflict


NOW = datetime(2026, 10, 18, 16, 0, tzinfo=HK)
STRONG = "I feel completely overwhelmed."


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: NOW)
    return Engine(tmp_path)


def add(engine, text, *, days=0, demo=False, confirmed=True, when=None):
    recorded = when if when is not None else NOW - timedelta(days=days, minutes=1)
    recording = engine.create_recording("synthetic-warning.wav", None, str(uuid4()), recorded, demo)
    engine.ingest_segments(recording["id"], [{"start": 0, "end": 8, "text": text}],
                           subject_confirmed=confirmed)
    memories = [memory for memory in engine.state()["memories"] if memory["recording_id"] == recording["id"]]
    return recording, memories


def enable(engine, **settings):
    return engine.settings_patch({"preferences": {"warning": True}, **settings})


def generated(engine, source, quote, content=STRONG):
    recording = engine.create_recording("synthetic-llm-warning.wav", None, str(uuid4()), NOW - timedelta(minutes=1))
    engine.ingest_segments(recording["id"], [{"start": 0, "end": 8, "text": source}],
                           subject_confirmed=True, extract=False)
    snapshot = engine.agent_snapshot(recording["id"])
    prepared = engine.prepare_agent_memories(snapshot, [{
        "source_segment_id": snapshot["segments"][0]["id"], "content": content, "quote": quote,
        "category": "mood", "memory_type": "episodic", "scope": "recorded_conversation",
    }])
    engine.apply_agent_memories(recording["id"], prepared, snapshot["revision"], {"status": "completed"})
    return recording, engine.get_source(prepared[0]["id"])["memory"]


def test_real_warning_requires_its_own_opt_in_but_not_legacy_demo_permission(engine):
    add(engine, STRONG)
    assert engine.evaluate()["cards"] == []
    assert engine.state()["settings"]["warning_demo_enabled"] is False
    enable(engine)
    card = engine.evaluate()["cards"][0]
    assert card["type"] == "warning" and card["support_basis"] == "acute_distress"
    assert card["is_demo"] is False and card["demo_only"] is False
    assert card["warning_trigger"] == {
        "basis": "acute_distress", "topics": ["stress"], "dates": ["2026-10-18"],
        "day_count": 1, "window_days": 1,
        "evidence_quotes": [{"memory_id": card["memory_ids"][0], "text": STRONG}],
    }
    assert "fictional" not in (card["title"] + card["summary"]).lower()
    assert "not a diagnosis" in card["condition_reason"]
    assert engine.evaluate()["cards"][0]["id"] == card["id"]
    engine.settings_patch({"warning_demo_enabled": True})
    engine.settings_patch({"warning_demo_enabled": False})
    assert engine.evaluate()["cards"][0]["id"] == card["id"]
    engine.settings_patch({"preferences": {"warning": False}})
    assert engine.evaluate()["cards"] == []


@pytest.mark.parametrize("text", ["I can't cope today.", "I feel hopeless.", "我撐不住了。", "我感到絕望。", "我快崩溃了。"])
def test_strong_direct_english_and_chinese_expressions_trigger_real_warning(engine, text):
    enable(engine)
    add(engine, text)
    assert engine.evaluate()["cards"][0]["support_basis"] == "acute_distress"


@pytest.mark.parametrize("text", [
    "I am not feeling completely overwhelmed.", "I do not feel hopeless.",
    "If I feel hopeless, I will ask for help.", 'My friend said, "I feel hopeless."',
    "My friend said I feel hopeless.", "I felt hopeless yesterday but I feel better now.",
    "我沒有感到絕望。", "如果我感到絕望，我會找朋友。", "朋友說我感到絕望。",
    "This is a fictional warning demonstration.", "I am stressed about my exam.",
])
def test_no_warning_from_negation_conditionals_other_people_or_single_moderate_day(engine, text):
    enable(engine, warning_demo_enabled=True)
    add(engine, text)
    assert engine.evaluate()["cards"] == []


def test_repeated_distress_counts_distinct_days_with_current_evidence_and_exact_refs(engine):
    enable(engine)
    old = add(engine, "I am stressed about my exam.", days=6)[1][0]
    middle = add(engine, "I feel anxious about my work.", days=2)[1][0]
    current = add(engine, "我今天壓力很大。")[1][0]
    card = engine.evaluate()["cards"][0]
    assert card["support_basis"] == "repeated_distress"
    assert card["warning_trigger"]["dates"] == ["2026-10-12", "2026-10-16", "2026-10-18"]
    assert card["warning_trigger"]["day_count"] == 3 and card["warning_trigger"]["topics"] == ["stress"]
    assert set(card["memory_ids"]) == {old["id"], middle["id"], current["id"]}
    assert len(card["source_refs"]) == 3
    assert "3 distinct days" in card["condition_reason"]
    assert engine.state()["assessment"]["types"]["warning"]["support_basis"] == "repeated_distress"


@pytest.mark.parametrize("cases", [
    [(0, "I am stressed."), (0, "I feel anxious."), (0, "I feel under pressure.")],
    [(0, "I am stressed."), (1, "I am stressed."), (7, "I am stressed.")],
    [(1, "I am stressed."), (2, "I am stressed."), (3, "I am stressed.")],
    [(0, "I am sad."), (1, "I am lonely."), (2, "I am stressed.")],
])
def test_repeated_warning_rejects_duplicate_days_old_missing_today_or_mixed_topics(engine, cases):
    enable(engine)
    for days, text in cases:
        add(engine, text, days=days)
    assert engine.evaluate()["cards"] == []


def test_fictional_distress_never_contributes_to_real_warning(engine):
    enable(engine, warning_demo_enabled=True)
    add(engine, STRONG, demo=True)
    add(engine, "I am stressed.", days=2, demo=True)
    add(engine, "I am stressed.", days=1, demo=True)
    add(engine, "I am stressed.")
    assert engine.evaluate()["cards"] == []
    assert engine.state()["assessment"]["types"]["warning"]["condition_met"] is False


def test_warning_needs_confirmed_current_nonfuture_source(engine):
    enable(engine)
    recording, _ = add(engine, STRONG, confirmed=False)
    add(engine, STRONG, days=1)
    add(engine, STRONG, when=NOW + timedelta(minutes=1))
    assert engine.evaluate()["cards"] == []
    engine.confirm_recording(recording["id"])
    assert engine.evaluate()["cards"][0]["support_basis"] == "acute_distress"
    assert engine.evaluate(date=(NOW - timedelta(days=1)).date().isoformat())["cards"] == []


def test_real_warning_has_automatic_priority_and_other_support_stays_manually_available(engine):
    enable(engine)
    add(engine, "I need help preparing my presentation. I would like some reassurance.")
    add(engine, STRONG)
    result = engine.evaluate()
    assert result["assessment"]["matched_types"] == ["suggestion", "comfort", "warning"]
    assert [card["type"] for card in result["cards"]] == ["warning"]
    assert "Warning has priority" in engine.state()["assessment"]["reason"]
    for kind in ("suggestion", "comfort"):
        condition = result["assessment"]["types"][kind]
        assert condition["condition_met"] and condition["request_allowed"]
        assert condition["delivery"]["status"] == "paused"
        assert "Warning takes priority" in condition["delivery"]["reason"]
        assert condition["display_time"]["effective_at"] is None
    assert engine.request_support("comfort")["type"] == "comfort"


def test_prior_manually_requested_support_does_not_prevent_new_automatic_warning(engine):
    enable(engine)
    add(engine, "I would like some reassurance.")
    comfort = engine.request_support("comfort")
    add(engine, STRONG)
    result = engine.evaluate()
    assert {card["type"] for card in result["cards"]} == {"comfort", "warning"}
    preserved = next(card for card in result["cards"] if card["id"] == comfort["id"])
    warning = next(card for card in result["cards"] if card["type"] == "warning")
    assert preserved["delivery_mode"] == "requested"
    assert warning["delivery_mode"] == "automatic"
    assert engine.evaluate()["cards"] == result["cards"]


@pytest.mark.parametrize("gate", ["quiet", "snoozed", "future_time"])
def test_existing_direct_support_stays_visible_while_warning_waits_for_gate(engine, monkeypatch, gate):
    clock = [NOW]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    enable(engine)
    add(engine, "I would like some reassurance.")
    comfort = engine.request_support("comfort")
    future = (NOW + timedelta(minutes=30)).isoformat()
    if gate == "quiet":
        engine.settings_patch({"quiet_mode": True})
    elif gate == "snoozed":
        engine.settings_patch({"snoozed_until": future})
    else:
        engine.update_display_time("warning", future, None)
    add(engine, STRONG)
    assert [card["id"] for card in engine.evaluate()["cards"]] == [comfort["id"]]
    with engine._connect() as db:
        assert not any(card["type"] == "warning" for card in engine._all(db, "proactive"))
    if gate == "quiet":
        engine.settings_patch({"quiet_mode": False})
    else:
        clock[0] += timedelta(minutes=31)
    result = engine.evaluate()
    assert {card["type"] for card in result["cards"]} == {"comfort", "warning"}
    assert next(card for card in result["cards"] if card["id"] == comfort["id"])["delivery_mode"] == "requested"


def test_requested_card_keeps_its_prior_automatic_interval_without_starving_warning(engine, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    enable(engine)
    add(engine, "I would like some reassurance.")
    automatic = engine.evaluate()["cards"][0]
    comfort = engine.request_support("comfort")
    assert comfort["id"] == automatic["id"] and comfort["delivery_mode"] == "requested"
    add(engine, STRONG)
    waiting = engine.evaluate()
    assert [card["id"] for card in waiting["cards"]] == [comfort["id"]]
    assert "minimum interval" in waiting["assessment"]["reason"]
    clock[0] += timedelta(minutes=61)
    result = engine.evaluate()
    assert {card["type"] for card in result["cards"]} == {"comfort", "warning"}
    assert next(card for card in result["cards"] if card["type"] == "warning")["delivery_mode"] == "automatic"


def test_warning_schedule_defers_its_priority_and_manual_display_still_works(engine):
    enable(engine)
    add(engine, "I would like some reassurance.")
    add(engine, STRONG)
    future = (NOW + timedelta(hours=2)).isoformat()
    engine.update_display_time("warning", future, None)
    result = engine.evaluate()
    assert result["cards"][0]["type"] == "comfort"
    assert result["assessment"]["types"]["warning"]["delivery"]["status"] == "paused"
    assert result["assessment"]["types"]["warning"]["display_time"]["scheduled_at"] == future
    assert engine.request_support("warning")["support_basis"] == "acute_distress"


def test_real_warning_honors_quiet_mode_and_existing_cooldown(engine):
    enable(engine)
    add(engine, "I would like some reassurance.")
    assert engine.evaluate()["cards"][0]["type"] == "comfort"
    add(engine, STRONG)
    result = engine.evaluate()
    assert result["cards"] == [] and "minimum interval" in result["assessment"]["reason"]
    engine.settings_patch({"quiet_mode": True})
    assert "Quiet mode" in engine.evaluate()["assessment"]["reason"]
    assert engine.request_support("warning")["type"] == "warning"


def test_real_warning_dismissal_does_not_repeat_until_new_evidence(engine):
    enable(engine)
    add(engine, STRONG)
    card = engine.evaluate()["cards"][0]
    engine.feedback(card["id"], "dismiss")
    assert engine.evaluate()["cards"] == []


def test_user_correction_and_deletion_retract_warning_and_its_quote_metadata(engine):
    enable(engine)
    recording, memories = add(engine, STRONG)
    memory = memories[0]
    card = engine.evaluate()["cards"][0]
    engine.revise_memory(memory["id"], "I feel calm and able to cope today.", memory["version"])
    assert engine.evaluate()["cards"] == []
    with engine._connect() as db:
        cancelled = engine._get(db, "proactive", card["id"])
    assert cancelled["status"] == "invalidated" and not cancelled.get("warning_trigger")
    assert engine.get_recording(recording["id"])["segments"][0]["text"] == STRONG
    _, new_memories = add(engine, STRONG)
    requested = engine.request_support("warning")
    engine.delete_memory(new_memories[0]["id"])
    with engine._connect() as db:
        assert not engine._get(db, "proactive", requested["id"]).get("warning_trigger")
    assert engine.evaluate()["cards"] == []


def test_model_paraphrase_never_establishes_warning_without_grounded_distress(engine):
    enable(engine)
    generated(engine, "I finished my presentation.", "I finished my presentation.")
    assert engine.evaluate()["cards"] == []


def test_exact_grounded_model_quote_can_establish_warning_despite_neutral_paraphrase(engine):
    enable(engine)
    _, memory = generated(engine, STRONG, STRONG, "The speaker discusses their current day.")
    card = engine.evaluate()["cards"][0]
    assert card["memory_ids"] == [memory["id"]]
    assert card["warning_trigger"]["evidence_quotes"] == [{"memory_id": memory["id"], "text": STRONG}]
    with engine._connect() as db:
        engine._save_memory(db, {**memory, "source_quote": "I can't cope with the day."})
    assert engine.evaluate()["cards"] == []


@pytest.mark.parametrize("source,quote", [
    ('My friend said, "I feel hopeless."', "I feel hopeless."),
    ("朋友說『我撐不住了』。", "我撐不住了"),
    ("If I feel hopeless, I will ask for help.", "I feel hopeless"),
    ("I feel hopeless, if things go wrong.", "I feel hopeless"),
    ("I am calm. My friend said I feel hopeless.", "I am calm."),
])
def test_model_quote_cannot_borrow_reported_conditional_or_unrelated_atom_distress(engine, source, quote):
    enable(engine)
    generated(engine, source, quote)
    assert engine.evaluate()["cards"] == []


def test_current_user_correction_can_establish_or_withdraw_model_interpretation(engine):
    enable(engine)
    _, memory = generated(engine, "I finished my presentation.", "I finished my presentation.")
    corrected = engine.revise_memory(memory["id"], STRONG, memory["version"])
    card = engine.request_support("warning")
    assert card["versions"] == {corrected["id"]: corrected["version"]}
    assert card["warning_trigger"]["evidence_quotes"][0]["text"] == STRONG
    engine.revise_memory(corrected["id"], "I feel okay today.", corrected["version"])
    with pytest.raises(VersionConflict):
        engine.apply_proactive_text(card["id"], "Old warning", "Old warning words", card["versions"])
    assert engine.evaluate()["cards"] == []


def test_legacy_fictional_fixture_keeps_separate_permission_and_no_real_priority(engine):
    add(engine, "This is a fictional warning demonstration.", demo=True)
    enable(engine)
    assert engine.evaluate()["cards"] == []
    engine.settings_patch({"warning_demo_enabled": True})
    fixture = engine.evaluate()["cards"][0]
    assert fixture["support_basis"] == "fictional_warning" and fixture["demo_only"] is True
    add(engine, "I would like some reassurance.")
    result = engine.evaluate()
    assert result["cards"] == [] and "Several types" in result["assessment"]["reason"]
