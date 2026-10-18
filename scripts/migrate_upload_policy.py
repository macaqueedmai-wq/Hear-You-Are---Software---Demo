"""Apply the explicitly chosen upload-date policy to existing app evidence.

Run only after a consistent app-data backup. This uses existing persisted upload
timestamps; it does not change audio, call providers or approve speaker identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = next((root for root in (Path(__file__).resolve().parents[1], Path.cwd())
             if (root / "backend" / "hya").is_dir()), Path.cwd())
sys.path.insert(0, str(ROOT / "backend"))
from hya.engine import Engine

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the chosen upload-date policy after backup")
    args = parser.parse_args()
    if not args.apply:
        parser.error("Pass --apply after taking an application-data backup.")
    engine = Engine(os.environ.get("HYA_DATA_DIR", str(ROOT / "data")), model_mode=True)
    before = engine.list_recordings()
    def source_fingerprint(recordings):
        fields = ("id", "filename", "sha256", "file_path", "created_at", "segments")
        rows = [{key: recording.get(key) for key in fields}
                for recording in sorted(recordings, key=lambda value: value["id"])]
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    original = source_fingerprint(before)
    engine.align_upload_dates()
    after = engine.list_recordings()
    assert original == source_fingerprint(after), "Source evidence changed during date-policy migration"
    previous = {recording["id"]: recording.get("recorded_at") for recording in before}
    print(json.dumps({"status": "upload_dates_aligned", "recording_count": len(after),
                      "changed_recordings": sum(previous.get(recording["id"]) != recording.get("recorded_at") for recording in after),
                      "source_audio_identity_unchanged": True}), flush=True)


if __name__ == "__main__":
    main()
