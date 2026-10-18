"""Synthetic-only CPU ASR, speaker changes, storage and optional DeepSeek check.

python scripts/verify_sensevoice_asr.py --run
Add --with-deepseek to process only generated text through the configured API.
Without --run, only the two-voice offline fixture is prepared and inspected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hya.asr import inspect_audio
from hya.engine import Engine
from hya.sensevoice_asr import SenseVoiceTranscriber
from verify_qwen_asr import TURNS, generate_fixture, wer


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def peak_memory_mib() -> float | None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        values = Counters()
        values.cb = ctypes.sizeof(values)
        kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(values), values.cb):
            return round(values.PeakWorkingSetSize / 2**20, 1)
    else:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--with-deepseek", action="store_true")
    args = parser.parse_args()
    if args.with_deepseek and not args.run:
        parser.error("--with-deepseek requires --run")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = ROOT / "artifacts/private" / f"sensevoice-asr-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    fixture = output.with_suffix(".wav")
    report = {"checked_at_utc": stamp, "status": "failed", "step": "fixture",
              "input": "two offline synthetic English voices in A/B/A order",
              "asr_called": False, "external_asr_called": False, "deepseek_called": False,
              "application_data_used": False, "cloud_server_tested": False}
    try:
        boundaries = generate_fixture(fixture)
        audio = inspect_audio(fixture)
        report.update(audio=audio, expected_turns=boundaries, fixture_path=str(fixture.relative_to(ROOT)))
        if not args.run:
            report.update(status="fixture_passed", step="completed")
        else:
            load_dotenv(ROOT / ".env")
            report["step"] = "cpu_asr"
            transcriber = SenseVoiceTranscriber()
            report["health_before"] = transcriber.health()
            check(transcriber.available, transcriber.error or "The CPU speech models are not ready.")
            print("Running local CPU speech recognition and mandatory speaker diarization...", flush=True)
            start = time.monotonic()
            report["asr_called"] = True
            result = transcriber.transcribe(fixture, language="en")
            elapsed = time.monotonic() - start
            segments = result["segments"]
            check(bool(segments), "No speech transcript was returned for the synthetic recording.")
            check(all(0 <= part["start"] < part["end"] <= audio["duration"] + .001
                      and part.get("words", []) == [] for part in segments),
                  "The CPU pipeline returned invalid segment times or invented word alignment.")
            dominant = []
            for turn in boundaries:
                weights = {}
                for part in segments:
                    label = part.get("speaker", "unknown")
                    overlap = max(0, min(part["end"], turn["end"]) - max(part["start"], turn["start"]))
                    if overlap and label != "unknown":
                        weights[label] = weights.get(label, 0) + overlap
                dominant.append(max(weights, key=weights.get) if weights else None)
            labels = {part.get("speaker") for part in segments} - {None, "unknown"}
            known_characters = sum(len(part["text"]) for part in segments if part.get("speaker") != "unknown")
            known_fraction = known_characters / max(1, sum(len(part["text"]) for part in segments))
            error_rate = wer(" ".join(turn[2] for turn in TURNS), " ".join(part["text"] for part in segments))
            speakers_passed = bool(dominant[0] and dominant[1] and dominant[0] != dominant[1]
                                   and dominant[0] == dominant[2] and known_fraction >= .8)
            report.update(segments=segments, metrics={**result.get("metrics", {}),
                "elapsed_seconds": round(elapsed, 3), "real_time_factor": round(elapsed / audio["duration"], 3),
                "peak_process_working_set_mib": peak_memory_mib(), "word_error_rate": round(error_rate, 4),
                "anonymous_label_count": len(labels), "turn_dominant_labels": dominant,
                "labeled_transcript_character_fraction": round(known_fraction, 4),
                "speaker_change_and_return": speakers_passed, "real_conversation_accuracy": "not measured",
                "segment_count": len(segments), "word_timestamps": False})
            check(speakers_passed, "Synthetic speaker-change/return or known-speaker coverage check did not pass.")
            check(error_rate <= .2, "Synthetic transcription word error rate exceeds20percent.")
            report["step"] = "source_storage"
            with tempfile.TemporaryDirectory(prefix="hya-sensevoice-verification-") as folder:
                engine = Engine(Path(folder), model_mode=True)
                recording = engine.create_recording("synthetic-speakers.wav", fixture,
                    hashlib.sha256(fixture.read_bytes()).hexdigest(), None)
                engine.ingest_segments(recording["id"], segments, language="en", extract=False)
                stored = engine.get_recording(recording["id"])["segments"]
                check(len(stored) == len(segments) and all(
                    item["subject_id"] == "unknown" and not item["confirmed"]
                    and item["speaker_label"] == raw["speaker"] and item["text"] == raw["text"]
                    and item["words"] == [] and item["start"] == raw["start"] and item["end"] == raw["end"]
                    for item, raw in zip(stored, segments)), "Source storage altered the transcript, timing or unknown identity.")
                report["source_storage"] = "original segments retained; all identities unknown and unconfirmed"
                if args.with_deepseek:
                    from hya.memory_agents import MemoryAgents
                    from hya.llm import TextAnalyzer
                    from hya.pipeline import understand_recording
                    agents, analyzer = MemoryAgents(), TextAnalyzer()
                    check(agents.status()["configured"] and analyzer.status()["configured"], "DeepSeek must be configured.")
                    report["step"], report["deepseek_called"] = "deepseek", True
                    print("Processing only the synthetic transcript through DeepSeek...", flush=True)
                    understood = understand_recording(engine, agents, recording["id"])
                    pipeline = understood.get("memory_pipeline", {})
                    report["deepseek_pipeline"] = {"status": pipeline["status"], "stage": pipeline.get("stage"),
                        "stages": pipeline.get("stages", []), "error": pipeline.get("error")}
                    check(pipeline["status"] == "completed", "The synthetic DeepSeek memory pipeline did not complete.")
            report.update(status="passed", step="completed")
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output.relative_to(ROOT))}), flush=True)
    return 0 if report["status"] in {"passed", "fixture_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
