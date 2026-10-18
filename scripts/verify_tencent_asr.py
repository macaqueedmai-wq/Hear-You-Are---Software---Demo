"""Synthetic-only Tencent ASR smoke check; never reads application recordings.

Windows offline fixture check: python scripts/verify_tencent_asr.py
Live ASR: python scripts/verify_tencent_asr.py --live
Live ASR + four DeepSeek stages + cards: add --with-deepseek
Live calls consume provider quota and may incur charges. This script does not
enable postpaid billing or change any provider account setting.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import wave

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hya.asr import TranscriptionError, inspect_audio
from hya.cloud_asr import MAX_INLINE_BYTES, SUPPORTED_ENGINES, TencentTranscriber
from hya.engine import Engine, HK
from hya.llm import TextAnalyzer
from hya.memory_agents import AgentError, MemoryAgents
from hya.pipeline import understand_recording

REFERENCE = (
    "Today I felt nervous before my presentation. "
    "I talked with a friend after class and felt relieved. "
    "On most days I prefer quiet walks."
)
LEADING_SILENCE_SECONDS = 3


class VerificationError(RuntimeError):
    """Fixed, credential-free diagnostics for verification failures."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def generate_english_wav(path: Path) -> None:
    """Generate English PCM16/16 kHz/mono locally; do not load an ASR model."""
    check(os.name == "nt", "Synthetic speech generation requires Windows System.Speech.")
    spoken = path.with_name("speech-only.wav")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "Add-Type -AssemblyName System.Speech\n"
        "$hyaSpeech = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "try {\n"
        "  $hyaVoice = $hyaSpeech.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'en-US' } | Select-Object -First 1\n"
        "  if (-not $hyaVoice) { throw 'An en-US voice is required' }\n"
        "  $hyaSpeech.SelectVoice($hyaVoice.VoiceInfo.Name)\n"
        "  $hyaFormat = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)\n"
        "  $hyaSpeech.SetOutputToWaveFile($env:HYA_VERIFY_WAV, $hyaFormat)\n"
        "  $hyaSpeech.Speak($env:HYA_VERIFY_TEXT)\n"
        "} finally { $hyaSpeech.Dispose() }\n"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "HYA_VERIFY_WAV": str(spoken), "HYA_VERIFY_TEXT": REFERENCE},
            capture_output=True, check=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise VerificationError("Synthetic speech generation failed; check the installed en-US System.Speech voice.") from None
    with wave.open(str(spoken), "rb") as source:
        check(source.getnchannels() == 1 and source.getframerate() == 16000 and source.getsampwidth() == 2,
              "The synthetic fixture must be 16 kHz PCM16 mono.")
        params, frames = source.getparams(), source.readframes(source.getnframes())
    with wave.open(str(path), "wb") as output:
        output.setparams(params)
        output.writeframes(b"\0" * (16000 * 2 * LEADING_SILENCE_SECONDS))
        output.writeframes(frames)
    spoken.unlink()


def word_error_rate(reference: str, hypothesis: str) -> float:
    expected, actual = (re.findall(r"[a-z0-9]+", text.lower()) for text in (reference, hypothesis))
    previous = list(range(len(actual) + 1))
    for index, word in enumerate(expected, 1):
        current = [index]
        for position, observed in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[position] + 1,
                               previous[position - 1] + (word != observed)))
        previous = current
    return round(previous[-1] / max(1, len(expected)), 6)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send only the generated fixture to Tencent; consumes ASR quota.")
    parser.add_argument("--with-deepseek", action="store_true", help="Also send synthetic transcript text to the configured DeepSeek stages/cards.")
    args = parser.parse_args()
    if args.with_deepseek and not args.live:
        parser.error("--with-deepseek requires --live")
    # Offline mode neither reads server credentials nor starts any provider call.
    if args.live:
        load_dotenv(ROOT / ".env")
    stamp = datetime.now(HK)
    report = {
        "checked_at": stamp.isoformat(), "input": "single-voice synthetic English audio only",
        "application_data_used": False, "asr_called": False, "deepseek_called": False,
        "billing_configuration_changed": False, "status": "failed", "step": "fixture",
    }
    mode = "live" if args.live else "offline"
    output = ROOT / "artifacts/private" / f"tencent-asr-{mode}-{stamp:%Y%m%d-%H%M%S}.json"
    try:
        with tempfile.TemporaryDirectory(prefix="hya-synthetic-tencent-") as folder:
            fixture = Path(folder) / "synthetic-english.wav"
            generate_english_wav(fixture)
            audio = inspect_audio(fixture)
            check(fixture.stat().st_size <= MAX_INLINE_BYTES and audio["channels"] == 1,
                  "The synthetic fixture does not meet Tencent inline audio limits.")
            report["audio"] = {**audio, "bytes": fixture.stat().st_size,
                               "leading_silence_seconds": LEADING_SILENCE_SECONDS}
            if not args.live:
                report.update(status="offline_passed", step="completed")
            else:
                report["step"] = "asr"
                asr = TencentTranscriber()
                check(asr.available, asr.error or "Tencent ASR is unavailable; complete the server configuration.")
                report["engine"] = asr.model_name if asr.model_name in SUPPORTED_ENGINES else "unsupported"
                print("Sending the temporary synthetic mono fixture to Tencent ASR...", flush=True)
                started = time.monotonic()
                report["asr_called"] = True
                result = asr.transcribe(fixture, language="en")
                segments = result["segments"]
                check(bool(segments), "Tencent ASR returned no speech for the synthetic fixture.")
                for segment in segments:
                    check(all(isinstance(segment[k], (int, float)) and not isinstance(segment[k], bool)
                              and math.isfinite(segment[k]) for k in ("start", "end"))
                          and 0 <= segment["start"] <= segment["end"] <= audio["duration"] + 0.001,
                          "The synthetic ASR timestamps are outside the original audio timeline.")
                words = [word for segment in segments for word in segment.get("words", [])]
                first_spoken = min(word["start"] for word in words) if words else segments[0]["start"]
                report["asr"] = {
                    "status": "passed", "elapsed_seconds": round(time.monotonic() - started, 3),
                    "segment_count": len(segments), "word_count": len(words),
                    "anonymous_label_count": len({s.get("speaker") for s in segments} - {None, "unknown"}),
                    "word_error_rate": word_error_rate(REFERENCE, " ".join(s["text"] for s in segments)),
                    "first_spoken_seconds": first_spoken,
                    "leading_silence_offset_observed": first_spoken >= LEADING_SILENCE_SECONDS - 0.5,
                    "timestamps_in_original_bounds": True,
                    "multi_speaker_accuracy": "not evaluated: one synthetic voice",
                }
                report["step"] = "source_storage"
                engine = Engine(Path(folder) / "disposable-database", model_mode=True)
                recording = engine.create_recording("synthetic-english.wav", fixture,
                    hashlib.sha256(fixture.read_bytes()).hexdigest(), stamp)
                engine.ingest_segments(recording["id"], segments, language="en", extract=False)
                stored = engine.get_recording(recording["id"])["segments"]
                check(len(stored) == len(segments) and all(
                    item["subject_id"] == "unknown" and not item["confirmed"]
                    and item["start"] == raw["start"] and item["end"] == raw["end"]
                    and item["text"] == raw["text"] and item["words"] == raw.get("words", [])
                    and item["speaker_label"] == raw.get("speaker", "unknown")
                    for item, raw in zip(stored, segments)),
                    "Synthetic transcript storage changed source content, timing or unknown identity.")
                report["source_storage"] = "original timing/content retained; identity remains unknown"
                if args.with_deepseek:
                    report["step"] = "deepseek"
                    agents, analyzer = MemoryAgents(), TextAnalyzer()
                    check(agents.status()["configured"] and analyzer.deepseek and analyzer.status()["configured"],
                          "The required DeepSeek stages and card model must be configured before verification.")
                    print("Processing only the synthetic transcript through DeepSeek...", flush=True)
                    report["deepseek_called"] = True
                    result = understand_recording(engine, agents, recording["id"])
                    pipeline = result["memory_pipeline"]
                    memories = engine.state(stamp.date().isoformat())["memories"]
                    check(pipeline["status"] == "completed" and bool(memories), "No validated synthetic memory output.")
                    check(all(m["subject_id"] == "unknown" and not m["reviewed"] for m in memories),
                          "Synthetic memory processing changed identity or semantic-review flags.")
                    # This identity simulation affects only the disposable fixture.
                    engine.confirm_recording(recording["id"])
                    memories = engine.state(stamp.date().isoformat())["memories"]
                    report["step"] = "cards"
                    summaries = analyzer.summarize(memories)
                    check(bool(summaries), "No validated synthetic card JSON was returned.")
                    state = engine.apply_card_summaries(stamp.date().isoformat(), summaries,
                        {m["id"]: m["version"] for m in memories})
                    check(bool(state["cards"]) and all(c["analysis_mode"] == "llm" for c in state["cards"])
                          and all(not m["reviewed"] for m in state["memories"]),
                          "Synthetic cards were not validated or semantic review was bypassed.")
                    report["deepseek"] = {
                        "status": "passed", "memory_count": len(memories), "card_count": len(state["cards"]),
                        "stages": [{"stage": s["stage"], "status": s["status"]} for s in pipeline["stages"]],
                        "source_contracts": "validated", "semantic_review": "still required",
                    }
                report.update(status="passed", step="completed")
    except (TranscriptionError, AgentError, VerificationError) as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error="Synthetic verification failed; check dependencies and provider configuration.")
        if isinstance(error, httpx.HTTPStatusError):
            report["http_status"] = error.response.status_code
    finally:
        # No raw SDK messages, response bodies, credentials, transcripts or memory text.
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        print(f"Report: {output.relative_to(ROOT)}", flush=True)
    return 0 if report["status"] in {"passed", "offline_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
