"""Requested comfort uses confirmed evidence without changing automatic settings."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from datetime import timedelta
import threading
import time

import pytest

from hya import engine as engine_module
from hya.pipeline import understand_recording
from test_pipeline_api import existing_transcript, pipeline_api


REASSURANCE = "I would like some reassurance."


def prepare(pipeline_api, *, text=REASSURANCE, confirmed=True, day_offset=0):
    client, app, agents, asr, analyzer, now = pipeline_api
    agents.content = text
    recording = existing_transcript(app, now + timedelta(days=day_offset),
                                    text=text, confirmed=confirmed, suffix="requested-comfort")
    understand_recording(app.state.engine, agents, recording["id"])
    memory = next(memory for memory in app.state.engine.state()["memories"] if memory["recording_id"] == recording["id"])
    return app.state.engine.get_recording(recording["id"]), memory


def requested_cards(state):
    return [card for card in state["proactive"] if card.get("delivery_mode") == "requested"]


def wait_for_card(client, *, kind="comfort", delivery=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/api/state")
        assert response.status_code == 200, response.text
        state = response.json()
        cards = [card for card in state["proactive"] if card["type"] == kind
                 and (delivery is None or card.get("delivery_mode") == delivery)]
        if cards:
            return state, cards[0]
        time.sleep(.01)
    pytest.fail("The synthetic comfort/support card was not available")


def assert_delivery_diagnosis(state):
    comfort = state["assessment"]["types"]["comfort"]
    assert isinstance(comfort["request_allowed"], bool)
    for kind in ("reminder", "suggestion", "comfort"):
        delivery = state["assessment"]["types"][kind]["delivery"]
        assert isinstance(delivery["status"], str) and delivery["status"]
        assert isinstance(delivery["reason"], str) and delivery["reason"]


def test_comfort_now_bypasses_pause_snooze_cooldown_and_preserves_settings_exactly(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording, memory = prepare(pipeline_api)
    engine = app.state.engine
    # A dismissed automatic candidate establishes recent automatic delivery
    # history without making any provider call during fixture setup.
    automatic = engine.evaluate()["cards"][0]
    engine.feedback(automatic["id"], "dismiss")
    engine.settings_patch({"quiet_mode": True, "snoozed_until": (now + timedelta(hours=2)).isoformat(),
                           "frequency_minutes": 1440})
    before = deepcopy(engine.state()["settings"])
    diagnosis = client.get("/api/state").json()
    assert diagnosis["proactive"] == []
    assert_delivery_diagnosis(diagnosis)
    assert diagnosis["assessment"]["types"]["comfort"]["request_allowed"] is True
    response = client.post("/api/proactive/comfort-now", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["care_generation"]["status"] == "ready"
    assert len(result["cards"]) == 1
    card = result["cards"][0]
    assert card["type"] == "comfort" and card["generation_mode"] == "llm"
    assert card["delivery_mode"] == "requested" and card["requested_at"]
    assert card["memory_ids"] == [memory["id"]]
    for _ in range(3):
        state = client.get("/api/state").json()
        assert [value["id"] for value in requested_cards(state)] == [card["id"]]
        assert state["settings"] == before
    assert app.state.engine.get_recording(recording["id"])["segments"] == recording["segments"]
    assert app.state.engine.get_source(memory["id"])["memory"]["reviewed"] is False
    assert len(analyzer.care_calls) == 1


def test_requested_comfort_bypasses_automatic_multi_type_suppression(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    prepare(pipeline_api, text="I need help preparing my presentation. I would like some reassurance.")
    before = client.get("/api/state").json()
    assert set(before["assessment"]["matched_types"]) == {"suggestion", "comfort"}
    assert before["proactive"] == []
    settings = deepcopy(before["settings"])
    response = client.post("/api/proactive/comfort-now", json={"date": now.date().isoformat()})
    assert response.status_code == 200, response.text
    assert [card["type"] for card in response.json()["cards"]] == ["comfort"]
    state = client.get("/api/state").json()
    assert len(requested_cards(state)) == 1 and state["settings"] == settings
    assert len(analyzer.care_calls) == 1


@pytest.mark.parametrize("condition", [
    "disabled", "unknown_identity", "no_request", "historical_date", "future_date", "historical_source", "future_source",
])
def test_comfort_now_keeps_source_preference_and_today_gates(condition, pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    text = "I finished my presentation." if condition == "no_request" else REASSURANCE
    offset = -1 if condition == "historical_source" else 1 if condition == "future_source" else 0
    prepare(pipeline_api, text=text, confirmed=condition != "unknown_identity", day_offset=offset)
    if condition == "disabled":
        app.state.engine.settings_patch({"preferences": {"comfort": False}})
    body = {}
    if condition in {"historical_date", "future_date"}:
        body["date"] = (now + timedelta(days=-1 if condition == "historical_date" else 1)).date().isoformat()
    response = client.post("/api/proactive/comfort-now", json=body)
    assert response.status_code == 409, response.text
    assert not analyzer.care_calls
    if condition not in {"historical_date", "future_date"}:
        diagnosis = client.get("/api/state").json()
        assert_delivery_diagnosis(diagnosis)
        assert diagnosis["assessment"]["types"]["comfort"]["request_allowed"] is False
        assert requested_cards(diagnosis) == []


def test_concurrent_requests_join_one_model_call_and_reuse_the_same_active_card(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    prepare(pipeline_api)
    app.state.engine.settings_patch({"quiet_mode": True})
    started, release = threading.Event(), threading.Event()
    attempts = []
    original = analyzer.care_text

    def blocked(card, memories):
        attempts.append(card["id"])
        started.set()
        assert release.wait(5)
        return original(card, memories)

    monkeypatch.setattr(analyzer, "care_text", blocked)
    with ThreadPoolExecutor(max_workers=2) as requests:
        first = requests.submit(client.post, "/api/proactive/comfort-now", json={})
        second = requests.submit(client.post, "/api/proactive/comfort-now", json={})
        try:
            assert started.wait(3)
            for request in (first, second):
                with pytest.raises(TimeoutError):
                    request.result(timeout=.05)
            assert len(attempts) == 1
        finally:
            release.set()
        responses = [first.result(timeout=5), second.result(timeout=5)]
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    ids = [response.json()["cards"][0]["id"] for response in responses]
    assert ids[0] == ids[1]
    repeated = client.post("/api/proactive/comfort-now", json={})
    assert repeated.status_code == 200 and repeated.json()["cards"][0]["id"] == ids[0]
    state = client.get("/api/state").json()
    assert [card["id"] for card in requested_cards(state)] == [ids[0]]
    assert len(analyzer.care_calls) == len(attempts) == 1


def test_existing_automatic_llm_card_is_upgraded_without_duplicate_or_regeneration(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    prepare(pipeline_api)
    _, automatic = wait_for_card(client)
    assert automatic.get("delivery_mode") != "requested"
    calls = len(analyzer.care_calls)
    response = client.post("/api/proactive/comfort-now", json={})
    assert response.status_code == 200, response.text
    card = response.json()["cards"][0]
    assert card["id"] == automatic["id"] and card["delivery_mode"] == "requested" and card["requested_at"]
    assert len(analyzer.care_calls) == calls == 1


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_source_change_during_requested_model_work_cannot_commit_stale_comfort(pipeline_api, monkeypatch, mutation):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording, memory = prepare(pipeline_api)
    app.state.engine.settings_patch({"quiet_mode": True})
    started, release = threading.Event(), threading.Event()
    original = analyzer.care_text

    def blocked(card, memories):
        started.set()
        assert release.wait(5)
        return original(card, memories)

    monkeypatch.setattr(analyzer, "care_text", blocked)
    with ThreadPoolExecutor(max_workers=1) as requests:
        request = requests.submit(client.post, "/api/proactive/comfort-now", json={})
        try:
            assert started.wait(3)
            if mutation == "edit":
                changed = client.patch(f"/api/memories/{memory['id']}", json={
                    "content": "I do not want reassurance.", "version": memory["version"], "confirmed": True,
                })
            else:
                changed = client.delete(f"/api/memories/{memory['id']}")
            assert changed.status_code == 200, changed.text
        finally:
            release.set()
        response = request.result(timeout=5)
    assert response.status_code == 409, response.text
    state = client.get("/api/state").json()
    assert requested_cards(state) == [] and state["proactive"] == []
    assert app.state.engine.get_recording(recording["id"])["segments"] == recording["segments"]
    if mutation == "delete":
        assert state["memories"] == []
    else:
        assert state["memories"][0]["content"] == "I do not want reassurance."
    assert len(analyzer.care_calls) == 1


@pytest.mark.parametrize("action", ["dismiss", "snooze"])
def test_requested_feedback_stays_hidden_until_a_new_explicit_request(pipeline_api, action):
    client, app, agents, asr, analyzer, now = pipeline_api
    prepare(pipeline_api)
    app.state.engine.settings_patch({"quiet_mode": True})
    first = client.post("/api/proactive/comfort-now", json={})
    assert first.status_code == 200, first.text
    card = first.json()["cards"][0]
    feedback = client.post(f"/api/proactive/{card['id']}/feedback", json={"action": action})
    assert feedback.status_code == 200, feedback.text
    # Remove every automatic pause so the feedback itself must prevent a
    # dismissed/snoozed requested card from reappearing on later polling.
    app.state.engine.settings_patch({"quiet_mode": False, "snoozed_until": None, "frequency_minutes": 0})
    for _ in range(3):
        state = client.get("/api/state").json()
        assert requested_cards(state) == [] and state["proactive"] == []
    assert len(analyzer.care_calls) == 1
    repeated = client.post("/api/proactive/comfort-now", json={})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["cards"][0]["delivery_mode"] == "requested"
    assert len(requested_cards(client.get("/api/state").json())) == 1


def test_requested_delivery_timestamp_does_not_delay_later_automatic_suggestion(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    _, memory = prepare(pipeline_api)
    app.state.engine.settings_patch({"frequency_minutes": 1440})
    response = client.post("/api/proactive/comfort-now", json={})
    assert response.status_code == 200, response.text
    changed = client.patch(f"/api/memories/{memory['id']}", json={
        "content": "I need help preparing my presentation.", "version": memory["version"], "confirmed": True,
    })
    assert changed.status_code == 200, changed.text
    state, card = wait_for_card(client, kind="suggestion")
    assert card.get("delivery_mode") != "requested"
    assert requested_cards(state) == []
    assert len(analyzer.care_calls) == 2


@pytest.mark.parametrize("via_future_schedule", [False, True])
def test_upgrading_automatic_card_preserves_original_cooldown_receipt_after_source_change(pipeline_api, monkeypatch, via_future_schedule):
    client, app, agents, asr, analyzer, now = pipeline_api
    base = now.replace(hour=10, minute=0, second=0, microsecond=0)
    clock = [base]
    monkeypatch.setattr(engine_module, "_now", lambda: clock[0])
    _, memory = prepare((client, app, agents, asr, analyzer, base))
    app.state.engine.settings_patch({"frequency_minutes": 60})
    _, automatic = wait_for_card(client)
    assert automatic["created_at"] == base.isoformat()
    assert len(analyzer.care_calls) == 1
    if via_future_schedule:
        # Hiding a ready card before its direct-display upgrade must retain
        # the same automatic receipt as upgrading an already active card.
        app.state.engine.update_display_time("comfort", (base + timedelta(minutes=30)).isoformat(), None)
    clock[0] = base + timedelta(minutes=10)
    upgraded = client.post("/api/proactive/comfort-now", json={})
    assert upgraded.status_code == 200, upgraded.text
    card = upgraded.json()["cards"][0]
    assert card["id"] == automatic["id"] and card["delivery_mode"] == "requested"
    assert card["requested_at"] == clock[0].isoformat()
    assert card["automatic_receipt_at"] == base.isoformat()
    clock[0] = base + timedelta(minutes=15)
    assert client.post(f"/api/proactive/{card['id']}/feedback", json={"action": "helpful"}).status_code == 200
    clock[0] = base + timedelta(minutes=20)
    changed = client.patch(f"/api/memories/{memory['id']}", json={
        "content": "I need help preparing my presentation.", "version": memory["version"], "confirmed": True,
    })
    assert changed.status_code == 200, changed.text
    waiting = client.get("/api/state").json()
    assert waiting["proactive"] == []
    assert waiting["assessment"]["types"]["suggestion"]["condition_met"] is True
    assert "interval" in waiting["assessment"]["reason"]
    assert len(analyzer.care_calls) == 1
    # Eligibility returns one hour after the original automatic delivery,
    # rather than an hour after the explicit request or its helpful feedback.
    clock[0] = base + timedelta(minutes=61)
    _, suggestion = wait_for_card(client, kind="suggestion")
    assert suggestion.get("delivery_mode") != "requested"
    assert len(analyzer.care_calls) == 2
