"""Run the Third Wheel website:

    python -m third_wheel.web            # serve on http://127.0.0.1:8000
    python -m third_wheel.web --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(prog="third_wheel.web", description="Serve הגלגל 5.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="dev auto-reload")
    args = p.parse_args()
    # Exactly one worker, on purpose: the scan cache, single-flight locks and
    # simulation seeds all live in process memory. More workers would multiply
    # the load on Planet and let the scan and seat map disagree.
    uvicorn.run(
        "third_wheel.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
        log_level="info",
        # uvicorn's own access log records the FULL client IP. app.py already
        # writes a combined-format access log with the address anonymized
        # before it touches disk, so this one would only leak what that one
        # deliberately drops (into journald, where it lingers).
        access_log=False,
    )


if __name__ == "__main__":
    main()
