"""Synthetic HTTP checks for durable editable topics and automatic model care."""
from copy import deepcopy
import importlib
import threading
import time

from fastapi.testclient import TestClient
import pytest

from test_api import tiny_wav
from test_pipeline_api import existing_transcript, pipeline_api


def poll_state(client, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/api/state")
        assert response.status_code == 200, response.text
        value = response.json()
        if value["care_generation"]["status"] == status:
            return value
        time.sleep(.01)
    pytest.fail(f"Automatic care did not reach {status}")


def synthetic_evidence(app, now, *, confirmed=True, text="Please comfort me.", suffix="care"):
    engine = app.state.engine
    recording = engine.create_recording(f"fictional-{suffix}.txt", None, f"fictional-{suffix}", now)
    engine.ingest_segments(recording["id"], [{"start": 0, "end": 3, "text": text, "speaker": "speaker_0", "words": []}],
                           subject_confirmed=confirmed, extract=True)
    return engine.get_recording(recording["id"])


def add_topics(app, now, *, confirmed=False):
    recording = synthetic_evidence(app, now, confirmed=confirmed, text="I felt calm. I talked with Alex.", suffix="topics")
    engine = app.state.engine
    snapshot = engine.agent_snapshot(recording["id"])
    result = engine.commit_topic_groups(recording["id"], [{
        "id": "group-1", "title": "Fictional conversation", "summary": "The speaker described a fictional conversation.",
        "source_segment_ids": [segment["id"] for segment in recording["segments"]],
    }], snapshot["revision"])
    return result, result["topics"][0]


@pytest.mark.parametrize("supplied", [None, "1900-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "not a date"])
def test_new_upload_ignores_client_time_and_duplicate_keeps_server_chronology(pipeline_api, supplied):
    client, app, agents, asr, analyzer, now = pipeline_api
    data = {"language": "en"}
    if supplied is not None:
        data["recorded_at"] = supplied
    response = client.post("/api/recordings", files={"file": ("fictional.wav", tiny_wav(), "audio/wav")}, data=data)
    assert response.status_code == 200, response.text
    original = response.json()["recording"]
    assert original["recorded_at"] == original["created_at"] == now.isoformat()
    duplicate = client.post("/api/recordings", files={"file": ("again.wav", tiny_wav(), "audio/wav")},
                            data={"recorded_at": "wrong", "language": "en"})
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
    assert duplicate.json()["recording"]["recorded_at"] == original["recorded_at"]


def test_topics_are_durable_and_editable_before_identity_without_rewriting_sources(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording, topic = add_topics(app, now)
    raw = deepcopy(recording["segments"])
    memories = app.state.engine.state()["memories"]
    first = memories[0]
    response = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json={
        "version": topic["version"], "title": "Corrected topic", "summary": "A corrected fictional interpretation.",
        "memories": [{"id": first["id"], "version": first["version"], "content": "A corrected fictional memory."}],
    })
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["segments"] == raw
    assert updated["topics"][0]["title"] == "Corrected topic"
    assert updated["topics"][0]["version"] == topic["version"] + 1
    listed = next(item for item in client.get("/api/recordings").json() if item["id"] == recording["id"])
    assert listed["topics"] == updated["topics"]
    memory = client.get(f"/api/memories/{first['id']}/source").json()["memory"]
    assert memory["content"] == "A corrected fictional memory."
    assert memory["subject_id"] == "unknown" and memory["confirmed"] is False and memory["reviewed"] is False
    assert not agents.calls and not asr.calls and not analyzer.care_calls
    stale = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json={
        "version": topic["version"], "title": "Stale", "summary": "Stale interpretation.",
    })
    assert stale.status_code == 409
    assert client.get(f"/api/recordings/{recording['id']}").json()["topics"] == updated["topics"]


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 0}, {"title": ""}, {"summary": ""}, {"subject_id": "user"},
    {"memories": [{"id": "some-memory", "version": True, "content": "Correction"}]},
])
def test_topic_request_rejects_invalid_or_authority_fields_atomically(pipeline_api, change):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording, topic = add_topics(app, now)
    body = {"version": topic["version"], "title": topic["title"], "summary": topic["summary"], **change}
    response = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json=body)
    assert response.status_code == 422, response.text
    assert client.get(f"/api/recordings/{recording['id']}").json()["topics"] == recording["topics"]


def test_existing_transcript_organization_is_singleflight_and_never_retranscribes(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = existing_transcript(app, now)
    raw = deepcopy(recording["segments"])
    started, release = threading.Event(), threading.Event()
    calls = []

    def group(recording, segments, memories):
        calls.append(recording["id"])
        started.set()
        assert release.wait(5)
        return [{"id": "organized-1", "title": "Existing fictional source", "summary": "An organized fictional transcript.",
                 "source_segment_ids": [segment["id"] for segment in segments]}]

    monkeypatch.setattr(agents, "group_transcript", group)
    try:
        response = client.post(f"/api/recordings/{recording['id']}/organize")
        assert response.status_code == 200, response.text
        assert started.wait(3)
        assert client.post(f"/api/recordings/{recording['id']}/organize").status_code == 200
        assert calls == [recording["id"]]
        assert client.get(f"/api/recordings/{recording['id']}").json()["topic_pipeline"]["status"] in {"pending", "processing"}
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        final = client.get(f"/api/recordings/{recording['id']}").json()
        if final.get("topic_pipeline", {}).get("status") == "completed":
            break
        time.sleep(.01)
    assert final["topic_pipeline"]["status"] == "completed"
    assert final["topics"] and final["segments"] == raw
    assert app.state.engine.state()["memories"] == []
    assert not asr.calls and not agents.calls


def test_organize_requires_known_recording_and_completed_transcript(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    assert client.post("/api/recordings/missing/organize").status_code == 404
    recording = app.state.engine.create_recording("empty.txt", None, "empty-topic-source", now)
    assert client.post(f"/api/recordings/{recording['id']}/organize").status_code == 422
    assert not asr.calls and not agents.calls


@pytest.mark.parametrize("interrupted", ["pending", "processing"])
def test_restart_marks_interrupted_topic_job_failed_without_losing_durable_topics(pipeline_api, interrupted):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording, topic = add_topics(app, now)
    app.state.engine.update_topic_pipeline(recording["id"], {
        "status": interrupted, "stage": "consolidation", "error": None,
    })
    restarted = importlib.import_module("hya.app").create_app(
        app.state.engine.data_dir, transcriber=asr, analyzer=analyzer, memory_agents=agents)
    with TestClient(restarted) as restarted_client:
        response = restarted_client.get(f"/api/recordings/{recording['id']}")
        assert response.status_code == 200
        recovered = response.json()
        assert recovered["topic_pipeline"]["status"] == "failed"
        assert "interrupted" in recovered["topic_pipeline"]["error"]
        assert recovered["topics"] == recording["topics"]
        assert recovered["segments"] == recording["segments"]
    assert not agents.calls and not asr.calls and not analyzer.care_calls


def test_get_state_automatically_generates_support_without_semantic_review_or_manual_check(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    synthetic_evidence(app, now)
    state = poll_state(client, "ready")
    assert state["proactive"] and state["proactive"][0]["generation_mode"] == "llm"
    assert all(memory["reviewed"] is False for memory in state["memories"])
    assert len(analyzer.care_calls) == 1
    for _ in range(3):
        assert client.get("/api/state").status_code == 200
    assert len(analyzer.care_calls) == 1


def test_background_care_is_nonblocking_singleflight_and_confirmation_starts_it(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = synthetic_evidence(app, now, confirmed=False)
    started, release = threading.Event(), threading.Event()
    original = analyzer.care_text

    def blocked(card, memories):
        started.set()
        assert release.wait(5)
        return original(card, memories)

    monkeypatch.setattr(analyzer, "care_text", blocked)
    try:
        response = client.post(f"/api/recordings/{recording['id']}/confirm", json={})
        assert response.status_code == 200, response.text
        assert started.wait(3), "Identity confirmation must schedule model care without another button"
        for _ in range(3):
            state = client.get("/api/state").json()
            assert state["care_generation"]["status"] == "pending"
            assert state["proactive"] == []
        assert not analyzer.care_calls
    finally:
        release.set()
    assert poll_state(client, "ready")["proactive"]
    assert len(analyzer.care_calls) == 1


def test_generation_failure_remains_visible_without_poll_retry_and_explicit_post_retries(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    analyzer.fail_care = True
    synthetic_evidence(app, now)
    state = poll_state(client, "failed")
    assert state["proactive"] == [] and "failed" in state["assessment"]["reason"]
    for _ in range(3):
        refreshed = client.get("/api/state").json()
        assert refreshed["care_generation"]["status"] == "failed"
    assert len(analyzer.care_calls) == 1
    assert client.post("/api/proactive/evaluate", json={}).status_code == 502
    assert len(analyzer.care_calls) == 2
    analyzer.fail_care = False
    response = client.post("/api/proactive/evaluate", json={})
    assert response.status_code == 200, response.text
    assert response.json()["care_generation"]["status"] == "ready" and response.json()["cards"]
    assert len(analyzer.care_calls) == 3


def test_unconfigured_care_keeps_dashboard_available_and_explicit_post_returns_503(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    analyzer.configured = False
    synthetic_evidence(app, now)
    response = client.get("/api/state")
    assert response.status_code == 200
    state = response.json()
    assert state["care_generation"]["status"] == "unconfigured" and state["proactive"] == []
    assert "not configured" in state["assessment"]["reason"]
    assert client.post("/api/proactive/evaluate", json={}).status_code == 503
    assert not analyzer.care_calls


@pytest.mark.parametrize("error", [IndexError("synthetic envelope"), AttributeError("synthetic envelope")])
def test_bad_provider_envelope_is_a_safe_explicit_502_and_visible_background_failure(pipeline_api, monkeypatch, error):
    client, app, agents, asr, analyzer, now = pipeline_api
    synthetic_evidence(app, now)

    def fail(card, memories):
        raise error

    monkeypatch.setattr(analyzer, "care_text", fail)
    response = client.post("/api/proactive/evaluate", json={})
    assert response.status_code == 502
    assert "synthetic envelope" not in response.text
    assert client.get("/api/state").json()["care_generation"]["status"] == "failed"


def test_source_correction_during_generation_prevents_stale_support_commit(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    synthetic_evidence(app, now)
    started, release = threading.Event(), threading.Event()
    original = analyzer.care_text

    def blocked(card, memories):
        started.set()
        assert release.wait(5)
        return original(card, memories)

    monkeypatch.setattr(analyzer, "care_text", blocked)
    try:
        assert client.get("/api/state").status_code == 200
        assert started.wait(3)
        memory = app.state.engine.state()["memories"][0]
        response = client.patch(f"/api/memories/{memory['id']}", json={
            "content": "I completed my presentation.", "version": memory["version"], "confirmed": True,
        })
        assert response.status_code == 200
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while not analyzer.care_calls and time.monotonic() < deadline:
        time.sleep(.01)
    final = client.get("/api/state").json()
    assert final["proactive"] == [] and final["assessment"]["matched_types"] == []
    assert len(analyzer.care_calls) == 1
