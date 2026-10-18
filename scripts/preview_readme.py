"""Show the current UI with fictional English text in disposable storage.

Run after building web/dist. This preview needs no audio, downloaded models or
API keys, makes no model requests and never opens the normal app database.
All data is removed when the process exits. Default URL: http://127.0.0.1:8766.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PreviewSpeech:
    def health(self):
        return {"provider": "preview", "model": "Fictional transcript fixture", "device": "cpu",
                "state": "unavailable", "available": False, "loaded": False,
                "external": False, "speaker_diarization": False}

    def transcribe(self, path, language="en"):
        from backend.hya.asr import TranscriptionError
        raise TranscriptionError("This preview contains fictional text only. Run the normal demo to upload audio.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535.")
    if not (ROOT / "web/dist/index.html").is_file():
        raise SystemExit("Build the frontend first: cd web, then npm ci and npm run build.")
    # These environment changes affect only this preview process. Do not load
    # credentials from .env or inherit the ordinary app's database location.
    for key in list(os.environ):
        if key.startswith("HYA_"):
            os.environ.pop(key)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", HYA_MEMORY_MODE="local_rules",
                      HYA_DEPLOYMENT_MODE="local", HYA_ALLOW_EXTERNAL_LLM="false",
                      HYA_ALLOWED_HOSTS="localhost,127.0.0.1,::1,testserver")
    with tempfile.TemporaryDirectory(prefix="hear-you-are-readme-") as folder:
        os.environ["HYA_DATA_DIR"] = str(Path(folder) / "unused-default")
        from backend.hya.app import create_app
        from backend.hya.engine import HK
        import uvicorn

        app = create_app(Path(folder) / "preview", transcriber=PreviewSpeech())
        stamp = datetime.now(HK).replace(minute=0, second=0, microsecond=0)
        engine = app.state.engine
        recording = engine.create_recording("Fictional example: a presentation and a conversation (transcript only)",
                                            None, "readme-fictional-preview", stamp.isoformat(), True)
        lines = [
            "I felt nervous before the presentation because I had not prepared the opening.",
            "I feel calm after taking a short break.",
            "I talked with my friend Alex about our group project.",
            "I talked with my friend Maya and felt better after our conversation.",
            "I finished the first draft of my presentation today.",
            "I completed my presentation today.",
        ]
        engine.ingest_segments(recording["id"], [
            {"start": index * 8, "end": index * 8 + 7, "text": text}
            for index, text in enumerate(lines)
        ], subject_confirmed=True)
        print(f"Fictional English UI preview: http://127.0.0.1:{args.port}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
