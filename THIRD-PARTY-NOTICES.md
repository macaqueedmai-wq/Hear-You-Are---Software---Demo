# Third-party notices

Hear You Are's [MIT license](LICENSE) applies to its own code. Third-party code, model weights and runtime libraries retain their respective licenses and attribution requirements.

## Interface

**MoodHaven Journal**: Copyright (c) 2025 Ken LaCroix and MoodHaven Journal contributors; MIT. Sidebar components, the violet palette, cards and navigation styling were adapted from the [upstream project](https://github.com/kenlacroix/moodhaven-journal). The full license and source snapshots are retained in [third_party/moodhaven](third_party/moodhaven); the exact commit and adaptations are documented in [UI source](docs/UI-SOURCE.md). This repository does not include MoodHaven's third-party photos, audio effects, private data or upstream Git history.

## Current CPU speech pipeline

- **Sherpa-ONNX**: Apache-2.0. The app uses its CPU speech, VAD and speaker-diarization runtime through the Python package. See the [upstream license](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE).
- **SenseVoiceSmall INT8**: downloaded ONNX weights use the **FunASR Model Open Source License Agreement**, separate from the runtime's code license. The downloader preserves the release's `LICENSE-source` and the full `MODEL_LICENSE` under `models/sensevoice/`. See the [model license](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) and [Sherpa model source](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html).
- **Silero VAD**: MIT. The ONNX voice-activity model is downloaded from the Sherpa-ONNX release. See the [upstream project and license](https://github.com/snakers4/silero-vad).
- **pyannote segmentation 3.0**: MIT, Copyright (c) 2022 CNRS. The downloaded ONNX segmentation release includes its license, preserved as `models/diarization/SEGMENTATION-LICENSE`. See the [upstream model](https://huggingface.co/pyannote/segmentation-3.0).
- **CAM++ / 3D-Speaker**: Apache-2.0. The pipeline uses the Chinese/English speaker-embedding model distributed through Sherpa-ONNX. The downloader preserves `models/diarization/EMBEDDING-LICENSE` from the [upstream 3D-Speaker project](https://github.com/modelscope/3D-Speaker/blob/main/LICENSE).

Model weights are downloaded separately and excluded from Git. Source URLs, pinned hashes, runtime configuration and license handling are documented in the [memory pipeline](docs/MEMORY-PIPELINE.md#provider-configuration) and [`scripts/download_sensevoice_models.py`](scripts/download_sensevoice_models.py). Do not apply the project's MIT license to all model assets.

## Optional historical speech path

**faster-whisper** is MIT-licensed and uses CTranslate2 for local inference; see its [upstream project](https://github.com/SYSTRAN/faster-whisper). **Whisper large-v3-turbo** weights are covered by OpenAI Whisper's MIT license; see [OpenAI Whisper](https://github.com/openai/whisper). Converted weights were downloaded separately, not copied into the repository. The [historical ASR verification](docs/ASR-VERIFICATION.md) records that path's sources and hashes. These components remain available for legacy code and regression tests; they are not the current default ASR.

## Package dependencies

- **React, Vite, TypeScript and Lucide** are installed through npm. Their bundled license files govern their use; locked versions appear in [`web/package-lock.json`](web/package-lock.json).
- **FastAPI, Uvicorn, PyAV, NumPy, Sherpa-ONNX, CTranslate2 and ONNX Runtime** are used through Python packages or their runtime distributions. Their accompanying licenses remain in force. See the project's `requirements*.txt` files for dependency sets.
- **NVIDIA CUDA libraries**, when used by an optional adapter, are governed by their vendor terms. The current CPU SenseVoice path does not require CUDA.

This notice summarizes provenance; it does not replace the full licenses shipped with third-party packages or downloaded models.
