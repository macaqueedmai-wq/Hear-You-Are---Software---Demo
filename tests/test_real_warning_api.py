"""Real upload-to-Warning HTTP checks, with offline ASR and DeepSeek doubles."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from test_api import tiny_wav
from test_pipeline_api import wait_for_pipeline
from test_support_display_api import DisplayAnalyzer, support_api, wait_card


WORDS = "I feel completely overwhelmed."
ATOM = "The speaker describes feeling overwhelmed today."


class RealWarningAnalyzer(DisplayAnalyzer):
    def care_text(self, card, memories):
        self.calls.append({"card": deepcopy(card), "memories": deepcopy(memories)})
        self.started.set()
        if self.block:
            assert self.release.wait(5), "The offline Warning worker was not released"
        return {"title": "A check-in for a difficult day",
                "summary": "You said things feel overwhelming today. Would taking a pause and reaching out to someone you trust help?"}


@pytest.fixture
def real_warning_api(support_api, monkeypatch):
    ctx = support_api
    probe = RealWarningAnalyzer()
    monkeypatch.setattr(ctx.analyzer, "care_text", probe.care_text)
    ctx.app.state.memory_agents.content = ATOM

    def transcribe(path, language="en"):
        assert Path(path).is_file()
        return {"segments": [{"start": .1, "end": 2.8, "text": WORDS,
                              "speaker": "speaker_0", "words": []}],
                "language": language, "duration": 3.2, "metrics": {"provider": "offline-test"}}

    monkeypatch.setattr(ctx.app.state.transcriber, "transcribe", transcribe)
    ctx.warning_probe = probe
    try:
        yield ctx
    finally:
        probe.release.set()


def uploaded(ctx):
    response = ctx.client.post("/api/recordings", files={
        "file": ("warning-check.wav", tiny_wav(), "audio/wav")}, data={"language": "en"})
    assert response.status_code == 200, response.text
    recording = wait_for_pipeline(ctx.client, response.json()["recording"]["id"])
    state = ctx.client.get("/api/state").json()
    memories = [memory for memory in state["memories"] if memory["recording_id"] == recording["id"]]
    assert recording["is_demo"] is False and len(memories) == 1
    assert memories[0]["content"] == ATOM and memories[0]["source_quote"] == WORDS
    assert memories[0]["subject_id"] == "unknown" and not memories[0]["confirmed"]
    return recording, memories[0]


def enable(ctx, *, quiet=False):
    response = ctx.client.patch("/api/settings", json={
        "preferences": {"warning": True}, "warning_demo_enabled": False, "quiet_mode": quiet})
    assert response.status_code == 200, response.text
    assert response.json()["preferences"]["warning"] is True
    assert response.json()["warning_demo_enabled"] is False


def confirm(ctx, recording):
    response = ctx.client.post(f"/api/recordings/{recording['id']}/confirm", json={
        "segment_ids": [segment["id"] for segment in recording["segments"]]})
    assert response.status_code == 200, response.text
    return response.json()


def test_uploaded_audio_only_generates_real_warning_after_identity_confirmation(real_warning_api):
    ctx = real_warning_api
    recording, memory = uploaded(ctx)
    enable(ctx)
    pending = ctx.client.get("/api/state").json()
    assert pending["proactive"] == []
    assert pending["assessment"]["types"]["warning"]["condition_met"] is False
    assert ctx.warning_probe.calls == []

    confirm(ctx, recording)
    state, card = wait_card(ctx, "warning")
    current = next(value for value in state["memories"] if value["id"] == memory["id"])
    assert current["confirmed"] and current["subject_id"] == "user"
    assert current["reviewed"] is False
    assert card["is_demo"] is False and card["demo_only"] is False
    assert card["generation_mode"] == "llm" and card["support_basis"] == "acute_distress"
    assert card["date"] == recording["recorded_at"][:10] == ctx.clock[0].date().isoformat()
    assert card["memory_ids"] == card["current_memory_ids"] == [memory["id"]]
    assert card["versions"] == {current["id"]: current["version"]}
    assert card["source_refs"] == current["source_refs"]
    assert card["warning_trigger"] == {
        "basis": "acute_distress", "topics": ["stress"], "dates": [card["date"]],
        "day_count": 1, "window_days": 1,
        "evidence_quotes": [{"memory_id": memory["id"], "text": WORDS}],
    }
    assert card["title"] == "A check-in for a difficult day"
    assert "reaching out to someone you trust" in card["summary"]
    assert "fictional" not in (card["title"] + card["summary"] + card["condition_reason"]).lower()
    assert "not a diagnosis" in card["condition_reason"]
    assert len(ctx.warning_probe.calls) == 1
    sent = ctx.warning_probe.calls[0]
    assert sent["card"]["warning_trigger"] == card["warning_trigger"]
    assert [value["id"] for value in sent["memories"]] == [memory["id"]]

    source = ctx.client.get(f"/api/memories/{memory['id']}/source").json()
    assert source["recording"]["id"] == recording["id"]
    assert source["segments"][0]["id"] == recording["segments"][0]["id"]
    assert source["segments"][0]["text"] == WORDS
    assert ctx.client.get(f"/api/recordings/{recording['id']}/audio").content == tiny_wav()
    assert state["settings"]["preferences"]["warning"] is True
    assert state["settings"]["warning_demo_enabled"] is False


@pytest.mark.parametrize("change", ["turn_off", "correct_source"])
def test_inflight_real_warning_cannot_commit_after_permission_or_source_retraction(real_warning_api, change):
    ctx = real_warning_api
    recording, memory = uploaded(ctx)
    enable(ctx, quiet=True)
    confirmed = confirm(ctx, recording)
    current = next(value for value in ctx.client.get("/api/state").json()["memories"]
                   if value["id"] == memory["id"])
    assert ctx.warning_probe.calls == []
    ctx.warning_probe.block = True
    with ThreadPoolExecutor(max_workers=1) as requests:
        request = requests.submit(ctx.client.post, "/api/proactive/display-now", json={"type": "warning"})
        try:
            assert ctx.warning_probe.started.wait(3)
            if change == "turn_off":
                response = ctx.client.patch("/api/settings", json={"preferences": {"warning": False}})
            else:
                response = ctx.client.patch(f"/api/memories/{memory['id']}", json={
                    "content": "I feel calm and able to cope today.", "version": current["version"], "confirmed": True})
            assert response.status_code == 200, response.text
        finally:
            ctx.warning_probe.release.set()
        rejected = request.result(timeout=5)
    assert rejected.status_code == 409, rejected.text
    state = ctx.client.get("/api/state").json()
    assert state["proactive"] == []
    assert state["assessment"]["types"]["warning"]["condition_met"] is False
    assert len(ctx.warning_probe.calls) == 1
    assert ctx.client.get(f"/api/recordings/{recording['id']}").json()["segments"] == confirmed["segments"]
    assert ctx.client.get(f"/api/recordings/{recording['id']}/audio").content == tiny_wav()
