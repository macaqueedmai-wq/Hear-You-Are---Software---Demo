"""Verify real-source Warning in a disposable DB using only invented text.

--live-model sends these invented English/Chinese sentences to the configured
text provider. It never opens the production DB, uploads audio, or changes a
real person's preferences. No credentials are printed or written to reports.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

ROOT = next((root for root in (Path(__file__).resolve().parents[1], Path.cwd())
             if (root / "backend" / "hya").is_dir()), Path.cwd())
sys.path.insert(0, str(ROOT / "backend"))


def verify(*, live_model=False):
    from hya.engine import Engine, HK, VersionConflict
    from hya.llm import TextAnalyzer
    analyzer = TextAnalyzer() if live_model else None
    if analyzer:
        assert analyzer.status()["configured"], "Text provider is not configured"
    results = []
    with tempfile.TemporaryDirectory(prefix="hya-real-warning-verify-") as temporary:
        for name, texts in [
            ("strong_english", [(0, "I feel completely overwhelmed and I can't cope today.")]),
            ("strong_chinese", [(0, "我今天感到絕望，我撐不住了。")]),
            ("repeated_distress", [(-6, "I feel stressed about work."),
                                   (-2, "I feel anxious about work."),
                                   (0, "I feel stressed about work.")]),
        ]:
            engine = Engine(Path(temporary) / name, model_mode=True)
            now = datetime.now(HK)
            for offset, text in texts:
                recording = engine.create_recording("Invented verification transcript", None, str(uuid4()),
                                                     (now + timedelta(days=offset)).isoformat(), False)
                engine.ingest_segments(recording["id"], [{"start": 0, "end": 8, "text": text}],
                                       extract=False, subject_confirmed=True)
                snapshot = engine.agent_snapshot(recording["id"])
                memories = engine.prepare_agent_memories(snapshot, [{
                    "source_segment_id": snapshot["segments"][0]["id"],
                    "content": "The speaker describes how they feel.", "quote": text,
                    "category": "mood", "memory_type": "episodic", "scope": "recorded_conversation",
                }])
                engine.apply_agent_memories(recording["id"], memories, snapshot["revision"], {"status": "completed"})
            assert not engine.evaluate()["cards"], "Warning must be opt-in"
            engine.settings_patch({"preferences": {"warning": True}})
            assert engine.state()["settings"]["warning_demo_enabled"] is False
            card = engine.evaluate()["cards"][0]
            assert card["type"] == "warning" and not card["is_demo"] and not card["demo_only"]
            assert card["warning_trigger"]["day_count"] == (3 if name == "repeated_distress" else 1)
            snapshot = engine.proactive_text_snapshot(card["id"], card["versions"], card.get("display_schedule_revision", 0))
            wording = analyzer.care_text(snapshot["card"], snapshot["memories"]) if analyzer else {
                "title": "A check-in for you", "summary": "You described feeling overwhelmed. Would a short pause help?"}
            engine.apply_proactive_text(card["id"], **wording, expected_versions=card["versions"],
                                        expected_display_revision=card.get("display_schedule_revision", 0))
            current = engine.state()["proactive"][0]
            assert current["generation_mode"] == "llm" and current["displayed_at"]
            assert current["versions"] == card["versions"] and current["source_refs"] == card["source_refs"]
            assert current["warning_trigger"] == card["warning_trigger"]
            assert "fictional" not in (current["title"] + current["summary"]).casefold()
            engine.settings_patch({"preferences": {"warning": False}})
            assert not engine.state()["proactive"]
            try:
                engine.apply_proactive_text(card["id"], **wording, expected_versions=card["versions"])
            except VersionConflict:
                pass
            else:
                raise AssertionError("A disabled Warning accepted stale model text")
            results.append({"scenario": name, "support_basis": card["support_basis"],
                            "source_count": len(card["source_refs"]), "day_count": card["warning_trigger"]["day_count"],
                            "title": current["title"], "summary": current["summary"], "status": "passed"})
    return {"status": "passed", "invented_text_only": True, "production_data_accessed": False,
            "asr_called": False, "external_model_called": bool(live_model),
            "provider": analyzer.status()["provider"] if analyzer else "offline stub", "scenarios": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live-model", action="store_true")
    args = parser.parse_args()
    try:
        report = verify(live_model=args.live_model)
    except Exception as error:
        report = {"status": "failed", "error_type": type(error).__name__, "invented_text_only": True}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
