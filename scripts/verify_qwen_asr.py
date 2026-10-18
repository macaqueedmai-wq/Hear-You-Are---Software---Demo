"""Verify local transcription, alignment and automatic speaker clustering.

python scripts/verify_qwen_asr.py             # Generate/check fixture only.
python scripts/verify_qwen_asr.py --run       # Run cached models, no cloud calls.

The fixture uses two Windows offline voices in A/B/A order. It checks a known
speaker change and speaker recurrence, not real conversation accuracy. Neither
application recordings nor DeepSeek are accessed by this script.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hya.asr import inspect_audio
from hya.engine import Engine
from hya.qwen_asr import QwenTranscriber

TURNS = (
    ("Microsoft David Desktop", "A", "Today I felt nervous before my presentation. I practiced in the library before meeting my friend."),
    ("Microsoft Zira Desktop", "B", "I listened to the presentation and felt proud of the progress. We can take a quiet walk after class."),
    ("Microsoft David Desktop", "A", "Thank you for listening to me today. Tomorrow I want to practice again and feel more confident."),
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def generate_fixture(path: Path) -> list[dict]:
    check(os.name == "nt", "This synthetic fixture requires Windows System.Speech.")
    script = """$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$hyaSpeech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $hyaSpeech.SelectVoice($env:HYA_VERIFY_VOICE)
  $hyaSpeech.Rate = 0
  $hyaFormat = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
  $hyaSpeech.SetOutputToWaveFile($env:HYA_VERIFY_WAV, $hyaFormat)
  $hyaSpeech.Speak($env:HYA_VERIFY_TEXT)
} finally { $hyaSpeech.Dispose() }
"""
    frames = bytearray(b"\0" * (16000 * 2 * 3))
    boundaries = []
    for index, (voice, speaker, text) in enumerate(TURNS):
        part = path.with_name(f"voice-{index}.wav")
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "HYA_VERIFY_VOICE": voice, "HYA_VERIFY_TEXT": text, "HYA_VERIFY_WAV": str(part)},
            check=True, capture_output=True, timeout=30,
        )
        with wave.open(str(part), "rb") as audio:
            check(audio.getnchannels() == 1 and audio.getframerate() == 16000 and audio.getsampwidth() == 2,
                  "The generated fixture must be mono 16 kHz PCM16.")
            values = audio.readframes(audio.getnframes())
        start = len(frames) / 32000
        frames.extend(values)
        boundaries.append({"voice": speaker, "start": start, "end": len(frames) / 32000})
        frames.extend(b"\0" * 24000)
        part.unlink()
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(frames)
    return boundaries


def wer(reference: str, hypothesis: str) -> float:
    expected, observed = (re.findall(r"[a-z0-9]+", value.lower()) for value in (reference, hypothesis))
    previous = list(range(len(observed) + 1))
    for index, word in enumerate(expected, 1):
        current = [index]
        for position, actual in enumerate(observed, 1):
            current.append(min(current[-1] + 1, previous[position] + 1, previous[position - 1] + (word != actual)))
        previous = current
    return previous[-1] / max(1, len(expected))


def speaker_metrics(segments: list[dict], boundaries: list[dict]) -> dict:
    dominant = []
    for turn in boundaries:
        durations = {}
        for segment in segments:
            overlap = max(0, min(segment["end"], turn["end"]) - max(segment["start"], turn["start"]))
            label = segment.get("speaker", "unknown")
            if overlap and label != "unknown":
                durations[label] = durations.get(label, 0) + overlap
        dominant.append(max(durations, key=durations.get) if durations else None)
    labels = {segment.get("speaker") for segment in segments} - {None, "unknown"}
    all_words = [(word, segment.get("speaker", "unknown"))
                 for segment in segments for word in segment.get("words", [])]
    word_seconds = sum(max(0, word["end"] - word["start"]) for word, _ in all_words)
    labeled_seconds = sum(max(0, word["end"] - word["start"]) for word, label in all_words if label != "unknown")
    return {"anonymous_label_count": len(labels), "turn_dominant_labels": dominant,
            "labeled_word_time_fraction": round(labeled_seconds / word_seconds, 4) if word_seconds else 0,
            "two_voices_distinguished": bool(dominant[0] and dominant[1] and dominant[0] != dominant[1]),
            "recurring_voice_consistent": bool(dominant[0] and dominant[0] == dominant[2]),
            "real_conversation_accuracy": "not measured; synthetic A/B/A voices only"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run the cached local Qwen and diarization models.")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = ROOT / "artifacts/private" / f"qwen-asr-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    fixture = output.with_suffix(".wav")
    report = {"checked_at_utc": stamp, "status": "failed", "step": "fixture", "asr_called": False,
              "application_data_used": False, "external_asr_called": False, "deepseek_called": False,
              "input": "two offline synthetic English voices in A/B/A order"}
    try:
        boundaries = generate_fixture(fixture)
        audio = inspect_audio(fixture)
        report.update(audio=audio, expected_turns=boundaries, fixture_path=str(fixture.relative_to(ROOT)))
        if not args.run:
            report.update(status="fixture_passed", step="completed")
        else:
            load_dotenv(ROOT / ".env")
            report["step"] = "local_asr"
            transcriber = QwenTranscriber()
            report["health_before"] = transcriber.health()
            check(transcriber.available, transcriber.error or "The local Qwen runtime is unavailable.")
            check(transcriber.diarization_enabled, "Speaker diarization is required for this verification.")
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            print("Running cached local ASR, alignment and speaker diarization on synthetic audio...", flush=True)
            start = time.monotonic()
            report["asr_called"] = True
            result = transcriber.transcribe(fixture, language="en")
            elapsed = time.monotonic() - start
            segments = result["segments"]
            check(bool(segments), "No transcript was returned for the synthetic voices.")
            words = [word for segment in segments for word in segment.get("words", [])]
            for segment in segments:
                check(0 <= segment["start"] <= segment["end"] <= audio["duration"] + .001,
                      "A segment falls outside the original recording timeline.")
            check(all(math.isfinite(word["start"]) and 0 <= word["start"] <= word["end"] <= audio["duration"] + .001
                      for word in words), "A word timestamp falls outside the recording.")
            speakers = speaker_metrics(segments, boundaries)
            error_rate = wer(" ".join(turn[2] for turn in TURNS), " ".join(part["text"] for part in segments))
            report.update(metrics={**result["metrics"], **speakers, "elapsed_seconds": round(elapsed, 3),
                "real_time_factor": round(elapsed / audio["duration"], 3), "word_error_rate": round(error_rate, 4),
                "segment_count": len(segments), "word_count": len(words),
                "peak_cuda_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1) if torch.cuda.is_available() else None},
                segments=segments)
            check(speakers["two_voices_distinguished"] and speakers["recurring_voice_consistent"]
                  and speakers["labeled_word_time_fraction"] >= .8,
                  "Synthetic speaker-change or returning-speaker clustering did not pass; inspect the report.")
            check(error_rate <= .2, "Synthetic transcription word error rate exceeds 20 percent.")
            report["step"] = "source_storage"
            with tempfile.TemporaryDirectory(prefix="hya-qwen-verification-") as folder:
                engine = Engine(Path(folder), model_mode=True)
                recording = engine.create_recording("synthetic-speakers.wav", fixture,
                    hashlib.sha256(fixture.read_bytes()).hexdigest(), recorded_at=None)
                engine.ingest_segments(recording["id"], segments, language="en", extract=False)
                stored = engine.get_recording(recording["id"])["segments"]
                check(len(stored) == len(segments) and all(
                    item["subject_id"] == "unknown" and not item["confirmed"]
                    and item["speaker_label"] == raw["speaker"] and item["text"] == raw["text"]
                    and item["words"] == raw["words"] and item["start"] == raw["start"] and item["end"] == raw["end"]
                    for item, raw in zip(stored, segments)), "Source storage changed timing, speaker labels or unknown identity.")
            report.update(status="passed", step="completed", source_storage="unaltered evidence; all identities unknown and unconfirmed")
    except Exception as error:
        report["status"] = "failed"
        # Model errors are already curated by QwenTranscriber. Fixture errors
        # use fixed diagnostics rather than subprocess output or model paths.
        report["error"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output.relative_to(ROOT))}), flush=True)
    return 0 if report["status"] in {"passed", "fixture_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
