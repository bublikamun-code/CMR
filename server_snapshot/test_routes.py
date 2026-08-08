"""Smoke test: the app imports and exposes its expected routes.

Run from the server_snapshot directory:  python test_routes.py

Previously this caught every exception, printed it and still exited 0, so a broken
import passed CI. It now exits non-zero on failure.
"""

import sys

sys.path.insert(0, ".")

# Routers that must always be mounted. Kept in sync with main.py.
EXPECTED_PREFIXES = [
    "/auth",
    "/kanban",
    "/clients",
    "/suppliers",
    "/tags",
    "/payments",
    "/writeoffs",
    "/activity",
    "/custom",
    "/email-parser",
    "/workflows",
    "/webhooks",
    "/health",
]


def main() -> int:
    try:
        from main import app
    except Exception:
        import traceback

        print("FAIL: application failed to import")
        traceback.print_exc()
        return 1

    paths = [getattr(r, "path", "") for r in app.routes]
    print(f"Loaded {len(paths)} routes")

    missing = [p for p in EXPECTED_PREFIXES if not any(x.startswith(p) for x in paths)]
    if missing:
        print(f"FAIL: no routes found for: {', '.join(missing)}")
        return 1

    print("OK: all expected router prefixes are mounted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
