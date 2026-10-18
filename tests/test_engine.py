"""Behavioral checks for evidence, lifecycle, condition gates and cached summaries."""

import json
import sqlite3
from datetime import timedelta

import pytest

from hya.engine import Engine, NotFoundError, VersionConflict, _now


@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path)


def add(engine, text, *, confirmed=True, recorded_at=None, is_demo=False, sha=None, reviewed=True):
    stamp = recorded_at or _now().isoformat()
    recording = engine.create_recording("test.wav", None, sha or f"hash-{text}", stamp, is_demo)
    engine.ingest_segments(recording["id"], [{"start": 2.5, "end": 9.5, "text": text}], subject_confirmed=confirmed)
    memory = next(m for m in engine.state()["memories"] if m["recording_id"] == recording["id"])
    if confirmed and reviewed:
        state = engine.state(memory["event_time"][:10])
        source_ids = {m["id"] for m in state["memories"] if m["recording_id"] == recording["id"]}
        for card in state["cards"]:
            if source_ids.intersection(card["memory_ids"]):
                engine.confirm_card(card["id"], card["versions"], card["review_token"])
        memory = engine.get_source(memory["id"])["memory"]
    return recording, memory


def test_raw_source_and_unknown_speaker_require_explicit_confirmation(engine):
    recording, memory = add(engine, "I would like some reassurance.", confirmed=False)
    assert memory["status"] == "pending"
    assert memory["subject_id"] == "unknown"
    assert engine.state()["cards"] == []
    assert engine.evaluate()["cards"] == []
    source = engine.get_source(memory["id"])
    assert source["segments"][0]["start"] == 2.5
    assert source["segments"][0]["text"] == "I would like some reassurance."
    engine.confirm_recording(recording["id"], [source["segments"][0]["id"]])
    current = engine.get_source(memory["id"])["memory"]
    assert current["version"] == 2 and current["confirmed"]
    assert current["reviewed"] is False and engine.evaluate()["cards"][0]["type"] == "comfort"
    card = engine.state()["cards"][0]
    engine.confirm_card(card["id"], card["versions"], card["review_token"])
    assert engine.evaluate()["cards"][0]["type"] == "comfort"


def test_conversation_that_helped_emotion_remains_visible_in_social_card(engine):
    add(engine, "Today I felt nervous before my presentation.")
    _, social = add(engine, "I talked with my friend Sarah after class, and our conversation helped me feel better.")
    add(engine, "Tomorrow I need to review my slides.")
    cards = {card["category"]: card for card in engine.state()["cards"]}
    assert set(cards) == {"mood", "social", "moments"}
    assert social["id"] in cards["social"]["memory_ids"]
    assert "feeling better after talking with a friend" in cards["social"]["summary"]


def test_partial_identity_confirmation_does_not_assign_other_speakers(engine):
    recording = engine.create_recording("two-speakers.wav", None, "two-speakers", _now().isoformat())
    engine.ingest_segments(recording["id"], [
        {"start": 0, "end": 3, "text": "I need help preparing my presentation."},
        {"start": 3, "end": 6, "text": "Please comfort me."},
    ])
    segments = engine.get_recording(recording["id"])["segments"]
    engine.confirm_recording(recording["id"], [segments[0]["id"]])
    memories = engine.state()["memories"]
    assert sum(m["subject_id"] == "user" for m in memories) == 1
    assert sum(m["status"] == "pending" for m in memories) == 1
    assert engine.evaluate()["assessment"]["matched_types"] == ["suggestion"]
    card = engine.state()["cards"][0]
    engine.confirm_card(card["id"], card["versions"], card["review_token"])
    assert engine.evaluate()["assessment"]["matched_types"] == ["suggestion"]
    with pytest.raises(ValueError):
        engine.confirm_recording(recording["id"], ["not-a-segment"])


def test_unknown_recording_time_is_not_replaced_by_import_time(engine):
    recording = engine.create_recording("undated.wav", None, "undated", None)
    engine.ingest_segments(recording["id"], [{"start": 0, "end": 2, "text": "I feel calm."}], subject_confirmed=True)
    memory = engine.state()["memories"][0]
    assert memory["event_time"] is None
    assert engine.state()["cards"] == []
    assert engine.state()["coverage"]["undated_memories"] == 1


def test_hash_dedup_and_retry_preserve_evidence_count(engine):
    recording, memory = add(engine, "I felt happy today.", sha="same-file-hash")
    duplicate = engine.create_recording("different-name.wav", None, "same-file-hash", _now().isoformat())
    assert duplicate["duplicate"] and duplicate["id"] == recording["id"]
    ingest = engine.ingest_segments(recording["id"], [{"start": 2.5, "end": 9.5, "text": "I felt happy today."}])
    assert ingest["added_segments"] == 0
    assert len(engine.state()["memories"]) == 1
    assert engine.state()["cards"][0]["recording_count"] == 1
    engine.revise_memory(memory["id"], "I felt calm today.", memory["version"])
    engine.ingest_segments(recording["id"], [{"start": 2.5, "end": 9.5, "text": "I felt happy today."}])
    assert engine.state()["memories"][0]["content"] == "I felt calm today."


def test_correction_versions_original_text_and_display_invalidation(engine):
    recording, memory = add(engine, "I need help preparing my presentation.")
    card = engine.evaluate()["cards"][0]
    updated = engine.revise_memory(memory["id"], "I finished the presentation today.", memory["version"])
    assert updated["version"] == memory["version"] + 1
    source = engine.get_source(memory["id"])
    assert source["segments"][0]["text"] == memory["content"]
    assert source["versions"][0]["status"] == "superseded"
    assert "finishing the presentation" in engine.state()["cards"][0]["summary"]
    assert engine.state()["proactive"] == []
    assert engine.evaluate()["cards"] == []
    with pytest.raises(VersionConflict):
        engine.revise_memory(memory["id"], "stale edit", memory["version"])
    with pytest.raises(VersionConflict):
        engine.feedback(card["id"], "helpful")


def test_delete_memory_scrubs_history_and_derived_output_without_deleting_recording(engine):
    recording, memory = add(engine, "I would like some reassurance.")
    card = engine.evaluate()["cards"][0]
    engine.apply_proactive_text(card["id"], "私人情境標題", "根據私人情境提供支持。", card["versions"])
    engine.revise_memory(memory["id"], "I need emotional support.", memory["version"])
    engine.delete_memory(memory["id"])
    with pytest.raises(NotFoundError):
        engine.get_source(memory["id"])
    assert engine.get_recording(recording["id"])["segments"]
    assert engine.state()["memories"] == [] and engine.state()["cards"] == []
    with sqlite3.connect(engine.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] == 0
        memory_payload = db.execute("SELECT payload FROM memories").fetchone()[0]
        assert "reassurance" not in memory_payload and "support" not in memory_payload
        proactive = json.loads(db.execute("SELECT payload FROM proactive").fetchone()[0])
        assert proactive["summary"] == "" and proactive["source_refs"] == []
        assert proactive["title"] == "Cancelled suggestion"


def test_delete_recording_cascades_and_removes_only_managed_audio(engine):
    path = engine.audio_dir / "sample.wav"
    path.write_bytes(b"fake test bytes")
    recording = engine.create_recording("sample.wav", path, "audio-to-remove", _now().isoformat())
    engine.ingest_segments(recording["id"], [{"start": 0, "end": 2, "text": "Please comfort me."}], subject_confirmed=True)
    engine.evaluate()
    engine.delete_recording(recording["id"])
    assert not path.exists()
    assert engine.state()["recordings"] == [] and engine.state()["memories"] == []
    with pytest.raises(NotFoundError):
        engine.get_recording(recording["id"])
    with sqlite3.connect(engine.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] == 0


def test_reminder_needs_all_structured_preconditions(engine):
    _, memory = add(engine, "I need to send my presentation.")
    assert engine.evaluate()["cards"] == []  # Mention alone is not a due time/permission.
    future = (_now() + timedelta(days=1)).isoformat()
    memory = engine.revise_memory(memory["id"], memory["content"], memory["version"],
                                  follow_up={"due_at": future, "allowed": True, "completed": False})
    assert engine.evaluate()["cards"] == []
    past = (_now() - timedelta(minutes=1)).isoformat()
    memory = engine.revise_memory(memory["id"], memory["content"], memory["version"],
                                  follow_up={"due_at": past, "allowed": False, "completed": False})
    assert engine.evaluate()["cards"] == []
    memory = engine.revise_memory(memory["id"], memory["content"], memory["version"],
                                  follow_up={"due_at": past, "allowed": True, "completed": False})
    card = engine.evaluate()["cards"][0]
    assert card["type"] == "reminder"
    result = engine.feedback(card["id"], "done")
    assert result["status"] == "done"
    updated = engine.get_source(memory["id"])["memory"]
    assert updated["follow_up"]["completed"] and updated["version"] == memory["version"] + 1
    assert engine.evaluate()["cards"] == []


@pytest.mark.parametrize("text,kind", [
    ("I need help preparing my presentation.", "suggestion"),
    ("Please comfort me.", "comfort"),
])
def test_precise_single_type_gates(engine, text, kind):
    add(engine, text)
    evaluation = engine.evaluate()
    assert evaluation["assessment"]["matched_types"] == [kind]
    assert evaluation["cards"][0]["type"] == kind
    assert engine.evaluate()["cards"][0]["id"] == evaluation["cards"][0]["id"]


@pytest.mark.parametrize("text", [
    "I felt sad today.",
    "My friend needs help preparing a presentation.",
    "I completed my draft.",
    "I need help with something.",
])
def test_no_match_and_unclear_need_do_not_create_proactive_card(engine, text):
    add(engine, text)
    assert engine.evaluate()["cards"] == []


def test_multiple_types_are_not_resolved_by_invented_priority(engine):
    add(engine, "I need help preparing my presentation. Please comfort me.")
    evaluation = engine.evaluate()
    assert set(evaluation["assessment"]["matched_types"]) == {"suggestion", "comfort"}
    assert "Several types" in evaluation["assessment"]["reason"] and evaluation["cards"] == []


def test_warning_requires_fictional_source_and_two_explicit_opt_ins(engine):
    _, memory = add(engine, "This is a fictional warning demonstration.", is_demo=True)
    assert engine.evaluate()["cards"] == []
    engine.settings_patch({"preferences": {"warning": True}})
    assert engine.evaluate()["cards"] == []
    engine.settings_patch({"warning_demo_enabled": True})
    card = engine.evaluate()["cards"][0]
    assert card["type"] == "warning" and card["is_demo"]
    engine.settings_patch({"warning_demo_enabled": False})
    assert engine.state()["proactive"] == []
    with pytest.raises(VersionConflict):
        engine.feedback(card["id"], "helpful")


def test_real_recording_cannot_trigger_demo_warning(engine):
    add(engine, "This is a fictional warning demonstration.", is_demo=False)
    engine.settings_patch({"preferences": {"warning": True}, "warning_demo_enabled": True})
    assert engine.evaluate()["cards"] == []


def test_mixed_real_and_demo_evidence_remains_labelled(engine):
    add(engine, "Please comfort me.", is_demo=True)
    add(engine, "I would like some reassurance.", is_demo=False)
    daily = engine.state()["cards"][0]
    proactive = engine.evaluate()["cards"][0]
    for card in (daily, proactive):
        assert card["is_demo"] is True and card["demo_only"] is False
        assert {ref["is_demo"] for ref in card["source_refs"]} == {True, False}


def test_preferences_and_quiet_mode_revalidated_before_display(engine):
    add(engine, "Please comfort me.")
    card = engine.evaluate()["cards"][0]
    engine.settings_patch({"quiet_mode": True})
    assert engine.state()["proactive"] == [] and engine.evaluate()["cards"] == []
    with pytest.raises(VersionConflict):
        engine.feedback(card["id"], "dismiss")
    engine.settings_patch({"quiet_mode": False, "preferences": {"comfort": False}})
    assert engine.evaluate()["cards"] == []


def test_dismiss_and_snooze_never_rewrite_personal_facts(engine):
    _, memory = add(engine, "Please comfort me.")
    card = engine.evaluate()["cards"][0]
    engine.feedback(card["id"], "dismiss")
    assert engine.get_source(memory["id"])["memory"] == memory
    assert engine.evaluate()["cards"] == []
    _, other = add(engine, "I need help preparing my presentation.")
    # Close comfort preference so this is a single match, then remove frequency delay.
    engine.settings_patch({"preferences": {"comfort": False}, "frequency_minutes": 0})
    card = engine.evaluate()["cards"][0]
    engine.feedback(card["id"], "snooze")
    assert engine.get_source(other["id"])["memory"] == other
    assert engine.evaluate()["cards"] == []


def test_frequency_limits_new_candidates_but_does_not_hide_same_displayed_card(engine):
    _, memory = add(engine, "Please comfort me.")
    card = engine.evaluate()["cards"][0]
    engine.feedback(card["id"], "dismiss")
    add(engine, "I need emotional support.")
    evaluation = engine.evaluate()
    assert not evaluation["cards"] and "minimum interval" in evaluation["assessment"]["reason"]
    engine.settings_patch({"frequency_minutes": 0})
    fresh = engine.evaluate()["cards"][0]
    assert fresh["id"] != card["id"]
    assert engine.evaluate()["cards"][0]["id"] == fresh["id"]


def test_persistence_retrieval_and_date_filter(engine):
    yesterday = _now() - timedelta(days=1)
    _, memory = add(engine, "I talked with Alex about the group project.", recorded_at=yesterday.isoformat())
    restarted = Engine(engine.data_dir)
    assert restarted.retrieve("project", date=yesterday.date().isoformat(), person="Alex")[0]["id"] == memory["id"]
    assert restarted.retrieve("project", date=_now().date().isoformat()) == []
    assert restarted.state(yesterday.date().isoformat())["cards"][0]["category"] == "social"


def test_historic_or_future_requests_do_not_become_current_emotional_needs(engine):
    yesterday = _now() - timedelta(days=1)
    add(engine, "Please comfort me.", recorded_at=yesterday.isoformat())
    assert engine.evaluate(date=yesterday.date().isoformat())["cards"] == []
    future = _now() + timedelta(hours=1)
    add(engine, "I need help preparing my presentation.", recorded_at=future.isoformat())
    assert engine.evaluate(date=future.date().isoformat())["cards"] == []


def test_changed_content_does_not_inherit_old_task_permission(engine):
    _, memory = add(engine, "I need to send my presentation.")
    memory = engine.revise_memory(memory["id"], memory["content"], memory["version"],
                                  follow_up={"due_at": (_now() - timedelta(minutes=1)).isoformat(),
                                             "allowed": True, "completed": False})
    assert engine.evaluate()["cards"]
    revised = engine.revise_memory(memory["id"], "I already sent the presentation.", memory["version"])
    assert revised["follow_up"] is None
    assert engine.evaluate()["cards"] == []


def test_demo_data_is_explicit_idempotent_and_scenarios_use_real_gates(engine):
    state = engine.seed_demo()
    assert {c["category"] for c in state["cards"]} == {"mood", "social", "moments"}
    assert all(r["is_demo"] and not r["has_audio"] for r in state["recordings"])
    assert len(engine.seed_demo()["memories"]) == 3
    assert engine.set_demo_scenario("comfort")["proactive"][0]["type"] == "comfort"
    assert engine.set_demo_scenario("none")["proactive"] == []
    multiple = engine.set_demo_scenario("multiple")
    assert multiple["proactive"] == [] and len(multiple["assessment"]["matched_types"]) == 2
    assert engine.set_demo_scenario("warning")["proactive"] == []


def test_retention_cascades_and_starts_on_import_not_old_event_date(engine):
    old_event = _now() - timedelta(days=400)
    recording, _ = add(engine, "I talked with Alex.", recorded_at=old_event.isoformat())
    assert engine.cleanup_retention()["deleted_recordings"] == 0
    with sqlite3.connect(engine.db_path) as db:
        payload = json.loads(db.execute("SELECT payload FROM recordings WHERE id=?", (recording["id"],)).fetchone()[0])
        payload["created_at"] = (_now() - timedelta(days=31)).isoformat()
        db.execute("UPDATE recordings SET payload=? WHERE id=?", (json.dumps(payload), recording["id"]))
    assert engine.cleanup_retention()["deleted_recordings"] == 1
    assert engine.state()["recordings"] == [] and engine.state()["memories"] == []


def test_llm_summaries_are_version_checked_and_source_bound(engine):
    _, memory = add(engine, "I feel happy today.")
    summary = [{"category": "mood", "title": "心情片段", "summary": "你提到今天感到開心。", "memory_ids": [memory["id"]]}]
    state = engine.apply_card_summaries(_now().date().isoformat(), summary, {memory["id"]: memory["version"]})
    assert state["cards"][0]["analysis_mode"] == "llm"
    assert state["cards"][0]["provisional"]
    assert state["cards"][0]["source_refs"][0]["text"] == memory["content"]
    engine.revise_memory(memory["id"], "I feel calm today.", memory["version"])
    assert engine.state()["cards"][0]["analysis_mode"] == "local_rules"
    with pytest.raises(VersionConflict):
        engine.apply_card_summaries(_now().date().isoformat(), summary, {memory["id"]: memory["version"]})


def test_added_evidence_invalidates_llm_snapshot(engine):
    _, memory = add(engine, "I feel happy today.")
    summary = [{"category": "mood", "title": "心情片段", "summary": "你提到開心。", "memory_ids": [memory["id"]]}]
    engine.apply_card_summaries(_now().date().isoformat(), summary, {memory["id"]: memory["version"]})
    add(engine, "I feel nervous this evening.")
    assert engine.state()["cards"][0]["analysis_mode"] == "local_rules"
    with pytest.raises(VersionConflict):
        engine.apply_card_summaries(_now().date().isoformat(), summary, {memory["id"]: memory["version"]})


def test_optional_proactive_model_can_only_change_wording(engine):
    _, memory = add(engine, "Please comfort me.")
    card = engine.evaluate()["cards"][0]
    current = engine.apply_proactive_text(card["id"], "給自己一點空間", "你提到希望得到支持，可以先停一停。", card["versions"])
    assert current["generation_mode"] == "llm" and current["provisional"]
    for field in ("type", "condition_reason", "memory_ids", "source_refs", "versions", "action_text"):
        assert current[field] == card[field]
    assert engine.get_source(memory["id"])["memory"] == memory


def test_proactive_model_response_rejected_after_preferences_evidence_or_type_conflict(engine, monkeypatch):
    from hya import engine as engine_module

    clock = [_now().replace(hour=10, minute=0, second=0, microsecond=0)]
    monkeypatch.setattr(engine_module, "_now", lambda: clock[0])
    monkeypatch.setitem(globals(), "_now", lambda: clock[0])
    _, memory = add(engine, "Please comfort me.")
    card = engine.evaluate()["cards"][0]
    with pytest.raises(VersionConflict):
        engine.apply_proactive_text(card["id"], "支持", "可以先停一停。", {memory["id"]: 999})
    engine.settings_patch({"quiet_mode": True})
    with pytest.raises(VersionConflict):
        engine.apply_proactive_text(card["id"], "支持", "可以先停一停。", card["versions"])
    engine.settings_patch({"quiet_mode": False})
    # Cancelling displayed content must retain its automatic delivery interval.
    assert engine.evaluate()["cards"] == []
    clock[0] += timedelta(minutes=61)
    fresh = engine.evaluate()["cards"][0]
    add(engine, "I need help preparing my presentation.")
    with pytest.raises(VersionConflict):
        engine.apply_proactive_text(fresh["id"], "支持", "可以先停一停。", fresh["versions"])
    assert engine.state()["proactive"] == []


def test_settings_validation_is_atomic(engine):
    original = engine.state()["settings"]
    with pytest.raises(ValueError):
        engine.settings_patch({"quiet_mode": True, "retention_days": -2})
    assert engine.state()["settings"] == original


def test_ingest_validation_rolls_back_partial_transcript(engine):
    recording = engine.create_recording("bad.wav", None, "bad", _now().isoformat())
    with pytest.raises(ValueError):
        engine.ingest_segments(recording["id"], [
            {"start": 0, "end": 2, "text": "First sentence."},
            {"start": 3, "end": 1, "text": "Invalid timestamp."},
        ])
    assert engine.get_recording(recording["id"])["segments"] == []


def test_legacy_memory_without_review_flag_keeps_identity_based_support_without_marking_reviewed(engine):
    _, memory = add(engine, "Please comfort me.")
    assert engine.evaluate()["cards"]
    with sqlite3.connect(engine.db_path) as db:
        payload = json.loads(db.execute("SELECT payload FROM memories WHERE id=?", (memory["id"],)).fetchone()[0])
        payload.pop("reviewed")
        db.execute("UPDATE memories SET payload=? WHERE id=?", (json.dumps(payload), memory["id"]))
    restarted = Engine(engine.data_dir)
    assert restarted.state()["memories"][0]["reviewed"] is False
    assert restarted.state()["proactive"]
    assert restarted.evaluate()["cards"][0]["type"] == "comfort"


def test_sleep_pattern_counts_distinct_verified_days_not_duplicate_uploads(engine):
    now = _now()
    for offset in (-1, 0):
        add(engine, "I did not get enough sleep last night.", recorded_at=(now + timedelta(days=offset)).isoformat(), sha=f"sleep-{offset}")
    add(engine, "I did not get enough sleep last night.", sha="sleep-0")
    assert engine.evaluate()["cards"] == []
    recording, memory = add(engine, "I did not get enough sleep last night.",
                            recorded_at=(now - timedelta(days=2)).isoformat(), sha="sleep--2", reviewed=False)
    assert engine.evaluate()["cards"][0]["support_basis"] == "sleep_pattern"
    assert engine.get_source(memory["id"])["memory"]["reviewed"] is False
    card = engine.state(memory["event_time"][:10])["cards"][0]
    engine.confirm_card(card["id"], card["versions"], card["review_token"])
    support = engine.evaluate()["cards"][0]
    assert support["support_basis"] == "sleep_pattern"
    assert len(support["historical_memory_ids"]) == 2 and len(support["current_memory_ids"]) == 1
    assert support["can_schedule_bedtime"]


def test_emotional_history_support_keeps_every_period_and_invalidates_changed_history(engine):
    state = engine.set_demo_scenario("emotional_history")
    support = state["proactive"][0]
    assert support["support_basis"] == "emotional_follow_up" and support["type"] == "comfort"
    assert support["historical_memory_ids"] and support["current_memory_ids"]
    assert set(support["memory_ids"]) == set(support["versions"])
    old_id = support["historical_memory_ids"][0]
    memory = engine.get_source(old_id)["memory"]
    engine.revise_memory(old_id, "I felt calm yesterday.", memory["version"])
    assert not any(c["id"] == support["id"] for c in engine.state()["proactive"])
    with pytest.raises(VersionConflict):
        engine.apply_proactive_text(support["id"], "支持", "先停一停。", support["versions"])


@pytest.mark.parametrize("text", [
    "I have not finished my presentation.",
    "I never talked with my friend Alex.",
    "I do not feel stressed.",
    "I do not feel lonely.",
])
def test_negated_statements_are_not_paraphrased_as_positive_events_or_feelings(engine, text):
    add(engine, text)
    assert "could not reliably summarise" in engine.state()["cards"][0]["summary"]
    assert not engine._stress_signal(text) and not engine._lonely_signal(text)
    assert engine.evaluate()["cards"] == []


def test_sleep_pattern_needs_explicit_insufficiency_not_an_hour_threshold(engine):
    now = _now()
    for offset in (-2, -1, 0):
        add(engine, "I only slept 12 hours.", recorded_at=(now + timedelta(days=offset)).isoformat(), sha=f"hours-{offset}")
    assert engine.evaluate()["cards"] == []
    assert "not getting enough sleep" not in engine.state()["cards"][0]["summary"]
    assert engine._sleep_signal("I did not get enough sleep last night.")
    assert engine._sleep_signal("I didn't get enough sleep last night.")
    assert not engine._sleep_signal("I am not sleep deprived.")


def test_negated_social_improvement_does_not_trigger_historical_encouragement(engine):
    now = _now()
    add(engine, "I felt lonely.", recorded_at=(now - timedelta(days=1)).isoformat())
    add(engine, "I never talked with my friend Alex and felt better.")
    assert engine.evaluate()["cards"] == []


def test_review_slides_and_meeting_reminder_are_summarized_without_task_authorization(engine):
    add(engine, "Tomorrow I need to review my slides at 9 in the morning. Please remind me to prepare before the meeting.")
    card = engine.state()["cards"][0]
    assert "review your slides at 9 tomorrow morning" in card["summary"] and "prepare before the meeting" in card["summary"]
    assert all(memory["follow_up"] is None for memory in engine.state()["memories"])
    assert engine.evaluate()["cards"] == []


def test_mixed_known_and_unknown_content_explicitly_reports_uncovered_memories(engine):
    add(engine, "I completed my presentation. I explored a new topic in the archive.")
    card = engine.state()["cards"][0]
    assert "finishing the presentation" in card["summary"]
    assert "Another 1 memory could not be reliably summarised" in card["summary"]
    assert len(card["memory_ids"]) == 2 and len(card["versions"]) == 2
