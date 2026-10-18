"""Tencent recording ASR, with anonymous diarization and original time offsets.

Only audio validation is shared with ``asr``; Whisper is never imported or
loaded here. Audio is sent unchanged to Tencent's CreateRecTask API. The inline
API accepts at most 5 MiB, and the selected 16k engines require mono audio.

API contracts:
https://cloud.tencent.com/document/api/1093/37823
https://cloud.tencent.com/document/api/1093/37822
https://cloud.tencent.com/document/api/1093/37824#SentenceWords
"""

from __future__ import annotations

import base64
import importlib.metadata
import math
import os
from pathlib import Path
import time
from typing import Any, Callable

from .asr import TranscriptionError, inspect_audio

MAX_INLINE_BYTES = 5 * 1024 * 1024
DEFAULT_ENGINE = "16k_zh_en_meeting"
# These documented engines support both detailed word timing and diarization.
SUPPORTED_ENGINES = {"16k_zh_en_meeting", "16k_zh_en_2.0", "16k_zh_en", "16k_en"}
_INVALID_RESULT = "Tencent Cloud returned invalid transcript details or timestamps; please retry."


def _setting_number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number from {minimum:g} to {maximum:g}") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a finite number from {minimum:g} to {maximum:g}")
    return value


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _seconds(value: Any) -> float:
    # Do not coerce missing fields, numeric strings, booleans or NaN to timing.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptionError(_INVALID_RESULT)
    try:
        result = float(value) / 1000
    except (OverflowError, ValueError):
        raise TranscriptionError(_INVALID_RESULT) from None
    if not math.isfinite(result) or result < 0:
        raise TranscriptionError(_INVALID_RESULT)
    return result


def _provider_error(error: Exception) -> str:
    # Never expose SDK messages: they can include request content or credentials.
    try:
        code = error.get_code() if hasattr(error, "get_code") else ""
    except Exception:
        code = ""
    if not isinstance(code, str):
        code = ""
    if code.startswith(("AuthFailure", "UnauthorizedOperation")):
        return "Tencent Cloud ASR authentication failed; check the server credentials and permissions."
    if code.startswith(("LimitExceeded", "RequestLimitExceeded")):
        return "Tencent Cloud ASR is rate-limited or its quota is exhausted; check the account, then retry."
    if code.startswith("FailedOperation.ServiceNotOpened"):
        return "Tencent Cloud ASR is not enabled for this account; enable the service before retrying."
    if code.startswith(("InvalidParameter", "MissingParameter")):
        return "Tencent Cloud ASR rejected the request; check the configured engine and audio format."
    return "Tencent Cloud ASR could not complete the request; check the service and network, then retry."


class TencentTranscriber:
    """Synchronous adapter over Tencent's bounded asynchronous recording API.

    Optional client/models/clock/sleep injection supports offline contract tests.
    Configuration comes only from server environment variables, never the UI.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        models: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secret_id = os.environ.get("HYA_TENCENT_SECRET_ID", "").strip()
        self._secret_key = os.environ.get("HYA_TENCENT_SECRET_KEY", "").strip()
        self._token = os.environ.get("HYA_TENCENT_TOKEN", "").strip() or None
        self.region = os.environ.get("HYA_TENCENT_REGION", "ap-guangzhou").strip()
        self.model_name = os.environ.get("HYA_TENCENT_ENGINE", DEFAULT_ENGINE).strip()
        self.timeout_seconds = _setting_number("HYA_TENCENT_TIMEOUT_SECONDS", 300, 1, 3600)
        self.poll_seconds = _setting_number("HYA_TENCENT_POLL_SECONDS", 2, 0.1, 30)
        self._client, self._models = client, models
        self._clock, self._sleep = clock, sleep
        self._http_profile: Any = None
        self.configured = bool(self._secret_id and self._secret_key and self.region)
        self._sdk_available = client is not None and models is not None
        if not self._sdk_available:
            # Package metadata avoids importing any SDK modules during health
            # checks and does not mistake an unrelated Tencent SDK for ASR.
            for distribution in ("tencentcloud-sdk-python-asr", "tencentcloud-sdk-python"):
                try:
                    importlib.metadata.version(distribution)
                    self._sdk_available = True
                    break
                except importlib.metadata.PackageNotFoundError:
                    pass
        self.available = self.configured and self._sdk_available and self.model_name in SUPPORTED_ENGINES
        self.state = "idle" if self.available else "unavailable"
        self.error = self._configuration_error()

    def _configuration_error(self) -> str | None:
        if not self.configured:
            return "Tencent Cloud ASR is not configured; set the server SecretId and SecretKey."
        if self.model_name not in SUPPORTED_ENGINES:
            return "The configured Tencent ASR engine does not support this demo's timing and diarization contract."
        if not self._sdk_available:
            return "The Tencent Cloud ASR SDK is not installed; install the backend dependencies."
        return None

    def health(self) -> dict[str, Any]:
        # No authenticated request, audio upload, model load or SDK import.
        return {
            "provider": "tencent",
            "model": self.model_name,
            "device": "cloud",
            "configured": self.configured,
            "available": self.available,
            "loaded": False,
            "state": self.state,
            "error": self.error,
            "local_only": False,
            "external": True,
            "speaker_diarization": True,
            "speaker_identification": False,
            "speaker_label_scope": "recording",
            "max_upload_bytes": MAX_INLINE_BYTES,
            "max_audio_seconds": 1200,
            "required_channels": 1,
            "fallback_reason": None,
        }

    def inspect_audio(self, path: str | Path, max_seconds: float = 1200) -> dict[str, Any]:
        target = Path(path)
        if target.is_file() and target.stat().st_size > MAX_INLINE_BYTES:
            raise TranscriptionError("Tencent Cloud inline ASR accepts recordings up to 5 MB; split or compress this file first.")
        audio = inspect_audio(target, max_seconds=max_seconds)
        if audio.get("channels") != 1:
            raise TranscriptionError("Tencent Cloud ASR requires mono audio; export a mono recording and import it again.")
        return audio

    def _sdk(self) -> tuple[Any, Any]:
        if self._client is None or self._models is None:
            try:
                from tencentcloud.common import credential
                from tencentcloud.common.profile.client_profile import ClientProfile
                from tencentcloud.common.profile.http_profile import HttpProfile
                from tencentcloud.asr.v20190614 import asr_client, models
            except ImportError:
                raise TranscriptionError("The Tencent Cloud ASR SDK is not installed; install the backend dependencies.") from None
            credentials = credential.Credential(self._secret_id, self._secret_key, self._token)
            self._http_profile = HttpProfile()
            self._http_profile.endpoint = "asr.tencentcloudapi.com"
            self._http_profile.reqTimeout = min(30, self.timeout_seconds)
            profile = ClientProfile(httpProfile=self._http_profile)
            self._client = asr_client.AsrClient(credentials, self.region, profile)
            self._models = models
        return self._client, self._models

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise TranscriptionError(
                "Tencent Cloud ASR timed out while waiting for the result; the remote task may still be processing."
            )
        if self._http_profile is not None:
            self._http_profile.reqTimeout = min(30, remaining)
        return remaining

    def _segments(self, data: Any, duration: float) -> list[dict[str, Any]]:
        details = _field(data, "ResultDetail")
        if details is None and not _field(data, "Result"):
            details = []  # Successful silence may have no sentence details.
        if not isinstance(details, list):
            raise TranscriptionError(_INVALID_RESULT)
        if not details and _field(data, "Result"):
            # Do not invent timestamps by parsing Tencent's plain text result.
            raise TranscriptionError(_INVALID_RESULT)
        segments = []
        for sentence in details:
            text = _field(sentence, "FinalSentence")
            start = _seconds(_field(sentence, "StartMs"))
            end = _seconds(_field(sentence, "EndMs"))
            if not isinstance(text, str) or end < start or end > duration + 0.001:
                raise TranscriptionError(_INVALID_RESULT)
            raw_words = _field(sentence, "Words")
            if raw_words is None:
                raw_words = []
            if not isinstance(raw_words, list):
                raise TranscriptionError(_INVALID_RESULT)
            words = []
            for word in raw_words:
                # Tencent word offsets are relative to the sentence start.
                word_start = start + _seconds(_field(word, "OffsetStartMs"))
                word_end = start + _seconds(_field(word, "OffsetEndMs"))
                content = _field(word, "Word")
                if not isinstance(content, str) or word_end < word_start or word_end > end + 0.001:
                    raise TranscriptionError(_INVALID_RESULT)
                words.append({"start": round(word_start, 3), "end": round(word_end, 3), "word": content})
            speaker_id = _field(sentence, "SpeakerId")
            speaker = (
                f"speaker_{speaker_id}"
                if isinstance(speaker_id, int) and not isinstance(speaker_id, bool) and 0 <= speaker_id <= 1000
                else "unknown"
            )
            if text.strip():
                segments.append({
                    "start": round(start, 3), "end": round(end, 3),
                    "text": text.strip(), "speaker": speaker, "words": words,
                })
        return segments

    def transcribe(self, path: str | Path, language: str = "en") -> dict[str, Any]:
        if language not in {"en", "auto"}:
            raise TranscriptionError("This demo accepts English or automatic language selection.")
        if not self.available:
            raise TranscriptionError(self.error or "Tencent Cloud ASR is unavailable.")
        started = self._clock()
        self.state = "transcribing"
        try:
            audio = self.inspect_audio(path)
            duration = float(audio["duration"])
            if not math.isfinite(duration) or duration <= 0:
                raise TranscriptionError(_INVALID_RESULT)
            # Bound the read again in case the local file changed after inspection.
            with Path(path).open("rb") as handle:
                payload = handle.read(MAX_INLINE_BYTES + 1)
            if len(payload) > MAX_INLINE_BYTES:
                raise TranscriptionError("Tencent Cloud inline ASR accepts recordings up to 5 MB; split or compress this file first.")
            if not payload:
                raise TranscriptionError("The recording file is empty; import a valid recording.")
            client, models = self._sdk()
            deadline = self._clock() + self.timeout_seconds
            request = models.CreateRecTaskRequest()
            request.EngineModelType = self.model_name
            request.ChannelNum = 1
            request.ResTextFormat = 1
            request.SourceType = 1
            request.Data = base64.b64encode(payload).decode("ascii")
            request.DataLen = len(payload)
            request.SpeakerDiarization = 1
            request.SpeakerNumber = 0
            # Keep raw phrasing; do not request emotion inference or rewriting.
            request.FilterDirty = 0
            request.FilterModal = 0
            request.FilterPunc = 0
            request.ConvertNumMode = 0
            self._remaining(deadline)
            created = client.CreateRecTask(request)
            task_id = _field(_field(created, "Data"), "TaskId")
            if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
                raise TranscriptionError(_INVALID_RESULT)
            query = models.DescribeTaskStatusRequest()
            query.TaskId = task_id
            while True:
                self._remaining(deadline)
                response = client.DescribeTaskStatus(query)
                self._remaining(deadline)
                data = _field(response, "Data")
                status = _field(data, "Status")
                if isinstance(status, bool) or not isinstance(status, int) or status not in {0, 1, 2, 3}:
                    raise TranscriptionError(_INVALID_RESULT)
                if status == 3:
                    raise TranscriptionError("Tencent Cloud ASR could not recognize this recording; check the audio and account, then retry.")
                if status == 2:
                    segments = self._segments(data, duration)
                    break
                self._sleep(min(self.poll_seconds, self._remaining(deadline)))
            elapsed = self._clock() - started
            self.state, self.error = "ready", None
            return {
                "segments": segments,
                # The task API does not report a detected language. Preserve
                # the request rather than fabricate language confidence.
                "language": language,
                "duration": duration,
                "metrics": {
                    "provider": "tencent", "model": self.model_name, "device": "cloud",
                    "elapsed_seconds": round(elapsed, 3), "audio_processing": "cloud",
                    "speaker_diarization": True, "speaker_identification": False,
                    "speaker_label_scope": "recording", "fallback_reason": None,
                },
            }
        except TranscriptionError as error:
            self.state, self.error = "error", str(error)
            raise
        except Exception as error:
            self.state, self.error = "error", _provider_error(error)
            raise TranscriptionError(self.error) from None
