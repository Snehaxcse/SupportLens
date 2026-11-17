"""Supported single-process release entrypoint with explicit proxy trust."""
import argparse

import uvicorn

from .app import create_app
from .config import load_api_settings


def main():
    parser = argparse.ArgumentParser(description="Run one SupportLens API process.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(load_api_settings()), host=args.host, port=args.port,
                proxy_headers=False, access_log=False, limit_concurrency=64,
                timeout_keep_alive=5, timeout_graceful_shutdown=12)


if __name__ == "__main__":
    main()
