"""Entry point: `python -m jarvis` (or `uv run jarvis`).

    jarvis                       start the server and open the app window
    jarvis --no-browser          server only (used by the auto-start shortcut)
    jarvis --port 7727 --config dev.toml --data-dir C:\\temp\\jarvis-dev
                                 a second, fully separate instance for testing
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import urllib.request

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
    ap = argparse.ArgumentParser(prog="jarvis", description="JARVIS command center")
    ap.add_argument("--no-browser", action="store_true", help="don't open the app window")
    ap.add_argument("--port", type=int, help="listen on this port instead of [server].port")
    ap.add_argument("--config", help="use this config.toml")
    ap.add_argument("--data-dir", help="keep local state here instead of ./data")
    args = ap.parse_args()

    # These must be in the environment before the app modules are imported —
    # util.py reads them lazily, and uvicorn imports jarvis.app afterwards.
    if args.config:
        os.environ["JARVIS_CONFIG"] = os.path.abspath(args.config)
    if args.data_dir:
        os.environ["JARVIS_DATA_DIR"] = os.path.abspath(args.data_dir)
    if args.port:
        os.environ["JARVIS_PORT"] = str(args.port)

    import uvicorn

    from .launcher import open_app_window
    from .util import load_config, load_env

    load_env()
    cfg = load_config()
    srv = cfg.get("server", {})
    host = srv.get("host", "127.0.0.1")
    port = int(os.environ.get("JARVIS_PORT") or srv.get("port", 7717))
    url = f"http://{host}:{port}"

    no_browser = args.no_browser or bool(os.environ.get("JARVIS_NO_BROWSER"))
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

    # Open HUDs hold a long-lived event stream; don't let them stall Ctrl+C.
    uvicorn.run("jarvis.app:app", host=host, port=port, log_level="warning",
                timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
