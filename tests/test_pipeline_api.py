"""Offline HTTP checks for separate ASR and DeepSeek memory job lifecycles."""
from __future__ import annotations

from datetime import datetime
import importlib
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from hya import engine as engine_module
from hya.engine import HK
from hya.memory_agents import AgentError, STAGES
from hya.pipeline import understand_recording
from test_api import tiny_wav
from test_memory_pipeline import CALL_ORDER, FakeAgents, SOURCE_TEXT


class PipelineAgents(FakeAgents):
    def __init__(self):
        super().__init__()
        self.not_found_stage = None

    def status(self):
        return {"configured": True, "provider": "A2Agent", "model": "fictional-deepseek-model",
                "base_url": "https://gateway.example.test/v1", "text_only": True,
                "stages": {stage: {"configured": True, "optional": stage == "raw_evidence"}
                           for stage in STAGES}}

    def _stage(self, name, *inputs):
        super()._stage(name, *inputs)
        if name == self.not_found_stage:
            # The real stage client converts a provider HTTP 404 to this safe error.
            raise AgentError(f"DeepSeek {name} model or endpoint was not found; confirm the permitted model ID with the gateway.")


class PipelineTranscriber:
    def __init__(self):
        self.calls = []
        self.block = False
        self.started, self.release = threading.Event(), threading.Event()

    def health(self):
        return {"provider": "fictional-external-asr", "model": "fictional-speech-model",
                "external": True, "available": True, "state": "ready", "loaded": False}

    def transcribe(self, path, language="en"):
        assert Path(path).is_file()
        self.calls.append((str(path), language))
        self.started.set()
        if self.block:
            assert self.release.wait(5), "Test did not release the ASR worker"
        return {"segments": [{"start": 0.1, "end": 2.8, "text": SOURCE_TEXT,
                              "speaker": "speaker_0", "words": []}],
                "language": "en", "duration": 3.2, "metrics": {"provider": "fictional-external-asr"}}


class PipelineAnalyzer:
    def __init__(self):
        self.configured = True
        self.fail_care = False
        self.care_calls = []

    def status(self):
        return {"configured": self.configured, "mode": "llm", "label": "DeepSeek test double",
                "external": True, "model": "fictional-deepseek-model"}

    def summarize(self, memories):
        return [{"category": category, "title": "Fictional review",
                 "summary": "The speaker described this recorded experience.",
                 "memory_ids": [item["id"] for item in memories if item["category"] == category]}
                for category in sorted({item["category"] for item in memories})]

    def care_text(self, card, memories):
        self.care_calls.append(card["id"])
        if self.fail_care:
            raise AgentError("The fictional model cannot generate this card.")
        return {"title": "A small rehearsal", "summary": "Would you like to practise your opening once?"}


@pytest.fixture
def pipeline_api(tmp_path, monkeypatch, isolated_provider_mode):
    # Explicit dependency puts this override after the shared local-mode fixture.
    monkeypatch.setenv("HYA_MEMORY_MODE", "deepseek")
    monkeypatch.setenv("HYA_DATA_DIR", str(tmp_path / "unused-default"))
    monkeypatch.setenv("HYA_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("HYA_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
    monkeypatch.setenv("HYA_MAX_UPLOAD_MB", "100")
    monkeypatch.setenv("HYA_MAX_AUDIO_MINUTES", "20")
    monkeypatch.setenv("HYA_LLM_BASE_URL", "")
    monkeypatch.setenv("HYA_LLM_MODEL", "")
    monkeypatch.setenv("HYA_LLM_API_KEY", "")
    monkeypatch.setenv("HYA_ALLOW_EXTERNAL_LLM", "false")
    monkeypatch.setenv("HYA_DEEPSEEK_MODEL", "fictional-deepseek-model")
    for stage in STAGES:
        monkeypatch.setenv(f"HYA_DEEPSEEK_{stage.upper()}_API_KEY", f"fictional-pipeline-key-{stage}")
    now = datetime.now(HK).replace(microsecond=0)
    monkeypatch.setattr(engine_module, "_now", lambda: now)
    module = importlib.import_module("hya.app")
    agents, transcriber, analyzer = PipelineAgents(), PipelineTranscriber(), PipelineAnalyzer()
    app = module.create_app(tmp_path / "pipeline-data", transcriber=transcriber,
                            analyzer=analyzer, memory_agents=agents)
    with TestClient(app) as client:
        try:
            yield client, app, agents, transcriber, analyzer, now
        finally:
            transcriber.release.set()


def upload(client, now, *, marker=0):
    response = client.post("/api/recordings", files={"file": ("fictional.wav", tiny_wav(marker), "audio/wav")},
                           data={"recorded_at": now.isoformat(), "language": "en"})
    assert response.status_code == 200, response.text
    return response.json()["recording"]


def wait_for_pipeline(client, recording_id, expected="completed"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/recordings/{recording_id}")
        assert response.status_code == 200, response.text
        recording = response.json()
        assert recording["status"] != "failed", recording
        pipeline = recording.get("memory_pipeline", {})
        if pipeline.get("status") == expected:
            return recording
        if pipeline.get("status") == "failed" and expected != "failed":
            pytest.fail(f"Memory pipeline failed unexpectedly: {pipeline}")
        time.sleep(0.01)
    pytest.fail(f"Memory pipeline did not reach {expected} within five seconds")


def existing_transcript(app, now, *, text=SOURCE_TEXT, suffix="existing", confirmed=False):
    engine = app.state.engine
    recording = engine.create_recording(f"{suffix}.txt", None, f"fictional-transcript-{suffix}", now.isoformat())
    engine.ingest_segments(recording["id"], [{"start": 0, "end": 3, "text": text, "speaker": "speaker_0"}],
                           subject_confirmed=confirmed, extract=False)
    engine.set_recording_status(recording["id"], "completed")
    return engine.get_recording(recording["id"])


def test_successful_upload_preserves_raw_evidence_and_commits_model_inferences(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = upload(client, now)
    result = wait_for_pipeline(client, recording["id"])
    assert result["status"] == "completed" and result["error"] is None
    assert result["segments"][0]["text"] == SOURCE_TEXT
    assert result["segments"][0]["speaker"] == "unknown"
    assert result["segments"][0]["speaker_label"] == "speaker_0"
    assert agents.calls == list(CALL_ORDER)
    assert len(asr.calls) == 1
    assert result["memory_pipeline"]["advisory_only"] is True
    state = client.get("/api/state").json()
    assert len(state["memories"]) == 1
    assert all(memory["evidence_kind"] == "ai_inference" and memory["extraction_mode"] == "deepseek"
               and not memory["confirmed"] and not memory["reviewed"] for memory in state["memories"])
    assert client.get(f"/api/recordings/{recording['id']}/audio").content == tiny_wav()


@pytest.mark.parametrize("failed_stage", ["atomic", "consolidation", "retrieval", "governance"])
def test_stage_model_404_preserves_asr_and_retry_reuses_transcript(pipeline_api, failed_stage):
    client, app, agents, asr, analyzer, now = pipeline_api
    agents.not_found_stage = failed_stage
    recording = upload(client, now)
    failed = wait_for_pipeline(client, recording["id"], "failed")
    assert failed["status"] == "completed" and failed["error"] is None
    assert failed["segments"][0]["text"] == SOURCE_TEXT
    report = failed["memory_pipeline"]
    assert report["stage"] == failed_stage
    assert "model or endpoint was not found" in report["error"]
    assert not {"memory_ids", "raw_review", "consolidation", "retrieved_memory_ids", "governance", "transcript_groups"}.intersection(report)
    assert client.get("/api/state").json()["memories"] == []
    assert client.get(f"/api/recordings/{recording['id']}/audio").content == tiny_wav()
    original_segments = failed["segments"]

    agents.not_found_stage = None
    response = client.post(f"/api/recordings/{recording['id']}/retry")
    assert response.status_code == 200, response.text
    recovered = wait_for_pipeline(client, recording["id"])
    assert recovered["status"] == "completed"
    assert recovered["memory_pipeline"]["error"] is None
    assert recovered["segments"] == original_segments
    assert len(asr.calls) == 1  # Semantic retries never rerun successful ASR.
    assert len(client.get("/api/state").json()["memories"]) == 1
    assert len(client.get("/api/recordings").json()) == 1


def test_understand_existing_transcript_is_queued_then_completed_without_asr(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    # Occupy the single worker so the existing transcript's pending state cannot race.
    asr.block = True
    busy = upload(client, now)
    assert asr.started.wait(2)
    target = existing_transcript(app, now)
    try:
        response = client.post(f"/api/recordings/{target['id']}/understand")
        assert response.status_code == 200, response.text
        assert response.json()["memory_pipeline"]["status"] == "pending"
        assert response.json()["status"] == "completed"
        assert client.delete(f"/api/recordings/{busy['id']}").status_code == 200
    finally:
        asr.release.set()
    completed = wait_for_pipeline(client, target["id"])
    assert completed["segments"] == target["segments"]
    assert completed["memory_pipeline"]["status"] == "completed"
    assert len(asr.calls) == 1  # Only the deliberately blocked/deleted upload used ASR.
    assert agents.calls == list(CALL_ORDER)


@pytest.mark.parametrize("action", ["understand", "retry"])
def test_unknown_recording_cannot_start_a_memory_job(pipeline_api, action):
    client, app, agents, asr, analyzer, now = pipeline_api
    response = client.post(f"/api/recordings/missing/{action}")
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)
    assert not agents.calls and not asr.calls


def test_understand_requires_a_stored_transcript(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = app.state.engine.create_recording("not-transcribed.wav", None, "fictional-empty", now.isoformat())
    response = client.post(f"/api/recordings/{recording['id']}/understand")
    assert response.status_code == 422
    assert "completed transcript" in response.json()["detail"]
    assert not agents.calls and not asr.calls


def test_health_reports_configured_model_pipeline_without_credentials_or_calls(pipeline_api):
    client, app, agents, asr, analyzer, now = pipeline_api
    response = client.get("/api/health")
    assert response.status_code == 200
    pipeline = response.json()["memory_pipeline"]
    assert pipeline["mode"] == "deepseek" and pipeline["configured"] is True
    assert pipeline["model"] == "fictional-deepseek-model"
    assert set(pipeline["stages"]) == set(STAGES)
    assert "fictional-pipeline-key" not in response.text
    assert "api_key" not in response.text.lower()
    assert not agents.calls and not asr.calls


def test_semantic_search_preserves_ranking_and_drops_source_deleted_during_request(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    for index in range(3):
        recording = existing_transcript(app, now, suffix=f"search-{index}", confirmed=True)
        response = client.post(f"/api/recordings/{recording['id']}/understand")
        assert response.status_code == 200, response.text
        wait_for_pipeline(client, recording["id"])
    expected = []

    def rank_and_delete(query, candidates):
        assert query == "presentation"
        assert len(candidates) == 3
        ranked = [item["id"] for item in reversed(candidates)]
        app.state.engine.delete_memory(ranked[1])
        expected.extend([ranked[0], ranked[2]])
        return ranked

    monkeypatch.setattr(agents, "retrieve", rank_and_delete)
    response = client.get("/api/memories", params={"query": "presentation"})
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == expected
    assert len(response.json()) == 2
    assert len(asr.calls) == 0


@pytest.mark.parametrize("configured", [True, False])
def test_deepseek_care_failure_never_returns_a_local_template(pipeline_api, configured):
    client, app, agents, asr, analyzer, now = pipeline_api
    text = "I need help preparing my presentation."
    agents.content = text
    recording = existing_transcript(app, now, text=text, confirmed=False)
    # Complete setup synchronously: this test isolates the explicit care POST,
    # rather than racing the upload worker's automatic care callback.
    understand_recording(app.state.engine, agents, recording["id"])
    memory = client.get("/api/state").json()["memories"][0]
    # Isolate the explicit POST path; normal HTTP confirmation also schedules
    # automatic care and is covered by test_topic_api.
    app.state.engine.confirm_recording(recording["id"])
    assert app.state.engine.evaluate()["cards"], "The test must have an otherwise eligible care candidate"
    analyzer.configured = configured
    analyzer.fail_care = True
    response = client.post("/api/proactive/evaluate", json={"date": now.date().isoformat()})
    assert response.status_code == (502 if configured else 503), response.text
    assert "DeepSeek" in response.json()["detail"]
    assert client.get("/api/state").json()["proactive"] == []
    assert len(analyzer.care_calls) == (1 if configured else 0)


def test_care_source_revision_during_generation_never_exposes_refreshed_local_template(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    text = "I need help preparing my presentation."
    agents.content = text
    recording = existing_transcript(app, now, text=text, confirmed=False)
    understand_recording(app.state.engine, agents, recording["id"])
    memory = client.get("/api/state").json()["memories"][0]
    app.state.engine.confirm_recording(recording["id"])
    current = app.state.engine.get_source(memory["id"])["memory"]
    previous = app.state.engine.evaluate()["cards"][0]
    generate = analyzer.care_text
    revised = []

    def revise_source_during_generation(card, source_memories):
        result = generate(card, source_memories)
        # Simulate a valid user correction while the network request is running.
        # The explicit presentation-help request remains eligible for a new card.
        revised.append(app.state.engine.revise_memory(current["id"],
            "I need help preparing my presentation and rehearsing its opening.", current["version"]))
        return result

    monkeypatch.setattr(analyzer, "care_text", revise_source_during_generation)
    response = client.post("/api/proactive/evaluate", json={"date": now.date().isoformat()})
    assert response.status_code == 200, response.text
    assert len(analyzer.care_calls) == 1 and len(revised) == 1
    assert response.json()["cards"] == []
    assert "Sources changed" in response.json()["analysis_note"]
    refreshed = app.state.engine.evaluate()["cards"]
    assert len(refreshed) == 1, "A new eligible candidate must exist to exercise the refresh filter"
    assert refreshed[0]["id"] != previous["id"]
    assert refreshed[0]["versions"] == {current["id"]: revised[0]["version"]}
    assert refreshed[0]["generation_mode"] == "local_templates"
    assert app.state.engine.state()["proactive"] == []


@pytest.mark.parametrize("interrupted_status", ["pending", "processing"])
def test_startup_repairs_interrupted_memory_job_and_retry_preserves_completed_asr(pipeline_api, interrupted_status):
    client, app, agents, asr, analyzer, now = pipeline_api
    recording = existing_transcript(app, now, suffix="interrupted")
    app.state.engine.update_pipeline(recording["id"], {
        "status": interrupted_status, "stage": "atomic", "model": "fictional-deepseek-model",
        "stages": [{"stage": "atomic", "status": interrupted_status}],
        "raw_review": {"issues": [{"source_segment_id": recording["segments"][0]["id"], "reason": "Uncommitted advisory."}]},
        "consolidation": {"proposals": []}, "retrieved_memory_ids": ["uncommitted-memory"],
        "governance": {"reviews": []}, "memory_ids": ["uncommitted-memory"],
    })
    # Re-open the same persistent SQLite directory without launching a real job.
    restarted_agents, restarted_asr = PipelineAgents(), PipelineTranscriber()
    restarted = importlib.import_module("hya.app").create_app(app.state.engine.data_dir,
        transcriber=restarted_asr, analyzer=PipelineAnalyzer(), memory_agents=restarted_agents)
    with TestClient(restarted) as restarted_client:
        response = restarted_client.get(f"/api/recordings/{recording['id']}")
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered["status"] == "completed" and recovered["error"] is None
        assert recovered["segments"] == recording["segments"]
        report = recovered["memory_pipeline"]
        assert report["status"] == "failed"
        assert "interrupted" in report["error"] and "existing transcript" in report["error"]
        assert not {"raw_review", "consolidation", "retrieved_memory_ids", "governance", "memory_ids", "transcript_groups"}.intersection(report)
        assert not agents.calls and not restarted_agents.calls
        assert not asr.calls and not restarted_asr.calls

        response = restarted_client.post(f"/api/recordings/{recording['id']}/retry")
        assert response.status_code == 200, response.text
        completed = wait_for_pipeline(restarted_client, recording["id"])
        assert completed["status"] == "completed"
        assert completed["segments"] == recording["segments"]
        assert restarted_agents.calls == list(CALL_ORDER)
        assert not asr.calls and not restarted_asr.calls
        assert len(restarted_client.get("/api/state").json()["memories"]) == 1
