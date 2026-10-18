from __future__ import annotations

import base64
import copy
import sys
from types import SimpleNamespace
import wave

import pytest

from hya import cloud_asr
from hya.asr import TranscriptionError


def make_wave(path, seconds=5.0, channels=1):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * int(16000 * channels * 2 * seconds))
    return path


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Client:
    def __init__(self, results):
        self.results = iter(results)
        self.created = []
        self.polled = []

    def CreateRecTask(self, request):
        self.created.append(request)
        return SimpleNamespace(Data=SimpleNamespace(TaskId=123))

    def DescribeTaskStatus(self, request):
        self.polled.append(request.TaskId)
        return SimpleNamespace(Data=next(self.results))


@pytest.fixture
def configured(monkeypatch):
    for key in list(cloud_asr.os.environ):
        if key.startswith("HYA_TENCENT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HYA_TENCENT_SECRET_ID", "test-only-id")
    monkeypatch.setenv("HYA_TENCENT_SECRET_KEY", "test-only-secret")
    return monkeypatch


def adapter(client):
    clock = Clock()
    models = SimpleNamespace(CreateRecTaskRequest=SimpleNamespace, DescribeTaskStatusRequest=SimpleNamespace)
    return cloud_asr.TencentTranscriber(client=client, models=models, clock=clock, sleep=clock.sleep)


def success():
    return {
        "Status": 2, "AudioDuration": 5.0, "Result": "Hello there.",
        "ResultDetail": [{
            "FinalSentence": " Hello there. ", "StartMs": 3200, "EndMs": 4100,
            "SpeakerId": 0,
            "Words": [{"Word": "Hello", "OffsetStartMs": 0, "OffsetEndMs": 300},
                      {"Word": "there", "OffsetStartMs": 400, "OffsetEndMs": 900}],
        }],
    }


def test_cloud_asr_missing_configuration_never_imports_whisper_or_calls_provider(configured, tmp_path, monkeypatch):
    configured.delenv("HYA_TENCENT_SECRET_ID")
    client = Client([])
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    transcriber = adapter(client)
    health = transcriber.health()
    assert health["configured"] is False
    assert health["available"] is False
    assert health["local_only"] is False
    assert health["speaker_identification"] is False
    assert "test-only-secret" not in str(health)
    with pytest.raises(TranscriptionError, match="not configured"):
        transcriber.transcribe(tmp_path / "missing.wav")
    assert client.created == []


def test_cloud_asr_success_preserves_audio_offsets_and_anonymous_speakers(configured, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    client = Client([{"Status": 0}, {"Status": 1}, success()])
    transcriber = adapter(client)
    audio = make_wave(tmp_path / "audio.wav")
    original = audio.read_bytes()
    result = transcriber.transcribe(audio)
    assert audio.read_bytes() == original
    request = client.created[0]
    assert request.EngineModelType == "16k_zh_en_meeting"
    assert request.SourceType == 1
    assert request.ResTextFormat == 1
    assert request.ChannelNum == 1
    assert request.SpeakerDiarization == 1
    assert request.SpeakerNumber == 0
    assert request.DataLen == len(original)
    assert base64.b64decode(request.Data) == original
    assert client.polled == [123, 123, 123]
    segment = result["segments"][0]
    assert segment == {
        "start": 3.2, "end": 4.1, "text": "Hello there.", "speaker": "speaker_0",
        "words": [{"start": 3.2, "end": 3.5, "word": "Hello"},
                  {"start": 3.6, "end": 4.1, "word": "there"}],
    }
    assert result["duration"] == 5
    assert result["language"] == "en"
    assert result["metrics"]["speaker_identification"] is False
    assert result["metrics"]["speaker_label_scope"] == "recording"
    assert transcriber.health()["state"] == "ready"


@pytest.mark.parametrize("field,value", [
    ("StartMs", float("nan")), ("EndMs", float("inf")), ("StartMs", -1),
    ("EndMs", 100), ("EndMs", 6000), ("StartMs", "3200"), ("StartMs", True),
])
def test_cloud_asr_rejects_invalid_sentence_timestamps(configured, tmp_path, field, value):
    response = success()
    response["ResultDetail"][0][field] = value
    transcriber = adapter(Client([response]))
    with pytest.raises(TranscriptionError, match="invalid transcript"):
        transcriber.transcribe(make_wave(tmp_path / "audio.wav"))
    assert transcriber.health()["state"] == "error"


@pytest.mark.parametrize("field,value", [
    ("OffsetStartMs", -1), ("OffsetEndMs", float("nan")),
    ("OffsetEndMs", float("inf")), ("OffsetEndMs", 1200),
    ("OffsetStartMs", 400), ("Word", None),
])
def test_cloud_asr_rejects_invalid_word_offsets(configured, tmp_path, field, value):
    response = success()
    response["ResultDetail"][0]["Words"][0][field] = value
    with pytest.raises(TranscriptionError, match="invalid transcript"):
        adapter(Client([response])).transcribe(make_wave(tmp_path / "audio.wav"))


def test_cloud_asr_oversized_file_rejected_before_decode_or_provider(configured, tmp_path, monkeypatch):
    audio = tmp_path / "large.wav"
    with audio.open("wb") as handle:
        handle.truncate(cloud_asr.MAX_INLINE_BYTES + 1)
    monkeypatch.setattr(cloud_asr, "inspect_audio", lambda *args, **kwargs: pytest.fail("must bound size before decoding"))
    client = Client([])
    with pytest.raises(TranscriptionError, match="5 MB"):
        adapter(client).transcribe(audio)
    assert client.created == []


def test_cloud_asr_rejects_stereo_before_provider(configured, tmp_path):
    client = Client([])
    with pytest.raises(TranscriptionError, match="mono audio"):
        adapter(client).transcribe(make_wave(tmp_path / "stereo.wav", channels=2))
    assert client.created == []


def test_cloud_asr_does_not_publish_provider_failure_text(configured, tmp_path):
    client = Client([{"Status": 3, "ErrorMsg": "PRIVATE test-only-secret audio contents"}])
    transcriber = adapter(client)
    with pytest.raises(TranscriptionError, match="could not recognize") as caught:
        transcriber.transcribe(make_wave(tmp_path / "audio.wav"))
    assert "PRIVATE" not in str(caught.value)
    assert "test-only-secret" not in str(transcriber.health())


@pytest.mark.parametrize("stage", ["create", "poll"])
def test_cloud_asr_sanitizes_sdk_exceptions_without_cause_or_model_fallback(configured, tmp_path, stage):
    client = Client([])
    class PrivateError(Exception):
        def get_code(self):
            return "AuthFailure.SignatureFailure"
    def fail(request):
        raise PrivateError("test-only-secret request audio content")
    if stage == "create":
        client.CreateRecTask = fail
    else:
        client.DescribeTaskStatus = fail
    transcriber = adapter(client)
    with pytest.raises(TranscriptionError, match="authentication failed") as caught:
        transcriber.transcribe(make_wave(tmp_path / "audio.wav"))
    assert caught.value.__suppress_context__
    assert caught.value.__cause__ is None
    assert "test-only-secret" not in str(transcriber.health())
    assert transcriber.health()["model"] == "16k_zh_en_meeting"


def test_cloud_asr_polling_has_bounded_deadline(configured, tmp_path):
    configured.setenv("HYA_TENCENT_TIMEOUT_SECONDS", "1")
    client = Client([{"Status": 1}] * 20)
    transcriber = adapter(client)
    with pytest.raises(TranscriptionError, match="timed out"):
        transcriber.transcribe(make_wave(tmp_path / "audio.wav"))
    assert len(client.created) == 1
    assert len(client.polled) == 1
    assert transcriber.health()["state"] == "error"


def test_cloud_asr_missing_details_never_fabricates_timestamps(configured, tmp_path):
    with pytest.raises(TranscriptionError, match="invalid transcript"):
        adapter(Client([{"Status": 2, "Result": "No detailed timing"}])).transcribe(make_wave(tmp_path / "audio.wav"))


def test_cloud_asr_silence_returns_no_invented_segments(configured, tmp_path):
    result = adapter(Client([{"Status": 2, "Result": "", "ResultDetail": None}])).transcribe(make_wave(tmp_path / "audio.wav"))
    assert result["segments"] == []


def test_cloud_asr_unknown_speaker_never_becomes_user(configured, tmp_path):
    response = copy.deepcopy(success())
    response["ResultDetail"][0]["SpeakerId"] = "user"
    result = adapter(Client([response])).transcribe(make_wave(tmp_path / "audio.wav"))
    assert result["segments"][0]["speaker"] == "unknown"


def test_cloud_asr_health_detects_missing_sdk_without_importing_it(configured, monkeypatch):
    def missing_package(name):
        raise cloud_asr.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(cloud_asr.importlib.metadata, "version", missing_package)
    transcriber = cloud_asr.TencentTranscriber()
    assert transcriber.health()["configured"] is True
    assert transcriber.health()["available"] is False
    assert "SDK is not installed" in transcriber.health()["error"]


@pytest.mark.parametrize("status", [True, "2", None, 4])
def test_cloud_asr_rejects_invalid_task_status(configured, tmp_path, status):
    with pytest.raises(TranscriptionError, match="invalid transcript"):
        adapter(Client([{"Status": status}])).transcribe(make_wave(tmp_path / "audio.wav"))


def test_cloud_asr_uses_decoded_duration_instead_of_untrusted_provider_duration(configured, tmp_path):
    response = success()
    response["AudioDuration"] = float("nan")
    result = adapter(Client([response])).transcribe(make_wave(tmp_path / "audio.wav"))
    assert result["duration"] == 5


def test_cloud_asr_unsupported_engine_and_nonfinite_timeout_rejected(configured):
    configured.setenv("HYA_TENCENT_ENGINE", "16k_multi_lang")
    transcriber = adapter(Client([]))
    assert transcriber.health()["available"] is False
    assert "does not support" in transcriber.health()["error"]
    configured.setenv("HYA_TENCENT_TIMEOUT_SECONDS", "NaN")
    with pytest.raises(ValueError, match="finite number"):
        adapter(Client([]))
