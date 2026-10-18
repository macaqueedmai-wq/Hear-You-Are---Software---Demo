"""CPU SenseVoiceSmall INT8 with mandatory local VAD and speaker diarization.

Only local model files are read. Segment times are selected audio sample-window
boundaries from Silero VAD, speaker turns and bounded inference slices. Native
SenseVoice CTC timestamps identify token starts, not word ends: ``words=[]`` is
deliberate. Native decoded text/ITN is preserved without guessing word timing.

https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/python-api-examples/generate-subtitles.py
"""
from __future__ import annotations

import gc
import importlib.util
import math
from numbers import Integral, Real
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from .asr import TranscriptionError, inspect_audio
from .diarization import DiarizationError, LocalDiarizer
from .qwen_asr import _decode_audio  # PyAV/NumPy only; never calls Qwen or imports Torch.

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_RATE = 16000
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_AUDIO_SECONDS = 20 * 60
MAX_SEGMENTS = 10000
MAX_TOKENS = 25000
MODEL_NAME = "SenseVoiceSmall INT8 (2024-07-17)"
_JOB_LOCK = threading.RLock()
_CODES = {"en", "zh", "yue", "ja", "ko"}
_INVALID_RESULT = "SenseVoice returned invalid transcription details; no transcript was stored."


def _dependencies_available() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("numpy", "av", "sherpa_onnx"))
    except (ImportError, ValueError):
        return False


def _model_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _setting(name: str, default: float, minimum: float, maximum: float, *, integer: bool = False):
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.") from None
    if not math.isfinite(value) or not minimum <= value <= maximum or integer and not value.is_integer():
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
    return int(value) if integer else value


class _NativeRecognizer:
    def __init__(self, *, model_path: Path, tokens_path: Path, num_threads: int, language: str):
        import sherpa_onnx
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_path), tokens=str(tokens_path), num_threads=num_threads,
            sample_rate=SAMPLE_RATE, feature_dim=80, decoding_method="greedy_search",
            debug=False, provider="cpu", language=language, use_itn=True,
        )

    def transcribe(self, samples: Any) -> dict:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self.recognizer.decode_stream(stream)
        result = stream.result
        # Sherpa has already removed rich-metadata tokens and corrected times.
        return {"text": result.text, "language": result.lang,
                "tokens": list(result.tokens), "timestamps": list(result.timestamps)}

    def close(self) -> None:
        self.recognizer = None


class _NativeVad:
    def __init__(self, *, model_path: Path, num_threads: int, max_speech_duration: float):
        import sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_path)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.25
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = max_speech_duration
        config.sample_rate = SAMPLE_RATE
        config.num_threads = num_threads
        config.provider = "cpu"
        self.window_size = config.silero_vad.window_size
        # The soft max-duration threshold does not guarantee a hard cut. Keep
        # the full bounded source available until a continuous speech turn ends.
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=MAX_AUDIO_SECONDS + 1)

    def speech_windows(self, samples: Any) -> list[dict]:
        windows = []

        def drain():
            while not self.vad.empty():
                front = self.vad.front
                begin = int(front.start)
                end = begin + len(front.samples)
                # A detector's final padded frame cannot extend the selected
                # source clip past the physical end of the original waveform.
                end = min(end, len(samples))
                if begin < 0 or end < begin:
                    raise TranscriptionError("Silero VAD returned invalid audio windows; no transcript was stored.")
                if end > begin:
                    windows.append({"start_sample": begin, "end_sample": end})
                self.vad.pop()

        for offset in range(0, len(samples), self.window_size):
            # Feed the final partial window too, before flushing trailing speech.
            self.vad.accept_waveform(samples[offset:offset + self.window_size])
            drain()
        self.vad.flush()
        drain()
        return windows

    def close(self) -> None:
        self.vad = None


def _validated_windows(values: Any, sample_count: int) -> list[tuple[int, int]]:
    if not isinstance(values, list) or len(values) > MAX_SEGMENTS:
        raise TranscriptionError("Silero VAD returned invalid audio windows; no transcript was stored.")
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise TranscriptionError("Silero VAD returned invalid audio windows; no transcript was stored.")
        begin, end = value.get("start_sample"), value.get("end_sample")
        if (any(isinstance(number, bool) or not isinstance(number, Integral) for number in (begin, end))
                or not 0 <= begin < end <= sample_count):
            raise TranscriptionError("Silero VAD returned invalid audio windows; no transcript was stored.")
        result.append((int(begin), int(end)))
    result.sort()
    if any(previous[1] > current[0] for previous, current in zip(result, result[1:])):
        raise TranscriptionError("Silero VAD returned overlapping speech windows; no transcript was stored.")
    return result


def _validated_turns(values: Any, sample_count: int) -> list[tuple[int, int, str]]:
    if not isinstance(values, list) or len(values) > MAX_SEGMENTS:
        raise TranscriptionError("Local diarization returned invalid speaker turns; no transcript was stored.")
    duration = sample_count / SAMPLE_RATE
    turns = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("speaker"), str) or not re.fullmatch(r"speaker_\d{1,8}", value["speaker"]):
            raise TranscriptionError("Local diarization returned invalid speaker turns; no transcript was stored.")
        start, end = value.get("start"), value.get("end")
        if (any(isinstance(number, bool) or not isinstance(number, Real) for number in (start, end))
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration):
            raise TranscriptionError("Local diarization returned invalid speaker turns; no transcript was stored.")
        begin, finish = int(round(start * SAMPLE_RATE)), int(round(end * SAMPLE_RATE))
        if finish > begin:
            turns.append((begin, finish, value["speaker"]))
    return sorted(turns)


def _speech_spans(windows: list[tuple[int, int]], turns: list[tuple[int, int, str]], chunk_samples: int) -> list[tuple[int, int, str]]:
    spans = []
    for begin, end in windows:
        relevant = [turn for turn in turns if turn[0] < end and turn[1] > begin]
        boundaries = sorted({begin, end, *(max(begin, turn[0]) for turn in relevant), *(min(end, turn[1]) for turn in relevant)})
        merged = []
        for start, finish in zip(boundaries, boundaries[1:]):
            speakers = {speaker for first, last, speaker in relevant if first < finish and last > start}
            speaker = next(iter(speakers)) if len(speakers) == 1 else "unknown"
            if merged and merged[-1][2] == speaker and merged[-1][1] == start:
                merged[-1] = (merged[-1][0], finish, speaker)
            else:
                merged.append((start, finish, speaker))
        for start, finish, speaker in merged:
            for offset in range(start, finish, chunk_samples):
                spans.append((offset, min(offset + chunk_samples, finish), speaker))
                if len(spans) > MAX_SEGMENTS:
                    raise TranscriptionError("Too many local speech windows; split this recording before retrying.")
    return spans


def _decoded_result(value: Any, duration: float, requested_language: str) -> tuple[str, str, int]:
    if not isinstance(value, dict) or not isinstance(value.get("text"), str) or len(value["text"]) > 100000:
        raise TranscriptionError(_INVALID_RESULT)
    tokens, timestamps = value.get("tokens", []), value.get("timestamps", [])
    if (not isinstance(tokens, list) or not isinstance(timestamps, list) or len(tokens) != len(timestamps)
            or len(tokens) > MAX_TOKENS or any(not isinstance(token, str) or len(token) > 1000 for token in tokens)):
        raise TranscriptionError(_INVALID_RESULT)
    previous = 0.0
    for timestamp in timestamps:
        if (isinstance(timestamp, bool) or not isinstance(timestamp, Real) or not math.isfinite(timestamp)
                or not previous <= timestamp <= duration + 0.001):
            raise TranscriptionError(_INVALID_RESULT)
        previous = timestamp
    text = value["text"].strip()
    language = value.get("language")
    if language is None:
        language = requested_language
    elif not isinstance(language, str):
        raise TranscriptionError(_INVALID_RESULT)
    elif not language:
        language = requested_language
    else:
        match = re.fullmatch(r"<\|([a-z]+)\|>", language)
        language = match.group(1) if match else language
        if language not in _CODES:
            raise TranscriptionError(_INVALID_RESULT)
        if requested_language == "en" and language != "en":
            raise TranscriptionError("SenseVoice returned a language different from the requested English; please retry.")
    return text, language, len(timestamps)


class SenseVoiceTranscriber:
    """CPU-only source-window transcription with mandatory local speakers.

    Test factories receive model paths/configuration only, never app state or
    credentials. VAD backends expose speech_windows(samples); recognizer
    backends expose transcribe(samples). Optional close() releases each backend.
    """
    def __init__(self, *, recognizer_factory: Callable[..., Any] | None = None,
                 vad_factory: Callable[..., Any] | None = None, diarizer: Any = None,
                 audio_loader: Callable[[Path], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.model_name, self.device = MODEL_NAME, "cpu"
        self.diarization_enabled = True
        self._recognizer_factory, self._vad_factory = recognizer_factory or _NativeRecognizer, vad_factory or _NativeVad
        self._audio_loader, self._clock = audio_loader or _decode_audio, clock
        self._diarizer = diarizer if diarizer is not None else LocalDiarizer()
        self.loaded, self.state, self.error = False, "unavailable", None
        self.model_path = ROOT / "models/sensevoice/model.int8.onnx"
        self.tokens_path = ROOT / "models/sensevoice/tokens.txt"
        self.vad_path = ROOT / "models/sensevoice/silero_vad.onnx"
        self.num_threads, self.chunk_seconds = 4, 30.0
        try:
            for name, attribute in (("HYA_SENSEVOICE_MODEL_PATH", "model_path"),
                                    ("HYA_SENSEVOICE_TOKENS_PATH", "tokens_path"),
                                    ("HYA_SILERO_VAD_MODEL_PATH", "vad_path")):
                value = os.environ.get(name, "").strip()
                if value:
                    setattr(self, attribute, Path(value).expanduser().resolve())
            self.num_threads = _setting("HYA_SENSEVOICE_NUM_THREADS", 4, 1, 32, integer=True)
            self.chunk_seconds = _setting("HYA_SENSEVOICE_CHUNK_SECONDS", 30, 1, 30)
        except (OSError, ValueError) as error:
            self.error = str(error) if isinstance(error, ValueError) else "The configured local SenseVoice model paths could not be read."
        self.models_cached = all(_model_file(path) for path in (self.model_path, self.tokens_path, self.vad_path))
        self.dependencies_available = _dependencies_available()
        self.configured = self.error is None and self.models_cached
        if not self.error and not self.models_cached:
            self.error = "Local SenseVoice model, tokens or Silero VAD files are missing; prepare all three local files before retrying."
        if not self.error and not self.dependencies_available:
            self.error = "Install sherpa-onnx, NumPy and PyAV in the active Python environment for local SenseVoice."
        speaker_health = self._diarizer.health()
        if not self.error and not speaker_health.get("available"):
            self.error = speaker_health.get("error") or "Mandatory local speaker diarization is unavailable; prepare its dependencies and models."
        self.available = self.configured and self.dependencies_available and self.error is None
        self.state = "idle" if self.available else "unavailable"

    def health(self) -> dict:
        return {"provider": "sensevoice", "model": self.model_name, "device": "cpu",
                "configured": self.configured, "available": self.available, "loaded": self.loaded,
                "models_cached": self.models_cached, "dependencies_available": self.dependencies_available,
                "state": self.state, "error": self.error, "local_only": True, "external": False,
                "speaker_diarization": True, "speaker_identification": False, "speaker_label_scope": "recording",
                "timing_mode": "vad_diarization_segments", "word_timestamps": False,
                "token_timestamps": "start_only", "text_normalization": "native_itn",
                "max_upload_bytes": MAX_UPLOAD_BYTES, "max_audio_seconds": MAX_AUDIO_SECONDS,
                "required_channels": None, "fallback_reason": None}

    def inspect_audio(self, path: str | Path, max_seconds: float = MAX_AUDIO_SECONDS) -> dict:
        target = Path(path)
        if target.is_file() and target.stat().st_size > MAX_UPLOAD_BYTES:
            raise TranscriptionError("This demo accepts recordings up to 100 MiB; split or compress this file first.")
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, Real) or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("max_seconds must be finite and positive")
        return inspect_audio(target, max_seconds=min(MAX_AUDIO_SECONDS, max_seconds))

    def transcribe(self, path: str | Path, language: str = "en") -> dict:
        if language not in {"en", "auto"}:
            raise TranscriptionError("This demo accepts English or automatic language selection.")
        if not self.available:
            raise TranscriptionError(self.error or "Local SenseVoice ASR is unavailable.")
        with _JOB_LOCK:
            started = self._clock()
            vad, recognizer = None, None
            try:
                import numpy as np
                self.state, self.error = "transcribing", None
                audio = self.inspect_audio(path)
                samples = self._audio_loader(Path(path))
                if (not isinstance(samples, np.ndarray) or samples.ndim != 1 or not samples.size
                        or samples.size > SAMPLE_RATE * (MAX_AUDIO_SECONDS + 0.1)
                        or samples.dtype.kind not in "fiu" or not np.isfinite(samples).all()):
                    raise TranscriptionError("The recording could not be decoded into bounded 16 kHz mono audio.")
                samples = np.ascontiguousarray(samples, dtype=np.float32)
                if not np.isfinite(samples).all():
                    raise TranscriptionError("The recording contains nonfinite audio samples.")
                duration = samples.size / SAMPLE_RATE
                if abs(duration - audio["duration"]) > 0.2:
                    raise TranscriptionError("The decoded recording timeline changed; export a continuous WAV and import it again.")
                self.state = "detecting_speech"
                vad = self._vad_factory(model_path=self.vad_path, num_threads=1, max_speech_duration=self.chunk_seconds)
                self.loaded = True
                windows = _validated_windows(vad.speech_windows(samples), samples.size)
                self.state = "diarizing"
                turns = _validated_turns(self._diarizer.diarize(samples, sample_rate=SAMPLE_RATE), samples.size)
                spans = _speech_spans(windows, turns, int(round(self.chunk_seconds * SAMPLE_RATE)))
                segments, languages, token_count, empty_count = [], set(), 0, 0
                if spans:
                    self.state = "loading_model"
                    recognizer = self._recognizer_factory(model_path=self.model_path, tokens_path=self.tokens_path,
                                                           num_threads=self.num_threads, language=language)
                    self.state = "transcribing"
                    for begin, end, speaker in spans:
                        source = samples[begin:end]
                        text, detected, count = _decoded_result(recognizer.transcribe(source), source.size / SAMPLE_RATE, language)
                        if not text:
                            empty_count += 1
                            if source.size >= SAMPLE_RATE / 2 and np.any(np.abs(source) > 1e-7):
                                raise TranscriptionError("SenseVoice returned no text for detected speech; check the local models and runtime, then retry.")
                            continue
                        token_count += count
                        if token_count > MAX_TOKENS:
                            raise TranscriptionError(_INVALID_RESULT)
                        languages.add(detected)
                        segments.append({"start": begin / SAMPLE_RATE, "end": end / SAMPLE_RATE,
                                         "text": text, "speaker": speaker, "words": []})
                    if not segments:
                        raise TranscriptionError("SenseVoice returned no text for detected speech; check the local models and runtime, then retry.")
                self.state, self.error = "ready", None
                result_language = next(iter(languages)) if len(languages) == 1 else "mul" if languages else language
                voiced_samples = sum(end - begin for begin, end in windows)
                labeled_samples = sum(end - begin for begin, end, speaker in spans if speaker != "unknown")
                return {"segments": segments, "language": result_language, "duration": duration,
                        "metrics": {"provider": "sensevoice", "model": self.model_name, "device": "cpu",
                                    "elapsed_seconds": round(self._clock() - started, 3), "audio_processing": "local",
                                    "models_from_cache": True, "speaker_diarization": True,
                                    "speaker_identification": False, "speaker_label_scope": "recording",
                                    "timing_mode": "vad_diarization_segments", "word_timestamps": False,
                                    "token_start_count": token_count, "text_normalization": "native_itn",
                                    "speech_window_count": len(windows), "speech_span_count": len(spans),
                                    "empty_speech_span_count": empty_count,
                                    "labeled_speech_fraction": round(labeled_samples / voiced_samples, 6) if voiced_samples else None,
                                    "chunk_seconds": self.chunk_seconds, "fallback_reason": None}}
            except (TranscriptionError, DiarizationError) as error:
                self.state, self.error = "error", str(error)
                raise TranscriptionError(self.error) from None
            except Exception as error:
                if isinstance(error, ImportError):
                    message = "The local SenseVoice runtime is incomplete; install sherpa-onnx, NumPy and PyAV."
                elif isinstance(error, MemoryError):
                    message = "Local SenseVoice ran out of memory; reduce HYA_SENSEVOICE_CHUNK_SECONDS, then retry."
                else:
                    message = "Local SenseVoice transcription failed; check the cached models and runtime, then retry."
                self.state, self.error = "error", message
                raise TranscriptionError(message) from None
            finally:
                cleanup_failed = False
                for backend in (recognizer, vad):
                    close = getattr(backend, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            cleanup_failed = True
                recognizer, vad, self.loaded = None, None, False
                gc.collect()
                if cleanup_failed:
                    self.state, self.error = "error", "The local SenseVoice runtime could not be released; restart the demo and retry."
                    raise TranscriptionError(self.error) from None
