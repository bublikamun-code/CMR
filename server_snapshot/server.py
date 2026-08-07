#!/usr/bin/env python3
"""CRM entry point for hoster.by shared hosting."""
import os
import sys
import argparse

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from main import app

if __name__ == "__main__":
    import uvicorn

    # Parse port from env, args, or default
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args, _ = parser.parse_known_args()

    port = args.port or int(os.environ.get("PORT", os.environ.get("APPS_PORT", "8000")))
    host = args.host

    print(f"Starting CRM on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=2,
    )
