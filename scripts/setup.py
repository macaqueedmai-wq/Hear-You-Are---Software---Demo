"""Portable local setup. Run with Python 3.11+; add --gpu for NVIDIA CUDA 12."""
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
    parser.add_argument("--gpu", action="store_true", help="安裝可選 NVIDIA CUDA 12 函式庫")
    parser.add_argument("--dev", action="store_true", help="同時安裝 pytest 驗證工具")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("請安裝 Python 3.11 或更新版本。")
    env = ROOT / ".venv"
    binary = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not binary.exists():
        venv.create(env, with_pip=True)
    requirements = "requirements-gpu.txt" if args.gpu else "requirements.txt"
    subprocess.run([str(binary), "-m", "pip", "install", "-r", requirements], cwd=ROOT, check=True)
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
