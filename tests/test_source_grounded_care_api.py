"""Confirmed quotes can ground conservative care despite third-person atom prose.

Every audio, transcript, proposal and provider response is a synthetic double.
"""
import time

from test_pipeline_api import pipeline_api, upload, wait_for_pipeline


RAW_REASSURANCE = "I would like some reassurance."
THIRD_PERSON_ATOM = "The speaker still feels anxious about the presentation and would like some reassurance."


def upload_reassurance(pipeline_api, monkeypatch, *, invalid_quote=False):
    client, app, agents, asr, analyzer, now = pipeline_api
    original = asr.transcribe

    def synthetic_transcript(path, language="en"):
        result = original(path, language=language)
        result["segments"][0]["text"] = RAW_REASSURANCE
        return result

    monkeypatch.setattr(asr, "transcribe", synthetic_transcript)
    agents.content = THIRD_PERSON_ATOM
    if invalid_quote:
        agents.proposal_changes = {"quote": "I need emotional support but never said these words."}
    recording = upload(client, now)
    return wait_for_pipeline(client, recording["id"], expected="failed" if invalid_quote else "completed")


def wait_for_support(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/api/state")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["proactive"]:
            return state
        assert state["care_generation"]["status"] not in {"failed", "unconfigured"}, state["assessment"]
        time.sleep(.01)
    raise AssertionError("The confirmed synthetic reassurance quote did not produce automatic comfort")


def assert_no_reminder_or_suggestion(state):
    types = state["assessment"]["types"]
    for kind in ("reminder", "suggestion"):
        assert types[kind]["enabled"] is True
        assert types[kind]["condition_met"] is False
        assert types[kind]["matched_memory_count"] == 0
        assert types[kind]["reason_code"] and types[kind]["reason"]
    assert types["reminder"]["reason_code"] == "no_allowed_follow_up"
    assert types["suggestion"]["reason_code"] == "no_explicit_actionable_request"
    reminder_reason = types["reminder"]["reason"].lower()
    assert any(word in reminder_reason for word in ("permission", "allowed", "follow-up", "follow up"))
    suggestion_reason = types["suggestion"]["reason"].lower()
    assert any(word in suggestion_reason for word in ("help", "action", "request", "need"))


def test_full_upload_third_person_atom_uses_confirmed_exact_quote_for_automatic_comfort(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = upload_reassurance(pipeline_api, monkeypatch)
    initial = client.get("/api/state").json()
    assert initial["proactive"] == [] and not analyzer.care_calls
    assert len(initial["memories"]) == 1
    memory = initial["memories"][0]
    assert memory["content"] == THIRD_PERSON_ATOM and memory["source_quote"] == RAW_REASSURANCE
    assert memory["confirmed"] is False and memory["reviewed"] is False
    confirmed = client.post(f"/api/recordings/{recording['id']}/confirm", json={
        "segment_ids": [recording["segments"][0]["id"]],
    })
    assert confirmed.status_code == 200, confirmed.text
    state = wait_for_support(client)
    assert [card["type"] for card in state["proactive"]] == ["comfort"]
    assert state["proactive"][0]["generation_mode"] == "llm"
    assert state["proactive"][0]["memory_ids"] == [memory["id"]]
    assert all(item["reviewed"] is False for item in state["memories"])
    assert state["assessment"]["matched_types"] == ["comfort"]
    comfort = state["assessment"]["types"]["comfort"]
    assert comfort["enabled"] is True and comfort["condition_met"] is True
    assert comfort["matched_memory_count"] == 1 and comfort["reason_code"] and comfort["reason"]
    assert_no_reminder_or_suggestion(state)
    for _ in range(3):
        assert client.get("/api/state").json()["proactive"][0]["type"] == "comfort"
    assert len(analyzer.care_calls) == 1 and len(asr.calls) == 1
    assert app.state.engine.get_recording(recording["id"])["segments"][0]["text"] == RAW_REASSURANCE


def test_corrected_negation_is_authoritative_and_disables_old_quote_fallback(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = upload_reassurance(pipeline_api, monkeypatch)
    assert client.post(f"/api/recordings/{recording['id']}/confirm", json={}).status_code == 200
    state = wait_for_support(client)
    memory = state["memories"][0]
    current = client.get(f"/api/recordings/{recording['id']}").json()
    topic = next(topic for topic in current["topics"] if memory["id"] in topic["memory_ids"])
    response = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json={
        "version": topic["version"], "title": topic["title"], "summary": "The speaker does not want reassurance.",
        "memories": [{"id": memory["id"], "version": memory["version"], "content": "I do not want reassurance."}],
    })
    assert response.status_code == 200, response.text
    for _ in range(3):
        updated = client.get("/api/state").json()
        assert updated["proactive"] == []
        assert updated["assessment"]["types"]["comfort"]["condition_met"] is False
        assert updated["assessment"]["types"]["comfort"]["matched_memory_count"] == 0
        assert updated["assessment"]["matched_types"] == []
    corrected = updated["memories"][0]
    assert corrected["user_corrected"] is True and corrected["content"] == "I do not want reassurance."
    assert corrected["source_quote"] == RAW_REASSURANCE and corrected["reviewed"] is False
    assert response.json()["segments"][0]["text"] == RAW_REASSURANCE
    assert len(analyzer.care_calls) == 1


def test_exact_quote_does_not_bypass_unknown_speaker_identity(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    upload_reassurance(pipeline_api, monkeypatch)
    for _ in range(3):
        state = client.get("/api/state").json()
        assert state["proactive"] == [] and state["assessment"]["matched_types"] == []
        assert state["assessment"]["types"]["comfort"]["condition_met"] is False
        assert state["assessment"]["types"]["comfort"]["matched_memory_count"] == 0
    assert all(item["subject_id"] == "unknown" and not item["confirmed"] for item in state["memories"])
    assert not analyzer.care_calls


def test_invalid_provider_quote_fails_before_memory_or_support_can_be_committed(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = upload_reassurance(pipeline_api, monkeypatch, invalid_quote=True)
    assert recording["memory_pipeline"]["status"] == "failed"
    assert recording["segments"][0]["text"] == RAW_REASSURANCE
    assert client.post(f"/api/recordings/{recording['id']}/confirm", json={}).status_code == 200
    state = client.get("/api/state").json()
    assert state["memories"] == state["proactive"] == []
    assert state["assessment"]["matched_types"] == []
    assert state["assessment"]["types"]["comfort"]["condition_met"] is False
    assert not analyzer.care_calls and len(asr.calls) == 1
