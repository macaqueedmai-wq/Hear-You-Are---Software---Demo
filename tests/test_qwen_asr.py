"""Offline Qwen contracts: fictional weights and injected models, no downloads."""
from __future__ import annotations

import copy
import json
import sys
import wave

import numpy as np
import pytest

from hya import qwen_asr as module
from hya.asr import TranscriptionError


def weights(path):
    path.mkdir()
    for filename in ("config.json", "processor_config.json", "tokenizer.json"):
        (path / filename).write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"fictional-test-weights")
    return path


def audio_file(path, seconds=2, channels=1, leading_silence=0):
    frames = np.full((int(seconds * 16000), channels), 4096, dtype=np.int16)
    frames[:int(leading_silence * 16000)] = 0
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(frames.tobytes())
    return path


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for name in list(module.os.environ):
        if name.startswith("HYA_QWEN_"):
            monkeypatch.delenv(name)
    model, aligner = weights(tmp_path / "model"), weights(tmp_path / "aligner")
    monkeypatch.setenv("HYA_QWEN_MODEL_PATH", str(model))
    monkeypatch.setenv("HYA_QWEN_ALIGNER_PATH", str(aligner))
    monkeypatch.setenv("HYA_QWEN_DEVICE", "cpu")
    monkeypatch.setenv("HYA_QWEN_DIARIZATION", "false")
    monkeypatch.setenv("HYA_QWEN_CHUNK_SECONDS", "1")
    monkeypatch.setattr(module, "_runtime_dependencies", lambda: True)
    monkeypatch.setattr(module, "_select_device", lambda requested: "cpu")
    return monkeypatch


class Models:
    def __init__(self, *, transcription=None, alignment=None, error_stage=None, close_error=None):
        self.events = []
        self.transcription = transcription
        self.alignment = alignment
        self.error_stage = error_stage
        self.close_error = close_error

    def __call__(self, stage, path, device, threads):
        self.events.append(("load", stage, path.name, device, threads))
        owner = self

        class Model:
            def transcribe(self, samples, language, max_new_tokens):
                owner.events.append(("transcribe", len(samples), language, max_new_tokens))
                if owner.error_stage == "asr":
                    raise RuntimeError("private transcript /private/path secret-key-must-not-leak")
                if owner.transcription is not None:
                    return copy.deepcopy(owner.transcription)
                return {"text": "Hello there.", "language": language, "truncated": False}

            def align(self, samples, transcript, language):
                owner.events.append(("align", len(samples), transcript, language))
                if owner.error_stage == "aligner":
                    raise RuntimeError("private transcript /private/path secret-key-must-not-leak")
                if owner.alignment is not None:
                    return copy.deepcopy(owner.alignment)
                return [{"text": "Hello", "start_time": 0.1, "end_time": 0.3},
                        {"text": "there", "start_time": 0.4, "end_time": 0.8}]

            def close(self):
                owner.events.append(("close", stage))
                if owner.close_error == stage:
                    raise OSError("private cleanup exception secret-key-must-not-leak")

        return Model()


def test_qwen_health_never_loads_models_imports_whisper_or_exposes_paths(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    models = Models()
    transcriber = module.QwenTranscriber(model_loader=models)
    health = transcriber.health()
    assert health["provider"] == "qwen" and health["configured"] and health["available"]
    assert health["models_cached"] and health["dependencies_available"]
    assert health["model"] == module.DEFAULT_MODEL and health["aligner_model"] == module.DEFAULT_ALIGNER
    assert health["loaded"] is False and health["hardware_checked"] is False
    assert health["local_only"] is True and health["external"] is False
    assert health["speaker_diarization"] is False and health["speaker_identification"] is False
    assert health["required_channels"] is None and health["fallback_reason"] is None
    assert health["max_upload_bytes"] == 100 * 1024 * 1024 and health["max_audio_seconds"] == 1200
    assert str(tmp_path) not in json.dumps(health)
    assert models.events == []


def test_qwen_two_passes_unload_asr_before_alignment_and_preserve_global_offsets(tmp_path):
    models = Models()
    path = audio_file(tmp_path / "stereo.wav", seconds=2, channels=2)
    original = path.read_bytes()
    transcriber = module.QwenTranscriber(model_loader=models)
    result = transcriber.transcribe(path)
    assert [event[0] for event in models.events] == ["load", "transcribe", "transcribe", "close", "load", "align", "align", "close"]
    assert models.events[0][1:] == ("asr", "model", "cpu", 4)
    assert models.events[4][1:] == ("aligner", "aligner", "cpu", 4)
    assert models.events[1] == ("transcribe", 16000, "English", 1024)
    assert result["segments"] == [
        {"start": 0.1, "end": 0.8, "text": "Hello there.", "speaker": "unknown",
         "words": [{"start": 0.1, "end": 0.3, "word": "Hello"}, {"start": 0.4, "end": 0.8, "word": "there"}]},
        {"start": 1.1, "end": 1.8, "text": "Hello there.", "speaker": "unknown",
         "words": [{"start": 1.1, "end": 1.3, "word": "Hello"}, {"start": 1.4, "end": 1.8, "word": "there"}]},
    ]
    assert result["language"] == "en" and result["duration"] == 2
    assert result["metrics"]["provider"] == "qwen" and result["metrics"]["speaker_diarization"] is False
    assert result["metrics"]["fallback_reason"] is None and result["metrics"]["models_from_cache"] is True
    assert transcriber.health()["loaded"] is False and transcriber.health()["hardware_checked"] is True
    assert path.read_bytes() == original


def test_qwen_silence_is_not_trimmed_from_original_timeline(tmp_path, monkeypatch):
    monkeypatch.setenv("HYA_QWEN_CHUNK_SECONDS", "2")
    models = Models(alignment=[{"text": "Hello", "start_time": 1.2, "end_time": 1.5},
                              {"text": "there", "start_time": 1.6, "end_time": 1.9}])
    path = audio_file(tmp_path / "leading-silence.wav", seconds=4, leading_silence=3)
    result = module.QwenTranscriber(model_loader=models).transcribe(path)
    assert result["segments"][0]["start"] == 3.2
    assert result["segments"][0]["words"][1]["end"] == 3.9
    assert result["duration"] == 4
    assert len([event for event in models.events if event[0] == "transcribe"]) == 1


def test_qwen_complete_digital_silence_needs_no_inference(tmp_path):
    models = Models()
    result = module.QwenTranscriber(model_loader=models).transcribe(audio_file(tmp_path / "silence.wav", leading_silence=2))
    assert result["segments"] == [] and result["duration"] == 2
    assert models.events == []


def test_qwen_auto_language_is_detected_and_passed_to_aligner_without_english_fallback(tmp_path):
    models = Models(transcription={"text": "你好。", "language": "Cantonese", "truncated": False},
                    alignment=[{"text": "你", "start_time": 0.1, "end_time": 0.4},
                               {"text": "好", "start_time": 0.5, "end_time": 0.8}])
    result = module.QwenTranscriber(model_loader=models).transcribe(audio_file(tmp_path / "audio.wav", seconds=1), "auto")
    assert models.events[1][2] is None
    assert models.events[4][-1] == "Cantonese"
    assert result["language"] == "yue"
    assert result["segments"][0]["text"] == "你好。"


@pytest.mark.parametrize("output,match", [
    ({"text": "Hello.", "language": "English", "truncated": True}, "output limit"),
    ({"text": "Hello.", "language": "Arabic", "truncated": False}, "not supported"),
    ({"text": "Hello.", "language": None, "truncated": False}, "did not identify"),
    ({"text": "Hello.", "language": {}, "truncated": False}, "not supported"),
    ({"text": 4, "language": "English", "truncated": False}, "invalid transcription"),
    ({"text": "Hello", "language": "English"}, "invalid transcription"),
])
def test_qwen_invalid_or_truncated_asr_is_not_aligned(tmp_path, output, match):
    models = Models(transcription=output)
    transcriber = module.QwenTranscriber(model_loader=models)
    with pytest.raises(TranscriptionError, match=match):
        transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1), "auto")
    assert models.events[-1] == ("close", "asr")
    assert not any(event[:2] == ("load", "aligner") for event in models.events)
    assert transcriber.health()["state"] == "error" and transcriber.health()["loaded"] is False


@pytest.mark.parametrize("values", [
    [], [{"text": "Hello", "start_time": -0.1, "end_time": 0.3}],
    [{"text": "Hello", "start_time": 0.5, "end_time": 0.3}],
    [{"text": "Hello", "start_time": 0.5, "end_time": 1.1}],
    [{"text": "Hello", "start_time": float("nan"), "end_time": 0.3}],
    [{"text": "Hello", "start_time": 0.1, "end_time": float("inf")}],
    [{"text": "Hello", "start_time": True, "end_time": 0.3}],
    [{"text": "Hello", "start_time": "0.1", "end_time": 0.3}],
    [{"text": "Hello", "start_time": 0.5, "end_time": 0.6}, {"text": "there", "start_time": 0.2, "end_time": 0.8}],
    [{"text": "different", "start_time": 0.1, "end_time": 0.8}],
    [{"text": "", "start_time": 0.1, "end_time": 0.8}],
    [{"text": "Hello"}], [None], None,
])
def test_qwen_rejects_invalid_alignment_without_invented_words_or_timing(tmp_path, values):
    models = Models()
    # None needs an explicit override because Models(None) requests its success default.
    original = models.__call__

    def loader(*args):
        model = original(*args)
        if args[0] == "aligner":
            model.align = lambda *unused: values
        return model

    transcriber = module.QwenTranscriber(model_loader=loader)
    with pytest.raises(TranscriptionError, match="alignment|align"):
        transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert models.events[-1] == ("close", "aligner")
    assert transcriber.health()["loaded"] is False


def test_qwen_alignment_accepts_official_punctuation_removal_and_no_identity(tmp_path):
    models = Models(transcription={"text": "I'm well-known.", "language": None, "truncated": False},
                    alignment=[{"text": "Im", "start_time": 0.1, "end_time": 0.3},
                               {"text": "wellknown", "start_time": 0.4, "end_time": 0.8}])
    result = module.QwenTranscriber(model_loader=models).transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert result["segments"][0]["text"] == "I'm well-known."
    assert result["segments"][0]["speaker"] == "unknown"
    assert "subject_id" not in result["segments"][0]


@pytest.mark.parametrize("stage", ["asr", "aligner"])
def test_qwen_errors_are_curated_and_each_failed_stage_unloads(tmp_path, stage):
    models = Models(error_stage=stage)
    transcriber = module.QwenTranscriber(model_loader=models)
    with pytest.raises(TranscriptionError) as caught:
        transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert models.events[-1] == ("close", stage)
    assert "secret-key" not in str(caught.value) and "private transcript" not in str(caught.value)
    assert "private/path" not in json.dumps(transcriber.health())
    assert transcriber.health()["loaded"] is False


def test_qwen_cleanup_failure_cannot_report_success(tmp_path):
    transcriber = module.QwenTranscriber(model_loader=Models(close_error="aligner"))
    with pytest.raises(TranscriptionError, match="failed"):
        transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert transcriber.health()["state"] == "error" and transcriber.health()["loaded"] is False


def test_qwen_missing_cached_model_or_dependencies_never_loads(tmp_path, monkeypatch):
    models = Models()
    monkeypatch.setenv("HYA_QWEN_ALIGNER_PATH", str(tmp_path / "missing"))
    transcriber = module.QwenTranscriber(model_loader=models)
    assert transcriber.health()["models_cached"] is False and transcriber.health()["available"] is False
    with pytest.raises(TranscriptionError, match="not cached"):
        transcriber.transcribe(tmp_path / "missing.wav")
    monkeypatch.setenv("HYA_QWEN_ALIGNER_PATH", str(tmp_path / "aligner"))
    monkeypatch.setattr(module, "_runtime_dependencies", lambda: False)
    transcriber = module.QwenTranscriber(model_loader=models)
    assert transcriber.health()["models_cached"] is True and transcriber.health()["configured"] is True
    with pytest.raises(TranscriptionError, match="runtime"):
        transcriber.transcribe(tmp_path / "missing.wav")
    assert models.events == []


def test_qwen_resolves_hf_cache_without_model_download_or_hub_import(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    for name, source, revision in ((module.DEFAULT_MODEL, "model", module.DEFAULT_MODEL_REVISION),
                                   (module.DEFAULT_ALIGNER, "aligner", module.DEFAULT_ALIGNER_REVISION)):
        repo = root / ("models--" + name.replace("/", "--"))
        (repo / "snapshots").mkdir(parents=True)
        # A pinned snapshot download need not create refs/main.
        (tmp_path / source).rename(repo / "snapshots" / revision)
    monkeypatch.setenv("HYA_MODEL_CACHE", str(root))
    monkeypatch.delenv("HYA_QWEN_MODEL_PATH")
    monkeypatch.delenv("HYA_QWEN_ALIGNER_PATH")
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert module.QwenTranscriber(model_loader=Models()).health()["models_cached"] is True


def test_qwen_pinned_cache_does_not_silently_use_another_main_revision(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    repo = root / ("models--" + module.DEFAULT_MODEL.replace("/", "--"))
    (repo / "snapshots").mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs/main").write_text("different-unrequested-revision", encoding="utf-8")
    (tmp_path / "model").rename(repo / "snapshots/different-unrequested-revision")
    monkeypatch.setenv("HYA_MODEL_CACHE", str(root))
    monkeypatch.delenv("HYA_QWEN_MODEL_PATH")
    assert not module.QwenTranscriber(model_loader=Models()).health()["models_cached"]
    monkeypatch.setenv("HYA_QWEN_MODEL_REVISION", "main")
    assert module.QwenTranscriber(model_loader=Models()).health()["models_cached"]


def test_qwen_cache_index_requires_every_local_shard(tmp_path):
    path = tmp_path / "model"
    (path / "model.safetensors").unlink()
    (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "part.safetensors"}}), encoding="utf-8")
    assert not module._complete_model(path)
    (path / "part.safetensors").write_bytes(b"fictional")
    assert module._complete_model(path)
    (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "../part.safetensors"}}), encoding="utf-8")
    assert not module._complete_model(path)


@pytest.mark.parametrize("name,value", [
    ("HYA_QWEN_MODEL", "https://external.example/model"), ("HYA_QWEN_DEVICE", "other"),
    ("HYA_QWEN_CHUNK_SECONDS", "nan"), ("HYA_QWEN_CHUNK_SECONDS", "301"),
    ("HYA_QWEN_MAX_NEW_TOKENS", "31"), ("HYA_QWEN_MAX_NEW_TOKENS", "1024.5"),
    ("HYA_QWEN_NUM_THREADS", "0"), ("HYA_QWEN_NUM_THREADS", "33"),
    ("HYA_QWEN_DIARIZATION", "maybe"), ("HYA_QWEN_MODEL_REVISION", "../outside"),
])
def test_qwen_invalid_settings_fail_before_loading(name, value, monkeypatch, tmp_path):
    monkeypatch.setenv(name, value)
    models = Models()
    transcriber = module.QwenTranscriber(model_loader=models)
    assert transcriber.health()["available"] is False
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(tmp_path / "unused.wav")
    assert models.events == []


def test_qwen_decoding_rejects_nonfinite_audio_before_loading(tmp_path):
    models = Models()
    transcriber = module.QwenTranscriber(model_loader=models, audio_loader=lambda path: np.full(16000, np.nan))
    with pytest.raises(TranscriptionError, match="decoded"):
        transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert models.events == []


def test_qwen_file_cap_precedes_decode_and_duration_cap_is_enforced(tmp_path, monkeypatch):
    path = tmp_path / "oversized.wav"
    path.write_bytes(b"too-big")
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", 2)
    transcriber = module.QwenTranscriber(model_loader=Models())
    with pytest.raises(TranscriptionError, match="100 MiB"):
        transcriber.inspect_audio(path)
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", 100 * 1024 * 1024)
    limits = []
    monkeypatch.setattr(module, "inspect_audio", lambda path, max_seconds: limits.append(max_seconds) or {})
    transcriber.inspect_audio(path, max_seconds=9999)
    assert limits == [1200]


@pytest.mark.parametrize("language", ["zh", "yue", "fr", None])
def test_qwen_unsupported_request_language_never_loads(language, tmp_path):
    models = Models()
    with pytest.raises(TranscriptionError, match="English or automatic"):
        module.QwenTranscriber(model_loader=models).transcribe(tmp_path / "unused.wav", language)
    assert models.events == []


class Diarizer:
    def __init__(self, turns=None, *, available=True):
        self.turns = turns if turns is not None else []
        self.available = available
        self.calls = []

    def health(self):
        return {"available": self.available, "error": None if self.available else "Speaker models are not cached."}

    def diarize(self, samples, sample_rate):
        self.calls.append((len(samples), sample_rate))
        return copy.deepcopy(self.turns)


def test_qwen_default_requires_diarization_and_missing_models_cannot_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("HYA_QWEN_DIARIZATION")
    models = Models()
    diarizer = Diarizer(available=False)
    transcriber = module.QwenTranscriber(model_loader=models, diarizer=diarizer)
    health = transcriber.health()
    assert health["speaker_diarization"] is True and health["speaker_identification"] is False
    assert health["speaker_label_scope"] == "recording" and health["available"] is False
    with pytest.raises(TranscriptionError, match="Speaker models"):
        transcriber.transcribe(tmp_path / "unused.wav")
    assert models.events == [] and diarizer.calls == []


def test_qwen_assigns_anonymous_word_speakers_and_splits_source_punctuation(tmp_path, monkeypatch):
    monkeypatch.setenv("HYA_QWEN_DIARIZATION", "true")
    models = Models(transcription={"text": "Hello, there.", "language": None, "truncated": False})
    diarizer = Diarizer([{"start": 0, "end": 0.35, "speaker": "speaker_0"},
                        {"start": 0.35, "end": 1, "speaker": "speaker_1"}])
    transcriber = module.QwenTranscriber(model_loader=models, diarizer=diarizer)
    result = transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert result["segments"] == [
        {"start": 0.1, "end": 0.3, "text": "Hello,", "speaker": "speaker_0",
         "words": [{"start": 0.1, "end": 0.3, "word": "Hello"}]},
        {"start": 0.4, "end": 0.8, "text": "there.", "speaker": "speaker_1",
         "words": [{"start": 0.4, "end": 0.8, "word": "there"}]},
    ]
    assert diarizer.calls == [(16000, 16000)]
    assert result["metrics"]["speaker_diarization"] is True
    assert result["metrics"]["speaker_identification"] is False
    assert result["metrics"]["fallback_reason"] is None


@pytest.mark.parametrize("turns", [
    [],
    [{"start": 0, "end": 1, "speaker": "speaker_0"}, {"start": 0, "end": 1, "speaker": "speaker_1"}],
    [{"start": 0.1, "end": 0.2, "speaker": "speaker_0"}, {"start": 0.2, "end": 0.3, "speaker": "speaker_1"}],
])
def test_qwen_overlapping_tied_or_unmatched_speakers_remain_unknown(tmp_path, monkeypatch, turns):
    monkeypatch.setenv("HYA_QWEN_DIARIZATION", "true")
    result = module.QwenTranscriber(model_loader=Models(), diarizer=Diarizer(turns)).transcribe(
        audio_file(tmp_path / "audio.wav", seconds=1))
    assert [segment["speaker"] for segment in result["segments"]] == ["unknown"]
    assert result["segments"][0]["text"] == "Hello there."


def test_qwen_uses_dominant_overlap_and_deduplicates_same_speaker_turns():
    word = {"start": 0, "end": 1}
    # Repeated same-speaker turns must not outweigh the longer other speaker.
    turns = [{"start": 0, "end": 0.4, "speaker": "speaker_0"},
             {"start": 0, "end": 0.4, "speaker": "speaker_0"},
             {"start": 0.4, "end": 1, "speaker": "speaker_1"}]
    assert module._word_speaker(word, turns) == "speaker_1"
    # Even a short concurrent second voice is ambiguous, rather than a name.
    assert module._word_speaker(word, [{"start": 0, "end": 1, "speaker": "speaker_0"},
                                      {"start": 0.5, "end": 0.6, "speaker": "speaker_1"}]) == "unknown"


@pytest.mark.parametrize("turns", [
    [{"start": -0.1, "end": 0.2, "speaker": "speaker_0"}],
    [{"start": 0, "end": 1.1, "speaker": "speaker_0"}],
    [{"start": float("nan"), "end": 0.2, "speaker": "speaker_0"}],
    [{"start": 0, "end": True, "speaker": "speaker_0"}],
    [{"start": 0, "end": 0.2, "speaker": "Named person"}],
    None, {}, [None],
])
def test_qwen_invalid_diarization_does_not_return_single_speaker_fallback(tmp_path, monkeypatch, turns):
    monkeypatch.setenv("HYA_QWEN_DIARIZATION", "true")
    diarizer = Diarizer()
    diarizer.diarize = lambda *args, **kwargs: turns
    transcriber = module.QwenTranscriber(model_loader=Models(), diarizer=diarizer)
    with pytest.raises(TranscriptionError, match="invalid speaker turns"):
        transcriber.transcribe(audio_file(tmp_path / "audio.wav", seconds=1))
    assert transcriber.health()["state"] == "error"
