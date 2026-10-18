"""Recording-local CPU speaker diarization with cached sherpa-onnx models.

No models are downloaded. Health checks never import the inference runtime.
The model pipeline follows sherpa-onnx's offline speaker diarization example:
https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/python-api-examples/offline-speaker-diarization.py
"""
from __future__ import annotations

import importlib.util
import math
from numbers import Integral, Real
import os
from pathlib import Path
import threading
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_RATE = 16000
MAX_SEGMENTS = 100000
_JOB_LOCK = threading.RLock()
_INVALID_RESULT = "Local speaker diarization returned invalid time intervals; no speaker labels were stored."


class DiarizationError(RuntimeError):
    """A curated, user-visible local diarization failure."""


def _dependencies_available() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("numpy", "sherpa_onnx"))
    except (ImportError, ValueError):
        return False


def _model_file_available(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _create_diarizer(*, segmentation_path: Path, embedding_path: Path,
                     threshold: float, num_threads: int):
    # The only runtime import occurs after a diarize() request is validated.
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation_path), window_shift_ratio=0.1,
            ),
            num_threads=num_threads, provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(embedding_path), num_threads=num_threads, provider="cpu",
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=threshold),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not config.validate():
        raise DiarizationError("The local speaker diarization models could not be initialized; check the model files.")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def _validated_segments(values: Any, duration: float) -> list[dict]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_SEGMENTS:
        raise DiarizationError(_INVALID_RESULT)
    segments = []
    for value in values:
        if isinstance(value, dict):
            start, end, speaker = value.get("start"), value.get("end"), value.get("speaker")
        else:
            start, end, speaker = (getattr(value, name, None) for name in ("start", "end", "speaker"))
        if (any(isinstance(number, bool) or not isinstance(number, Real) for number in (start, end))
                or isinstance(speaker, bool) or not isinstance(speaker, Integral) or speaker < 0):
            raise DiarizationError(_INVALID_RESULT)
        try:
            start, end = float(start), float(end)
        except (ValueError, OverflowError):
            raise DiarizationError(_INVALID_RESULT) from None
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise DiarizationError(_INVALID_RESULT)
        # Keep model times unchanged: no rounding, clipping, merging or invented boundaries.
        segments.append({"start": start, "end": end, "speaker": f"speaker_{int(speaker)}"})
    return sorted(segments, key=lambda segment: (segment["start"], segment["end"], segment["speaker"]))


class LocalDiarizer:
    """Infer anonymous speaker intervals independently for each recording.

    An injected ``factory`` receives keyword arguments ``segmentation_path``
    and ``embedding_path`` (Path), ``threshold`` (float), ``num_threads`` (int).
    It returns a backend with ``sample_rate`` and ``process(samples)``. Results
    may be a sequence of segments or expose ``sort_by_start_time()`` as sherpa
    does. A backend's optional ``close()`` is called after success or failure.
    """

    def __init__(self, *, factory: Callable[..., Any] | None = None) -> None:
        self._factory = factory or _create_diarizer
        self._config_error = None
        self._last_error = None
        self.segmentation_path = ROOT / "models/diarization/segmentation.onnx"
        self.embedding_path = ROOT / "models/diarization/embedding.onnx"
        self.threshold, self.num_threads = 0.5, 2
        try:
            for name, attribute in (("HYA_DIARIZATION_SEGMENTATION_PATH", "segmentation_path"),
                                    ("HYA_DIARIZATION_EMBEDDING_PATH", "embedding_path")):
                value = os.environ.get(name, "").strip()
                if value:
                    setattr(self, attribute, Path(value).expanduser().resolve())
            self.threshold = float(os.environ.get("HYA_DIARIZATION_THRESHOLD", "0.5"))
            if not math.isfinite(self.threshold) or self.threshold < 0:
                raise ValueError
            threads = float(os.environ.get("HYA_DIARIZATION_NUM_THREADS", "2"))
            if not math.isfinite(threads) or not threads.is_integer() or not 1 <= threads <= 32:
                raise ValueError
            self.num_threads = int(threads)
        except (OSError, ValueError, TypeError, OverflowError):
            self._config_error = "Invalid local speaker diarization settings; use a finite nonnegative threshold and 1 to 32 threads."

    def health(self) -> dict:
        models_cached = all(_model_file_available(path) for path in (self.segmentation_path, self.embedding_path))
        dependencies_available = _dependencies_available()
        configured = self._config_error is None and models_cached
        error = self._config_error
        if error is None and not models_cached:
            error = "Local speaker diarization models are missing or empty; prepare the segmentation and embedding ONNX files."
        if error is None and not dependencies_available:
            error = "Install sherpa-onnx and NumPy in the active Python environment for local speaker diarization."
        return {
            "provider": "sherpa_onnx", "device": "cpu", "configured": configured,
            "available": configured and dependencies_available, "models_cached": models_cached,
            "dependencies_available": dependencies_available, "error": error or self._last_error,
            "local_only": True, "external": False, "speaker_diarization": True,
            "speaker_identification": False, "speaker_label_scope": "recording",
        }

    def diarize(self, samples: Any, sample_rate: int = SAMPLE_RATE) -> list[dict]:
        health = self.health()
        if not health["available"]:
            raise DiarizationError(health["error"])
        backend = None
        try:
            import numpy as np

            if isinstance(sample_rate, bool) or not isinstance(sample_rate, Integral) or sample_rate != SAMPLE_RATE:
                raise DiarizationError("Local speaker diarization requires 16 kHz mono audio.")
            values = np.asarray(samples)
            if (values.ndim != 1 or not values.size or values.dtype.kind not in "fiu"
                    or not np.isfinite(values).all()):
                raise DiarizationError("Local speaker diarization requires nonempty finite mono audio samples.")
            with np.errstate(over="ignore", invalid="ignore"):
                values = values.astype(np.float32, copy=False)
            if not np.isfinite(values).all():
                raise DiarizationError("Local speaker diarization requires finite mono audio samples.")
            with _JOB_LOCK:
                backend = self._factory(
                    segmentation_path=self.segmentation_path, embedding_path=self.embedding_path,
                    threshold=self.threshold, num_threads=self.num_threads,
                )
                if (isinstance(backend.sample_rate, bool) or not isinstance(backend.sample_rate, Integral)
                        or backend.sample_rate != sample_rate):
                    raise DiarizationError("The local diarization model expects a different audio sample rate.")
                result = backend.process(values)
                if hasattr(result, "sort_by_start_time"):
                    result = result.sort_by_start_time()
                result = _validated_segments(result, values.size / sample_rate)
            self._last_error = None
            return result
        except DiarizationError as error:
            self._last_error = str(error)
            raise
        except ImportError:
            self._last_error = "The local speaker diarization runtime is incomplete; reinstall sherpa-onnx and NumPy."
            raise DiarizationError(self._last_error) from None
        except Exception:
            self._last_error = "Local speaker diarization failed; check the cached ONNX models and runtime, then retry."
            raise DiarizationError(self._last_error) from None
        finally:
            if backend is not None:
                try:
                    close = getattr(backend, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    self._last_error = "The local speaker diarization runtime could not be released; restart the demo and retry."
                    raise DiarizationError(self._last_error) from None
                finally:
                    backend = None
