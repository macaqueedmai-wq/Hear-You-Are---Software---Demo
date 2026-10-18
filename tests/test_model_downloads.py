"""Offline model-download boundary checks; no real models, secrets or network."""
from __future__ import annotations

from contextlib import contextmanager
import builtins
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile

import httpx
import pytest


@pytest.fixture
def helper(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/download_qwen_models.py"
    spec = importlib.util.spec_from_file_location("synthetic_model_download_helper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def deny_network(*args, **kwargs):
        raise AssertionError("Model-download tests must not contact a provider or model host")

    monkeypatch.setattr(module.httpx, "stream", deny_network)
    monkeypatch.setattr(module, "snapshot_download", deny_network)
    return module


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def stream_bytes(payload):
    @contextmanager
    def response(*args, **kwargs):
        value = httpx.Response(200, content=payload,
            request=httpx.Request("GET", "https://synthetic.invalid/public-model"))
        try:
            yield value
        finally:
            value.close()
    return response


def tar_member(name, payload=b"", *, kind=tarfile.REGTYPE, linkname=""):
    member = tarfile.TarInfo(name)
    member.type, member.linkname = kind, linkname
    member.size = len(payload) if kind == tarfile.REGTYPE else 0
    return member, payload


def tar_bytes(*members):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:bz2") as archive:
        for member, payload in members:
            archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)
    return result.getvalue()


def fake_assets(helper, monkeypatch, archive, model=b"synthetic-segmentation", license_bytes=b"synthetic-license"):
    requested = []
    monkeypatch.setattr(helper, "SEGMENTATION_SHA256", sha256(model))
    monkeypatch.setattr(helper, "SEGMENTATION_LICENSE_SHA256", sha256(license_bytes))

    def download(url, destination, *args, **kwargs):
        requested.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url == helper.SEGMENTATION_URL:
            destination.write_bytes(archive)
        elif url == helper.EMBEDDING_URL:
            destination.write_bytes(b"synthetic-embedding")
        elif url.endswith("/LICENSE"):
            destination.write_bytes(b"synthetic-license")
        else:
            raise AssertionError("Unexpected asset request")

    monkeypatch.setattr(helper, "download", download)
    return requested


def test_hash_mismatch_does_not_publish_bytes_or_destroy_previous_file(helper, monkeypatch, tmp_path):
    destination = tmp_path / "embedding.onnx"
    destination.write_bytes(b"previous-local-file")
    monkeypatch.setattr(helper.httpx, "stream", stream_bytes(b"wrong-download"))

    with pytest.raises(RuntimeError, match="pinned SHA256"):
        helper.download("https://synthetic.invalid/model", destination, sha256(b"expected-model"))

    assert destination.read_bytes() == b"previous-local-file"
    assert not destination.with_suffix(".onnx.download").exists()


def test_oversized_download_is_removed_before_publication(helper, monkeypatch, tmp_path):
    destination = tmp_path / "segmentation.tar.bz2"
    monkeypatch.setattr(helper.httpx, "stream", stream_bytes(b"too-many-bytes"))

    with pytest.raises(RuntimeError, match="download limit"):
        helper.download("https://synthetic.invalid/archive", destination, max_bytes=4)

    assert not destination.exists()
    assert not destination.with_suffix(".bz2.download").exists()


def test_cached_segmentation_without_license_restores_only_allowed_archive_files(helper, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    outside = tmp_path / "must-stay-unchanged"
    outside.write_bytes(b"original-sentinel")
    model = b"synthetic-segmentation"
    prefix = "sherpa-onnx-pyannote-segmentation-3-0/"
    archive = tar_bytes(
        tar_member(prefix + "model.onnx", model),
        tar_member(prefix + "LICENSE", b"synthetic-MIT-license"),
        tar_member("../must-stay-unchanged", b"traversal-must-not-be-written"),
        tar_member(str(outside), b"absolute-path-must-not-be-written"),
        tar_member("unexpected-link", kind=tarfile.SYMTYPE, linkname=str(outside)),
    )
    requested = fake_assets(helper, monkeypatch, archive, model, b"synthetic-MIT-license")
    directory = workspace / "speaker-models"
    directory.mkdir()
    (directory / "segmentation.onnx").write_bytes(model)

    helper.download_diarization(directory)

    assert helper.SEGMENTATION_URL in requested
    assert (directory / "segmentation.onnx").read_bytes() == model
    assert (directory / "SEGMENTATION-LICENSE").read_bytes() == b"synthetic-MIT-license"
    assert outside.read_bytes() == b"original-sentinel"
    assert not (workspace / "unexpected-link").exists()
    assert not (directory / "unexpected-link").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_exact_model_member_must_not_be_an_archive_link(helper, monkeypatch, tmp_path, kind):
    outside = tmp_path / "outside-model"
    outside.write_bytes(b"outside-model-must-not-be-read")
    prefix = "sherpa-onnx-pyannote-segmentation-3-0/"
    archive = tar_bytes(
        tar_member(prefix + "model.onnx", kind=kind, linkname=str(outside)),
        tar_member(prefix + "LICENSE", b"synthetic-license"),
    )
    fake_assets(helper, monkeypatch, archive)
    directory = tmp_path / "speaker-models"

    with pytest.raises(RuntimeError, match="archive contents"):
        helper.download_diarization(directory)

    assert outside.read_bytes() == b"outside-model-must-not-be-read"
    assert not (directory / "segmentation.onnx").exists()


def test_archive_model_digest_is_checked_before_writing(helper, monkeypatch, tmp_path):
    prefix = "sherpa-onnx-pyannote-segmentation-3-0/"
    archive = tar_bytes(
        tar_member(prefix + "model.onnx", b"unexpected-model-bytes"),
        tar_member(prefix + "LICENSE", b"synthetic-license"),
    )
    fake_assets(helper, monkeypatch, archive, model=b"expected-model-bytes")
    directory = tmp_path / "speaker-models"

    with pytest.raises(RuntimeError, match="pinned SHA256"):
        helper.download_diarization(directory)

    assert not (directory / "segmentation.onnx").exists()


def test_cache_only_routes_speaker_assets_to_hf_home_not_local_model_directory(helper, monkeypatch, tmp_path):
    cache = tmp_path / "mounted-cache"
    local_models = tmp_path / "unused-local-models"
    destinations = []
    monkeypatch.setenv("HF_HOME", str(cache))
    monkeypatch.setattr(helper, "snapshot_download", lambda *args, **kwargs: cache / "fixture-snapshot")
    monkeypatch.setattr(helper, "download_diarization", destinations.append)
    monkeypatch.setattr(sys, "argv", ["download_qwen_models.py", "--cache-only", "--directory", str(local_models)])

    helper.main()

    assert destinations == [cache / "diarization"]
    assert not local_models.exists()


def test_default_download_routes_speaker_assets_inside_chosen_local_directory(helper, monkeypatch, tmp_path):
    local_models = tmp_path / "chosen-local-models"
    destinations = []
    monkeypatch.setenv("HF_HOME", str(tmp_path / "must-not-be-used"))
    monkeypatch.setattr(helper, "snapshot_download", lambda *args, **kwargs: local_models / "fixture-snapshot")
    monkeypatch.setattr(helper, "download_diarization", destinations.append)
    monkeypatch.setattr(sys, "argv", ["download_qwen_models.py", "--directory", str(local_models)])

    helper.main()

    assert destinations == [local_models / "diarization"]


@pytest.mark.parametrize("cached_license", [b"", b"tampered-cached-license"])
def test_embedding_license_repairs_empty_or_changed_cache(helper, monkeypatch, tmp_path, cached_license):
    directory = tmp_path / "speaker-models"
    directory.mkdir()
    segmentation, embedding, license_bytes = b"valid-segmentation", b"valid-embedding", b"pinned-license-document"
    (directory / "segmentation.onnx").write_bytes(segmentation)
    (directory / "SEGMENTATION-LICENSE").write_bytes(b"segmentation-license")
    (directory / "embedding.onnx").write_bytes(embedding)
    (directory / "EMBEDDING-LICENSE").write_bytes(cached_license)
    monkeypatch.setattr(helper, "SEGMENTATION_SHA256", sha256(segmentation))
    monkeypatch.setattr(helper, "SEGMENTATION_LICENSE_SHA256", sha256(b"segmentation-license"))
    monkeypatch.setattr(helper, "EMBEDDING_SHA256", sha256(embedding))
    monkeypatch.setattr(helper, "EMBEDDING_LICENSE_SHA256", sha256(license_bytes))
    monkeypatch.setattr(helper.httpx, "stream", stream_bytes(license_bytes))

    helper.download_diarization(directory)

    assert (directory / "EMBEDDING-LICENSE").read_bytes() == license_bytes
    assert not (directory / "EMBEDDING-LICENSE.download").exists()


def test_embedding_license_rejects_changed_remote_document(helper, monkeypatch, tmp_path):
    directory = tmp_path / "speaker-models"
    directory.mkdir()
    segmentation, embedding = b"valid-segmentation", b"valid-embedding"
    (directory / "segmentation.onnx").write_bytes(segmentation)
    (directory / "SEGMENTATION-LICENSE").write_bytes(b"segmentation-license")
    (directory / "embedding.onnx").write_bytes(embedding)
    monkeypatch.setattr(helper, "SEGMENTATION_SHA256", sha256(segmentation))
    monkeypatch.setattr(helper, "SEGMENTATION_LICENSE_SHA256", sha256(b"segmentation-license"))
    monkeypatch.setattr(helper, "EMBEDDING_SHA256", sha256(embedding))
    monkeypatch.setattr(helper, "EMBEDDING_LICENSE_SHA256", sha256(b"expected-license-document"))
    monkeypatch.setattr(helper.httpx, "stream", stream_bytes(b"changed-remote-license-document"))

    with pytest.raises(RuntimeError, match="pinned SHA256"):
        helper.download_diarization(directory)

    assert not (directory / "EMBEDDING-LICENSE").exists()
    assert not (directory / "EMBEDDING-LICENSE.download").exists()


def test_cache_only_expands_user_home_for_speaker_assets(helper, monkeypatch):
    destinations = []
    monkeypatch.setenv("HF_HOME", "~/synthetic-model-cache")
    monkeypatch.setattr(helper, "snapshot_download", lambda *args, **kwargs: "synthetic-cached-snapshot")
    monkeypatch.setattr(helper, "download_diarization", destinations.append)
    monkeypatch.setattr(sys, "argv", ["download_qwen_models.py", "--cache-only"])

    helper.main()

    assert destinations == [Path.home() / "synthetic-model-cache" / "diarization"]


@pytest.mark.parametrize("cached_license", [b"", b"changed-license"])
def test_segmentation_license_repairs_corrupt_cache(helper, monkeypatch, tmp_path, cached_license):
    model, license_bytes = b"synthetic-segmentation", b"synthetic-MIT-license"
    prefix = "sherpa-onnx-pyannote-segmentation-3-0/"
    archive = tar_bytes(tar_member(prefix + "model.onnx", model),
                        tar_member(prefix + "LICENSE", license_bytes))
    fake_assets(helper, monkeypatch, archive, model, license_bytes)
    directory = tmp_path / "speaker-models"
    directory.mkdir()
    (directory / "segmentation.onnx").write_bytes(model)
    (directory / "SEGMENTATION-LICENSE").write_bytes(cached_license)

    helper.download_diarization(directory)

    assert (directory / "SEGMENTATION-LICENSE").read_bytes() == license_bytes


@pytest.fixture
def sensevoice_helper(helper, monkeypatch):
    monkeypatch.setitem(sys.modules, "download_qwen_models", helper)
    monkeypatch.setattr(sys, "path", list(sys.path))
    source = Path(__file__).resolve().parents[1] / "scripts/download_sensevoice_models.py"
    spec = importlib.util.spec_from_file_location("synthetic_sensevoice_download_helper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def deny_speaker_download(*args, **kwargs):
        raise AssertionError("Each CPU download test must explicitly stub speaker assets")

    monkeypatch.setattr(module, "download_diarization", deny_speaker_download)
    return module


def sensevoice_assets(module, helper, monkeypatch, *, archive=None):
    assets = {"model": b"synthetic-int8-model", "tokens": b"synthetic-vocabulary",
              "source_license": b"synthetic-source-license", "model_license": b"synthetic-model-terms",
              "vad": b"synthetic-vad"}
    for setting, name in (("MODEL_SHA256", "model"), ("TOKENS_SHA256", "tokens"),
                          ("LICENSE_SOURCE_SHA256", "source_license"),
                          ("MODEL_LICENSE_SHA256", "model_license"), ("VAD_SHA256", "vad")):
        monkeypatch.setattr(module, setting, sha256(assets[name]))
    if archive is None:
        archive = tar_bytes(tar_member(module.PREFIX + "model.int8.onnx", assets["model"]),
                            tar_member(module.PREFIX + "tokens.txt", assets["tokens"]),
                            tar_member(module.PREFIX + "LICENSE", assets["source_license"]))
    monkeypatch.setattr(module, "ARCHIVE_SHA256", sha256(archive))
    requested, speaker_destinations = [], []

    def synthetic_stream(method, url, **kwargs):
        requested.append(url)
        if url == module.MODEL_URL:
            payload = archive
        elif url == module.VAD_URL:
            payload = assets["vad"]
        elif url.endswith("/MODEL_LICENSE"):
            payload = assets["model_license"]
        else:
            raise AssertionError("Unexpected CPU asset request")
        return stream_bytes(payload)(method, url, **kwargs)

    monkeypatch.setattr(helper.httpx, "stream", synthetic_stream)
    monkeypatch.setattr(module, "download_diarization", speaker_destinations.append)
    return assets, requested, speaker_destinations


def test_cpu_downloader_fresh_import_does_not_require_huggingface_or_torch(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/download_sensevoice_models.py"
    original_import = builtins.__import__

    def cpu_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"torch", "huggingface_hub", "transformers", "qwen_asr"}:
            raise AssertionError("The selected CPU downloader imported an optional large-model dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "download_qwen_models", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(builtins, "__import__", cpu_import)
    spec = importlib.util.spec_from_file_location("fresh_cpu_sensevoice_helper", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(module.prepare)


def test_sensevoice_ignores_paths_outside_exact_regular_archive_members(sensevoice_helper, helper, monkeypatch, tmp_path):
    module = sensevoice_helper
    outside = tmp_path / "must-stay-unchanged"
    outside.write_bytes(b"sentinel")
    archive = tar_bytes(
        tar_member(module.PREFIX + "model.int8.onnx", b"synthetic-int8-model"),
        tar_member(module.PREFIX + "tokens.txt", b"synthetic-vocabulary"),
        tar_member(module.PREFIX + "LICENSE", b"synthetic-source-license"),
        tar_member("../must-stay-unchanged", b"path-traversal"),
        tar_member(str(outside), b"absolute-path"),
        tar_member("extra-link", kind=tarfile.SYMTYPE, linkname=str(outside)),
    )
    assets, _, destinations = sensevoice_assets(module, helper, monkeypatch, archive=archive)
    directory = tmp_path / "cache"

    module.prepare(directory)

    assert (directory / "sensevoice/model.int8.onnx").read_bytes() == assets["model"]
    assert (directory / "sensevoice/tokens.txt").read_bytes() == assets["tokens"]
    assert (directory / "sensevoice/MODEL_LICENSE").read_bytes() == assets["model_license"]
    assert destinations == [directory / "diarization"]
    assert outside.read_bytes() == b"sentinel"
    assert not (directory / "extra-link").exists()
    assert not (tmp_path / "extra-link").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_sensevoice_rejects_links_at_expected_model_path(sensevoice_helper, helper, monkeypatch, tmp_path, kind):
    module = sensevoice_helper
    archive = tar_bytes(tar_member(module.PREFIX + "model.int8.onnx", kind=kind, linkname="outside"))
    sensevoice_assets(module, helper, monkeypatch, archive=archive)

    with pytest.raises(RuntimeError, match="archive contents"):
        module.prepare(tmp_path)

    assert not (tmp_path / "sensevoice/model.int8.onnx").exists()


@pytest.mark.parametrize("member_name, message", [("model.int8.onnx", "model"),
                                                  ("tokens.txt", "vocabulary"), ("LICENSE", "license source")])
def test_sensevoice_rejects_changed_inner_member_before_publication(sensevoice_helper, helper, monkeypatch, tmp_path, member_name, message):
    module = sensevoice_helper
    payloads = {"model.int8.onnx": b"synthetic-int8-model", "tokens.txt": b"synthetic-vocabulary",
                "LICENSE": b"synthetic-source-license"}
    payloads[member_name] = b"changed-inner-file"
    archive = tar_bytes(*(tar_member(module.PREFIX + name, payload) for name, payload in payloads.items()))
    sensevoice_assets(module, helper, monkeypatch, archive=archive)
    destination = tmp_path / "sensevoice" / ("LICENSE-source" if member_name == "LICENSE" else member_name)
    destination.parent.mkdir()
    destination.write_bytes(b"previous-file")

    with pytest.raises(RuntimeError, match=message + ".*pinned SHA256"):
        module.prepare(tmp_path)

    assert destination.read_bytes() == b"previous-file"
    assert not destination.with_suffix(destination.suffix + ".extract").exists()


@pytest.mark.parametrize("failure", ["truncated", "oversized"])
def test_sensevoice_failed_extraction_preserves_previous_model(sensevoice_helper, helper, monkeypatch, tmp_path, failure):
    module = sensevoice_helper
    sensevoice_assets(module, helper, monkeypatch)
    destination = tmp_path / "sensevoice/model.int8.onnx"
    destination.parent.mkdir()
    destination.write_bytes(b"previous-file")
    member = tarfile.TarInfo(module.PREFIX + "model.int8.onnx")
    member.size = 256 * 1024 * 1024 + 1 if failure == "oversized" else 10

    class BrokenArchive:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getmember(self, name):
            assert name == member.name
            return member

        def extractfile(self, value):
            assert failure == "truncated", "Oversized members must be rejected before reading"
            return io.BytesIO(b"short")

    monkeypatch.setattr(module.tarfile, "open", lambda *args, **kwargs: BrokenArchive())

    with pytest.raises(RuntimeError, match="incomplete" if failure == "truncated" else "archive contents"):
        module.prepare(tmp_path)

    assert destination.read_bytes() == b"previous-file"
    assert not destination.with_suffix(".onnx.extract").exists()


@pytest.mark.parametrize("name", ["tokens.txt", "LICENSE-source", "MODEL_LICENSE"])
@pytest.mark.parametrize("cached", [b"", b"changed-cache"])
def test_sensevoice_repairs_changed_vocabulary_and_licenses(sensevoice_helper, helper, monkeypatch, tmp_path, name, cached):
    module = sensevoice_helper
    assets, requested, _ = sensevoice_assets(module, helper, monkeypatch)
    directory = tmp_path / "sensevoice"
    directory.mkdir()
    expected = {"model.int8.onnx": assets["model"], "tokens.txt": assets["tokens"],
                "LICENSE-source": assets["source_license"], "MODEL_LICENSE": assets["model_license"]}
    for path, payload in expected.items():
        (directory / path).write_bytes(cached if path == name else payload)

    module.prepare(tmp_path)

    assert (directory / name).read_bytes() == expected[name]
    if name != "MODEL_LICENSE":
        assert module.MODEL_URL in requested


def test_sensevoice_model_terms_reject_changed_remote_document(sensevoice_helper, helper, monkeypatch, tmp_path):
    module = sensevoice_helper
    assets, _, _ = sensevoice_assets(module, helper, monkeypatch)
    directory = tmp_path / "sensevoice"
    directory.mkdir()
    for name, key in (("model.int8.onnx", "model"), ("tokens.txt", "tokens"),
                      ("LICENSE-source", "source_license"), ("silero_vad.onnx", "vad")):
        (directory / name).write_bytes(assets[key])
    monkeypatch.setattr(helper.httpx, "stream", stream_bytes(b"changed-remote-model-license"))

    with pytest.raises(RuntimeError, match="pinned SHA256"):
        module.prepare(tmp_path)

    assert not (directory / "MODEL_LICENSE").exists()
    assert not (directory / "MODEL_LICENSE.download").exists()
