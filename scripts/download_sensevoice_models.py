"""Download the small CPU ASR, VAD and recording-local speaker models.

Local: python scripts/download_sensevoice_models.py
Container: python scripts/download_sensevoice_models.py --directory /var/cache/hear-you-are

No API key, audio upload, Torch or CUDA is involved. SenseVoice weights use the
FunASR Model Open Source License; their terms differ from the code licenses.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_qwen_models import digest, download, download_diarization

MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
ARCHIVE_SHA256 = "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e"
MODEL_SHA256 = "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"
TOKENS_SHA256 = "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"
MODEL_LICENSE_SHA256 = "7dba975a2069691db4992b0592d70828b330d2f8a30a71450f4e152a554e84f8"
LICENSE_SOURCE_SHA256 = "221c6df10b0931a5629adad671ea48fb7747e034c414b6d2bfa275bc3dd4ea17"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
VAD_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
PREFIX = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/"


def prepare(directory: Path) -> None:
    directory = directory.expanduser().resolve()
    model_dir = directory / "sensevoice"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = model_dir / "model.int8.onnx"
    tokens = model_dir / "tokens.txt"
    if (not model.is_file() or digest(model) != MODEL_SHA256
            or not tokens.is_file() or digest(tokens) != TOKENS_SHA256
            or not (model_dir / "LICENSE-source").is_file()
            or digest(model_dir / "LICENSE-source") != LICENSE_SOURCE_SHA256):
        archive = model_dir / "sensevoice-int8.tar.bz2"
        download(MODEL_URL, archive, ARCHIVE_SHA256, max_bytes=180 * 1024 * 1024)
        with tarfile.open(archive, "r:bz2") as package:
            for name, destination, bound in (
                ("model.int8.onnx", model, 256 * 1024 * 1024),
                ("tokens.txt", tokens, 1024 * 1024),
                ("LICENSE", model_dir / "LICENSE-source", 64 * 1024),
            ):
                member = package.getmember(PREFIX + name)
                if not member.isfile() or not 0 < member.size <= bound:
                    raise RuntimeError("Unexpected SenseVoice model archive contents.")
                source = package.extractfile(member)
                if source is None:
                    raise RuntimeError("The SenseVoice model archive is incomplete.")
                temporary = destination.with_suffix(destination.suffix + ".extract")
                try:
                    with source, temporary.open("wb") as output:
                        remaining = member.size
                        while remaining:
                            data = source.read(min(remaining, 1024 * 1024))
                            if not data:
                                raise RuntimeError("A SenseVoice model archive member is incomplete.")
                            output.write(data)
                            remaining -= len(data)
                    if destination == model and digest(temporary) != MODEL_SHA256:
                        raise RuntimeError("The SenseVoice model did not match its pinned SHA256.")
                    if destination == tokens and digest(temporary) != TOKENS_SHA256:
                        raise RuntimeError("The SenseVoice vocabulary did not match its pinned SHA256.")
                    if destination.name == "LICENSE-source" and digest(temporary) != LICENSE_SOURCE_SHA256:
                        raise RuntimeError("The SenseVoice license source did not match its pinned SHA256.")
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
    download(VAD_URL, model_dir / "silero_vad.onnx", VAD_SHA256, max_bytes=2 * 1024 * 1024)
    license_path = model_dir / "MODEL_LICENSE"
    download("https://raw.githubusercontent.com/modelscope/FunASR/main/MODEL_LICENSE", license_path,
             MODEL_LICENSE_SHA256, max_bytes=128 * 1024)
    download_diarization(directory / "diarization")
    print(f"SenseVoice CPU speech models ready: {directory}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    prepare(args.directory)


if __name__ == "__main__":
    main()
