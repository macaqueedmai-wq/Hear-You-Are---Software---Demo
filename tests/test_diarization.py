from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from hya import diarization
from hya.diarization import DiarizationError, LocalDiarizer


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for name in list(diarization.os.environ):
        if name.startswith("HYA_DIARIZATION_"):
            monkeypatch.delenv(name)
    for name in ("SEGMENTATION", "EMBEDDING"):
        path = tmp_path / f"{name.lower()}.onnx"
        path.write_bytes(b"offline-test-model")
        monkeypatch.setenv(f"HYA_DIARIZATION_{name}_PATH", str(path))
    monkeypatch.setattr(diarization, "_dependencies_available", lambda: True)


class Backend:
    sample_rate = 16000

    def __init__(self, values=(), error=None):
        self.values, self.error = values, error
        self.closed = False
        self.inputs = []

    def process(self, samples):
        self.inputs.append(samples)
        if self.error:
            raise self.error
        return SimpleNamespace(sort_by_start_time=lambda: self.values)

    def close(self):
        self.closed = True


def segment(start=0.1, end=0.6, speaker=0):
    return SimpleNamespace(start=start, end=end, speaker=speaker)


def test_health_is_lazy_and_paths_or_environment_secrets_are_not_exposed(configured, monkeypatch):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "sherpa_onnx":
            raise AssertionError("health imported inference runtime")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-leak")
    factory = lambda **kwargs: pytest.fail("health created a backend")
    health = LocalDiarizer(factory=factory).health()
    assert health["available"] is True
    assert health["configured"] is True
    assert health["local_only"] is True
    assert health["speaker_identification"] is False
    assert health["speaker_label_scope"] == "recording"
    assert "do-not-leak" not in str(health)
    assert ".onnx" not in str(health)


def test_default_paths_are_repository_local(monkeypatch, tmp_path):
    for name in list(diarization.os.environ):
        if name.startswith("HYA_DIARIZATION_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(diarization, "ROOT", tmp_path)
    adapter = LocalDiarizer()
    assert adapter.segmentation_path == tmp_path / "models/diarization/segmentation.onnx"
    assert adapter.embedding_path == tmp_path / "models/diarization/embedding.onnx"


def test_missing_files_and_dependencies_fail_before_factory(configured, monkeypatch):
    adapter = LocalDiarizer(factory=lambda **kwargs: pytest.fail("factory called"))
    adapter.embedding_path.unlink()
    assert adapter.health()["configured"] is False
    with pytest.raises(DiarizationError, match="missing or empty"):
        adapter.diarize(np.zeros(16000))
    adapter.embedding_path.write_bytes(b"test")
    monkeypatch.setattr(diarization, "_dependencies_available", lambda: False)
    assert adapter.health()["configured"] is True
    assert adapter.health()["available"] is False
    with pytest.raises(DiarizationError, match="Install sherpa-onnx"):
        adapter.diarize(np.zeros(16000))


def test_empty_model_file_fails_health_and_ready_file_is_discovered(configured):
    adapter = LocalDiarizer()
    adapter.embedding_path.write_bytes(b"")
    assert adapter.health()["models_cached"] is False
    adapter.embedding_path.write_bytes(b"model-now-ready")
    assert adapter.health()["available"] is True


@pytest.mark.parametrize(("name", "value"), [
    ("THRESHOLD", "nan"), ("THRESHOLD", "inf"), ("THRESHOLD", "-0.1"),
    ("THRESHOLD", "private-invalid-value"), ("NUM_THREADS", "0"),
    ("NUM_THREADS", "33"), ("NUM_THREADS", "1.5"), ("NUM_THREADS", "nan"),
])
def test_invalid_settings_are_curated(configured, monkeypatch, name, value):
    monkeypatch.setenv(f"HYA_DIARIZATION_{name}", value)
    adapter = LocalDiarizer()
    assert adapter.health()["available"] is False
    assert adapter.health()["error"].startswith("Invalid local")
    assert "private-invalid-value" not in str(adapter.health())


def test_real_intervals_are_sorted_without_clipping_and_overlap_is_preserved(configured, monkeypatch):
    backend = Backend([segment(0.4, 0.9, 7), segment(0.1, 0.7, 2)])
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return backend
    monkeypatch.setenv("HYA_DIARIZATION_THRESHOLD", "0.75")
    monkeypatch.setenv("HYA_DIARIZATION_NUM_THREADS", "3")
    adapter = LocalDiarizer(factory=factory)
    assert adapter.diarize(np.zeros(16000, dtype=np.float64)) == [
        {"start": 0.1, "end": 0.7, "speaker": "speaker_2"},
        {"start": 0.4, "end": 0.9, "speaker": "speaker_7"},
    ]
    assert backend.inputs[0].dtype == np.float32
    assert backend.closed
    assert calls == [{"segmentation_path": adapter.segmentation_path,
                      "embedding_path": adapter.embedding_path,
                      "threshold": 0.75, "num_threads": 3}]


@pytest.mark.parametrize("values", [[], None, np.zeros((2, 20)), [np.nan], [np.inf],
                                   [True, False], ["1.0"], np.array([1e300])])
def test_invalid_audio_never_loads_a_model(configured, values):
    adapter = LocalDiarizer(factory=lambda **kwargs: pytest.fail("factory called"))
    with pytest.raises(DiarizationError):
        adapter.diarize(values)


@pytest.mark.parametrize("rate", [8000, 16000.0, True, "16000", float("nan")])
def test_wrong_sample_rate_fails_before_loading(configured, rate):
    adapter = LocalDiarizer(factory=lambda **kwargs: pytest.fail("factory called"))
    with pytest.raises(DiarizationError, match="16 kHz"):
        adapter.diarize(np.zeros(16000), rate)


@pytest.mark.parametrize("value", [segment(-0.1, 0.5), segment(0.5, 0.1),
                                   segment(0.2, 0.2), segment(0.1, 1.00001),
                                   segment(float("nan"), 0.5), segment(0.1, float("inf")),
                                   segment(True, 0.5), segment(0.1, 0.5, -1),
                                   segment(0.1, 0.5, True), segment(0.1, 0.5, 1.0),
                                   {"start": 0.1, "end": 0.5}, {}])
def test_invalid_model_intervals_fail_closed_and_release_backend(configured, value):
    backend = Backend([value])
    adapter = LocalDiarizer(factory=lambda **kwargs: backend)
    with pytest.raises(DiarizationError, match="invalid time intervals"):
        adapter.diarize(np.zeros(16000))
    assert backend.closed


def test_runtime_exception_is_curated_and_backend_released(configured):
    backend = Backend(error=RuntimeError("SECRET raw provider body and local path"))
    adapter = LocalDiarizer(factory=lambda **kwargs: backend)
    with pytest.raises(DiarizationError, match="Local speaker diarization failed") as error:
        adapter.diarize(np.zeros(16000))
    assert "SECRET" not in str(error.value)
    assert "SECRET" not in str(adapter.health())
    assert backend.closed


def test_cleanup_failure_is_curated(configured):
    class BadCleanupBackend(Backend):
        def close(self):
            raise RuntimeError("SECRET backend path")
    adapter = LocalDiarizer(factory=lambda **kwargs: BadCleanupBackend([]))
    with pytest.raises(DiarizationError, match="could not be released") as error:
        adapter.diarize(np.zeros(16000))
    assert "SECRET" not in str(error.value)
    assert "SECRET" not in str(adapter.health())


def test_model_rate_mismatch_and_empty_no_speech_output(configured):
    backend = Backend([])
    backend.sample_rate = 8000
    adapter = LocalDiarizer(factory=lambda **kwargs: backend)
    with pytest.raises(DiarizationError, match="different audio sample rate"):
        adapter.diarize(np.zeros(16000))
    assert backend.closed
    backend.sample_rate = 16000
    assert adapter.diarize(np.zeros(16000)) == []
    assert adapter.health()["error"] is None


def test_native_factory_uses_cpu_official_config_and_no_downloads(configured, monkeypatch):
    configs = {}
    class Config(SimpleNamespace):
        def validate(self):
            return True
    def config_type(name):
        def create(**kwargs):
            configs[name] = kwargs
            return Config(**kwargs)
        return create
    backend = Backend([segment()])
    fake_sherpa = SimpleNamespace(**{
        name: config_type(name) for name in (
            "OfflineSpeakerDiarizationConfig", "OfflineSpeakerSegmentationModelConfig",
            "OfflineSpeakerSegmentationPyannoteModelConfig", "SpeakerEmbeddingExtractorConfig",
            "FastClusteringConfig",
        )
    }, OfflineSpeakerDiarization=lambda config: backend)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_sherpa)
    assert LocalDiarizer().diarize(np.zeros(16000))[0]["speaker"] == "speaker_0"
    assert configs["OfflineSpeakerSegmentationModelConfig"]["provider"] == "cpu"
    assert configs["SpeakerEmbeddingExtractorConfig"]["provider"] == "cpu"
    assert configs["FastClusteringConfig"] == {"num_clusters": -1, "threshold": 0.5}
    assert configs["OfflineSpeakerDiarizationConfig"]["min_duration_on"] == 0.3
    assert configs["OfflineSpeakerDiarizationConfig"]["min_duration_off"] == 0.5
