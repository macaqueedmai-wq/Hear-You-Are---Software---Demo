from __future__ import annotations

import sys
from types import SimpleNamespace
import wave

import pytest

from hya import asr


def make_wave(path, seconds=1.0):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * int(16000 * 2 * seconds))
    return path


def test_audio_validation_decodes_without_whisper_loading(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    result = asr.inspect_audio(make_wave(tmp_path / "audio.wav", 2.5))
    assert result["duration"] == pytest.approx(2.5)
    assert result["sample_rate"] == 16000
    assert result["channels"] == 1


def test_corrupt_or_missing_audio_rejected_before_model(tmp_path):
    invalid = tmp_path / "not-real.mp3"
    invalid.write_text("This is not an audio stream.")
    with pytest.raises(asr.TranscriptionError, match="無法解碼"):
        asr.inspect_audio(invalid)
    with pytest.raises(asr.TranscriptionError, match="找不到"):
        asr.inspect_audio(tmp_path / "missing.wav")


def test_duration_limit_rejects_decoded_audio(tmp_path):
    with pytest.raises(asr.TranscriptionError, match="超過"):
        asr.inspect_audio(make_wave(tmp_path / "too-long.wav", 3), max_seconds=2)


def test_transcription_preserves_original_time_and_unconfirmed_identity(tmp_path, monkeypatch):
    calls = []
    word = SimpleNamespace(start=3.2, end=3.5, word="Hello", probability=0.95)
    segment = SimpleNamespace(start=3.2, end=4.1, text=" Hello there. ", words=[word])

    class FakeModel:
        def __init__(self, source, **kwargs):
            calls.append((source, kwargs))

        def transcribe(self, path, **kwargs):
            assert kwargs["vad_filter"] is True
            assert kwargs["word_timestamps"] is True
            assert kwargs["language"] == "en"
            return iter([segment]), SimpleNamespace(duration=5, duration_after_vad=1, language="en", language_probability=1)

    def no_runtime(self):
        self.available, self.device, self.compute_type = True, "cpu", "int8"

    monkeypatch.setattr(asr.Transcriber, "_prepare_runtime", no_runtime)
    monkeypatch.setattr(asr.importlib.util, "find_spec", lambda name: True)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_MODEL", None)
    monkeypatch.setattr(asr, "_MODEL_KEY", None)
    transcriber = asr.Transcriber()
    assert transcriber.health()["loaded"] is False
    assert calls == []  # health must never download weights.
    audio = make_wave(tmp_path / "audio.wav", 5)
    first = transcriber.transcribe(audio)
    second = transcriber.transcribe(audio)
    assert len(calls) == 1  # cached model reused for next recording.
    assert calls[0][0] == "large-v3-turbo"
    assert first["segments"][0]["start"] == 3.2
    assert first["segments"][0]["speaker"] == "unknown"
    assert first["segments"][0]["words"][0]["start"] == 3.2
    assert second["metrics"]["model_load_seconds"] == 0
    assert transcriber.health()["state"] == "ready"


def test_failed_model_load_has_truthful_error_and_no_model_substitution(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.Transcriber, "_prepare_runtime", lambda self: None)
    monkeypatch.setattr(asr, "_MODEL", None)
    monkeypatch.setattr(asr, "_MODEL_KEY", None)
    transcriber = asr.Transcriber(model_dir=tmp_path / "missing-model")
    transcriber.available = True
    with pytest.raises(asr.TranscriptionError, match="model.bin"):
        transcriber.transcribe(make_wave(tmp_path / "valid.wav"))
    assert transcriber.health()["state"] == "error"
    assert transcriber.health()["model"] == "large-v3-turbo"
    assert transcriber.health()["loaded"] is False
