"""HTTP integration checks use real SQLite/PyAV and a deterministic ASR double.

These tests never download weights, upload private audio, or call a text model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import io
from pathlib import Path
import time
import threading
import wave

from fastapi.testclient import TestClient
import pytest


class FakeTranscriber:
    def __init__(self):
        self.calls = []

    def health(self):
        return {"model": "large-v3-turbo", "device": "cpu", "compute_type": "int8",
                "available": True, "loaded": False, "state": "idle"}

    def transcribe(self, path, language="en"):
        assert Path(path).is_file()
        self.calls.append((str(path), language))
        return {"segments": [
            {"start": 0.1, "end": 1.4, "text": "Please comfort me.", "speaker": "unknown", "words": []},
            {"start": 1.5, "end": 2.8, "text": "I talked with my friend Sarah after class.", "speaker": "unknown", "words": []},
        ], "language": "en", "duration": 3.2,
            "metrics": {"model": "large-v3-turbo", "device": "cpu", "elapsed_seconds": 0.001}}


def tiny_wav(marker=0) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(int(marker).to_bytes(2, "little", signed=True) * 25600)  # Valid 3.2 second WAV; never a private recording.
    return stream.getvalue()


@pytest.fixture
def api(tmp_path, monkeypatch):
    # Importing the module also constructs its default app. Keep that harmless
    # instance inside this test's temp directory rather than the project data.
    monkeypatch.setenv("HYA_DATA_DIR", str(tmp_path / "unused-default"))
    monkeypatch.setenv("HYA_LLM_BASE_URL", "")
    monkeypatch.setenv("HYA_LLM_MODEL", "")
    monkeypatch.setenv("HYA_LLM_API_KEY", "")
    monkeypatch.setenv("HYA_ALLOW_EXTERNAL_LLM", "false")
    monkeypatch.setenv("HYA_MAX_UPLOAD_MB", "100")
    monkeypatch.setenv("HYA_MAX_AUDIO_MINUTES", "20")
    module = importlib.import_module("hya.app")
    fake = FakeTranscriber()
    app = module.create_app(tmp_path / "test-data", transcriber=fake)
    with TestClient(app) as client:
        yield client, app, fake, tmp_path / "test-data"


def upload(client, *, recorded_at=True, filename="english.wav", data=None):
    payload = {"language": "en"}
    if recorded_at:
        payload["recorded_at"] = recorded_at if isinstance(recorded_at, str) else datetime.now(timezone(timedelta(hours=8))).isoformat()
    response = client.post("/api/recordings", files={"file": (filename, data if data is not None else tiny_wav(), "audio/wav")}, data=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    # Historical/undated regression fixtures model old internal records. Public
    # clients cannot change the automatically assigned upload chronology.
    if recorded_at is False or isinstance(recorded_at, str):
        result["recording"] = client.app.state.engine.update_recording_time(
            result["recording"]["id"], recorded_at if isinstance(recorded_at, str) else None,
            result["recording"]["recorded_at"])
    return result


def completed(client, recording_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/recordings/{recording_id}")
        assert response.status_code == 200, response.text
        recording = response.json()
        if recording["status"] == "completed":
            return recording
        assert recording["status"] != "failed", recording
        time.sleep(0.01)
    pytest.fail("Background ASR did not complete within five seconds")


def review_cards(client, state=None):
    snapshot = state or client.get("/api/state").json()
    for card in snapshot["cards"]:
        response = client.post(f"/api/cards/{card['id']}/confirm", json={
            "expected_versions": card["versions"], "review_token": card["review_token"]})
        assert response.status_code == 200, response.text
    return client.get("/api/state", params={"date": snapshot["date"]}).json()


@pytest.mark.parametrize("filename,content,expected", [
    ("wrong.txt", b"not audio", 415),
    ("empty.wav", b"", 422),
    ("corrupt.wav", b"not really a wav file", 422),
])
def test_invalid_uploads_leave_no_recording_or_audio(api, filename, content, expected):
    client, app, fake, directory = api
    response = client.post("/api/recordings", files={"file": (filename, content, "audio/wav")})
    assert response.status_code == expected
    assert isinstance(response.json()["detail"], str)
    assert client.get("/api/recordings").json() == []
    assert list((directory / "audio").iterdir()) == []
    assert fake.calls == []


def test_oversized_upload_is_rejected_before_decode(api, monkeypatch):
    client, app, fake, directory = api
    monkeypatch.setenv("HYA_MAX_UPLOAD_MB", "1")
    response = client.post("/api/recordings", files={"file": ("oversized.wav", b"x" * (1024 * 1024 + 1), "audio/wav")})
    assert response.status_code == 413
    assert client.get("/api/recordings").json() == []
    assert list((directory / "audio").iterdir()) == []
    assert fake.calls == []


def test_actual_audio_duration_is_bounded_before_asr(api, monkeypatch):
    client, app, fake, directory = api
    monkeypatch.setenv("HYA_MAX_AUDIO_MINUTES", "0.01")
    response = client.post("/api/recordings", files={"file": ("long.wav", tiny_wav(), "audio/wav")})
    assert response.status_code == 422
    assert fake.calls == []
    assert list((directory / "audio").iterdir()) == []


def test_upload_deduplicates_by_audio_hash_without_extra_evidence(api):
    client, app, fake, directory = api
    first = upload(client)
    recording = completed(client, first["recording"]["id"])
    duplicate = upload(client, filename="renamed.wav")
    assert duplicate["duplicate"] is True
    assert duplicate["recording"]["id"] == recording["id"]
    assert len(client.get("/api/recordings").json()) == 1
    assert len(client.get("/api/state").json()["memories"]) == 2
    assert len(list((directory / "audio").iterdir())) == 1
    assert len(fake.calls) == 1


@pytest.mark.parametrize("actionable", [True, False])
def test_transcription_failure_keeps_safe_guidance_and_retry_recovers(api, monkeypatch, caplog, actionable):
    from hya.asr import TranscriptionError

    client, app, fake, directory = api
    transcribe = fake.transcribe
    attempts = []
    guidance = "音訊解碼套件版本不相容，請重新安裝後端依賴後重試。"
    internal_detail = "internal dependency diagnostic"

    def fail_once(path, language="en"):
        attempts.append(path)
        if len(attempts) == 1:
            if actionable:
                raise TranscriptionError(guidance) from TypeError(internal_detail)
            raise RuntimeError(internal_detail)
        return transcribe(path, language)

    monkeypatch.setattr(fake, "transcribe", fail_once)
    response = upload(client)
    recording_id = response["recording"]["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        recording = client.get(f"/api/recordings/{recording_id}").json()
        if recording["status"] == "failed":
            break
        time.sleep(0.01)
    else:
        pytest.fail("Background ASR did not report its failure within five seconds")

    expected = guidance if actionable else "轉錄未完成（RuntimeError）；請檢查模型、裝置及錄音格式，然後重試。"
    assert recording["error"] == expected
    assert internal_detail not in recording["error"]
    assert any(record.name == "hya" and record.exc_info for record in caplog.records)
    assert internal_detail in caplog.text  # Cause details remain available locally.
    assert client.get(f"/api/recordings/{recording_id}/audio").content == tiny_wav()
    assert client.get("/api/state").json()["memories"] == []

    retried = client.post(f"/api/recordings/{recording_id}/retry")
    assert retried.status_code == 200, retried.text
    recovered = completed(client, recording_id)
    assert recovered["error"] is None
    assert len(attempts) == 2
    assert len(fake.calls) == 1
    assert len(client.get("/api/recordings").json()) == 1
    assert len(client.get("/api/state").json()["memories"]) == 2


def test_pending_identity_partial_confirmation_and_source_audio_range(api):
    client, app, fake, directory = api
    raw = tiny_wav()
    recording = completed(client, upload(client, data=raw)["recording"]["id"])
    before = client.get("/api/state").json()
    assert len(before["memories"]) == 2
    assert all(m["subject_id"] == "unknown" and m["status"] == "pending" for m in before["memories"])
    assert before["cards"] == [] and before["proactive"] == []
    assert all(segment["speaker"] == "unknown" for segment in recording["segments"])
    first_segment = recording["segments"][0]
    result = client.post(f"/api/recordings/{recording['id']}/confirm", json={"segment_ids": [first_segment["id"]]})
    assert result.status_code == 200, result.text
    after = client.get("/api/state").json()
    assert sum(m["subject_id"] == "user" for m in after["memories"]) == 1
    assert sum(m["status"] == "pending" for m in after["memories"]) == 1
    assert len(after["cards"]) == 1 and after["cards"][0]["category"] == "mood"
    assert after["proactive"][0]["type"] == "comfort"
    assert after["cards"][0]["review_status"] == "pending"
    after = review_cards(client, after)
    assert after["proactive"][0]["type"] == "comfort"
    active = next(m for m in after["memories"] if m["status"] == "active")
    source = client.get(f"/api/memories/{active['id']}/source").json()
    assert source["segments"][0]["text"] == first_segment["text"]
    assert source["segments"][0]["start"] == 0.1
    ranged = client.get(f"/api/recordings/{recording['id']}/audio", headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206, ranged.text
    assert ranged.content == raw[:32]
    assert ranged.headers["content-range"] == f"bytes 0-31/{len(raw)}"


def test_stale_revision_and_recording_delete_cancel_derived_output(api):
    client, app, fake, directory = api
    recording = completed(client, upload(client)["recording"]["id"])
    segment_id = recording["segments"][0]["id"]
    assert client.post(f"/api/recordings/{recording['id']}/confirm", json={"segment_ids": [segment_id]}).status_code == 200
    state = client.get("/api/state").json()
    state = review_cards(client, state)
    memory = next(m for m in state["memories"] if m["status"] == "active")
    old_proactive = state["proactive"][0]
    original = memory["content"]
    updated = client.patch(f"/api/memories/{memory['id']}", json={"content": "I finished my presentation.", "version": memory["version"], "confirmed": True})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == memory["version"] + 1
    assert client.get(f"/api/memories/{memory['id']}/source").json()["segments"][0]["text"] == original
    stale = client.patch(f"/api/memories/{memory['id']}", json={"content": "Stale edit", "version": memory["version"]})
    assert stale.status_code == 409
    assert client.post(f"/api/proactive/{old_proactive['id']}/feedback", json={"action": "helpful"}).status_code == 409
    assert client.get("/api/state").json()["proactive"] == []
    assert client.delete(f"/api/recordings/{recording['id']}").status_code == 200
    final = client.get("/api/state").json()
    assert final["recordings"] == [] and final["memories"] == [] and final["cards"] == [] and final["proactive"] == []
    assert client.get(f"/api/recordings/{recording['id']}/audio").status_code == 404
    assert client.get(f"/api/memories/{memory['id']}/source").status_code == 404
    assert list((directory / "audio").iterdir()) == []


def test_undated_recording_keeps_null_event_time_and_no_daily_inference(api):
    client, app, fake, directory = api
    recording = completed(client, upload(client, recorded_at=False)["recording"]["id"])
    assert recording["recorded_at"] is None
    assert client.post(f"/api/recordings/{recording['id']}/confirm", json={}).status_code == 200
    state = client.get("/api/state").json()
    assert all(m["event_time"] is None for m in state["memories"])
    assert state["coverage"]["undated_memories"] == 2
    assert state["cards"] == [] and state["proactive"] == []


def test_memory_delete_cancels_outputs_but_preserves_raw_recording(api):
    client, app, fake, directory = api
    recording = completed(client, upload(client)["recording"]["id"])
    assert client.post(f"/api/recordings/{recording['id']}/confirm", json={"segment_ids": [recording["segments"][0]["id"]]}).status_code == 200
    memory = next(m for m in client.get("/api/state").json()["memories"] if m["status"] == "active")
    deleted = client.delete(f"/api/memories/{memory['id']}")
    assert deleted.status_code == 200, deleted.text
    state = client.get("/api/state").json()
    assert state["cards"] == [] and state["proactive"] == []
    assert len(state["memories"]) == 1 and state["memories"][0]["status"] == "pending"
    assert client.get(f"/api/memories/{memory['id']}/source").status_code == 404
    raw = client.get(f"/api/recordings/{recording['id']}/audio")
    assert raw.status_code == 200 and raw.content == tiny_wav()


@pytest.mark.parametrize("verb,endpoint,payload", [
    ("get", "/api/recordings/missing", None),
    ("get", "/api/recordings/missing/audio", None),
    ("get", "/api/memories/missing/source", None),
    ("post", "/api/recordings/missing/confirm", {}),
    ("post", "/api/recordings/missing/retry", None),
    ("delete", "/api/recordings/missing", None),
    ("delete", "/api/memories/missing", None),
])
def test_unknown_records_return_404(api, verb, endpoint, payload):
    client, app, fake, directory = api
    kwargs = {"json": payload} if payload is not None else {}
    response = getattr(client, verb)(endpoint, **kwargs)
    assert response.status_code == 404, response.text
    assert isinstance(response.json()["detail"], str)


def test_delete_during_transcription_keeps_worker_copy_and_never_resurrects_source(api):
    client, app, fake, directory = api

    class PausedTranscriber(FakeTranscriber):
        def __init__(self):
            super().__init__()
            self.started, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
            self.readable_after_delete = False

        def transcribe(self, path, language="en"):
            self.started.set()
            try:
                assert self.release.wait(3), "Test did not release the worker"
                self.readable_after_delete = Path(path).is_file()
                return super().transcribe(path, language)
            finally:
                self.finished.set()

    paused = PausedTranscriber()
    module = importlib.import_module("hya.app")
    isolated_app = module.create_app(directory / "delete-race", transcriber=paused)
    with TestClient(isolated_app) as isolated:
        response = upload(isolated)
        recording_id = response["recording"]["id"]
        try:
            assert paused.started.wait(2)
            assert isolated.delete(f"/api/recordings/{recording_id}").status_code == 200
        finally:
            paused.release.set()
        assert paused.finished.wait(2)
        assert paused.readable_after_delete, "ASR needs a temporary processing copy when the original is deleted"
        assert isolated.get(f"/api/recordings/{recording_id}").status_code == 404
        assert isolated.get("/api/state").json()["memories"] == []


def test_external_llm_is_blocked_without_explicit_permission(api, monkeypatch):
    client, app, fake, directory = api
    monkeypatch.setenv("HYA_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("HYA_LLM_MODEL", "test-model")
    monkeypatch.setenv("HYA_ALLOW_EXTERNAL_LLM", "false")
    from hya.llm import TextAnalyzer
    analyzer = TextAnalyzer()
    module = importlib.import_module("hya.app")
    protected_app = module.create_app(directory / "external-block", transcriber=fake, analyzer=analyzer)
    with TestClient(protected_app) as protected:
        assert protected.post("/api/demo/seed").status_code == 200
        def forbidden_client(*args, **kwargs):
            pytest.fail("An external LLM request was attempted without permission")
        monkeypatch.setattr(importlib.import_module("hya.llm").httpx, "Client", forbidden_client)
        status = protected.get("/api/analysis/status").json()
        assert status["external"] is True and status["configured"] is False
        assert "explicitly allowed" in status["reason"]
        response = protected.post("/api/analysis/generate", json={})
        assert response.status_code == 422, response.text
        assert protected.get("/api/state").json()["cards"]


def test_unknown_api_returns_json_404_instead_of_spa(api):
    client, app, fake, directory = api
    for verb in ("get", "post", "patch", "delete"):
        response = getattr(client, verb)("/api/this-does-not-exist")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert "API" in response.json()["detail"]


def test_llm_receives_only_selected_day_confirmed_card_sources(api):
    client, app, fake, directory = api

    class CapturingAnalyzer:
        def __init__(self):
            self.captured = []

        def status(self):
            return {"configured": True, "mode": "llm", "external": False}

        def summarize(self, memories):
            self.captured = list(memories)
            return [{"category": category, "title": "你曾表達需要支持", "summary": "這一天你曾表達希望獲得安慰。",
                     "memory_ids": [memory["id"] for memory in memories if memory["category"] == category]}
                    for category in sorted({memory["category"] for memory in memories})]

    analyzer = CapturingAnalyzer()
    isolated_app = importlib.import_module("hya.app").create_app(directory / "selected-day", transcriber=FakeTranscriber(), analyzer=analyzer)
    with TestClient(isolated_app) as isolated:
        today = datetime.now(timezone(timedelta(hours=8)))
        older = today - timedelta(days=1)
        first = completed(isolated, upload(isolated, recorded_at=older.isoformat(), data=tiny_wav(1))["recording"]["id"])
        second = completed(isolated, upload(isolated, recorded_at=today.isoformat(), data=tiny_wav(2))["recording"]["id"])
        assert isolated.post(f"/api/recordings/{first['id']}/confirm", json={}).status_code == 200
        assert isolated.post(f"/api/recordings/{second['id']}/confirm", json={}).status_code == 200
        response = isolated.post("/api/analysis/generate", json={"date": older.date().isoformat()})
        assert response.status_code == 200, response.text
        assert len(analyzer.captured) == 2
        assert all(m["recording_id"] == first["id"] for m in analyzer.captured)
        assert all(m["event_time"].startswith(older.date().isoformat()) for m in analyzer.captured)
        cards = isolated.get("/api/state", params={"date": older.date().isoformat()}).json()["cards"]
        mood = next(c for c in cards if c["category"] == "mood")
        assert mood["summary"] == "這一天你曾表達希望獲得安慰。"
        assert set(mood["memory_ids"]).issubset({m["id"] for m in analyzer.captured})


@pytest.mark.parametrize("raw_content", [
    "not JSON",
    "[]",
    '{"cards": "wrong type"}',
    '{"cards": [42]}',
    '{"cards": [{"category": "mood", "title": "模型標題", "summary": "模型摘要", "memory_ids": ["invented-source"]}]}',
])
def test_invalid_llm_output_preserves_evidence_cards_and_returns_readable_error(api, monkeypatch, raw_content):
    client, app, fake, directory = api
    monkeypatch.setenv("HYA_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("HYA_LLM_MODEL", "mock-local-model")
    from hya.llm import TextAnalyzer
    isolated_app = importlib.import_module("hya.app").create_app(directory / "invalid-llm", transcriber=FakeTranscriber(), analyzer=TextAnalyzer())
    with TestClient(isolated_app, raise_server_exceptions=False) as isolated:
        assert isolated.post("/api/demo/seed").status_code == 200
        before = isolated.get("/api/state").json()["cards"]

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": raw_content}}]}

        class MockLocalClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def post(self, url, **kwargs):
                assert url == "http://localhost:11434/v1/chat/completions"
                return FakeResponse()

        monkeypatch.setattr(importlib.import_module("hya.llm").httpx, "Client", MockLocalClient)
        response = isolated.post("/api/analysis/generate", json={})
        assert response.status_code in {422, 502}, response.text
        assert isinstance(response.json()["detail"], str)
        after = isolated.get("/api/state").json()["cards"]
        assert {(c["category"], c["summary"], tuple(c["memory_ids"])) for c in before} == {
            (c["category"], c["summary"], tuple(c["memory_ids"])) for c in after
        }
