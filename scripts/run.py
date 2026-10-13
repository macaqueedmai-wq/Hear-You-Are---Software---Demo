"""Run the local server; optionally open the desktop browser."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if not (ROOT / "web/dist/index.html").exists():
        raise SystemExit("前端尚未建置，請先執行 python scripts/setup.py。")
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("缺少後端套件，請先執行 python scripts/setup.py。")
    url = f"http://127.0.0.1:{args.port}"
    if args.open:
        def show_when_ready():
            import time
            for _ in range(30):
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except OSError:
                    time.sleep(0.5)
        threading.Thread(target=show_when_ready, daemon=True).start()
    print(f"Hear You Are：{url}（按 Ctrl+C 停止）")
    uvicorn.run("backend.hya.app:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
