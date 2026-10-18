"""Offline SenseVoice/VAD/diarization contracts; never use real model weights."""
from __future__ import annotations

import copy
import json
import os
import sys
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from hya import sensevoice_asr as module
from hya.asr import TranscriptionError
from hya.diarization import DiarizationError


RATE = 16000
PRIVATE = "private-transcript /private/model secret-must-not-leak"


def audio_file(path, *, seconds=2, channels=1, leading_silence=0):
    samples = np.full((round(seconds * RATE), channels), 4096, dtype=np.int16)
    samples[:round(leading_silence * RATE)] = 0
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples.tobytes())
    return path


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("HYA_SENSEVOICE_"):
            monkeypatch.delenv(name)
    directory = tmp_path / "sensevoice"
    directory.mkdir()
    for name, filename in (("MODEL", "model.int8.onnx"), ("TOKENS", "tokens.txt"), ("VAD", "silero_vad.onnx")):
        path = directory / filename
        path.write_bytes(b"fictional-offline-test-file")
        variable = "HYA_SILERO_VAD_MODEL_PATH" if name == "VAD" else f"HYA_SENSEVOICE_{name}_PATH"
        monkeypatch.setenv(variable, str(path))
    monkeypatch.setenv("HYA_SENSEVOICE_NUM_THREADS", "4")
    monkeypatch.setenv("HYA_SENSEVOICE_CHUNK_SECONDS", "30")
    monkeypatch.setattr(module, "_dependencies_available", lambda: True)
    return monkeypatch


class Models:
    def __init__(self, *, windows=None, output=None, error_stage=None, close_error=None):
        self.windows, self.output = windows, output
        self.error_stage, self.close_error = error_stage, close_error
        self.calls, self.events, self.backends = [], [], []

    def recognizer_factory(self, **kwargs):
        self.calls.append(("recognizer", kwargs))
        owner = self

        class Recognizer:
            closed = False

            def transcribe(self, samples):
                owner.events.append(("transcribe", samples.copy()))
                if owner.error_stage == "recognizer":
                    raise RuntimeError(PRIVATE)
                if owner.output is not None:
                    return copy.deepcopy(owner.output)
                duration = len(samples) / RATE
                return {
                    "text": "The 50 cats.", "language": "<|en|>",
                    "tokens": ["The", " ", "5", "0", " cats", "."],
                    "timestamps": [duration * fraction for fraction in (0.1, 0.2, 0.3, 0.4, 0.6, 0.8)],
                }

            def close(self):
                self.closed = True
                owner.events.append(("close", "recognizer"))
                if owner.close_error == "recognizer":
                    raise OSError(PRIVATE)

        backend = Recognizer()
        self.backends.append(backend)
        return backend

    def vad_factory(self, **kwargs):
        self.calls.append(("vad", kwargs))
        owner = self

        class Vad:
            closed = False

            def speech_windows(self, samples):
                owner.events.append(("vad", samples.copy()))
                if owner.error_stage == "vad":
                    raise RuntimeError(PRIVATE)
                if owner.windows is not None:
                    return copy.deepcopy(owner.windows)
                return [{"start_sample": 0, "end_sample": len(samples)}]

            def close(self):
                self.closed = True
                owner.events.append(("close", "vad"))
                if owner.close_error == "vad":
                    raise OSError(PRIVATE)

        backend = Vad()
        self.backends.append(backend)
        return backend


class Diarizer:
    def __init__(self, intervals=None, *, available=True, error=None):
        self.intervals, self.available, self.error = intervals, available, error
        self.calls = []

    def health(self):
        return {"available": self.available, "configured": self.available,
                "error": None if self.available else "Local diarization models are unavailable.",
                "speaker_diarization": True, "speaker_identification": False,
                "speaker_label_scope": "recording"}

    def diarize(self, samples, sample_rate=RATE):
        self.calls.append((samples.copy(), sample_rate))
        if self.error:
            raise self.error
        if self.intervals is not None:
            return copy.deepcopy(self.intervals)
        return [{"start": 0.0, "end": len(samples) / sample_rate, "speaker": "speaker_0"}]


def adapter(models=None, diarizer=None, **kwargs):
    models = models or Models()
    return module.SenseVoiceTranscriber(
        recognizer_factory=models.recognizer_factory, vad_factory=models.vad_factory,
        diarizer=diarizer if diarizer is not None else Diarizer(), **kwargs,
    )


def boundaries(result):
    return [(segment["start"], segment["end"], segment["speaker"]) for segment in result["segments"]]


def test_health_is_local_lazy_and_does_not_import_torch_or_disclose_paths(tmp_path, monkeypatch):
    models = Models()
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    transcriber = adapter(models)
    health = transcriber.health()
    assert health["available"] and health["configured"]
    assert health["provider"] == "sensevoice"
    assert health["local_only"] is True and health["external"] is False
    assert health["speaker_diarization"] is True
    assert health["speaker_identification"] is False
    assert health["speaker_label_scope"] == "recording"
    assert str(tmp_path) not in json.dumps(health)
    assert models.calls == [] and models.events == []


def test_vad_and_speaker_intervals_preserve_three_seconds_of_global_silence(tmp_path):
    models = Models(windows=[{"start_sample": 3 * RATE, "end_sample": 5 * RATE}])
    diarizer = Diarizer([
        {"start": 3.0, "end": 4.0, "speaker": "speaker_0"},
        {"start": 4.0, "end": 4.5, "speaker": "speaker_1"},
        {"start": 4.5, "end": 5.0, "speaker": "speaker_0"},
    ])
    path = audio_file(tmp_path / "stereo.wav", seconds=6, channels=2, leading_silence=3)
    original = path.read_bytes()
    result = adapter(models, diarizer).transcribe(path)
    assert boundaries(result) == [(3.0, 4.0, "speaker_0"), (4.0, 4.5, "speaker_1"), (4.5, 5.0, "speaker_0")]
    assert result["duration"] == 6 and result["language"] == "en"
    assert [len(event[1]) for event in models.events if event[0] == "transcribe"] == [RATE, RATE // 2, RATE // 2]
    assert all(segment["words"] == [] for segment in result["segments"])
    assert diarizer.calls[0][1] == RATE and len(diarizer.calls[0][0]) == 6 * RATE
    assert diarizer.calls[0][0].ndim == 1
    assert path.read_bytes() == original
    assert all(backend.closed for backend in models.backends)


@pytest.mark.parametrize("intervals,expected", [
    ([{"start": 0.0, "end": 2.0, "speaker": "speaker_0"},
      {"start": 0.5, "end": 1.5, "speaker": "speaker_1"}],
     [(0.0, 0.5, "speaker_0"), (0.5, 1.5, "unknown"), (1.5, 2.0, "speaker_0")]),
    ([{"start": 0.5, "end": 1.5, "speaker": "speaker_4"}],
     [(0.0, 0.5, "unknown"), (0.5, 1.5, "speaker_4"), (1.5, 2.0, "unknown")]),
    ([{"start": 0.0, "end": 2.0, "speaker": "speaker_0"},
      {"start": 0.0, "end": 2.0, "speaker": "speaker_1"}], [(0.0, 2.0, "unknown")]),
    ([], [(0.0, 2.0, "unknown")]),
])
def test_only_one_unambiguous_active_speaker_is_assigned(tmp_path, intervals, expected):
    result = adapter(diarizer=Diarizer(intervals)).transcribe(audio_file(tmp_path / "audio.wav"))
    assert boundaries(result) == expected


def test_each_asr_input_is_bounded_and_final_partial_chunk_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("HYA_SENSEVOICE_CHUNK_SECONDS", "2")
    models = Models()
    result = adapter(models).transcribe(audio_file(tmp_path / "audio.wav", seconds=5.3))
    lengths = [len(event[1]) for event in models.events if event[0] == "transcribe"]
    assert lengths == [2 * RATE, 2 * RATE, round(1.3 * RATE)]
    assert boundaries(result) == [(0.0, 2.0, "speaker_0"), (2.0, 4.0, "speaker_0"), (4.0, 5.3, "speaker_0")]
    assert all(length <= 2 * RATE for length in lengths)


def test_native_itn_text_and_first_four_content_tokens_are_preserved(tmp_path):
    text = "These are four ordinary words: 50 cats."
    tokens = ["These", " are", " four", " ordinary", " words", ":", " ", "5", "0", " cats", "."]
    output = {"text": text, "language": "<|en|>", "tokens": tokens,
              "timestamps": [0.1 + 0.1 * index for index in range(len(tokens))]}
    result = adapter(Models(output=output)).transcribe(audio_file(tmp_path / "audio.wav"))
    assert result["segments"][0]["text"] == text
    assert result["segments"][0]["words"] == []


def test_decoded_text_remains_authoritative_without_claiming_token_text_alignment(tmp_path):
    output = {"text": "Different normalized text.", "language": "<|en|>",
              "tokens": ["original", " tokens"], "timestamps": [0.1, 0.5]}
    result = adapter(Models(output=output)).transcribe(audio_file(tmp_path / "audio.wav"))
    assert result["segments"][0]["text"] == output["text"]
    assert result["segments"][0]["words"] == []


@pytest.mark.parametrize("requested,detected,expected", [("en", "<|en|>", "en"), ("auto", "<|yue|>", "yue")])
def test_language_hint_is_passed_without_silent_model_fallback(tmp_path, requested, detected, expected):
    output = {"text": "Hello.", "language": detected, "tokens": ["Hello", "."], "timestamps": [0.1, 0.5]}
    models = Models(output=output)
    result = adapter(models).transcribe(audio_file(tmp_path / "audio.wav"), language=requested)
    kwargs = next(kwargs for stage, kwargs in models.calls if stage == "recognizer")
    assert kwargs["language"] == requested and kwargs["num_threads"] == 4
    assert result["language"] == expected
    assert result["metrics"]["provider"] == "sensevoice"
    assert result["metrics"].get("fallback_reason") is None


def test_absent_detected_language_retains_requested_auto(tmp_path):
    output = {"text": "Hello.", "language": None, "tokens": ["Hello", "."], "timestamps": [0.1, 0.5]}
    result = adapter(Models(output=output)).transcribe(audio_file(tmp_path / "audio.wav"), language="auto")
    assert result["language"] == "auto"


def test_empty_output_for_detected_long_speech_does_not_masquerade_as_silence(tmp_path):
    models = Models(output={"text": "", "language": None, "tokens": [], "timestamps": []})
    with pytest.raises(TranscriptionError):
        adapter(models).transcribe(audio_file(tmp_path / "audio.wav"))
    assert all(backend.closed for backend in models.backends)


@pytest.mark.parametrize("change", [
    {"timestamps": [float("nan"), 0.5]}, {"timestamps": [0.1, float("inf")]},
    {"timestamps": [-0.1, 0.5]}, {"timestamps": [0.1, 2.1]},
    {"timestamps": [0.6, 0.1]}, {"timestamps": [True, 0.5]},
    {"timestamps": ["0.1", 0.5]}, {"timestamps": [0.1]},
    {"timestamps": None}, {"tokens": ["Hello", 3]}, {"tokens": []},
    {"text": 3}, {"language": {}},
])
def test_malformed_asr_contract_fails_closed_with_curated_error(tmp_path, change):
    output = {"text": "Hello.", "language": "<|en|>", "tokens": ["Hello", "."], "timestamps": [0.1, 0.5]}
    output.update(change)
    models = Models(output=output)
    transcriber = adapter(models)
    with pytest.raises(TranscriptionError) as error:
        transcriber.transcribe(audio_file(tmp_path / "audio.wav"))
    assert PRIVATE not in str(error.value)
    assert all(backend.closed for backend in models.backends)
    assert transcriber.health().get("loaded", False) is False


@pytest.mark.parametrize("windows", [
    [{"start_sample": -1, "end_sample": RATE}],
    [{"start_sample": 0, "end_sample": 2 * RATE + 1}],
    [{"start_sample": RATE, "end_sample": 0}],
    [{"start_sample": True, "end_sample": RATE}],
    [{"start_sample": 0.0, "end_sample": RATE}],
    [{"start_sample": 0, "end_sample": 0}],
    [{"start_sample": 0}], None,
])
def test_invalid_vad_source_boundaries_do_not_trigger_asr(tmp_path, windows):
    models = Models(windows=windows)
    if windows is None:
        original = models.vad_factory
        def bad_factory(**kwargs):
            backend = original(**kwargs)
            backend.speech_windows = lambda samples: None
            return backend
        models.vad_factory = bad_factory
    with pytest.raises(TranscriptionError):
        adapter(models).transcribe(audio_file(tmp_path / "audio.wav"))
    assert not any(event[0] == "transcribe" for event in models.events)
    assert all(backend.closed for backend in models.backends)


@pytest.mark.parametrize("stage", ["recognizer", "vad", "diarizer"])
def test_runtime_failure_is_curated_and_all_created_models_close(tmp_path, stage):
    models = Models(error_stage=stage)
    diarizer = Diarizer(error=DiarizationError("Local speaker diarization failed.") if stage == "diarizer" else None)
    transcriber = adapter(models, diarizer)
    with pytest.raises(TranscriptionError) as error:
        transcriber.transcribe(audio_file(tmp_path / "audio.wav"))
    assert PRIVATE not in str(error.value)
    assert PRIVATE not in json.dumps(transcriber.health())
    assert all(backend.closed for backend in models.backends)
    assert transcriber.health().get("loaded", False) is False


@pytest.mark.parametrize("stage", ["recognizer", "vad"])
def test_cleanup_failure_prevents_success_and_is_curated(tmp_path, stage):
    models = Models(close_error=stage)
    with pytest.raises(TranscriptionError) as error:
        adapter(models).transcribe(audio_file(tmp_path / "audio.wav"))
    assert PRIVATE not in str(error.value)
    assert all(backend.closed for backend in models.backends)


@pytest.mark.parametrize("missing", ["MODEL", "TOKENS", "VAD", "dependencies", "diarizer"])
def test_incomplete_runtime_never_falls_back_or_loads_models(tmp_path, monkeypatch, missing):
    if missing in {"MODEL", "TOKENS", "VAD"}:
        variable = "HYA_SILERO_VAD_MODEL_PATH" if missing == "VAD" else f"HYA_SENSEVOICE_{missing}_PATH"
        monkeypatch.setenv(variable, str(tmp_path / "missing-file"))
    elif missing == "dependencies":
        monkeypatch.setattr(module, "_dependencies_available", lambda: False)
    models = Models()
    transcriber = adapter(models, Diarizer(available=missing != "diarizer"))
    assert transcriber.health()["available"] is False
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(audio_file(tmp_path / "audio.wav"))
    assert models.calls == []


@pytest.mark.parametrize("name,value", [("NUM_THREADS", "0"), ("NUM_THREADS", "1.5"),
                                       ("NUM_THREADS", "nan"), ("CHUNK_SECONDS", "0"),
                                       ("CHUNK_SECONDS", "31"), ("CHUNK_SECONDS", "inf")])
def test_unbounded_or_invalid_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(f"HYA_SENSEVOICE_{name}", value)
    transcriber = adapter()
    assert transcriber.health()["available"] is False


def test_unsupported_language_rejected_before_any_model_load(tmp_path):
    models = Models()
    with pytest.raises(TranscriptionError):
        adapter(models).transcribe(audio_file(tmp_path / "audio.wav"), language="unsupported")
    assert models.calls == []


def test_tiny_empty_detected_span_can_be_skipped_only_when_other_speech_succeeds(tmp_path):
    models = Models(windows=[{"start_sample": 0, "end_sample": RATE // 5},
                            {"start_sample": RATE // 2, "end_sample": 3 * RATE // 2}])
    original_factory = models.recognizer_factory
    def factory(**kwargs):
        backend = original_factory(**kwargs)
        original_transcribe = backend.transcribe
        def transcribe(samples):
            if len(samples) < RATE // 2:
                return {"text": "", "language": None, "tokens": [], "timestamps": []}
            return original_transcribe(samples)
        backend.transcribe = transcribe
        return backend
    models.recognizer_factory = factory
    result = adapter(models).transcribe(audio_file(tmp_path / "audio.wav"))
    assert boundaries(result) == [(0.5, 1.5, "speaker_0")]
    assert result["metrics"]["empty_speech_span_count"] == 1


def test_native_vad_submits_final_partial_window_before_flush_and_bounds_padding(monkeypatch, tmp_path):
    accepted = []
    native = SimpleNamespace(queue=[], flushed=False)
    native.accept_waveform = lambda samples: accepted.append(samples.copy())
    native.empty = lambda: not native.queue
    def flush():
        native.flushed = True
        native.queue.append(SimpleNamespace(start=500, samples=np.zeros(200, dtype=np.float32)))
    native.flush = flush
    native.pop = lambda: native.queue.pop(0)
    class Detector:
        def __init__(self, config, buffer_size_in_seconds):
            assert config.provider == "cpu"
            assert config.sample_rate == RATE
            assert buffer_size_in_seconds >= 1200
        def accept_waveform(self, samples):
            native.accept_waveform(samples)
        def empty(self):
            return native.empty()
        @property
        def front(self):
            return native.queue[0]
        def pop(self):
            native.pop()
        def flush(self):
            native.flush()
    fake = SimpleNamespace(
        VadModelConfig=lambda: SimpleNamespace(silero_vad=SimpleNamespace(window_size=512)),
        VoiceActivityDetector=Detector,
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    vad = module._NativeVad(model_path=tmp_path / "vad.onnx", num_threads=2, max_speech_duration=30)
    samples = np.arange(513, dtype=np.float32) / 513
    assert vad.speech_windows(samples) == [{"start_sample": 500, "end_sample": 513}]
    assert [len(part) for part in accepted] == [512, 1]
    assert np.array_equal(np.concatenate(accepted), samples)
    assert native.flushed
    vad.close()
    assert vad.vad is None


@pytest.mark.parametrize("language", ["en", "auto"])
def test_native_recognizer_uses_cpu_itn_and_keeps_already_processed_tokens(monkeypatch, tmp_path, language):
    calls = []
    expected = {"text": "one two three four five", "language": "<|en|>",
                "tokens": ["one", " two", " three", " four", " five"],
                "timestamps": [0.1, 0.2, 0.3, 0.4, 0.5]}
    stream = SimpleNamespace(
        result=SimpleNamespace(text=expected["text"], lang=expected["language"],
                               tokens=expected["tokens"], timestamps=expected["timestamps"]),
        accept_waveform=lambda rate, samples: calls.append(("waveform", rate, len(samples))),
    )
    native = SimpleNamespace(create_stream=lambda: stream,
                             decode_stream=lambda value: calls.append(("decode", value)))
    def from_sense_voice(**kwargs):
        calls.append(("config", kwargs))
        return native
    fake = SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_sense_voice=from_sense_voice))
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    recognizer = module._NativeRecognizer(model_path=tmp_path / "model.onnx", tokens_path=tmp_path / "tokens.txt",
                                          num_threads=4, language=language)
    assert recognizer.transcribe(np.zeros(RATE, dtype=np.float32)) == expected
    kwargs = calls[0][1]
    assert kwargs["provider"] == "cpu" and kwargs["use_itn"] is True
    assert kwargs["language"] == language and kwargs["num_threads"] == 4
    assert calls[1] == ("waveform", RATE, RATE)
    assert calls[2][0] == "decode"
    recognizer.close()
    assert recognizer.recognizer is None
