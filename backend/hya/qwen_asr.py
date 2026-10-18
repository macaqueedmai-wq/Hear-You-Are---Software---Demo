"""Local Qwen3 ASR and forced alignment using native Hugging Face models.

Only cached local weights are accepted. Imports/model loading happen during
transcription, never during health checks. Two passes unload the ASR model
before loading the aligner to keep GPU memory bounded on a small GPU.

https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf
https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B-hf
"""
from __future__ import annotations

import gc
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any, Callable

from .asr import TranscriptionError, inspect_audio

DEFAULT_MODEL = "Qwen/Qwen3-ASR-1.7B-hf"
DEFAULT_ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B-hf"
DEFAULT_MODEL_REVISION = "bcd2b5b7f32b480ab5790554cfa8347f246a14f3"
DEFAULT_ALIGNER_REVISION = "c07281df297b9905d24a508279258cccf987a064"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_AUDIO_SECONDS = 20 * 60
SAMPLE_RATE = 16000
MAX_WORDS = 25000
_JOB_LOCK = threading.RLock()
_INVALID_ALIGNMENT = "Qwen returned invalid or out-of-bounds word alignment; no transcript was stored."
_LANGUAGES = {"english": "en", "chinese": "zh", "cantonese": "yue", "french": "fr",
              "german": "de", "italian": "it", "japanese": "ja", "korean": "ko",
              "portuguese": "pt", "russian": "ru", "spanish": "es"}


def _runtime_dependencies() -> bool:
    try:
        if not all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "numpy", "av", "accelerate")):
            return False
        version = importlib.metadata.version("transformers")
        match = re.match(r"^(\d+)\.(\d+)", version)
        return bool(match and tuple(map(int, match.groups())) >= (5, 13))
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
        return False


def _complete_model(path: Path) -> bool:
    if not path.is_dir() or not all((path / name).is_file() for name in ("config.json", "processor_config.json", "tokenizer.json")):
        return False
    if (path / "model.safetensors").is_file():
        return (path / "model.safetensors").stat().st_size > 0
    index = path / "model.safetensors.index.json"
    try:
        if index.stat().st_size > 2 * 1024 * 1024:
            return False
        weight_map = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        return isinstance(weight_map, dict) and bool(weight_map) and all(
            isinstance(name, str) and Path(name).name == name
            and (path / name).is_file() and (path / name).stat().st_size > 0
            for name in set(weight_map.values()))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _cached_model(model: str, explicit_path: str, revision: str = "") -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        return candidate if _complete_model(candidate) else None
    cache_roots = []
    for value in (os.environ.get("HYA_MODEL_CACHE"), os.environ.get("HF_HUB_CACHE"),
                  str(Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))) / "hub")):
        if value:
            cache_roots.append(Path(value).expanduser())
    for root in cache_roots:
        repo = root / ("models--" + model.replace("/", "--"))
        if revision:
            candidate = repo / "snapshots" / revision
            if _complete_model(candidate):
                return candidate.resolve()
        try:
            cached_revision = (repo / "refs" / (revision or "main")).read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cached_revision):
                candidate = repo / "snapshots" / cached_revision
                if _complete_model(candidate):
                    return candidate.resolve()
        except OSError:
            pass
    return None


def _setting(name: str, default: float, minimum: float, maximum: float, *, integer: bool = False) -> float | int:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.") from None
    if not math.isfinite(value) or not minimum <= value <= maximum or (integer and not value.is_integer()):
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
    return int(value) if integer else value


def _select_device(requested: str) -> str:
    import torch
    if requested == "cuda" and not torch.cuda.is_available():
        raise TranscriptionError("Qwen CUDA is unavailable; check the NVIDIA driver and PyTorch CUDA installation.")
    return "cuda" if requested == "cuda" or requested == "auto" and torch.cuda.is_available() else "cpu"


def _decode_audio(path: Path):
    """Decode into mono 16 kHz float audio, retaining leading/trailing silence."""
    import av
    import numpy as np
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
    chunks, total, decoded_seconds = [], 0, 0.0
    origin = None
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            if frame.pts is not None and frame.time_base is not None:
                timestamp = float(frame.pts * frame.time_base)
                if origin is None:
                    origin = timestamp - decoded_seconds
                if not math.isfinite(timestamp) or abs(timestamp - origin - decoded_seconds) > 0.1:
                    raise TranscriptionError("The recording has a discontinuous audio timeline; export a continuous WAV and import it again.")
            decoded_seconds += frame.samples / frame.sample_rate
            frame.pts = None  # The verified continuous sample timeline starts at zero.
            for sampled in resampler.resample(frame):
                values = sampled.to_ndarray().reshape(-1)
                total += values.size
                if total > SAMPLE_RATE * (MAX_AUDIO_SECONDS + 0.1):
                    raise TranscriptionError("The recording exceeds this demo's 20 minute audio limit.")
                chunks.append(values)
        for sampled in resampler.resample(None):
            values = sampled.to_ndarray().reshape(-1)
            total += values.size
            chunks.append(values)
    if not chunks or total > SAMPLE_RATE * (MAX_AUDIO_SECONDS + 0.1):
        raise TranscriptionError("The recording contains no usable audio or exceeds the 20 minute limit.")
    return np.concatenate(chunks).astype(np.float32, copy=False)


class _NativeModel:
    def __init__(self, stage: str, path: Path, device: str, threads: int):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoModelForTokenClassification, AutoProcessor
        self.torch, self.device = torch, device
        torch.set_num_threads(threads)
        self.processor = AutoProcessor.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        model_class = AutoModelForMultimodalLM if stage == "asr" else AutoModelForTokenClassification
        self.model = model_class.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False,
            dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            device_map={"": device}, attn_implementation="sdpa",
        ).eval()

    def transcribe(self, samples: Any, language: str | None, max_new_tokens: int) -> dict:
        inputs = self.processor.apply_transcription_request(audio=samples, language=language, sampling_rate=SAMPLE_RATE)
        inputs = inputs.to(self.model.device, self.model.dtype)
        with self.torch.inference_mode():
            output_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated = output_ids[:, inputs["input_ids"].shape[1]:]
        eos = self.model.generation_config.eos_token_id
        eos = {eos} if isinstance(eos, int) else set(eos or [])
        truncated = generated.shape[1] >= max_new_tokens and (not generated.shape[1] or int(generated[0, -1]) not in eos)
        parsed = self.processor.decode(generated, return_format="parsed")
        if isinstance(parsed, list) and len(parsed) == 1:
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            raise TranscriptionError("Qwen returned an invalid transcription result; please retry.")
        return {"text": parsed.get("transcription"), "language": parsed.get("language"), "truncated": truncated}

    def align(self, samples: Any, transcript: str, language: str) -> list[dict]:
        inputs, words = self.processor.prepare_forced_aligner_inputs(audio=samples, transcript=transcript,
                                                                    language=language, sampling_rate=SAMPLE_RATE)
        inputs = inputs.to(self.model.device, self.model.dtype)
        with self.torch.inference_mode():
            output = self.model(**inputs)
        aligned = self.processor.decode_forced_alignment(
            logits=output.logits, input_ids=inputs["input_ids"], word_lists=words,
            timestamp_token_id=self.model.config.timestamp_token_id,
        )
        if not isinstance(aligned, list) or len(aligned) != 1:
            raise TranscriptionError(_INVALID_ALIGNMENT)
        return aligned[0]

    def close(self) -> None:
        self.model, self.processor = None, None
        gc.collect()
        if self.device == "cuda":
            self.torch.cuda.empty_cache()


def _lexical(value: str) -> str:
    return "".join(character for character in value if unicodedata.category(character)[0] in {"L", "N", "M"})


def _validated_alignment(values: Any, text: str, duration: float, offset: float) -> list[dict]:
    if not isinstance(values, list) or not values or len(values) > MAX_WORDS:
        raise TranscriptionError(_INVALID_ALIGNMENT)
    result, previous = [], 0.0
    for value in values:
        if (not isinstance(value, dict) or not isinstance(value.get("text"), str)
                or not value["text"].strip() or not _lexical(value["text"]) or len(value["text"]) > 1000):
            raise TranscriptionError(_INVALID_ALIGNMENT)
        start, end = value.get("start_time"), value.get("end_time")
        if any(isinstance(number, bool) or not isinstance(number, (int, float)) for number in (start, end)):
            raise TranscriptionError(_INVALID_ALIGNMENT)
        try:
            start, end = float(start), float(end)
        except (ValueError, OverflowError):
            raise TranscriptionError(_INVALID_ALIGNMENT) from None
        if not math.isfinite(start) or not math.isfinite(end) or start < previous or end < start or end > duration + 0.001:
            raise TranscriptionError(_INVALID_ALIGNMENT)
        previous = start
        result.append({"start": round(offset + start, 6), "end": round(offset + end, 6), "word": value["text"].strip()})
    if _lexical("".join(word["word"] for word in result)) != _lexical(text):
        raise TranscriptionError("Qwen word alignment did not match the source transcript; no transcript was stored.")
    return result


def _word_speaker(word: dict, turns: list[dict]) -> str:
    """Use actual temporal overlap; concurrent different speakers stay unknown."""
    overlaps = []
    for turn in turns:
        if turn["start"] >= word["end"]:
            break
        start, end = max(turn["start"], word["start"]), min(turn["end"], word["end"])
        if end > start:
            overlaps.append((start, end, turn["speaker"]))
    if not overlaps:
        return "unknown"
    for index, (start, end, speaker) in enumerate(overlaps):
        if any(other != speaker and min(end, other_end) - max(start, other_start) > 1e-6
               for other_start, other_end, other in overlaps[index + 1:]):
            return "unknown"
    totals: dict[str, float] = {}
    for speaker in {item[2] for item in overlaps}:
        previous_end = -1.0
        for start, end, _ in sorted(item for item in overlaps if item[2] == speaker):
            totals[speaker] = totals.get(speaker, 0) + max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return ranked[0][0] if len(ranked) == 1 or ranked[0][1] - ranked[1][1] > 1e-6 else "unknown"


def _split_speakers(segment: dict, turns: list[dict]) -> list[dict]:
    """Split on word-level speaker changes while retaining transcript punctuation."""
    words = segment["words"]
    labels = [_word_speaker(word, turns) for word in words]
    # The alignment validator already checked lexical equality with source text.
    positions = [index for index, character in enumerate(segment["text"])
                 if unicodedata.category(character)[0] in {"L", "N", "M"}]
    lexical_count, word_starts = 0, []
    for word in words:
        word_starts.append(positions[lexical_count])
        lexical_count += len(_lexical(word["word"]))
    result, begin = [], 0
    for index in range(1, len(words) + 1):
        if index < len(words) and labels[index] == labels[begin]:
            continue
        source_start = 0 if begin == 0 else word_starts[begin]
        source_end = len(segment["text"]) if index == len(words) else word_starts[index]
        group = words[begin:index]
        result.append({"start": group[0]["start"], "end": max(word["end"] for word in group),
                       "text": segment["text"][source_start:source_end].strip(),
                       "speaker": labels[begin], "words": group})
        begin = index
    return result


class QwenTranscriber:
    """Cached Qwen inference; injectable loaders keep contract tests offline.

    ``model_loader(stage, local_path, device, num_threads)`` returns an object
    with ``transcribe(...)``/``align(...)`` and ``close()``. No injected object
    receives application state, API credentials, or an external audio URL.
    """
    def __init__(self, *, model_loader: Callable[..., Any] | None = None,
                 audio_loader: Callable[[Path], Any] | None = None,
                 diarizer: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.model_name = os.environ.get("HYA_QWEN_MODEL", DEFAULT_MODEL).strip()
        self.aligner_name = os.environ.get("HYA_QWEN_ALIGNER", DEFAULT_ALIGNER).strip()
        self.requested_device = os.environ.get("HYA_QWEN_DEVICE", "auto").strip()
        self.model_revision = os.environ.get("HYA_QWEN_MODEL_REVISION", DEFAULT_MODEL_REVISION if self.model_name == DEFAULT_MODEL else "").strip()
        self.aligner_revision = os.environ.get("HYA_QWEN_ALIGNER_REVISION", DEFAULT_ALIGNER_REVISION if self.aligner_name == DEFAULT_ALIGNER else "").strip()
        self.diarization_enabled = os.environ.get("HYA_QWEN_DIARIZATION", "true").strip().lower() != "false"
        self._diarizer, self._diarizer_health = diarizer, None
        self.device = self.requested_device
        self._loader, self._audio_loader, self._clock = model_loader or _NativeModel, audio_loader or _decode_audio, clock
        self.loaded, self.hardware_checked = False, False
        self.error = None
        self._model_path, self._aligner_path = None, None
        try:
            if not all(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name) for name in (self.model_name, self.aligner_name)):
                raise ValueError("Set valid local Qwen model identifiers; external model URLs are not accepted.")
            if self.requested_device not in {"auto", "cuda", "cpu"}:
                raise ValueError("HYA_QWEN_DEVICE must be auto, cuda or cpu.")
            if os.environ.get("HYA_QWEN_DIARIZATION", "true").strip().lower() not in {"true", "false"}:
                raise ValueError("HYA_QWEN_DIARIZATION must be true or false.")
            if not all(not revision or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", revision)
                       for revision in (self.model_revision, self.aligner_revision)):
                raise ValueError("Set valid cached Qwen model revisions without paths or URLs.")
            self.chunk_seconds = _setting("HYA_QWEN_CHUNK_SECONDS", 30, 1, 300)
            self.max_new_tokens = _setting("HYA_QWEN_MAX_NEW_TOKENS", 1024, 32, 8192, integer=True)
            self.num_threads = _setting("HYA_QWEN_NUM_THREADS", 4, 1, 32, integer=True)
            self._model_path = _cached_model(self.model_name, os.environ.get("HYA_QWEN_MODEL_PATH", "").strip(), self.model_revision)
            self._aligner_path = _cached_model(self.aligner_name, os.environ.get("HYA_QWEN_ALIGNER_PATH", "").strip(), self.aligner_revision)
            if self.diarization_enabled:
                if self._diarizer is None:
                    from .diarization import LocalDiarizer
                    self._diarizer = LocalDiarizer()
                self._diarizer_health = self._diarizer.health()
        except (OSError, ValueError) as error:
            self.error = str(error) if isinstance(error, ValueError) else "The configured local Qwen model directories could not be read."
        except ImportError:
            self.error = "The local speaker diarization runtime is missing; install its required packages before retrying."
        self.models_cached = self._model_path is not None and self._aligner_path is not None
        self.dependencies_available = _runtime_dependencies()
        self.configured = self.error is None and self.models_cached
        if not self.error and not self.models_cached:
            self.error = "Qwen weights are not cached; download the models first and set HYA_QWEN_MODEL_PATH and HYA_QWEN_ALIGNER_PATH."
        if not self.error and not self.dependencies_available:
            self.error = "Install the local Qwen runtime with PyTorch, Transformers >=5.13, Accelerate, NumPy and PyAV."
        if not self.error and self.diarization_enabled and not self._diarizer_health.get("available"):
            self.error = self._diarizer_health.get("error") or "Local speaker diarization is not ready; check its packages and cached models."
        self.available = self.configured and self.dependencies_available and self.error is None
        self.state = "idle" if self.available else "unavailable"

    def health(self) -> dict:
        return {"provider": "qwen", "model": self.model_name, "aligner_model": self.aligner_name,
                "model_revision": self.model_revision, "aligner_revision": self.aligner_revision,
                "device": self.device, "configured": self.configured, "available": self.available,
                "models_cached": self.models_cached, "dependencies_available": self.dependencies_available,
                "hardware_checked": self.hardware_checked, "loaded": self.loaded, "state": self.state,
                "error": self.error, "local_only": True, "external": False,
                "speaker_diarization": self.diarization_enabled, "speaker_identification": False,
                "speaker_label_scope": "recording", "max_upload_bytes": MAX_UPLOAD_BYTES,
                "max_audio_seconds": MAX_AUDIO_SECONDS, "required_channels": None, "fallback_reason": None}

    def inspect_audio(self, path: str | Path, max_seconds: float = MAX_AUDIO_SECONDS) -> dict:
        target = Path(path)
        if target.is_file() and target.stat().st_size > MAX_UPLOAD_BYTES:
            raise TranscriptionError("This demo accepts recordings up to 100 MiB; split or compress this file first.")
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("max_seconds must be finite and positive")
        return inspect_audio(target, max_seconds=min(MAX_AUDIO_SECONDS, max_seconds))

    def transcribe(self, path: str | Path, language: str = "en") -> dict:
        if language not in {"en", "auto"}:
            raise TranscriptionError("This demo accepts English or automatic language selection.")
        if not self.available:
            raise TranscriptionError(self.error or "Qwen ASR is unavailable.")
        with _JOB_LOCK:
            started = self._clock()
            try:
                import numpy as np
                self.state, self.error = "transcribing", None
                audio = self.inspect_audio(path)
                samples = self._audio_loader(Path(path))
                if (not isinstance(samples, np.ndarray) or samples.ndim != 1 or not samples.size
                        or samples.size > SAMPLE_RATE * (MAX_AUDIO_SECONDS + 0.1)
                        or not np.isfinite(samples).all()):
                    raise TranscriptionError("The recording could not be decoded into bounded 16 kHz mono audio.")
                duration = samples.size / SAMPLE_RATE
                if abs(duration - audio["duration"]) > 0.2:
                    raise TranscriptionError("The decoded recording timeline changed; export a continuous WAV and import it again.")
                self.device = _select_device(self.requested_device)
                self.hardware_checked = True
                size = int(round(self.chunk_seconds * SAMPLE_RATE))
                chunks = [(offset, samples[offset:offset + size]) for offset in range(0, samples.size, size)
                          if np.any(np.abs(samples[offset:offset + size]) > 1e-8)]
                transcripts = []
                if chunks:
                    model = None
                    try:
                        self.state = "loading_asr"
                        model = self._loader("asr", self._model_path, self.device, self.num_threads)
                        self.loaded, self.state = True, "transcribing"
                        for offset, chunk in chunks:
                            output = model.transcribe(chunk, "English" if language == "en" else None, self.max_new_tokens)
                            if (not isinstance(output, dict) or not isinstance(output.get("text"), str)
                                    or len(output["text"]) > 100000 or not isinstance(output.get("truncated"), bool)):
                                raise TranscriptionError("Qwen returned an invalid transcription result; please retry.")
                            if output["truncated"]:
                                raise TranscriptionError("Qwen reached its output limit; reduce HYA_QWEN_CHUNK_SECONDS or increase HYA_QWEN_MAX_NEW_TOKENS, then retry.")
                            text = output["text"].strip()
                            if not text:
                                continue
                            detected = output.get("language")
                            if detected is None or detected == "":
                                if language != "en":
                                    raise TranscriptionError("Qwen did not identify a language for alignment; select English or retry.")
                                detected = "English"
                            if not isinstance(detected, str) or detected.lower() not in _LANGUAGES:
                                raise TranscriptionError("The detected language is not supported by the local Qwen forced aligner.")
                            if language == "en" and detected.lower() != "english":
                                raise TranscriptionError("Qwen returned a language different from the requested English; please retry.")
                            transcripts.append((offset, chunk, text, detected))
                    finally:
                        try:
                            if model is not None:
                                model.close()
                        finally:
                            self.loaded = False
                segments, word_count = [], 0
                if transcripts:
                    model = None
                    try:
                        self.state = "loading_aligner"
                        model = self._loader("aligner", self._aligner_path, self.device, self.num_threads)
                        self.loaded, self.state = True, "aligning"
                        for offset, chunk, text, detected in transcripts:
                            words = _validated_alignment(model.align(chunk, text, detected), text, chunk.size / SAMPLE_RATE, offset / SAMPLE_RATE)
                            word_count += len(words)
                            if word_count > MAX_WORDS:
                                raise TranscriptionError(_INVALID_ALIGNMENT)
                            segments.append({"start": words[0]["start"], "end": max(word["end"] for word in words),
                                             "text": text, "speaker": "unknown", "words": words})
                    finally:
                        try:
                            if model is not None:
                                model.close()
                        finally:
                            self.loaded = False
                languages = {_LANGUAGES[item[3].lower()] for item in transcripts}
                result_language = next(iter(languages)) if len(languages) == 1 else "mul" if languages else language
                if self.diarization_enabled:
                    self.state = "diarizing"
                    from .diarization import DiarizationError
                    try:
                        turns = self._diarizer.diarize(samples, sample_rate=SAMPLE_RATE)
                    except DiarizationError as error:
                        raise TranscriptionError(str(error)) from None
                    if not isinstance(turns, list) or len(turns) > 10000:
                        raise TranscriptionError("Local diarization returned invalid speaker turns; no transcript was stored.")
                    for turn in turns:
                        if not isinstance(turn, dict) or not isinstance(turn.get("speaker"), str) or not re.fullmatch(r"speaker_\d{1,4}", turn["speaker"]):
                            raise TranscriptionError("Local diarization returned invalid speaker turns; no transcript was stored.")
                        start, end = turn.get("start"), turn.get("end")
                        if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (start, end))
                                or start < 0 or end < start or end > duration + 0.001):
                            raise TranscriptionError("Local diarization returned invalid speaker turns; no transcript was stored.")
                    turns = sorted(turns, key=lambda turn: turn["start"])
                    segments = [part for segment in segments for part in _split_speakers(segment, turns)]
                self.state, self.error = "ready", None
                return {"segments": segments, "language": result_language, "duration": duration,
                        "metrics": {"provider": "qwen", "model": self.model_name, "aligner_model": self.aligner_name,
                                    "device": self.device, "elapsed_seconds": round(self._clock() - started, 3),
                                    "audio_processing": "local", "models_from_cache": True,
                                    "speaker_diarization": self.diarization_enabled, "speaker_identification": False,
                                    "speaker_label_scope": "recording", "chunk_seconds": self.chunk_seconds,
                                    "fallback_reason": None}}
            except TranscriptionError as error:
                self.state, self.error = "error", str(error)
                raise
            except Exception as error:
                if type(error).__name__ in {"OutOfMemoryError", "MemoryError"}:
                    message = "Qwen ran out of memory; close other GPU jobs or reduce HYA_QWEN_CHUNK_SECONDS, then retry."
                elif isinstance(error, ImportError):
                    message = "The local Qwen runtime is incomplete; install its required packages in the active Python environment."
                else:
                    message = "Local Qwen transcription or alignment failed; check the cached models and runtime, then retry."
                self.loaded, self.state, self.error = False, "error", message
                raise TranscriptionError(message) from None
