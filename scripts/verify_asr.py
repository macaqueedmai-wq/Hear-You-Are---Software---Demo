"""Run real local ASR against a reproducible synthetic English recording.

Windows: python scripts/verify_asr.py --generate --runs 2
Any OS: python scripts/verify_asr.py --audio example.wav --reference-file text.txt
This is a smoke test, not an accent/noise accuracy benchmark.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from hya.asr import Transcriber, inspect_audio

REFERENCE = (
    "Today I felt nervous before my presentation. "
    "I talked with my friend Sarah after class, and our conversation helped me feel better. "
    "Tomorrow I need to review my slides at nine in the morning. "
    "Please remind me to prepare before the meeting."
)


def word_error_rate(reference: str, hypothesis: str) -> float:
    normalize = lambda text: re.findall(r"[a-z0-9]+", text.lower())
    expected, actual = normalize(reference), normalize(hypothesis)
    previous = list(range(len(actual) + 1))
    for index, word in enumerate(expected, start=1):
        current = [index]
        for position, observed in enumerate(actual, start=1):
            current.append(min(
                current[-1] + 1,
                previous[position] + 1,
                previous[position - 1] + (word != observed),
            ))
        previous = current
    return previous[-1] / max(1, len(expected))


def generate_english_wav(path: Path) -> str:
    if os.name != "nt":
        raise RuntimeError("Synthetic speech generation uses Windows System.Speech; pass --audio on other systems.")
    path.parent.mkdir(parents=True, exist_ok=True)
    speech_path = path.with_name(path.stem + "-speech.wav")
    # Environment variables avoid interpolating filesystem paths into shell code.
    script = (
        "Add-Type -AssemblyName System.Speech\n"
        "$hyaSpeech = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$hyaVoice = $hyaSpeech.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'en-US' } | Select-Object -First 1\n"
        "if (-not $hyaVoice) { throw 'An en-US System.Speech voice is required' }\n"
        "$hyaSpeech.SelectVoice($hyaVoice.VoiceInfo.Name)\n"
        "$hyaSpeech.Rate = 0\n"
        "$hyaSpeech.SetOutputToWaveFile($env:HYA_VERIFY_WAV)\n"
        "$hyaSpeech.Speak($env:HYA_VERIFY_TEXT)\n"
        "$hyaSpeech.Dispose()\n"
        "Write-Output $hyaVoice.VoiceInfo.Name\n"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "HYA_VERIFY_WAV": str(speech_path), "HYA_VERIFY_TEXT": REFERENCE},
        capture_output=True, text=True, check=True, timeout=30,
    )
    # Deliberate leading silence verifies that VAD timestamps still point into
    # the original audio rather than the shortened voiced-only audio.
    with wave.open(str(speech_path), "rb") as source:
        params = source.getparams()
        frames = source.readframes(source.getnframes())
    with wave.open(str(path), "wb") as output:
        output.setparams(params)
        output.writeframes(b"\0" * (params.framerate * params.nchannels * params.sampwidth * 3))
        output.writeframes(frames)
    speech_path.unlink()
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--reference-file", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "verification" / "asr-results.json")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least one")
    audio_path = args.audio or ROOT / "data" / "verification" / "synthetic-english.wav"
    voice = generate_english_wav(audio_path) if args.generate else None
    if not audio_path.is_file():
        parser.error("Pass --generate on Windows or supply --audio")
    reference = args.reference_file.read_text(encoding="utf-8").strip() if args.reference_file else REFERENCE if args.generate else None
    transcriber = Transcriber(device=args.device, model_dir=args.model_dir)
    report = {
        "fixture": "synthetic English, three seconds leading silence" if args.generate else "user-supplied local verification fixture",
        "voice": voice,
        "reference": reference,
        "audio": inspect_audio(audio_path),
        "runtime": {package: importlib.metadata.version(package) for package in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface-hub")},
        "health_before": transcriber.health(),
        "runs": [],
    }
    print(json.dumps({"health": report["health_before"], "audio": report["audio"]}, ensure_ascii=False), flush=True)
    for index in range(args.runs):
        result = transcriber.transcribe(audio_path, language="en")
        text = " ".join(segment["text"] for segment in result["segments"])
        result["transcript"] = text
        result["run"] = index + 1
        result["word_error_rate"] = round(word_error_rate(reference, text), 6) if reference else None
        result["original_offsets_preserved"] = (
            bool(result["segments"]) and result["segments"][0]["start"] >= 2.5
        ) if args.generate else None
        report["runs"].append(result)
        print(json.dumps({"run": index + 1, "metrics": result["metrics"], "wer": result["word_error_rate"], "transcript": text}, ensure_ascii=False), flush=True)
    report["health_after"] = transcriber.health()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Local verification saved; audio/model files must remain git-ignored.", flush=True)


if __name__ == "__main__":
    main()
