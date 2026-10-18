"""Portable CPU SenseVoice setup. Run with Python 3.11 or newer."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-models", action="store_true", help="下載 CPU 轉錄、VAD 及說話人分群模型")
    parser.add_argument("--dev", action="store_true", help="同時安裝 pytest 驗證工具")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("請安裝 Python 3.11 或更新版本。")
    env = ROOT / ".venv"
    binary = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not binary.exists():
        venv.create(env, with_pip=True)
    subprocess.run([str(binary), "-m", "pip", "install", "-r", "requirements-sensevoice.txt"], cwd=ROOT, check=True)
    if args.download_models:
        subprocess.run([str(binary), "scripts/download_sensevoice_models.py"], cwd=ROOT, check=True)
    if args.dev:
        subprocess.run([str(binary), "-m", "pip", "install", "-r", "requirements-dev.txt"], cwd=ROOT, check=True)
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not npm:
        raise SystemExit("請安裝 Node.js 20.19+ 或 22.12+，再執行設定。")
    subprocess.run([npm, "ci"], cwd=ROOT / "web", check=True)
    subprocess.run([npm, "run", "build"], cwd=ROOT / "web", check=True)
    print("設定完成。Windows 請雙擊 Start-Demo.cmd，或用虛擬環境的 Python 執行 scripts/run.py。")


if __name__ == "__main__":
    main()
