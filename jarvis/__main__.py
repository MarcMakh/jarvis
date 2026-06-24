"""Entry point: `python -m jarvis` (or `uv run jarvis`)."""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request

import uvicorn

from .launcher import open_app_window
from .util import load_config, load_env

BANNER = r"""
       _   _    ____   _  _  ___  ____
      | | / \  |  _ \ | || ||_ _|/ ___|
   _  | |/ _ \ | |_) || || | | | \___ \
  | |_| / ___ \|  _ < |__   _| |  ___) |
   \___/_/   \_\_| \_\   |_| |___|____/
"""


def _is_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1.5):
            return True
    except Exception:
        return False


def main() -> None:
    load_env()
    cfg = load_config()
    srv = cfg.get("server", {})
    host = srv.get("host", "127.0.0.1")
    port = int(srv.get("port", 7717))
    url = f"http://{host}:{port}"

    no_browser = "--no-browser" in sys.argv or os.environ.get("JARVIS_NO_BROWSER")
    want_window = srv.get("open_browser", True) and not no_browser

    # If a server is already running (e.g. the auto-start instance), don't try to
    # bind the port again — just open the app window and exit.
    if _is_up(url):
        print(BANNER)
        print(f"  JARVIS already online -> {url}")
        if want_window:
            open_app_window(url)
        return

    print(BANNER)
    print(f"  JARVIS online -> {url}")
    print("  Press Ctrl+C to shut down.\n")

    if want_window:

        def _open() -> None:
            # wait for uvicorn to start serving, then open the autoplay app window
            for _ in range(40):
                time.sleep(0.4)
                if _is_up(url):
                    break
            open_app_window(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("jarvis.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
