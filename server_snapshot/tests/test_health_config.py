"""Синтаксис и семантика health/security конфигурации репозитория."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    ROOT / "server_snapshot" / "scripts" / "restart_crm.sh",
    ROOT / "server_snapshot" / "scripts" / "watchdog_crm.sh",
)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_shell_scripts_have_valid_bash_syntax(script):
    result = subprocess.run(
        ["/bin/bash", "-n", str(script)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_docker_compose_examples_have_valid_syntax(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("docker compose недоступен")
    for name in (
        "docker-compose.yml",
        "docker-compose.override.yml",
        "docker-compose.production.yml",
    ):
        shutil.copyfile(ROOT / name, tmp_path / name)
    (tmp_path / ".env.local").write_text("CRM_DEPLOYMENT=development\n", encoding="utf-8")
    (tmp_path / ".env.production").write_text(
        "CRM_DEPLOYMENT=production\nCRM_COOKIE_SECURE=true\n",
        encoding="utf-8",
    )

    for name in ("docker-compose.yml", "docker-compose.production.yml"):
        result = subprocess.run(
            ["docker", "compose", "-f", str(tmp_path / name), "config", "--quiet"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_container_healthchecks_use_liveness_only():
    backend = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
    frontend = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")

    assert "urlopen('http://127.0.0.1:8000/health/live'" in backend
    assert "urlopen('http://127.0.0.1:8000/health/ready'" not in backend
    assert "http://127.0.0.1/_nginx_live" in frontend
    assert "127.0.0.1/health\"" not in frontend
    assert "Перед публикацией трафика" in backend
    assert "/health/ready" in backend


def test_docker_proxy_network_upload_and_deployment_settings_are_explicit():
    backend = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
    development = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    app_locations = (ROOT / "nginx" / "snippets" / "app-locations.conf").read_text(
        encoding="utf-8"
    )
    nginx_configs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "nginx").rglob("*.conf")
    )

    assert "CRM_DEPLOYMENT=production" in backend
    assert "CRM_DEPLOYMENT=development" not in backend
    assert '"--no-proxy-headers"' in backend
    assert '"--proxy-headers"' not in backend
    assert '"--forwarded-allow-ips"' not in backend
    assert "CRM_TRUSTED_PROXY_NETWORKS=172.20.0.0/16,172.30.0.0/24" in backend

    assert 'CRM_DEPLOYMENT: "development"' in development
    assert 'CRM_TRUSTED_PROXY_NETWORKS: "172.30.0.0/24"' in development
    assert 'CRM_DEPLOYMENT: "production"' in production
    assert 'CRM_TRUSTED_PROXY_NETWORKS: "172.20.0.0/16"' in production
    assert development.count('CRM_UPLOADS_DIR: "/app/uploads"') == 1
    assert production.count('CRM_UPLOADS_DIR: "/app/uploads"') == 1
    assert "subnet: 172.30.0.0/24" in development
    assert "subnet: 172.20.0.0/16" in production

    assert "proxy_set_header X-Forwarded-For $remote_addr;" in app_locations
    assert "$proxy_add_x_forwarded_for" not in nginx_configs
    assert "CRM_COOKIE_SECURE=" not in backend
    assert "CRM_COOKIE_SECURE" in production


def test_bare_metal_uvicorn_disables_builtin_proxy_headers():
    server = (ROOT / "server_snapshot" / "server.py").read_text(encoding="utf-8")
    assert "proxy_headers=False" in server
    assert "forwarded_allow_ips" not in server
    assert 'CRM_TRUSTED_PROXY_NETWORKS", "127.0.0.1/32"' in server


def test_public_health_endpoints_are_proxied_not_static():
    app_locations = (ROOT / "nginx" / "snippets" / "app-locations.conf").read_text(
        encoding="utf-8"
    )
    https = (ROOT / "nginx" / "https.conf").read_text(encoding="utf-8")

    assert "|api|health)(/|$)" in app_locations
    assert "location = /health" not in app_locations
    assert "location /health {" not in app_locations
    assert "location = /_nginx_live" in app_locations
    assert "location = /health" not in https
    assert "location = /_nginx_live" in https


def test_watchdog_restarts_immediately_if_resurrect_leaves_crm_stopped():
    watchdog = SCRIPTS[1].read_text(encoding="utf-8")

    resurrect_at = watchdog.index("if pm2 resurrect")
    recheck_at = watchdog.index(
        'if [ "$(pm2_status)" != "online" ]', resurrect_at
    )
    start_at = watchdog.index(
        'cd "$APP_DIR" && pm2 start ecosystem.config.js', recheck_at
    )

    assert resurrect_at < recheck_at < start_at
    assert "PM2 resurrect completed without online CRM" in watchdog


def test_scripts_use_liveness_for_process_and_readiness_for_dependencies():
    restart = SCRIPTS[0].read_text(encoding="utf-8")
    watchdog = SCRIPTS[1].read_text(encoding="utf-8")

    assert "/health/ready" in restart
    assert "/health/live" not in restart
    assert '"health/live"' in watchdog
    assert '"health/ready"' in watchdog
    assert "application not ready" in watchdog
