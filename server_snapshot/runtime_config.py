"""Проверяемая конфигурация процессов и cookie-security policy."""
import os
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_DEPLOYMENT_MODES = {"development", "test", "production"}


class RuntimeConfigError(RuntimeError):
    """Ошибка конфигурации, которую безопасно показать при запуске."""


def _environment(environ=None):
    return os.environ if environ is None else environ


def parse_strict_bool(name: str, value: str) -> bool:
    """Разобрать булево значение без молчаливых truthy-строк."""
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RuntimeConfigError(
        f"{name} должен иметь значение true или false"
    )


def deployment_mode(environ=None) -> str:
    """Вернуть явно настроенный режим приложения."""
    env = _environment(environ)
    mode = env.get("CRM_DEPLOYMENT", "development").strip().lower()
    if mode not in _DEPLOYMENT_MODES:
        raise RuntimeConfigError(
            "CRM_DEPLOYMENT должен быть development, test или production"
        )
    return mode


def auth_cookie_secure(environ=None) -> bool:
    """Получить единый флаг ``Secure`` для auth-cookie.

    Локальная конфигурация без переменной сохраняет HTTP-тесты работоспособными.
    В production отсутствующее, невалидное или выключенное значение закрывает
    конфигурацию: сервис не должен молча перейти на cookie без ``Secure``.
    """
    env = _environment(environ)
    raw_value = env.get("CRM_COOKIE_SECURE")
    production = deployment_mode(env) == "production"
    if raw_value is None or not raw_value.strip():
        if production:
            raise RuntimeConfigError(
                "CRM_COOKIE_SECURE=true обязателен в production"
            )
        return False

    secure = parse_strict_bool("CRM_COOKIE_SECURE", raw_value)
    if production and not secure:
        raise RuntimeConfigError(
            "CRM_COOKIE_SECURE=false запрещён в production"
        )
    return secure


def trusted_proxy_networks(environ=None) -> tuple[IPv4Network | IPv6Network, ...]:
    """Разобрать явный список CIDR сетей доверенных прямых прокси.

    Пустое значение означает отсутствие доверия forwarded-заголовкам. Любой
    невалидный элемент закрывает startup, а не отключает проверку частично.
    """
    env = _environment(environ)
    raw_value = env.get("CRM_TRUSTED_PROXY_NETWORKS", "").strip()
    if not raw_value:
        return ()

    networks = []
    for raw_network in raw_value.split(","):
        value = raw_network.strip()
        try:
            networks.append(ip_network(value, strict=True))
        except ValueError as exc:
            raise RuntimeConfigError(
                f"CRM_TRUSTED_PROXY_NETWORKS содержит невалидный CIDR: {value!r}"
            ) from exc
    return tuple(networks)


def validate_startup_config(environ=None) -> bool:
    """Проверить security-конфигурацию до создания FastAPI-приложения."""
    trusted_proxy_networks(environ)
    return auth_cookie_secure(environ)


def uploads_dir(environ=None) -> Path:
    """Вернуть единый настроенный каталог загрузок."""
    env = _environment(environ)
    configured = env.get("CRM_UPLOADS_DIR")
    if configured:
        return Path(configured)
    return Path(env.get("CRM_DATA_DIR", ".")) / "uploads"
