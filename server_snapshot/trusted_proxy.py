"""Pure-ASGI обработка forwarded-заголовков только от явных подсетей прокси."""
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address


_FORWARDED_HEADERS = {
    b"x-forwarded-for",
    b"x-forwarded-host",
    b"x-forwarded-proto",
}


def _single_header(scope, name: bytes) -> str | None:
    """Вернуть одно ASCII-значение; дубликаты считаются неоднозначными."""
    values = [value for key, value in scope.get("headers", []) if key.lower() == name]
    if len(values) != 1:
        return None
    try:
        value = values[0].decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return value or None


def _client_address(scope) -> IPv4Address | IPv6Address | None:
    peer = scope.get("client")
    if not isinstance(peer, (tuple, list)) or not peer:
        return None
    try:
        return ip_address(peer[0])
    except (TypeError, ValueError):
        return None


def _is_trusted_peer(
    scope,
    networks: tuple[IPv4Network | IPv6Network, ...],
) -> bool:
    address = _client_address(scope)
    if address is None:
        return False
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def _forwarded_client(value: str | None) -> IPv4Address | IPv6Address | None:
    """Принять только один IP: nginx обязан перезаписывать X-Forwarded-For."""
    if value is None or "," in value:
        return None
    try:
        return ip_address(value)
    except ValueError:
        return None


def _forwarded_scheme(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.lower()
    return normalized if normalized in {"http", "https"} else None


def _forwarded_host(value: str | None) -> str | None:
    if (
        value is None
        or not value.isascii()
        or len(value) > 255
        or any(char.isspace() or ord(char) < 33 for char in value)
    ):
        return None
    return value


class TrustedProxyMiddleware:
    """Применять forwarded identity только при попадании адреса в список CIDR.

    Промежуточный слой намеренно не зависит от Uvicorn: его ``forwarded-allow-ips``
    в версии 0.30 не выполняет CIDR-сопоставление. Невалидные и неоднозначные
    forwarded-заголовки удаляются из ASGI scope и не меняют прямое соединение.
    """

    def __init__(self, app, networks: tuple[IPv4Network | IPv6Network, ...]):
        self.app = app
        self.networks = tuple(networks)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Копируем scope: входной объект принадлежит серверному адаптеру, а
        # middleware не должен менять его до явной проверки доверия.
        downstream_scope = dict(scope)
        headers = list(scope.get("headers", []))
        clean_headers = [
            (name, value) for name, value in headers
            if name.lower() not in _FORWARDED_HEADERS
        ]
        downstream_scope["headers"] = clean_headers

        if not _is_trusted_peer(scope, self.networks):
            await self.app(downstream_scope, receive, send)
            return

        forwarded_for = _single_header(scope, b"x-forwarded-for")
        forwarded_proto = _single_header(scope, b"x-forwarded-proto")
        forwarded_host = _single_header(scope, b"x-forwarded-host")

        client = _forwarded_client(forwarded_for)
        scheme = _forwarded_scheme(forwarded_proto)
        host = _forwarded_host(forwarded_host)

        if client is not None:
            downstream_scope["client"] = (str(client), 0)
            clean_headers.append((b"x-forwarded-for", str(client).encode("ascii")))
        if scheme is not None:
            downstream_scope["scheme"] = scheme
            clean_headers.append((b"x-forwarded-proto", scheme.encode("ascii")))
        if host is not None:
            clean_headers = [
                (name, value) for name, value in clean_headers
                if name.lower() != b"host"
            ]
            clean_headers.extend((
                (b"host", host.encode("ascii")),
                (b"x-forwarded-host", host.encode("ascii")),
            ))

        downstream_scope["headers"] = clean_headers
        await self.app(downstream_scope, receive, send)
