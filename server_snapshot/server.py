#!/usr/bin/env python3
"""CRM entry point for hoster.by shared hosting."""
import argparse
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_SCRIPT_DIR)

# 2026-08-31: подхватываем боевые переменные из .pm2.env рядом со скриптом.
# Причина: окружение lived в PM2 (--update-env / дамп), и после одного
# рестарта из пустого ssh-шелла и после ребута сервера воркеры теряли
# CRM_DATA_DIR и молча открывали пустую базу в корне сайта. setdefault —
# явно заданное окружение всегда сильнее файла.
_env_file = os.path.join(_SCRIPT_DIR, ".pm2.env")
if os.path.isfile(_env_file):
    # Наличие боевого env-файла делает cookie preflight fail-closed. Сам файл
    # не меняется этой правкой; владелец должен явно добавить CRM_COOKIE_SECURE=true.
    os.environ.setdefault("CRM_DEPLOYMENT", "production")
    # Боевой nginx подключается к Uvicorn через loopback. Явный CRM CIDR
    # из .pm2.env остаётся сильнее этого безопасного default.
    os.environ.setdefault("CRM_TRUSTED_PROXY_NETWORKS", "127.0.0.1/32")
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from runtime_config import validate_startup_config

validate_startup_config()

# Импорт ради побочного эффекта и ради порядка: main.py на импорте создаёт
# uploads/tenants и вызывает create_all. Это должно произойти один раз в
# родительском процессе и ПОСЛЕ загрузки .pm2.env выше (database.py читает
# CRM_DATA_DIR на импорте), а не одновременно в двух воркерах uvicorn.
from main import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    # Parse port from env, args, or default
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None)
    # 0.0.0.0 намеренно: приложение на общем хостинге слушает порт, который
    # проксирует веб-сервер, и bind только на loopback сломал бы доступ.
    parser.add_argument("--host", type=str, default="0.0.0.0")  # noqa: S104
    args, _ = parser.parse_known_args()

    port = args.port or int(os.environ.get("PORT", os.environ.get("APPS_PORT", "8000")))
    host = args.host

    print(f"Starting CRM on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level="info",
        # Forwarded identity применяет TrustedProxyMiddleware только для
        # CRM_TRUSTED_PROXY_NETWORKS. Встроенная обработка Uvicorn отключена:
        # её trust list не поддерживает CIDR и не является нужной границей.
        proxy_headers=False,
        workers=2,
    )
