"""Local Whisper transcription. Audio and transcript never leave this process.

Only model weights are fetched from Hugging Face on first use. A process-wide
lock serializes model loading/inference so concurrent uploads cannot exhaust a
small GPU. PyAV validation is independent of downloading/loading Whisper.
"""

from __future__ import annotations

import ctypes
import importlib.util
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

_JOB_LOCK = threading.RLock()
_MODEL: Any = None
_MODEL_KEY: tuple[str, str, str, str | None] | None = None
_DLL_HANDLES: list[Any] = []
_DLL_DIRECTORIES: set[str] = set()


class TranscriptionError(RuntimeError):
    """An actionable, user-visible ASR failure."""


def _register_cuda_dlls() -> None:
    """Support pip NVIDIA runtimes and explicit external DLL directories."""
    if os.name != "nt":
        return
    candidates = [
        Path(item) for item in os.environ.get("HYA_CUDA_DLL_DIRS", "").split(os.pathsep)
        if item.strip()
    ]
    for item in sys.path:
        for component in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            candidates.append(Path(item) / "nvidia" / component / "bin")
    for path in candidates:
        resolved = str(path.resolve())
        if not path.is_dir() or resolved in _DLL_DIRECTORIES:
            continue
        _DLL_DIRECTORIES.add(resolved)
        # CTranslate2 uses the system loader for some sub-libraries. Keep both
        # the Python DLL directory handle and PATH entry for the process life.
        if hasattr(os, "add_dll_directory"):
            _DLL_HANDLES.append(os.add_dll_directory(resolved))
        os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")


def _windows_cuda_libraries_available() -> bool:
    if os.name != "nt":
        return True
    try:
        for library in ("cublas64_12.dll", "cudnn64_9.dll"):
            _DLL_HANDLES.append(ctypes.WinDLL(library))
        return True
    except OSError:
        return False


def inspect_audio(path: str | Path, max_seconds: float = 20 * 60) -> dict[str, Any]:
    """Decode/bound an audio stream without loading Whisper or changing files.

    Container duration is only a hint: decoded frame samples and time stamps
    also enforce the limit. This avoids trusting a filename or corrupt metadata.
    """
    target = Path(path)
    if not target.is_file():
        raise TranscriptionError("找不到錄音檔案，請重新匯入。")
    if target.stat().st_size == 0:
        raise TranscriptionError("錄音檔案是空的，請選擇有效的音訊。")
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    try:
        import av
    except ImportError as error:
        raise TranscriptionError("未安裝音訊解碼套件 PyAV，請完成後端依賴安裝。") from error
    try:
        with av.open(str(target)) as container:
            if not container.streams.audio:
                raise TranscriptionError("檔案沒有可讀取的音訊軌，請選擇錄音檔案。")
            stream = container.streams.audio[0]
            if stream.duration is not None and stream.time_base is not None:
                hinted_duration = float(stream.duration * stream.time_base)
                if hinted_duration > max_seconds + 0.1:
                    raise TranscriptionError(f"錄音超過 {max_seconds / 60:g} 分鐘，請先分段再匯入。")
            decoded_seconds = 0.0
            earliest: float | None = None
            latest = 0.0
            frame_count = 0
            sample_rate = 0
            channels = 0
            for frame in container.decode(stream):
                sample_rate = int(frame.sample_rate or stream.codec_context.sample_rate or 0)
                if sample_rate <= 0:
                    raise TranscriptionError("無法讀取錄音的取樣率。")
                seconds = float(frame.samples) / sample_rate
                decoded_seconds += seconds
                channels = len(frame.layout.channels)
                frame_count += 1
                if frame.pts is not None and frame.time_base is not None:
                    timestamp = float(frame.pts * frame.time_base)
                    earliest = timestamp if earliest is None else min(earliest, timestamp)
                    latest = max(latest, timestamp + seconds)
                timeline_seconds = latest - earliest if earliest is not None else 0.0
                if max(decoded_seconds, timeline_seconds) > max_seconds + 0.1:
                    raise TranscriptionError(f"錄音超過 {max_seconds / 60:g} 分鐘，請先分段再匯入。")
            if frame_count == 0 or decoded_seconds <= 0:
                raise TranscriptionError("錄音沒有可解碼的音訊，請檢查檔案是否損壞。")
            duration = max(decoded_seconds, latest - earliest if earliest is not None else 0.0)
            return {
                "duration": round(duration, 6),
                "sample_rate": sample_rate,
                "channels": channels,
                "format": container.format.name,
            }
    except TranscriptionError:
        raise
    except Exception as error:
        raise TranscriptionError("無法解碼錄音，請檢查檔案是否損壞或使用 WAV、MP3、M4A、FLAC。") from error


class Transcriber:
    def __init__(
        self,
        model_name: str = "large-v3-turbo",
        device: str = "auto",
        compute_type: str = "auto",
        model_dir: str | Path | None = None,
    ) -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device must be auto, cpu or cuda")
        self.model_name = model_name
        self.requested_device = device
        self.requested_compute_type = compute_type
        self.model_dir = str(Path(model_dir).expanduser().resolve()) if model_dir else None
        self.device = "cpu"
        self.compute_type = "int8"
        self.available = importlib.util.find_spec("faster_whisper") is not None
        self.loaded = False
        self.state = "idle" if self.available else "unavailable"
        self.error: str | None = None
        self.fallback_reason: str | None = None
        self._prepare_runtime()

    def _prepare_runtime(self) -> None:
        # CTranslate2 may import the Hub indirectly; set download behaviour
        # before any of those imports initialize their environment constants.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        if not self.available:
            self.error = "未安裝 faster-whisper，請完成後端依賴安裝。"
            return
        _register_cuda_dlls()
        try:
            import ctranslate2
            has_gpu = ctranslate2.get_cuda_device_count() > 0
            has_libraries = _windows_cuda_libraries_available() if has_gpu else False
            if self.requested_device == "cuda" and not (has_gpu and has_libraries):
                self.available = False
                self.state = "unavailable"
                self.error = "CUDA 未就緒：請檢查 NVIDIA 驅動、CUDA 12 cuBLAS 與 cuDNN 9。"
                return
            self.device = "cuda" if self.requested_device != "cpu" and has_gpu and has_libraries else "cpu"
            if self.requested_device == "auto" and self.device == "cpu":
                self.fallback_reason = "未偵測到可用 CUDA／相關函式庫，使用相同模型的 CPU 執行。"
            self.compute_type = (
                "float16" if self.device == "cuda" else "int8"
            ) if self.requested_compute_type == "auto" else self.requested_compute_type
            supported = ctranslate2.get_supported_compute_types(self.device)
            if self.compute_type not in supported:
                self.available = False
                self.state = "unavailable"
                self.error = f"目前裝置不支援精度 {self.compute_type}，請調整 HYA_ASR_COMPUTE_TYPE。"
        except Exception:
            self.available = False
            self.state = "unavailable"
            self.error = "無法初始化 CTranslate2，請檢查轉錄套件及執行環境。"

    def health(self) -> dict[str, Any]:
        # No model download or weight loading when the UI polls health.
        return {
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "available": self.available,
            "loaded": self.loaded and _MODEL is not None and _MODEL_KEY == self._cache_key(),
            "state": self.state,
            "error": self.error,
            "fallback_reason": self.fallback_reason,
            "local_only": True,
            "speaker_identification": False,
        }

    def inspect_audio(self, path: str | Path, max_seconds: float = 20 * 60) -> dict[str, Any]:
        return inspect_audio(path, max_seconds)

    def _cache_key(self) -> tuple[str, str, str, str | None]:
        return (self.model_dir or self.model_name, self.device, self.compute_type, os.environ.get("HF_HOME"))

    def _load_model(self) -> tuple[Any, float]:
        global _MODEL, _MODEL_KEY
        if not self.available:
            raise TranscriptionError(self.error or "轉錄服務未就緒。")
        source = self.model_dir or self.model_name
        key = self._cache_key()
        if _MODEL is not None and _MODEL_KEY == key:
            self.loaded = True
            return _MODEL, 0.0
        self.state = "loading"
        self.loaded = False
        started = time.perf_counter()
        if self.model_dir and not (Path(self.model_dir) / "model.bin").is_file():
            raise TranscriptionError("本地模型目錄缺少 model.bin，請提供 CTranslate2 格式的 Whisper 模型。")
        # Disabled by default for predictable Windows downloads. Users can
        # explicitly override; this never changes the requested model.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from faster_whisper import WhisperModel
        # Keep only one model in this process; release old GPU allocations
        # before another explicitly configured model/device is loaded.
        _MODEL = None
        _MODEL_KEY = None
        model = WhisperModel(
            source,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=max(1, min(8, os.cpu_count() or 4)),
            num_workers=1,
            download_root=os.environ.get("HYA_MODEL_CACHE") or None,
            local_files_only=os.environ.get("HYA_ASR_OFFLINE", "").lower() in {"1", "true", "yes"},
        )
        _MODEL, _MODEL_KEY = model, key
        self.loaded = True
        return model, time.perf_counter() - started

    def transcribe(self, path: str | Path, language: str = "en") -> dict[str, Any]:
        if language not in {"en", "auto"}:
            raise TranscriptionError("本 MVP 支援英語或自動語言偵測，請選擇 en 或 auto。")
        audio = self.inspect_audio(path)
        requested = time.perf_counter()
        self.state = "queued"
        with _JOB_LOCK:
            queue_seconds = time.perf_counter() - requested
            began = time.perf_counter()
            try:
                model, load_seconds = self._load_model()
                self.state = "transcribing"
                inference_start = time.perf_counter()
                iterator, info = model.transcribe(
                    str(path),
                    language=None if language == "auto" else language,
                    task="transcribe",
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                segments = []
                for segment in iterator:
                    text = segment.text.strip()
                    if not text:
                        continue
                    start, end = float(segment.start), float(segment.end)
                    if not math.isfinite(start) or not math.isfinite(end):
                        raise TranscriptionError("轉錄時間戳無效，請重試。")
                    segments.append({
                        "start": round(max(0.0, start), 3),
                        "end": round(max(start, end), 3),
                        "text": text,
                        "speaker": "unknown",
                        "words": [{
                            "start": round(float(word.start), 3),
                            "end": round(float(word.end), 3),
                            "word": word.word,
                            "probability": round(float(word.probability), 6),
                        } for word in (segment.words or [])],
                    })
                inference_seconds = time.perf_counter() - inference_start
                elapsed = time.perf_counter() - began
                duration = float(info.duration or audio["duration"])
                self.state = "ready"
                self.error = None
                return {
                    "segments": segments,
                    "language": info.language,
                    "duration": duration,
                    "metrics": {
                        "model": self.model_name,
                        "device": self.device,
                        "compute_type": self.compute_type,
                        "elapsed_seconds": round(elapsed, 3),
                        "queue_seconds": round(queue_seconds, 3),
                        "model_load_seconds": round(load_seconds, 3),
                        "inference_seconds": round(inference_seconds, 3),
                        "rtfx": round(duration / inference_seconds, 3) if inference_seconds else None,
                        "duration_after_vad": float(info.duration_after_vad),
                        "language_probability": float(info.language_probability),
                        "vad_filter": True,
                        "beam_size": 5,
                        "speaker_identification": False,
                        "fallback_reason": self.fallback_reason,
                    },
                }
            except TranscriptionError as error:
                self.state = "error"
                self.error = str(error)
                raise
            except Exception as error:
                self.state = "error"
                self.error = (
                    "轉錄失敗，請檢查本地模型／網絡下載、磁碟空間及 CUDA 環境後重試；"
                    "系統沒有改用其他模型。"
                )
                raise TranscriptionError(self.error) from error
