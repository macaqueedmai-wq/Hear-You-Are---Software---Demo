"""Exercise upload -> automatic atoms/topics -> edit -> automatic support.

All evidence, speech and text-model responses are synthetic. Both the default
app singleton and the tested app use disposable directories. No real app data
or external model is read or called.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave

ROOT = next((root for root in (Path(__file__).resolve().parents[1], Path.cwd())
             if (root / "backend" / "hya").is_dir()), Path.cwd())
sys.path.insert(0, str(ROOT / "backend"))

TEXTS = ["Please comfort me.", "I felt worried before my presentation.",
         "Talking about the presentation helps me.", "We discussed a weekend walk.",
         "The walk will be on the quiet trail.", "I will bring water for the walk."]


class FixtureSpeech:
    def __init__(self):
        self.calls = 0

    def health(self):
        return {"state": "idle", "available": True, "loaded": False}

    def transcribe(self, path, language="en"):
        self.calls += 1
        return {"segments": [{"start": i * 0.5, "end": i * 0.5 + 0.4,
                               "text": text, "speaker": "speaker_0", "words": []}
                              for i, text in enumerate(TEXTS)], "language": "en", "metrics": {}}


class FixtureAgents:
    def status(self):
        return {"configured": True, "model": "synthetic", "provider": "synthetic",
                "stages": {"raw_evidence": {"configured": False}}}

    def extract(self, recording, segments):
        # Model summaries use third person; intent must survive this paraphrase.
        contents = ["The speaker asked for comfort.", "The speaker felt worried before their presentation.",
                    *TEXTS[2:]]
        return [{"content": contents[i], "category": ["mood", "mood", "social", "social", "moments", "moments"][i],
                 "memory_type": "episodic", "scope": "recorded_conversation",
                 "source_segment_id": segment["id"], "quote": segment["text"]}
                for i, segment in enumerate(segments)]

    def consolidate(self, memories):
        return {"proposals": []}

    def group_transcript(self, recording, segments, atomic_proposals):
        return [{"id": f"topic-{i + 1}", "title": title, "summary": summary,
                 "source_segment_ids": [segment["id"] for segment in segments[i * 3:i * 3 + 3]]}
                for i, (title, summary) in enumerate([
                    ("Support before a presentation", "The speaker asks for comfort and describes presentation worries."),
                    ("Weekend walk", "The speaker discusses a quiet walk and bringing water.")])]

    def retrieve(self, query, memories):
        return [memory["id"] for memory in memories]

    def govern(self, memories, settings):
        return {"reviews": []}


class FixtureAnalyzer:
    def __init__(self):
        self.care_calls = 0
        self.summary_calls = 0
        self.summary_inputs = []

    def status(self):
        return {"configured": True, "external": False, "mode": "llm"}

    def summarize(self, memories):
        self.summary_calls += 1
        self.summary_inputs.append([(memory["id"], memory["version"], memory["content"]) for memory in memories])
        return [{"category": category, "title": "Synthetic daily summary",
                 "summary": "The speaker described these synthetic events.",
                 "memory_ids": [memory["id"] for memory in memories if memory["category"] == category]}
                for category in dict.fromkeys(memory["category"] for memory in memories)]

    def care_text(self, card, memories):
        self.care_calls += 1
        return {"title": "A little support", "summary": "Would a short quiet pause help right now?"}


def wait_for(read, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.03)
    raise AssertionError("Synthetic background workflow did not complete")


def verify():
    with tempfile.TemporaryDirectory(prefix="hya-upload-smoke-") as folder:
        os.environ.update(HYA_DATA_DIR=str(Path(folder) / "unused-default"),
                          HYA_MEMORY_MODE="deepseek", HYA_ALLOW_EXTERNAL_LLM="false",
                          HYA_ALLOWED_HOSTS="testserver,localhost,127.0.0.1", HYA_DEPLOYMENT_MODE="local")
        from fastapi.testclient import TestClient
        from hya.app import create_app

        speech, analyzer = FixtureSpeech(), FixtureAnalyzer()
        app = create_app(Path(folder) / "fixture", transcriber=speech,
                         analyzer=analyzer, memory_agents=FixtureAgents())
        with TestClient(app) as client:
            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
                wav.writeframes(b"\0\0" * 25_600)
            uploaded = client.post("/api/recordings", files={"file": ("synthetic-upload.wav", audio.getvalue(), "audio/wav")},
                                   data={"recorded_at": "2000-01-01T00:00:00+08:00", "language": "en"})
            assert uploaded.status_code == 200
            ident = uploaded.json()["recording"]["id"]
            read_recording = lambda: client.get(f"/api/recordings/{ident}").json()
            recording = wait_for(read_recording, lambda value: value.get("memory_pipeline", {}).get("status") == "completed")
            assert recording["recorded_at"] == recording["created_at"]
            assert recording["recorded_at"].startswith("20") and not recording["recorded_at"].startswith("2000-")
            original_segments = recording["segments"]
            topics = recording["topics"]
            assert len(topics) == 2 and [topic["number"] for topic in topics] == [1, 2]
            state = client.get("/api/state").json()
            assert len(state["memories"]) == 6 and all(not memory["reviewed"] for memory in state["memories"])
            assert not state["proactive"] and analyzer.care_calls == 0
            assert state["summary_generation"]["status"] == "no-sources" and analyzer.summary_calls == 0
            topic = topics[0]
            memory = next(memory for memory in state["memories"] if memory["id"] in topic["memory_ids"] and "worried" in memory["content"])
            correction = {"version": topic["version"], "title": "Corrected support topic", "summary": "The speaker would like reassurance.",
                          "memories": [{"id": memory["id"], "version": memory["version"], "content": "The speaker felt worried before an important presentation."}]}
            route = f"/api/recordings/{ident}/topics/{topic['id']}"
            edited = client.patch(route, json=correction)
            assert edited.status_code == 200
            changed_topic = next(value for value in edited.json()["topics"] if value["id"] == topic["id"])
            assert changed_topic["edited"] and changed_topic["version"] == topic["version"] + 1
            assert edited.json()["segments"] == original_segments
            assert client.patch(route, json=correction).status_code == 409
            corrected = next(value for value in client.get("/api/state").json()["memories"] if value["id"] == memory["id"])
            assert corrected["content"] == "The speaker felt worried before an important presentation." and corrected["subject_id"] == "unknown"
            # Speaker identity is explicitly simulated only in this disposable fixture.
            confirmed = client.post(f"/api/recordings/{ident}/confirm", json={"segment_ids": [s["id"] for s in original_segments]})
            assert confirmed.status_code == 200
            ready = wait_for(lambda: client.get("/api/state").json(), lambda value: bool(value["proactive"])
                             and value.get("summary_generation", {}).get("status") == "ready")
            assert len(ready["cards"]) == 3 and all(card["analysis_mode"] == "llm" for card in ready["cards"])
            confirmed_segments = read_recording()["segments"]
            assert all(not value["reviewed"] for value in ready["memories"])
            assert ready["proactive"][0]["generation_mode"] == "llm"
            assert ready["proactive"][0]["type"] == "comfort"
            # The request itself was never rewritten by the fixture user.
            request = next(value for value in ready["memories"] if value["content"] == "The speaker asked for comfort.")
            assert not request.get("user_corrected") and request["id"] in ready["proactive"][0]["memory_ids"]
            assert read_recording()["topics"][0]["title"] == "Corrected support topic"
            assert analyzer.care_calls == 1 and speech.calls == 1 and analyzer.summary_calls == 1
            for _ in range(3):
                assert client.get("/api/state").json()["proactive"]
            assert analyzer.care_calls == 1 and analyzer.summary_calls == 1
            # A direct request can show Comfort during snooze without resuming automation.
            automatic_card = ready["proactive"][0]
            assert client.post(f"/api/proactive/{automatic_card['id']}/feedback", json={"action": "snooze"}).status_code == 200
            paused = client.get("/api/state").json()
            assert not paused["proactive"] and paused["assessment"]["types"]["comfort"]["condition_met"]
            paused_settings = paused["settings"]
            before_request_memories = paused["memories"]
            direct = client.post("/api/proactive/display-now", json={"type": "comfort", "date": ready["date"]})
            assert direct.status_code == 200
            requested = direct.json()["cards"]
            assert len(requested) == 1 and requested[0]["type"] == "comfort"
            assert requested[0]["delivery_mode"] == "requested" and requested[0]["generation_mode"] == "llm"
            for _ in range(3):
                displayed = client.get("/api/state").json()
                assert [card["id"] for card in displayed["proactive"]] == [requested[0]["id"]]
                assert displayed["settings"] == paused_settings and displayed["memories"] == before_request_memories
            repeated = client.post("/api/proactive/display-now", json={"type": "comfort", "date": ready["date"]}).json()
            assert repeated["cards"][0]["id"] == requested[0]["id"] and analyzer.care_calls == 2
            assert read_recording()["segments"] == confirmed_segments
            for kind in ("reminder", "suggestion", "comfort", "warning"):
                timing = displayed["assessment"]["types"][kind]["display_time"]
                assert set(timing) == {"scheduled_at", "effective_at", "displayed_at", "reason"}
            # Editing a future display time postpones an already requested card.
            future = (datetime.now(timezone(timedelta(hours=8))) + timedelta(minutes=10)).isoformat()
            schedule_patch = {"type": "comfort", "display_at": future, "expected_display_at": None}
            scheduled_response = client.patch("/api/proactive/display-time", json=schedule_patch)
            assert scheduled_response.status_code == 200
            scheduled = scheduled_response.json()
            assert not scheduled["proactive"]
            assert scheduled["assessment"]["types"]["comfort"]["display_time"]["scheduled_at"] == future
            assert client.patch("/api/proactive/display-time", json=schedule_patch).status_code == 409
            # Generic manual display bypasses scheduling without changing it.
            immediate = client.post("/api/proactive/display-now", json={"type": "comfort", "date": ready["date"]})
            assert immediate.status_code == 200 and immediate.json()["cards"]
            immediate_card = immediate.json()["cards"][0]
            assert immediate_card["type"] == "comfort" and immediate_card["generation_mode"] == "llm"
            calls_after_display = analyzer.care_calls
            for _ in range(3):
                visible = client.get("/api/state").json()
                assert visible["settings"] == scheduled["settings"]
                assert visible["memories"] == before_request_memories
                assert visible["assessment"]["types"]["comfort"]["display_time"]["scheduled_at"] == future
                assert any(card["id"] == immediate_card["id"] for card in visible["proactive"])
            again = client.post("/api/proactive/display-now", json={"type": "comfort", "date": ready["date"]}).json()
            assert again["cards"][0]["id"] == immediate_card["id"] and analyzer.care_calls == calls_after_display
            assert read_recording()["segments"] == confirmed_segments
            # Correct one atomic fact after ready: regenerate summaries automatically.
            latest_topic = read_recording()["topics"][0]
            latest_memory = next(value for value in ready["memories"] if value["id"] == memory["id"])
            replacement_text = "The speaker felt slightly worried before the presentation."
            assert client.patch(route, json={"version": latest_topic["version"], "title": latest_topic["title"],
                "summary": latest_topic["summary"], "memories": [{"id": latest_memory["id"],
                "version": latest_memory["version"], "content": replacement_text}]}).status_code == 200
            revised = wait_for(lambda: client.get("/api/state").json(), lambda value:
                               value.get("summary_generation", {}).get("status") == "ready"
                               and all(card["analysis_mode"] == "llm" for card in value["cards"]))
            assert analyzer.summary_calls == 2
            assert any(ident == memory["id"] and text == replacement_text
                       for ident, version, text in analyzer.summary_inputs[-1])
            assert read_recording()["segments"] == confirmed_segments
            engine = app.state.engine
            legacy = engine.create_recording("synthetic-legacy", None, "synthetic-legacy-hash", "2000-01-01T00:00:00+08:00")
            engine.align_upload_dates()
            legacy = engine.get_recording(legacy["id"])
            assert legacy["recorded_at"] == legacy["created_at"]
            versions = {value["id"]: value["version"] for value in engine.state()["memories"]}
            engine.align_upload_dates()
            assert versions == {value["id"]: value["version"] for value in engine.state()["memories"]}
    return {"status": "passed", "synthetic_fixture_only": True, "application_data_used": False,
            "external_model_called": False, "upload_timestamp_automatic": True,
            "automatic_atomic_memories": 6, "numbered_topics": 2, "topic_and_fact_edit": True,
            "stale_edit_rejected": True, "raw_transcript_preserved": True,
            "edited_topics_survive_speaker_confirmation": True,
            "automatic_support_without_interpretation_review": True,
            "automatic_daily_summary_categories": 3,
            "daily_summary_single_call_per_source_version": True,
            "daily_summaries_refresh_after_fact_edit": True,
            "third_person_atoms_keep_source_intent": True,
            "care_generation_single_call_per_candidate": True, "requested_comfort_during_snooze": True,
            "requested_comfort_keeps_settings_and_sources": True, "display_time_metadata_all_four_types": True,
            "future_schedule_hides_visible_support": True, "stale_schedule_edit_rejected": True,
            "generic_manual_display_keeps_schedule_and_sources": True, "upload_date_migration_idempotent": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = verify()
    except Exception as error:
        report = {"status": "failed", "error_type": type(error).__name__, "synthetic_fixture_only": True}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
