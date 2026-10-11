"""Start DineMaster: `uv run python -m dinemaster` serves on 127.0.0.1:8160 and opens the browser."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"  # this machine only
PORT = 8160


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dinemaster", description="Run the DineMaster dashboard on this machine.")
    parser.add_argument("--port", type=int, default=PORT, help=f"port to serve on (default {PORT})")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)
    if not args.no_open:
        threading.Timer(0.8, webbrowser.open, [f"http://{HOST}:{args.port}/"]).start()
    uvicorn.run("dinemaster.web.app:create_app", factory=True, host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
