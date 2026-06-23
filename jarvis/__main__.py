"""Entry point: `python -m jarvis` (or `uv run jarvis`)."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

import uvicorn

from .util import load_config, load_env


def main() -> None:
    load_env()
    cfg = load_config()
    srv = cfg.get("server", {})
    host = srv.get("host", "127.0.0.1")
    port = int(srv.get("port", 7717))
    url = f"http://{host}:{port}"

    no_browser = "--no-browser" in sys.argv or os.environ.get("JARVIS_NO_BROWSER")

    banner = r"""
       _   _    ____   _  _  ___  ____
      | | / \  |  _ \ | || ||_ _|/ ___|
   _  | |/ _ \ | |_) || || | | | \___ \
  | |_| / ___ \|  _ < |__   _| |  ___) |
   \___/_/   \_\_| \_\   |_| |___|____/
"""
    print(banner)
    print(f"  JARVIS online -> {url}")
    print("  Press Ctrl+C to shut down.\n")

    if srv.get("open_browser", True) and not no_browser:

        def _open() -> None:
            time.sleep(1.4)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("jarvis.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
