"""Безопасная доставка webhook с pinning IP и ограниченными попытками."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
from typing import Callable
import uuid
from urllib.parse import SplitResult, urlsplit

import models
from database import SessionLocal
from webhook_security import (
    WebhookEncryptionConfigError,
    WebhookSecretDeliveryError,
    decrypt_secret,
    obtain_fernet,
)

logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 500
CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (0.5, 1.0)
MAX_WORKERS = 4
MAX_PENDING = 32


class WebhookURLValidationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class WebhookTransportError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, response_status: int | None = None):
        self.code = code
        self.retryable = retryable
        self.response_status = response_status
        super().__init__(code)


@dataclass(frozen=True)
class ResolvedWebhookTarget:
    scheme: str
    hostname: str
    port: int
    path: str
    ip: str


@dataclass(frozen=True)
class DeliveryOutcome:
    correlation_id: str
    status: str
    attempts: int
    response_status: int | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_url_shape(url: str) -> SplitResult:
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise WebhookURLValidationError("url_invalid")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise WebhookURLValidationError("url_invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise WebhookURLValidationError("url_invalid") from exc
    if parsed.scheme not in ("http", "https"):
        raise WebhookURLValidationError("url_scheme_invalid")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise WebhookURLValidationError("url_userinfo_or_host_invalid")
    if "%" in parsed.hostname:
        raise WebhookURLValidationError("url_host_invalid")
    if parsed.query or parsed.fragment:
        raise WebhookURLValidationError("url_query_or_fragment_forbidden")
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise WebhookURLValidationError("url_port_forbidden")
    return parsed


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # is_global исключает private/loopback/link-local/reserved/multicast/unspecified
    # и numeric alternate forms после канонического разбора stdlib.
    return address.is_global


def resolve_public_target(
    url: str,
    *,
    resolver: Callable[..., list] | None = None,
) -> ResolvedWebhookTarget:
    """Проверить URL и вернуть один публичный numeric IP для connect."""
    parsed = _validate_url_shape(url)
    resolver = resolver or socket.getaddrinfo
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        address_info = resolver(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, socket.gaierror) as exc:
        raise WebhookURLValidationError("dns_failed") from exc
    if not address_info:
        raise WebhookURLValidationError("dns_failed")

    for info in address_info:
        try:
            numeric_ip = str(ipaddress.ip_address(info[4][0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise WebhookURLValidationError("dns_result_invalid") from exc
        if not _is_public_ip(numeric_ip):
            raise WebhookURLValidationError("address_not_public")
        return ResolvedWebhookTarget(
            scheme=parsed.scheme,
            hostname=hostname,
            port=port,
            path=parsed.path or "/",
            ip=numeric_ip,
        )
    raise WebhookURLValidationError("dns_failed")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: ResolvedWebhookTarget, timeout: float):
        super().__init__(target.hostname, target.port, timeout=timeout)
        self._pinned_ip = target.ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), timeout=self.timeout
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: ResolvedWebhookTarget, timeout: float):
        context = ssl.create_default_context()
        super().__init__(
            target.hostname,
            target.port,
            timeout=timeout,
            context=context,
        )
        self._pinned_ip = target.ip

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_ip, self.port), timeout=self.timeout
        )
        try:
            self.sock = self._context.wrap_socket(
                raw_socket, server_hostname=self.host
            )
        except Exception:
            raw_socket.close()
            raise


def pinned_http_transport(
    target: ResolvedWebhookTarget,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> int:
    """Подключиться к уже проверенному numeric IP, сохранив SNI/Host исходного URL."""
    connection_type = (
        _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
    )
    connection = connection_type(target, timeout)
    try:
        connection.request("POST", target.path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status
    finally:
        connection.close()


def _request_once(
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    transport: Callable[..., int],
    resolver: Callable[..., list],
    timeout: float,
) -> int:
    try:
        target = resolve_public_target(url, resolver=resolver)
    except WebhookURLValidationError as exc:
        raise WebhookTransportError(exc.code, retryable=False) from exc
    try:
        return transport(target, body, headers, timeout)
    except (socket.timeout, TimeoutError) as exc:
        raise WebhookTransportError("timeout", retryable=True) from exc
    except WebhookTransportError:
        raise
    except OSError as exc:
        raise WebhookTransportError("network_error", retryable=True) from exc
    except Exception as exc:
        # Наружу не выпускаются тексты исключений: они способны содержать URL.
        raise WebhookTransportError("transport_error", retryable=False) from exc


def build_signed_body(payload: dict, secret: str | None) -> tuple[bytes, dict[str, str]]:
    """Сериализовать body один раз и подписать именно эти байты."""
    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = signature
    return body, headers


def create_delivery(
    session,
    *,
    webhook_id: int | None,
    event: str,
    tenant_id: int | None,
) -> models.WebhookDelivery:
    delivery = models.WebhookDelivery(
        correlation_id=str(uuid.uuid4()),
        webhook_id=webhook_id,
        event=event,
        status="queued",
        tenant_id=tenant_id,
    )
    session.add(delivery)
    session.commit()
    session.refresh(delivery)
    return delivery


def _finish_delivery(
    session,
    delivery: models.WebhookDelivery,
    *,
    status: str,
    attempt_count: int,
    error_code: str | None,
    response_status: int | None,
) -> None:
    delivery.status = status
    delivery.attempt_count = attempt_count
    delivery.last_error_code = error_code
    delivery.response_status = response_status
    delivery.completed_at = _now()
    delivery.next_attempt_at = None
    session.commit()


def mark_dead_letter(delivery_id: int) -> str | None:
    session = SessionLocal()
    try:
        delivery = session.get(models.WebhookDelivery, delivery_id)
        if delivery and delivery.completed_at is None:
            _finish_delivery(
                session,
                delivery,
                status="dead_letter",
                attempt_count=delivery.attempt_count,
                error_code="queue_full",
                response_status=None,
            )
            return delivery.correlation_id
        return delivery.correlation_id if delivery else None
    finally:
        session.close()


def deliver_delivery(
    delivery_id: int,
    payload: dict,
    *,
    session_factory=SessionLocal,
    transport: Callable[..., int] = pinned_http_transport,
    resolver: Callable[..., list] = socket.getaddrinfo,
    sleeper: Callable[[float], None] = time.sleep,
    timeout: float = CONNECT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> DeliveryOutcome:
    """Доставить payload с ограниченными retry и записью каждой попытки."""
    if max_attempts < 1 or max_attempts > 10:
        raise ValueError("Недопустимое число попыток webhook")

    session = session_factory()
    try:
        delivery = session.get(models.WebhookDelivery, delivery_id)
        if delivery is None:
            raise LookupError("webhook delivery not found")
        webhook = (
            session.get(models.Webhook, delivery.webhook_id)
            if delivery.webhook_id is not None
            else None
        )
        if webhook is None:
            _finish_delivery(
                session, delivery, status="failed", attempt_count=0,
                error_code="webhook_missing", response_status=None,
            )
            return DeliveryOutcome(
                delivery.correlation_id, "failed", 0, error_code="webhook_missing"
            )

        try:
            fernet = obtain_fernet()
            secret = decrypt_secret(webhook.secret, fernet)
        except WebhookEncryptionConfigError:
            error_code = "encryption_key_unavailable"
            _finish_delivery(
                session, delivery, status="failed", attempt_count=0,
                error_code=error_code, response_status=None,
            )
            return DeliveryOutcome(
                delivery.correlation_id, "failed", 0, error_code=error_code
            )
        except WebhookSecretDeliveryError as exc:
            _finish_delivery(
                session, delivery, status="failed", attempt_count=0,
                error_code=exc.code, response_status=None,
            )
            return DeliveryOutcome(
                delivery.correlation_id, "failed", 0, error_code=exc.code
            )

        try:
            body, headers = build_signed_body(payload, secret)
        except (TypeError, ValueError, UnicodeError):
            error_code = "payload_invalid"
            _finish_delivery(
                session, delivery, status="failed", attempt_count=0,
                error_code=error_code, response_status=None,
            )
            return DeliveryOutcome(
                delivery.correlation_id, "failed", 0, error_code=error_code
            )

        last_error = "transport_error"
        last_status = None
        for attempt_number in range(1, max_attempts + 1):
            attempt = models.WebhookAttempt(
                delivery_id=delivery.id,
                attempt_number=attempt_number,
                status="running",
            )
            session.add(attempt)
            delivery.status = "delivering"
            delivery.attempt_count = attempt_number
            attempt.started_at = _now()
            session.commit()

            try:
                response_status = _request_once(
                    webhook.url,
                    body,
                    headers,
                    transport=transport,
                    resolver=resolver,
                    timeout=timeout,
                )
            except WebhookTransportError as exc:
                last_error = exc.code
                last_status = exc.response_status
                attempt.status = "failed"
                attempt.error_code = exc.code
                attempt.response_status = exc.response_status
                attempt.completed_at = _now()
                delivery.last_error_code = exc.code
                delivery.response_status = exc.response_status
                session.commit()
                logger.warning(
                    "Webhook delivery failed correlation_id=%s attempt=%s code=%s",
                    delivery.correlation_id, attempt_number, exc.code,
                )
                if not exc.retryable or attempt_number == max_attempts:
                    terminal = "dead_letter" if exc.retryable else "failed"
                    _finish_delivery(
                        session, delivery, status=terminal,
                        attempt_count=attempt_number, error_code=exc.code,
                        response_status=exc.response_status,
                    )
                    return DeliveryOutcome(
                        delivery.correlation_id, terminal, attempt_number,
                        exc.response_status, exc.code,
                    )
                delay = RETRY_BACKOFF_SECONDS[
                    min(attempt_number - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                delivery.next_attempt_at = _now() + timedelta(seconds=delay)
                session.commit()
                sleeper(delay)
                continue

            if 200 <= response_status < 300:
                attempt.status = "succeeded"
                attempt.response_status = response_status
                attempt.completed_at = _now()
                session.add(attempt)
                _finish_delivery(
                    session, delivery, status="succeeded",
                    attempt_count=attempt_number, error_code=None,
                    response_status=response_status,
                )
                return DeliveryOutcome(
                    delivery.correlation_id, "succeeded", attempt_number, response_status
                )

            if 300 <= response_status < 400:
                error_code = "redirect_rejected"
                retryable = False
            elif response_status in (408, 425, 429) or 500 <= response_status < 600:
                error_code = "http_retryable"
                retryable = True
            else:
                error_code = "http_rejected"
                retryable = False
            last_error = error_code
            last_status = response_status
            logger.warning(
                "Webhook delivery failed correlation_id=%s attempt=%s code=%s",
                delivery.correlation_id,
                attempt_number,
                error_code,
            )
            attempt.status = "failed"
            attempt.error_code = error_code
            attempt.response_status = response_status
            attempt.completed_at = _now()
            delivery.last_error_code = error_code
            delivery.response_status = response_status
            session.commit()

            if not retryable or attempt_number == max_attempts:
                terminal = "dead_letter" if retryable else "failed"
                _finish_delivery(
                    session, delivery, status=terminal,
                    attempt_count=attempt_number, error_code=error_code,
                    response_status=response_status,
                )
                return DeliveryOutcome(
                    delivery.correlation_id, terminal, attempt_number,
                    response_status, error_code,
                )
            delay = RETRY_BACKOFF_SECONDS[
                min(attempt_number - 1, len(RETRY_BACKOFF_SECONDS) - 1)
            ]
            delivery.next_attempt_at = _now() + timedelta(seconds=delay)
            session.commit()
            sleeper(delay)

        raise AssertionError(f"unreachable webhook retry state: {last_error}:{last_status}")
    finally:
        session.close()


class BoundedDeliveryExecutor:
    """Фиксированный пул и жёсткий лимит ожидающих задач."""

    def __init__(self, max_workers: int = MAX_WORKERS, max_pending: int = MAX_PENDING):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="webhook-delivery",
        )
        self._slots = threading.BoundedSemaphore(max_pending)

    def submit(self, function, *args, **kwargs) -> bool:
        if not self._slots.acquire(blocking=False):
            return False

        def _run():
            try:
                function(*args, **kwargs)
            finally:
                self._slots.release()

        self._executor.submit(_run)
        return True


_executor = BoundedDeliveryExecutor()
submit_delivery = _executor.submit


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DeliveryOutcome",
    "ResolvedWebhookTarget",
    "WebhookTransportError",
    "WebhookURLValidationError",
    "build_signed_body",
    "create_delivery",
    "deliver_delivery",
    "mark_dead_letter",
    "pinned_http_transport",
    "resolve_public_target",
    "submit_delivery",
]
