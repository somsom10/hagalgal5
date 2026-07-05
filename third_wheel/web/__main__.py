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
    uvicorn.run(
        "third_wheel.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
