"""Live smoke check using synthetic text and a disposable database only.

Run from the project root: python scripts/verify_deepseek.py
Calls the four configured text stages and daily-card generation (billable).
Does not read the real application database, recordings or audio, or call ASR.
Only safe status/count metadata is written to artifacts/private/.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hya.engine import Engine, HK
from hya.llm import TextAnalyzer
from hya.memory_agents import MemoryAgents
from hya.pipeline import understand_recording


class ObservedAgents(MemoryAgents):
    def __init__(self):
        super().__init__()
        self.checks = []

    def request(self, stage, payload, **kwargs):
        started = time.monotonic()
        print(f"Requesting synthetic {stage} JSON...", flush=True)
        result = super().request(stage, payload, **kwargs)
        self.checks.append({"stage": stage, "request": "completed",
                            "seconds": round(time.monotonic() - started, 2)})
        return result


def main() -> int:
    load_dotenv(ROOT / ".env")
    stamp = datetime.now(HK)
    report = {"checked_at": stamp.isoformat(), "input": "synthetic text only",
              "asr_called": False, "application_data_used": False, "status": "failed"}
    output = ROOT / "artifacts/private" / f"deepseek-live-{stamp:%Y%m%d-%H%M%S}.json"
    try:
        agents = ObservedAgents()
        report.update(provider=agents.status()["provider"], model=agents.model)
        with tempfile.TemporaryDirectory(prefix="hya-synthetic-deepseek-") as folder:
            engine = Engine(Path(folder), model_mode=True)
            texts = ["I felt nervous before a presentation.",
                     "I talked with a friend after class and felt relieved.",
                     "On most days I prefer quiet walks."]
            recording = engine.create_recording(
                "synthetic-transcript-only", None,
                hashlib.sha256(" ".join(texts).encode()).hexdigest(), stamp)
            engine.ingest_segments(recording["id"], [
                {"start": i * 4.0, "end": i * 4.0 + 3.0, "text": text,
                 "speaker": "speaker_0", "words": []}
                for i, text in enumerate(texts)], extract=False)
            result = understand_recording(engine, agents, recording["id"])
            pipeline = result["memory_pipeline"]
            report["stages"] = pipeline["stages"]
            memories = engine.state(stamp.date().isoformat())["memories"]
            assert pipeline["status"] == "completed" and memories, "No validated memory output"
            assert all(not item["reviewed"] and item["subject_id"] == "unknown" for item in memories)
            # Simulated identity confirmation is confined to this disposable fixture.
            engine.confirm_recording(recording["id"])
            date = stamp.date().isoformat()
            memories = engine.state(date)["memories"]
            print("Requesting synthetic daily-card JSON...", flush=True)
            summaries = TextAnalyzer().summarize(memories)
            assert summaries, "No validated card output"
            state = engine.apply_card_summaries(
                date, summaries, {item["id"]: item["version"] for item in memories})
            assert state["cards"] and all(card["analysis_mode"] == "llm" for card in state["cards"])
            assert all(not item["reviewed"] for item in state["memories"])
            report.update(status="passed", memory_count=len(memories), card_count=len(state["cards"]),
                          source_contracts="validated", semantic_review="still required")
    except Exception as error:
        # Never dump HTTP request objects, provider bodies, credentials or model text.
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        if isinstance(error, httpx.HTTPStatusError):
            report["http_status"] = error.response.status_code
    finally:
        if "agents" in locals():
            report["requests"] = agents.checks
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        print(f"Report: {output.relative_to(ROOT)}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
