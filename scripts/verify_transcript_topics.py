"""Live topic-review smoke check using invented text and a disposable database.

Run with --live to authorize configured DeepSeek text requests (billable).
No application recordings/database/audio are read or changed. An optional
--output saves only verification counts/status; no keys or model text.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = next((root for root in (Path(__file__).resolve().parents[1], Path.cwd())
             if (root / "backend" / "hya").is_dir()), Path.cwd())
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv
from hya.engine import Engine, HK
from hya.memory_agents import MemoryAgents
from hya.pipeline import understand_recording


def verify() -> dict:
    started = time.monotonic()
    texts = [
        "I have a presentation tomorrow.",
        "I felt nervous when I rehearsed it.",
        "Practising the presentation with my friend helped me feel calmer.",
        "Let's talk about our weekend hike instead.",
        "For the hike, I would like the quiet trail by the lake.",
        "I will pack lunch and bring water for the hike.",
    ]
    with tempfile.TemporaryDirectory(prefix="hya-topic-smoke-") as folder:
        engine = Engine(folder, model_mode=True)
        recording = engine.create_recording(
            "synthetic-topic-review-only", None,
            hashlib.sha256("\n".join(texts).encode()).hexdigest(), datetime.now(HK),
        )
        engine.ingest_segments(recording["id"], [
            {"text": text, "start": i * 5.0, "end": i * 5.0 + 4.0,
             "speaker": "speaker_0" if i < 3 else "speaker_1", "words": []}
            for i, text in enumerate(texts)
        ], extract=False)
        original = deepcopy(engine.get_recording(recording["id"])["segments"])
        result = understand_recording(engine, MemoryAgents(), recording["id"])
        pipeline = result["memory_pipeline"]
        groups = pipeline["transcript_groups"]
        assert pipeline["status"] == "completed"
        assert result["segments"] == original
        assert [ident for group in groups for ident in group["source_segment_ids"]] == [
            segment["id"] for segment in original
        ]
        assert 0 < len(groups) < len(original), "Expected meaningful multi-fragment topic groups"
        assert any(len(group["source_segment_ids"]) > 1 for group in groups)
        assert all(group["title"] and group["summary"] for group in groups)
        memories = engine.state()["memories"]
        assert memories and all(not memory["reviewed"] and not memory["confirmed"]
                                and memory["subject_id"] == "unknown" for memory in memories)
        assert all(segment["subject_id"] == "unknown" for segment in result["segments"])
        assert engine.state()["proactive"] == []
        return {
            "status": "passed", "provider": "DeepSeek", "synthetic_text_only": True,
            "application_data_used": False, "asr_called": False,
            "source_segment_count": len(original), "topic_count": len(groups),
            "topic_source_counts": [len(group["source_segment_ids"]) for group in groups],
            "sources_unchanged": True, "ordered_partition": True,
            "identity_still_unconfirmed": True, "understanding_still_unreviewed": True,
            "memory_count": len(memories),
            "stages": pipeline["stages"], "elapsed_seconds": round(time.monotonic() - started, 3),
            "checked_at": datetime.now(HK).isoformat(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call configured DeepSeek with invented text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to run billable synthetic DeepSeek text requests.")
    load_dotenv(ROOT / ".env")
    try:
        report = verify()
    except Exception as error:
        report = {"status": "failed", "error_type": type(error).__name__,
                  "application_data_used": False, "synthetic_text_only": True}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
