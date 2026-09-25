"""CIDR trust boundary для forwarded identity на чистом ASGI scope."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest

from runtime_config import (
    RuntimeConfigError,
    trusted_proxy_networks,
    validate_startup_config,
)
from trusted_proxy import TrustedProxyMiddleware


ROOT = Path(__file__).resolve().parent.parent


def _run_middleware(networks, peer, headers):
    observed = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    async def app(scope, receive_call, send_call):
        observed.update(scope)
        await send_call({"type": "http.response.start", "status": 200,
                         "headers": []})
        await send_call({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "scheme": "http",
        "client": peer,
        "headers": headers,
    }
    asyncio.run(TrustedProxyMiddleware(app, networks)(scope, receive, send))
    return observed


def _forwarded_headers():
    return [
        (b"host", b"backend:8000"),
        (b"x-forwarded-for", b"198.51.100.27"),
        (b"x-forwarded-proto", b"https"),
        (b"x-forwarded-host", b"crm.example"),
    ]


def test_trusted_proxy_cidr_overwrites_forwarded_identity():
    networks = trusted_proxy_networks({
        "CRM_TRUSTED_PROXY_NETWORKS": "172.30.0.0/24",
    })

    scope = _run_middleware(
        networks,
        ("172.30.0.8", 43100),
        _forwarded_headers(),
    )

    assert scope["client"] == ("198.51.100.27", 0)
    assert scope["scheme"] == "https"
    assert (b"host", b"crm.example") in scope["headers"]


def test_untrusted_peer_cannot_spoof_forwarded_identity():
    networks = trusted_proxy_networks({
        "CRM_TRUSTED_PROXY_NETWORKS": "172.30.0.0/24",
    })

    scope = _run_middleware(
        networks,
        ("172.31.0.8", 43100),
        _forwarded_headers(),
    )

    assert scope["client"] == ("172.31.0.8", 43100)
    assert scope["scheme"] == "http"
    assert (b"host", b"backend:8000") in scope["headers"]
    assert not any(
        name.lower().startswith(b"x-forwarded-")
        for name, _value in scope["headers"]
    )


def test_trusted_peer_with_malformed_forwarded_values_fails_closed():
    networks = trusted_proxy_networks({
        "CRM_TRUSTED_PROXY_NETWORKS": "172.20.0.0/16",
    })

    scope = _run_middleware(
        networks,
        ("172.20.1.9", 43100),
        [
            (b"host", b"backend:8000"),
            (b"x-forwarded-for", b"not-an-ip"),
            (b"x-forwarded-proto", b"javascript"),
            (b"x-forwarded-host", b"bad host"),
        ],
    )

    assert scope["client"] == ("172.20.1.9", 43100)
    assert scope["scheme"] == "http"
    assert (b"host", b"backend:8000") in scope["headers"]


def test_empty_trust_list_accepts_no_forwarded_identity():
    assert trusted_proxy_networks({}) == ()
    scope = _run_middleware((), ("127.0.0.1", 43100), _forwarded_headers())
    assert scope["client"] == ("127.0.0.1", 43100)
    assert scope["scheme"] == "http"


@pytest.mark.parametrize("raw", [
    "not-a-cidr",
    "172.20.0.1/16",
    "172.20.0.0/99",
    "172.20.0.0/16,",
])
def test_malformed_trusted_proxy_cidr_fails_startup(raw):
    with pytest.raises(RuntimeConfigError, match="CRM_TRUSTED_PROXY_NETWORKS"):
        validate_startup_config({
            "CRM_DEPLOYMENT": "test",
            "CRM_TRUSTED_PROXY_NETWORKS": raw,
        })


def test_main_import_rejects_malformed_trusted_proxy_cidr(tmp_path):
    env = os.environ.copy()
    env.update({
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "test",
        "CRM_COOKIE_SECURE": "false",
        "CRM_TRUSTED_PROXY_NETWORKS": "172.20.0.1/16",
    })

    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert "CRM_TRUSTED_PROXY_NETWORKS содержит невалидный CIDR" in result.stderr
