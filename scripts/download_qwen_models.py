"""Download the selected public Qwen weights; no recording or API key is used.

Local: python scripts/download_qwen_models.py
Container HF cache: python scripts/download_qwen_models.py --cache-only
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tarfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    ("Qwen/Qwen3-ASR-1.7B-hf", "bcd2b5b7f32b480ab5790554cfa8347f246a14f3", "qwen3-asr-1.7b-hf"),
    ("Qwen/Qwen3-ForcedAligner-0.6B-hf", "c07281df297b9905d24a508279258cccf987a064", "qwen3-aligner-0.6b-hf"),
)
RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = RELEASE + "/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = RELEASE + "/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SEGMENTATION_ARCHIVE_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
SEGMENTATION_SHA256 = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
SEGMENTATION_LICENSE_SHA256 = "14d7016ad68e7394d6e6b78d96cc2ae431c905287b89674cfdf021e79e62b8ba"
EMBEDDING_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
EMBEDDING_LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"


def snapshot_download(*args, **kwargs):
    # CPU SenseVoice setup reuses the small asset helpers in this module and
    # must not require Hugging Face, PyTorch or the optional Qwen runtime.
    from huggingface_hub import snapshot_download as implementation
    return implementation(*args, **kwargs)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def download(url: str, destination: Path, expected_sha256: str | None = None,
             max_bytes: int = 64 * 1024 * 1024) -> None:
    if destination.is_file() and expected_sha256 and digest(destination) == expected_sha256:
        return
    temporary = destination.with_suffix(destination.suffix + ".download")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(120, connect=20)) as response:
            response.raise_for_status()
            total = 0
            with temporary.open("wb") as output:
                for data in response.iter_bytes(1024 * 1024):
                    total += len(data)
                    if total > max_bytes:
                        raise RuntimeError("The public model asset exceeded its download limit.")
                    output.write(data)
        if not total or expected_sha256 and digest(temporary) != expected_sha256:
            raise RuntimeError("The downloaded public model did not match the pinned SHA256.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def download_diarization(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    segmentation = directory / "segmentation.onnx"
    if (not segmentation.is_file() or digest(segmentation) != SEGMENTATION_SHA256
            or not (directory / "SEGMENTATION-LICENSE").is_file()
            or digest(directory / "SEGMENTATION-LICENSE") != SEGMENTATION_LICENSE_SHA256):
        archive = directory / "segmentation.tar.bz2"
        download(SEGMENTATION_URL, archive, SEGMENTATION_ARCHIVE_SHA256)
        # Read only two exact regular-file members; never extract archive paths.
        with tarfile.open(archive, "r:bz2") as package:
            for name, destination, bound in (
                ("sherpa-onnx-pyannote-segmentation-3-0/model.onnx", segmentation, 8 * 1024 * 1024),
                ("sherpa-onnx-pyannote-segmentation-3-0/LICENSE", directory / "SEGMENTATION-LICENSE", 64 * 1024),
            ):
                member = package.getmember(name)
                if not member.isfile() or not 0 < member.size <= bound:
                    raise RuntimeError("Unexpected segmentation model archive contents.")
                source = package.extractfile(member)
                if source is None:
                    raise RuntimeError("The segmentation model archive is incomplete.")
                with source:
                    payload = source.read(bound + 1)
                if len(payload) != member.size:
                    raise RuntimeError("The segmentation model archive member is invalid.")
                if destination == segmentation and hashlib.sha256(payload).hexdigest() != SEGMENTATION_SHA256:
                    raise RuntimeError("The segmentation model did not match the pinned SHA256.")
                if destination.name == "SEGMENTATION-LICENSE" and hashlib.sha256(payload).hexdigest() != SEGMENTATION_LICENSE_SHA256:
                    raise RuntimeError("The segmentation license did not match the pinned SHA256.")
                destination.write_bytes(payload)
    download(EMBEDDING_URL, directory / "embedding.onnx", EMBEDDING_SHA256)
    license_path = directory / "EMBEDDING-LICENSE"
    download("https://raw.githubusercontent.com/modelscope/3D-Speaker/main/LICENSE", license_path,
             EMBEDDING_LICENSE_SHA256, max_bytes=64 * 1024)
    (directory / "SOURCES.txt").write_text(
        "Pyannote segmentation 3.0 ONNX: MIT; " + SEGMENTATION_URL + "\n"
        "CAM++ zh/en speaker embedding: Apache-2.0; " + EMBEDDING_URL + "\n",
        encoding="utf-8",
    )
    print(f"Speaker diarization models ready: {directory}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "models")
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args()
    for repo, revision, folder in MODELS:
        print(f"Downloading public model {repo} at {revision}", flush=True)
        path = snapshot_download(
            repo, revision=revision, token=False,
            local_dir=None if args.cache_only else args.directory / folder,
            allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.tiktoken", "*.jinja", "README.md", "LICENSE"],
        )
        print(f"Ready: {path}", flush=True)
    directory = (Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface")))
                 if args.cache_only else args.directory).expanduser()
    download_diarization(directory / "diarization")


if __name__ == "__main__":
    main()
