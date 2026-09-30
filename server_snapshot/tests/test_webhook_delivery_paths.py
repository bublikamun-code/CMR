"""Непокрытые ветки `webhook_delivery.py` и `webhook_security.py`.

Файл НЕ дублирует `tests/test_webhook_security.py` (там уже SSRF-матрица,
подпись тела, ретрай по таймауту, redirect и legacy-plaintext) и
`tests/test_activity_and_webhooks.py` (там HTTP-контуры роутера).

Что здесь закрывается и почему это важно:

* `webhook_security` — источник ключа и его файл на диске (гонка создания,
  права, явный путь), строгая нормализация ключа, разбор сохранённого секрета
  (не-utf8, пусто, слишком длинное, legacy-plaintext) и явная конвертация.
  Каждая из этих проверок — гарантия «секрет не утечёт и не будет доставлен
  как мусор»;
* `webhook_delivery` — классификация ошибок транспорта и HTTP-статусов
  (что повторяется, а что нет), отсутствие утечек URL/пароля в лог, запись
  каждой попытки и `response_status` в аудит, backoff-планирование, отмена
  доставки из переполненной очереди, жёсткий лимит ожидающих задач;
* SSRF-гарантии уровня формы URL, DNS-ответа и IP-pinning: connect всегда
  идёт на уже проверенный numeric IP, SNI/Host остаются исходными.

Сеть не используется: автофикстура `_no_real_network` подменяет
`socket.socket`, `socket.create_connection`, `socket.gethostbyname` и
`socket.getaddrinfo` на «взрыв», поэтому любой непокрытый сетевой вызов
немедленно уронит тест. Резолвер и транспорт во всех остальных тестах
передаются явно, а сам файл не пользуется фикстурой `client`.

Приватные имена (`_PinnedHTTPConnection`, `_PinnedHTTPSConnection`,
`_create_key_file_exclusive`, `_resolved_key_path`) импортируются напрямую:
именно в них живут гарантии IP-pinning и эксклюзивного создания файла ключа.

Что осталось непокрытым намеренно — недостижимый код:
* `webhook_delivery.py:103-104` — `_is_public_ip` получает уже разобранный
  `str(ipaddress.ip_address(...))`, `ValueError` там невозможен;
* `webhook_delivery.py:146` и `:478` — цикл либо возвращает результат, либо
  бросает, либо выходит по последней попытке;
* `webhook_security.py:83-84, 88` — строка длиной 44 по regex всегда
  декодируется в 32 байта без ошибки base64.

Известный живой дефект помечен `@pytest.mark.known_bug` + `xfail`:
`resolve_public_target` не ловит `UnicodeError` от IDNA-кодирования.
"""
from __future__ import annotations

import base64
import socket
import ssl
import stat
import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import database
import models
import webhook_delivery as delivery_module
import webhook_security as security_module
from webhook_delivery import (
    BoundedDeliveryExecutor,
    WebhookTransportError,
    WebhookURLValidationError,
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    create_delivery,
    deliver_delivery,
    mark_dead_letter,
    pinned_http_transport,
    resolve_public_target,
)
from webhook_security import (
    MAX_CIPHERTEXT_LENGTH,
    MAX_SECRET_LENGTH,
    WebhookEncryptionConfigError,
    WebhookSecretDeliveryError,
    WebhookSecretState,
    classify_stored_secret,
    convert_legacy_secret,
    decrypt_secret,
    encrypt_secret,
    normalize_fernet_key,
    obtain_fernet,
    webhook_key_path,
)

# Публичный адрес из документации EXAMPLE, реальный DNS здесь не резолвится:
# резолвер во всех тестах подменён.
PUBLIC_IP = "93.184.216.34"

VALID_HEX_KEY = "00" * 31 + "07"
OTHER_HEX_KEY = "00" * 31 + "08"


# ---------------------------------------------------------------------------
# Автофикстуры и фабрики
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Запретить любую настоящую сеть внутри этого файла.

    `deliver_delivery` и `resolve_public_target` принимают резолвер/транспорт
    параметром, поэтому «пропущенная» подмена должна приводить к падению, а
    не к тихому обращению к боевому DNS.
    """

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("тест не должен ходить в сеть")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket, "gethostbyname", _forbidden)

def _public_resolver(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]

def _webhook(db, **overrides):
    values = {
        "url": "https://example.com/hook",
        "events": '["card.created"]',
        "is_active": True,
        "tenant_id": None,
    }
    values.update(overrides)
    webhook = models.Webhook(**values)
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook

def _delivery_fixture(db, secret="delivery-secret"):
    """Доставка с зашифрованным секретом; возвращает id строки аудита."""
    fernet = obtain_fernet()
    webhook = _webhook(db, secret=encrypt_secret(secret, fernet))
    delivery = create_delivery(
        db, webhook_id=webhook.id, event="card.created", tenant_id=None
    )
    return delivery.id

def _must_not_be_called(*_args, **_kwargs):
    raise AssertionError("transport не должен вызываться")

def _write_key_file(path: Path, hex_key: str, mode: int = 0o600) -> Path:
    path.write_text(hex_key, encoding="ascii")
    path.chmod(mode)
    return path

def _fernet_for(hex_key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(bytes.fromhex(hex_key)))

def _no_sleeps(_delay):
    return None


# ---------------------------------------------------------------------------
# webhook_security: нормализация ключа
# ---------------------------------------------------------------------------

def test_normalize_fernet_key_accepts_hex_bytes_and_urlsafe_base64():
    """Ровно 64 hex и 44 url-safe base64 дают одни и те же 32 байта."""
    raw = normalize_fernet_key(VALID_HEX_KEY)
    assert len(raw) == 32
    assert normalize_fernet_key(VALID_HEX_KEY.encode("ascii")) == raw
    assert normalize_fernet_key(f"  {VALID_HEX_KEY}\n") == raw

    # Ключ, сгенерированный библиотекой в рантайме, принимается без изменений
    # и остаётся взаимозаменяемым с своей base64-формой.
    runtime_key = Fernet.generate_key()
    assert normalize_fernet_key(runtime_key) == base64.urlsafe_b64decode(runtime_key)
    same_key = Fernet(base64.urlsafe_b64encode(normalize_fernet_key(runtime_key)))
    assert same_key.decrypt(Fernet(runtime_key).encrypt(b"exchange")) == b"exchange"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(b"\xff" * 32, id="non_ascii_bytes"),
        pytest.param(1234567890, id="int"),
        pytest.param(None, id="none"),
        pytest.param(["00" * 32], id="list"),
    ],
)
def test_normalize_fernet_key_rejects_anything_not_a_key(value):
    """Ни один мусорный тип не проходит наружу как ключ, и текст ключа не echoes."""
    with pytest.raises(WebhookEncryptionConfigError) as caught:
        normalize_fernet_key(value)
    assert str(caught.value) == "Ключ шифрования webhook имеет неверный формат"

def test_normalize_fernet_key_accepts_only_strict_lengths():
    """Ровно 64 hex или ровно 44 url-safe base64; ни символом больше, ни меньше."""
    raw = bytes.fromhex("fbefbe" * 10 + "fbfb")  # base64 от него содержит '-' и '_'
    valid = base64.urlsafe_b64encode(raw).decode("ascii")
    assert len(valid) == 44 and "-" in valid and "_" in valid
    assert normalize_fernet_key(valid) == raw

    for bad in (
        "00" * 30,
        "00" * 33,
        valid + "=",
        valid[:-1],
        valid.replace("-", "+").replace("_", "/"),  # стандартный, не url-safe алфавит
        valid.replace("-", "+"),  # смешанный алфавит тоже не ключ
    ):
        with pytest.raises(WebhookEncryptionConfigError):
            normalize_fernet_key(bad)


# ---------------------------------------------------------------------------
# webhook_security: путь и файл ключа
# ---------------------------------------------------------------------------

def test_key_path_prefers_explicit_argument_then_env_then_data_dir(tmp_path):
    """Приоритет источника: аргумент > CRM_WEBHOOK_ENCRYPTION_KEY_FILE > CRM_DATA_DIR."""
    app_dir = tmp_path / "app"
    data_dir = tmp_path / "data"
    for directory in (app_dir, data_dir):
        directory.mkdir()

    assert webhook_key_path({"CRM_DATA_DIR": str(data_dir)}) == (
        data_dir / ".webhook_encryption_key"
    )
    assert webhook_key_path({}, app_dir=app_dir) == (
        app_dir / ".webhook_encryption_key"
    )

    environ = {
        "CRM_DATA_DIR": str(data_dir),
        "CRM_WEBHOOK_ENCRYPTION_KEY_FILE": str(tmp_path / "from_env"),
    }
    assert security_module._resolved_key_path(environ, None) == tmp_path / "from_env"
    assert security_module._resolved_key_path(environ, tmp_path / "explicit") == (
        tmp_path / "explicit"
    )
    # Пустая переменная не должна перебивать канонический путь.
    assert security_module._resolved_key_path(
        {**environ, "CRM_WEBHOOK_ENCRYPTION_KEY_FILE": "   "}, None
    ) == data_dir / ".webhook_encryption_key"

def test_key_file_from_environment_is_used_verbatim(tmp_path):
    """Файл из переменной читается, а не перезаписывается и не генерируется."""
    key_path = _write_key_file(tmp_path / "custom.key", VALID_HEX_KEY)
    environ = {
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "development",
        "CRM_WEBHOOK_ENCRYPTION_KEY_FILE": str(key_path),
    }

    fernet = obtain_fernet(environ)
    assert _fernet_for(VALID_HEX_KEY).decrypt(fernet.encrypt(b"from-file")) is not None
    assert key_path.read_text(encoding="ascii") == VALID_HEX_KEY
    assert not (tmp_path / ".webhook_encryption_key").exists()

def test_key_file_argument_wins_over_key_file_environment(tmp_path):
    """Явный аргумент — сильнее переменной окружения."""
    from_env = _write_key_file(tmp_path / "from_env", VALID_HEX_KEY)
    explicit = _write_key_file(tmp_path / "explicit", OTHER_HEX_KEY)
    environ = {
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "development",
        "CRM_WEBHOOK_ENCRYPTION_KEY_FILE": str(from_env),
    }

    fernet = obtain_fernet(environ, key_path=explicit)
    assert _fernet_for(OTHER_HEX_KEY).decrypt(fernet.encrypt(b"explicit")) is not None
    assert from_env.read_text(encoding="ascii") == VALID_HEX_KEY

def test_key_file_with_group_or_world_access_is_refused(tmp_path):
    """Файл ключа, доступный группе или миру, — ошибка конфигурации."""
    key_path = _write_key_file(tmp_path / ".webhook_encryption_key", VALID_HEX_KEY, 0o644)
    environ = {"CRM_DATA_DIR": str(tmp_path), "CRM_DEPLOYMENT": "development"}

    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet(environ)
    assert "0600" in str(caught.value)
    assert VALID_HEX_KEY not in str(caught.value)
    # Права не «чинятся» молча: файл остаётся как был.
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o644

def test_unreadable_key_file_is_reported_without_path_or_content(tmp_path):
    """Директория вместо файла ключа даёт контролируемую ошибку."""
    broken = tmp_path / ".webhook_encryption_key"
    broken.mkdir(mode=0o700)
    environ = {"CRM_DATA_DIR": str(tmp_path), "CRM_DEPLOYMENT": "development"}

    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet(environ)
    assert "Не удалось прочитать" in str(caught.value)
    assert str(tmp_path) not in str(caught.value)

def test_oversized_key_file_is_refused(tmp_path):
    """Файл ключа больше 256 байт не читается «как есть»."""
    key_path = tmp_path / ".webhook_encryption_key"
    key_path.write_text("00" * 200, encoding="ascii")
    key_path.chmod(0o600)
    environ = {"CRM_DATA_DIR": str(tmp_path), "CRM_DEPLOYMENT": "development"}

    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet(environ)
    assert "неверный формат" in str(caught.value)

def test_key_file_creation_never_overwrites_an_existing_key(tmp_path):
    """Параллельные процессы не затирают уже созданный ключ.

    `_create_key_file_exclusive` — приватный хелпер, но именно он реализует
    гарантию «key file создаётся эксклюзивно», поэтому проверяется напрямую.
    """
    key_path = tmp_path / ".webhook_encryption_key"

    security_module._create_key_file_exclusive(key_path)
    first_content = key_path.read_text(encoding="ascii")
    assert len(first_content) == 64
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    security_module._create_key_file_exclusive(key_path)
    assert key_path.read_text(encoding="ascii") == first_content
    assert list(tmp_path.glob(".webhook_encryption_key.*.tmp")) == []

def test_key_file_creation_survives_lost_race_and_cleans_temporary(
    tmp_path, monkeypatch
):
    """Проигравший в гонке процесс не падает и не оставляет временных файлов."""
    key_path = _write_key_file(tmp_path / ".webhook_encryption_key", VALID_HEX_KEY)
    observed = {}

    def racing_link(temporary_path, target_path, **_kwargs):
        # Сценарий «проигравший»: временный файл уже удалён конкурентом.
        observed["target"] = str(target_path)
        Path(temporary_path).unlink()
        raise FileExistsError(target_path)

    monkeypatch.setattr(security_module.os, "link", racing_link)
    security_module._create_key_file_exclusive(key_path)

    assert observed["target"] == str(key_path)
    assert key_path.read_text(encoding="ascii") == VALID_HEX_KEY
    assert list(tmp_path.iterdir()) == [key_path]

def test_existing_key_file_is_reused_instead_of_regenerated(tmp_path):
    """В development второй запуск читает файл, а не генерирует новый ключ."""
    key_path = tmp_path / ".webhook_encryption_key"
    _write_key_file(key_path, VALID_HEX_KEY)
    environ = {"CRM_DATA_DIR": str(tmp_path), "CRM_DEPLOYMENT": "development"}

    fernet = obtain_fernet(environ)
    assert key_path.read_text(encoding="ascii") == VALID_HEX_KEY
    assert _fernet_for(VALID_HEX_KEY).decrypt(fernet.encrypt(b"from-file")) is not None

    # Повторный вызов обязан остаться на том же ключе (иначе секреты в БД
    # перестали бы расшифровываться после рестарта).
    second = obtain_fernet(environ)
    assert second.decrypt(fernet.encrypt(b"same-key")) is not None
    assert key_path.read_text(encoding="ascii") == VALID_HEX_KEY

def test_obtain_fernet_without_cryptography_is_a_config_error(monkeypatch):
    """Без библиотеки шифрования доставка падает, а не идёт с plaintext."""
    monkeypatch.setattr(security_module, "Fernet", None)
    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet({"CRM_WEBHOOK_ENCRYPTION_KEY": VALID_HEX_KEY})
    assert "недоступно" in str(caught.value)

def test_broken_fernet_constructor_becomes_config_error(monkeypatch):
    """Ошибка конструирования Fernet не выпускается наружу как есть."""

    class ExplodingFernet:
        def __init__(self, _key):
            raise ValueError("key rejected by library")

    monkeypatch.setattr(security_module, "Fernet", ExplodingFernet)
    with pytest.raises(WebhookEncryptionConfigError) as caught:
        obtain_fernet({"CRM_WEBHOOK_ENCRYPTION_KEY": VALID_HEX_KEY})
    assert "неверный формат" in str(caught.value)
    assert "rejected by library" not in str(caught.value)

def test_validate_encryption_config_is_a_real_readiness_check(tmp_path):
    """Проверка конфигурации возвращает True только при рабочем ключе."""
    configured = {
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DEPLOYMENT": "test",
        "CRM_WEBHOOK_ENCRYPTION_KEY": VALID_HEX_KEY,
    }
    assert security_module.validate_encryption_config(configured) is True

    production_without_key = {
        "CRM_DATA_DIR": str(tmp_path / "empty"),
        "CRM_DEPLOYMENT": "production",
    }
    with pytest.raises(WebhookEncryptionConfigError) as caught:
        security_module.validate_encryption_config(production_without_key)
    assert "не настроено" in str(caught.value)


# ---------------------------------------------------------------------------
# webhook_security: разбор сохранённого секрета
# ---------------------------------------------------------------------------


class _FakeFernet:
    """Подмена Fernet: отдаёт заранее заданные байты вместо расшифровки."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def decrypt(self, _token):
        self.calls += 1
        return self.payload

def test_classify_reports_not_configured_for_empty_values():
    """Пустой секрет — это «не настроено», а не «испорчен»."""
    for empty in (None, ""):
        classified = classify_stored_secret(empty, obtain_fernet())
        assert classified.state is WebhookSecretState.NOT_CONFIGURED
        assert classified.plaintext is None


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(12345, id="not_a_string"),
        pytest.param("   padded   ", id="untrimmed"),
        pytest.param("A" * (MAX_CIPHERTEXT_LENGTH + 1), id="too_long"),
    ],
)
def test_classify_rejects_values_that_cannot_be_ciphertext(stored):
    """Значение, которое не может быть Fernet-токеном, не идёт в расшифровку."""
    fernet = obtain_fernet()
    classified = classify_stored_secret(stored, fernet)
    assert classified.state is WebhookSecretState.INVALID_CIPHERTEXT
    assert classified.plaintext is None

def test_decrypted_secret_must_be_utf8_text():
    """Не-utf8, пустой и слишком длинный результат — битый ciphertext, не legacy."""
    for payload in (b"\xff\xfe\x00", b"", b"x" * (MAX_SECRET_LENGTH + 1)):
        classified = classify_stored_secret("A" * 100, _FakeFernet(payload))
        assert classified.state is WebhookSecretState.INVALID_CIPHERTEXT
        assert classified.plaintext is None

def test_decrypt_secret_returns_none_when_nothing_stored():
    """Отсутствие секрета не превращается в ошибку доставки."""
    fernet = obtain_fernet()
    assert decrypt_secret(None, fernet) is None
    assert decrypt_secret("", fernet) is None

def test_decrypt_secret_maps_failures_to_stable_codes():
    """Наружу уходит только код; текста секрета/исключения нет."""
    fernet = obtain_fernet()
    with pytest.raises(WebhookSecretDeliveryError) as legacy:
        decrypt_secret("legacy-at-rest", fernet)
    assert legacy.value.code == "secret_legacy_plaintext"

    with pytest.raises(WebhookSecretDeliveryError) as broken:
        decrypt_secret("gAAAAA" + "A" * 120, fernet)
    assert broken.value.code == "secret_ciphertext_invalid"
    assert "gAAAA" not in str(broken.value)

def test_encrypt_secret_rejects_empty_and_oversized():
    """Пустой или гигантский секрет не шифруется «на всякий случай»."""
    fernet = obtain_fernet()
    for bad in ("", None, "x" * (MAX_SECRET_LENGTH + 1)):
        with pytest.raises(ValueError):
            encrypt_secret(bad, fernet)

def test_convert_legacy_secret_is_idempotent_and_never_echoes_plaintext():
    """Явная конвертация: один раз превращает plaintext в токен, дальше — no-op."""
    fernet = obtain_fernet()
    token = convert_legacy_secret("legacy-secret", fernet)

    assert token != "legacy-secret"
    assert decrypt_secret(token, fernet) == "legacy-secret"
    assert convert_legacy_secret(token, fernet) == token


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param(None, id="not_a_string"),
        pytest.param(b"bytes-secret", id="bytes"),
    ],
)
def test_convert_legacy_secret_refuses_non_string_input(value):
    """Конвертация принимает только строку — иначе секрет потеряется."""
    with pytest.raises(ValueError):
        convert_legacy_secret(value, obtain_fernet())

def test_convert_legacy_secret_refuses_damaged_ciphertext():
    """Испорченный токен нельзя «починить», превратив его в plaintext-токен."""
    with pytest.raises(ValueError):
        convert_legacy_secret("gAAAAA" + "A" * 120, obtain_fernet())

def test_convert_legacy_secret_refuses_oversized_plaintext():
    """Слишком длинный legacy-plaintext не шифруется и не падает в БД."""
    fernet = obtain_fernet()
    # Без хвостовых пробелов значение классифицируется как legacy и доходит
    # до проверки длины; с пробелом — отбрасывается раньше как «мусор».
    assert classify_stored_secret("секрет" * 1000, fernet).state is (
        WebhookSecretState.LEGACY_PLAINTEXT
    )
    with pytest.raises(ValueError):
        convert_legacy_secret("секрет" * 1000, fernet)

    with pytest.raises(ValueError):
        convert_legacy_secret("секрет " * 700, fernet)


# ---------------------------------------------------------------------------
# webhook_delivery: форма URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_code"),
    [
        pytest.param("", "url_invalid", id="empty"),
        pytest.param(
            "https://example.com/ho ok", "url_invalid", id="space_in_url"
        ),
        pytest.param(
            "https://example.com/hook\nX-Injected: 1",
            "url_invalid",
            id="newline_header_injection",
        ),
        pytest.param(
            "https://example.com/hook\x00", "url_invalid", id="null_byte"
        ),
        pytest.param(
            "https://example.com/" + "a" * 500, "url_invalid", id="too_long"
        ),
        pytest.param(
            "https://example.com:99999/hook", "url_invalid", id="port_out_of_range"
        ),
        pytest.param(
            "https://exa%6dple.com/hook", "url_host_invalid", id="percent_encoded_host"
        ),
        pytest.param("mailto:admin@example.com", "url_scheme_invalid", id="mailto"),
        pytest.param(
            "file:///etc/passwd", "url_scheme_invalid", id="file_scheme"
        ),

    ],
)
def test_url_shape_rejects_unsafe_forms_with_stable_codes(url, expected_code):
    """Каждая небезопасная форма URL даёт свой стабильный код отказа."""
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target(url, resolver=_public_resolver)
    assert caught.value.code == expected_code

def test_trailing_dot_host_is_resolved_not_blocked_by_shape():
    """«example.com.» — валидная форма; запрет обеспечивает проверка IP, не форма."""
    # Точка в конце приводит к FQDN-резолву, который может вернуть 127.0.0.1.
    resolver = lambda *_a, **_kw: [  # noqa: E731
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ]
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target("https://example.com./hook", resolver=resolver)
    assert caught.value.code == "address_not_public"

def test_default_ports_are_allowed_and_forwarded_to_resolver():
    """Стандартные порты не блокируются: :80 для http и :443 для https."""
    seen = []

    def recording_resolver(host, port, **_kwargs):
        seen.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]

    http_target = resolve_public_target("http://example.com/hook", resolver=recording_resolver)
    https_target = resolve_public_target("https://example.com/hook", resolver=recording_resolver)

    assert (http_target.port, https_target.port) == (80, 443)
    assert seen == [("example.com", 80), ("example.com", 443)]
    assert http_target.ip == https_target.ip == PUBLIC_IP
    assert http_target.path == "/hook"

def test_empty_path_is_normalized_to_root():
    """Путь без '/' не должен давать пустой request-target."""
    target = resolve_public_target("https://example.com", resolver=_public_resolver)
    assert target.path == "/"

def test_idn_hostname_is_handed_to_resolver_unchanged():
    """IDN-хост не нормализуется в коде: окончательное решение принимает IP."""
    seen = []

    def recording_resolver(host, _port, **_kwargs):
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]

    target = resolve_public_target(
        "https://пример.рф/hook", resolver=recording_resolver
    )
    assert seen == ["пример.рф"]
    assert target.ip == PUBLIC_IP


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=False,
    reason="resolve_public_target ловит только OSError/socket.gaierror, но реальный "
           "socket.getaddrinfo для IDNA-метки длиннее 63 символов бросает "
           "UnicodeError (подкласс ValueError) — он уходит наружу сырым, и "
           "POST /webhooks/ отвечает 500 вместо 400",
)
def test_idna_failure_of_resolver_is_reported_as_dns_failed():
    """IDNA-ошибка резолвера обязана становиться dns_failed, а не 500."""
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target(
            "https://" + "a" * 64 + ".example/hook",
            resolver=lambda *_a, **_kw: (_ for _ in ()).throw(
                UnicodeError("label empty or too long")
            ),
        )
    assert caught.value.code == "dns_failed"


# ---------------------------------------------------------------------------
# webhook_delivery: ответы DNS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(socket.gaierror("Name or service not known"), id="gaierror"),
        pytest.param(OSError("resolver unavailable"), id="oserror"),
    ],
)
def test_resolver_failure_is_reported_as_dns_failed(error):
    """Ошибка сети/DNS не превращается в «адрес не публичный» и не ретраится."""
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target(
            "https://example.com/hook",
            resolver=lambda *_a, **_kw: (_ for _ in ()).throw(error),
        )
    assert caught.value.code == "dns_failed"

def test_empty_dns_answer_is_dns_failed():
    """Пустой ответ резолвера — отказ, а не «соединение с пустым адресом»."""
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target("https://example.com/hook", resolver=lambda *_a, **_kw: [])
    assert caught.value.code == "dns_failed"


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("не-адрес", 443))],
            id="hostname_instead_of_ip",
        ),
        pytest.param(
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ())],
            id="empty_sockaddr",
        ),
        pytest.param(
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", None)],
            id="null_sockaddr",
        ),
    ],
)
def test_malformed_dns_answer_is_rejected(answer):
    """Мусорный sockaddr от резолвера не должен приводить к connect куда попало."""
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target("https://example.com/hook", resolver=lambda *_a, **_kw: answer)
    assert caught.value.code == "dns_result_invalid"

def test_resolver_returning_a_name_is_never_connected_by_name():
    """Резолвер, вернувший имя вместо numeric IP, приводит к отказу.

    Иначе connect ушёл бы на hostname и повторный резолв вернул бы другой IP —
    классический DNS-rebinding. Проверка стоит ДО `_is_public_ip`: не-числовой
    адрес не превращается в «публичный».
    """
    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("internal.example", 443))]
    with pytest.raises(WebhookURLValidationError) as caught:
        resolve_public_target("https://example.com/hook", resolver=lambda *_a, **_kw: answer)
    assert caught.value.code == "dns_result_invalid"

def test_ipv4_mapped_answer_is_carried_into_connect_verbatim():
    """Ответ резолвера не переписывается: в connect уходит ровно то, что вернул DNS."""
    answer = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:93.184.216.34", 443))]
    target = resolve_public_target("https://example.com/hook", resolver=lambda *_a, **_kw: answer)
    # Публичный IPv4-mapped адрес допускается, и его строковая форма — та же,
    # что вернул резолвер (никакой «нормализации», которая сменила бы цель).
    assert target.ip == "::ffff:93.184.216.34"

def test_only_the_first_dns_answer_is_used():
    """Берётся первый ответ; подмена второго адреса не влияет на connect."""
    answer = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    target = resolve_public_target("https://example.com/hook", resolver=lambda *_a, **_kw: answer)
    assert target.ip == PUBLIC_IP


# ---------------------------------------------------------------------------
# webhook_delivery: IP-pinning транспорта
# ---------------------------------------------------------------------------


class _FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeTLSContext:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def wrap_socket(self, sock, server_hostname=None):
        self.calls.append((sock, server_hostname))
        if self.error is not None:
            raise self.error
        return sock

def _dial_recorder(monkeypatch, returned=None):
    """Подменить socket.create_connection и записать фактический адрес."""
    calls = []
    sock = returned if returned is not None else _FakeSocket()

    def fake_create_connection(address, timeout=None):
        calls.append((address, timeout))
        return sock

    monkeypatch.setattr(delivery_module.socket, "create_connection", fake_create_connection)
    return calls, sock

def test_pinned_http_connection_dials_the_validated_ip_not_the_hostname(monkeypatch):
    """Connect идёт на numeric IP: повторный DNS на этапе соединения исключён."""
    target = resolve_public_target("https://example.com/hook", resolver=_public_resolver)
    calls, sock = _dial_recorder(monkeypatch)

    connection = _PinnedHTTPConnection(target, 3.0)
    assert (connection.host, connection.port) == ("example.com", 443)
    connection.connect()

    assert calls == [((PUBLIC_IP, 443), 3.0)]
    assert connection.sock is sock
    assert connection._pinned_ip == PUBLIC_IP

def test_pinned_https_connection_keeps_sni_of_original_url(monkeypatch):
    """TLS проверяет сертификат исходного имени, хотя сокет идёт на IP."""
    target = resolve_public_target("https://example.com/hook", resolver=_public_resolver)
    context = _FakeTLSContext()
    monkeypatch.setattr(delivery_module.ssl, "create_default_context", lambda: context)
    calls, sock = _dial_recorder(monkeypatch)

    connection = _PinnedHTTPSConnection(target, 2.0)
    assert (connection.host, connection.port) == ("example.com", 443)
    connection.connect()

    assert calls == [((PUBLIC_IP, 443), 2.0)]
    assert context.calls == [(sock, "example.com")]
    assert connection.sock is sock

def test_pinned_https_connection_closes_raw_socket_on_tls_failure(monkeypatch):
    """Ошибка TLS не оставляет «полуоткрытый» сокет в пуле."""
    target = resolve_public_target("https://example.com/hook", resolver=_public_resolver)
    context = _FakeTLSContext(error=ssl.SSLError("handshake failed"))
    monkeypatch.setattr(delivery_module.ssl, "create_default_context", lambda: context)
    _calls, sock = _dial_recorder(monkeypatch)
    connection = _PinnedHTTPSConnection(target, 2.0)

    with pytest.raises(ssl.SSLError):
        connection.connect()
    assert sock.closed is True

def test_pinned_transport_closes_connection_even_when_transport_fails(monkeypatch):
    """Сокет закрывается в finally, даже если ответ не прочитан."""
    target = resolve_public_target("https://example.com/hook", resolver=_public_resolver)
    state = {"closed": False}

    class FailingHTTPSConnection:
        def __init__(self, target, timeout):
            self.target = target
            self.timeout = timeout

        def request(self, *_args, **_kwargs):
            return None

        def getresponse(self):
            raise OSError("соединение оборвано до ответа")

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(delivery_module, "_PinnedHTTPSConnection", FailingHTTPSConnection)
    with pytest.raises(OSError):
        pinned_http_transport(target, b"{}", {"Content-Type": "application/json"}, 1.0)
    assert state["closed"] is True

def test_pinned_transport_posts_to_validated_path(monkeypatch):
    """В сеть уходит только проверенный path, без query и fragment."""
    target = resolve_public_target("http://example.com/a/b", resolver=_public_resolver)
    seen = {}

    class RecordingConnection:
        def __init__(self, target, timeout):
            seen["target"] = target
            seen["timeout"] = timeout

        def request(self, method, path, body=None, headers=None):
            seen["request"] = (method, path, body, headers)

        def getresponse(self):
            return type("Response", (), {"status": 200})()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(delivery_module, "_PinnedHTTPConnection", RecordingConnection)
    assert pinned_http_transport(target, b"{}", {"X": "1"}, 4.0) == 200
    assert seen["request"] == ("POST", "/a/b", b"{}", {"X": "1"})
    assert seen["target"].ip == PUBLIC_IP
    assert seen["timeout"] == 4.0
    assert seen["closed"] is True


# ---------------------------------------------------------------------------
# webhook_delivery: классификация ошибок транспорта
# ---------------------------------------------------------------------------

def test_transport_error_code_from_lower_layer_is_preserved(db):
    """WebhookTransportError снизу проходит наверх без замены кода и статуса."""
    delivery_id = _delivery_fixture(db)

    def rejecting_transport(*_args, **_kwargs):
        raise WebhookTransportError("http_retryable", retryable=True, response_status=503)

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=rejecting_transport,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
        max_attempts=1,
    )

    assert outcome.status == "dead_letter"
    assert outcome.error_code == "http_retryable"
    assert outcome.response_status == 503

    db.expire_all()
    delivery = db.get(models.WebhookDelivery, delivery_id)
    assert delivery.response_status == 503
    attempt = db.query(models.WebhookAttempt).filter_by(delivery_id=delivery_id).one()
    assert (attempt.error_code, attempt.response_status) == ("http_retryable", 503)

def test_network_os_error_is_retryable_and_never_leaks_url(db, caplog):
    """OSError ретраится, а его текст (в нём бывает URL) не попадает в лог."""
    delivery_id = _delivery_fixture(db)
    sensitive = "https://user:password@internal.example/hook?token=secret"
    calls = []
    sleeps = []

    def network_error(*_args, **_kwargs):
        calls.append(1)
        raise OSError(sensitive)

    with caplog.at_level("WARNING"):
        outcome = deliver_delivery(
            delivery_id,
            {"event": "card.created", "data": {}},
            transport=network_error,
            resolver=_public_resolver,
            sleeper=sleeps.append,
            max_attempts=2,
        )

    assert outcome.status == "dead_letter"
    assert outcome.error_code == "network_error"
    assert len(calls) == 2
    assert sleeps == [0.5]
    assert "network_error" in caplog.text
    assert sensitive not in caplog.text
    assert "password" not in caplog.text
    assert "token=secret" not in caplog.text

def test_retry_budget_out_of_range_is_rejected_before_any_write(db):
    """Недопустимое число попыток не создаёт ни попыток, ни аудита."""
    delivery_id = _delivery_fixture(db)
    for budget in (0, -1, 11):
        with pytest.raises(ValueError):
            deliver_delivery(
                delivery_id,
                {"event": "card.created", "data": {}},
                transport=_must_not_be_called,
                resolver=_public_resolver,
                sleeper=_no_sleeps,
                max_attempts=budget,
            )

    db.expire_all()
    delivery = db.get(models.WebhookDelivery, delivery_id)
    assert delivery.status == "queued"
    assert delivery.attempt_count == 0
    assert db.query(models.WebhookAttempt).filter_by(delivery_id=delivery_id).count() == 0

def test_unknown_delivery_id_raises_lookup_error(db):
    """Доставка несуществующего аудита — явная ошибка, не тихий «успех»."""
    with pytest.raises(LookupError):
        deliver_delivery(
            424242,
            {"event": "card.created", "data": {}},
            transport=_must_not_be_called,
            resolver=_public_resolver,
            sleeper=_no_sleeps,
        )
    assert db.query(models.WebhookDelivery).count() == 0

def test_delivery_without_webhook_fails_closed(db):
    """Удалённый/отсутствующий вебхук: попытка не делается, статус failed."""
    delivery = create_delivery(
        db, webhook_id=None, event="card.created", tenant_id=None
    )

    outcome = deliver_delivery(
        delivery.id,
        {"event": "card.created", "data": {}},
        transport=_must_not_be_called,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "webhook_missing"
    assert outcome.attempts == 0

    db.expire_all()
    stored = db.get(models.WebhookDelivery, delivery.id)
    assert (stored.status, stored.attempt_count) == ("failed", 0)
    assert stored.last_error_code == "webhook_missing"
    assert stored.completed_at is not None
    assert db.query(models.WebhookAttempt).filter_by(delivery_id=delivery.id).count() == 0

def test_unavailable_encryption_key_fails_delivery_without_transport(db, monkeypatch):
    """Нет ключа — доставка не идёт и не деградирует до plaintext."""
    delivery_id = _delivery_fixture(db)

    def broken_key(*_args, **_kwargs):
        raise WebhookEncryptionConfigError("Шифрование секрета webhook не настроено")

    monkeypatch.setattr(delivery_module, "obtain_fernet", broken_key)

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=_must_not_be_called,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "encryption_key_unavailable"
    db.expire_all()
    assert db.query(models.WebhookAttempt).filter_by(delivery_id=delivery_id).count() == 0

def test_unserializable_payload_never_reaches_transport(db):
    """Кривой payload отсекается до подписи и до сети."""
    delivery_id = _delivery_fixture(db)

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {"bad": object()}},
        transport=_must_not_be_called,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "payload_invalid"
    db.expire_all()
    assert db.query(models.WebhookAttempt).filter_by(delivery_id=delivery_id).count() == 0


# ---------------------------------------------------------------------------
# webhook_delivery: классификация HTTP-статусов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [
        pytest.param(408, "http_retryable", True, id="request_timeout"),
        pytest.param(425, "http_retryable", True, id="too_early"),
        pytest.param(429, "http_retryable", True, id="rate_limited"),
        pytest.param(500, "http_retryable", True, id="internal_error"),
        pytest.param(599, "http_retryable", True, id="last_5xx"),
        pytest.param(600, "http_rejected", False, id="beyond_5xx_range"),
        pytest.param(400, "http_rejected", False, id="bad_request"),
        pytest.param(401, "http_rejected", False, id="unauthorized"),
        pytest.param(404, "http_rejected", False, id="not_found"),
        pytest.param(422, "http_rejected", False, id="unprocessable"),
    ],
)
def test_http_status_classification_matrix(db, status, expected_code, retryable):
    """5xx/408/425/429 повторяются, остальное — нет; ретрай ограничен бюджетом."""
    delivery_id = _delivery_fixture(db)
    calls = []
    sleeps = []

    def status_transport(*_args, **_kwargs):
        calls.append(status)
        return status

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=status_transport,
        resolver=_public_resolver,
        sleeper=sleeps.append,
        max_attempts=2,
    )

    expected_attempts = 2 if retryable else 1
    assert len(calls) == expected_attempts
    assert sleeps == ([0.5] if retryable else [])
    assert outcome.attempts == expected_attempts
    assert outcome.error_code == expected_code
    assert outcome.response_status == status
    assert outcome.status == ("dead_letter" if retryable else "failed")

    db.expire_all()
    delivery = db.get(models.WebhookDelivery, delivery_id)
    assert delivery.status == outcome.status
    assert delivery.response_status == status
    assert delivery.last_error_code == expected_code
    attempts = (
        db.query(models.WebhookAttempt)
        .filter_by(delivery_id=delivery_id)
        .order_by(models.WebhookAttempt.attempt_number)
        .all()
    )
    assert len(attempts) == expected_attempts
    assert [attempt.attempt_number for attempt in attempts] == list(
        range(1, expected_attempts + 1)
    )
    assert all(attempt.status == "failed" for attempt in attempts)
    assert all(attempt.error_code == expected_code for attempt in attempts)
    assert all(attempt.response_status == status for attempt in attempts)
    assert all(attempt.completed_at is not None for attempt in attempts)

def test_backoff_deadline_is_persisted_between_attempts(db):
    """Между попытками сохраняется next_attempt_at: пауза видна и снаружи процесса."""
    delivery_id = _delivery_fixture(db)
    observed = []

    def observing_transport(*_args, **_kwargs):
        session = database.SessionLocal()
        try:
            row = session.get(models.WebhookDelivery, delivery_id)
            observed.append(row.next_attempt_at)
        finally:
            session.close()
        return 503

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=observing_transport,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
        max_attempts=2,
    )

    assert outcome.status == "dead_letter"
    assert len(observed) == 2
    assert observed[0] is None
    assert observed[1] is not None

    db.expire_all()
    delivery = db.get(models.WebhookDelivery, delivery_id)
    assert delivery.next_attempt_at is None  # после финала очередь не ждёт ретрая

def test_retry_cap_is_enforced_by_budget_not_by_time(db):
    """Бюджет попыток ограничивает ретраи даже при «вечно» таймаущем транспорте."""
    delivery_id = _delivery_fixture(db)
    calls = []

    def always_timeout(*_args, **_kwargs):
        calls.append(1)
        raise socket.timeout("таймаут")

    outcome = deliver_delivery(
        delivery_id,
        {"event": "card.created", "data": {}},
        transport=always_timeout,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
        max_attempts=1,
    )

    assert len(calls) == 1
    assert outcome.status == "dead_letter"
    assert outcome.error_code == "timeout"
    assert outcome.attempts == 1


# ---------------------------------------------------------------------------
# webhook_delivery: отмена доставки из переполненной очереди
# ---------------------------------------------------------------------------

def test_mark_dead_letter_closes_an_open_delivery_with_queue_full(db):
    """Доставка, не взятая в работу, честно помечается dead_letter/queue_full."""
    delivery = create_delivery(
        db, webhook_id=None, event="card.created", tenant_id=None
    )
    correlation_id = mark_dead_letter(delivery.id)

    assert correlation_id == delivery.correlation_id
    db.expire_all()
    stored = db.get(models.WebhookDelivery, delivery.id)
    assert stored.status == "dead_letter"
    assert stored.last_error_code == "queue_full"
    assert stored.attempt_count == 0
    assert stored.response_status is None
    assert stored.completed_at is not None
    assert stored.next_attempt_at is None

def test_mark_dead_letter_is_idempotent_and_keeps_finished_delivery(db):
    """Повторный вызов не перетирает результат уже завершённой доставки."""
    succeeded = _delivery_fixture(db)
    deliver_delivery(
        succeeded,
        {"event": "card.created", "data": {}},
        transport=lambda *_a, **_kw: 204,
        resolver=_public_resolver,
        sleeper=_no_sleeps,
    )

    correlation_id = mark_dead_letter(succeeded)
    assert correlation_id is not None

    db.expire_all()
    stored = db.get(models.WebhookDelivery, succeeded)
    assert stored.status == "succeeded"
    assert stored.last_error_code != "queue_full"
    assert stored.response_status == 204

def test_mark_dead_letter_of_unknown_delivery_returns_none(db):
    """Несуществующий id не поднимает исключение в фоновом обработчике."""
    assert mark_dead_letter(424242) is None


# ---------------------------------------------------------------------------
# webhook_delivery: ограничение очереди
# ---------------------------------------------------------------------------

def test_bounded_executor_releases_slot_even_when_task_fails():
    """Упавшая задача не «съедает» слот: счётчик ожидающих не утекает."""
    executor = BoundedDeliveryExecutor(max_workers=1, max_pending=1)
    finished = threading.Event()

    def boom():
        try:
            raise RuntimeError("ошибка доставки")
        finally:
            finished.set()

    try:
        assert executor.submit(boom) is True
        assert finished.wait(2) is True
        # Слот освободился: следующая задача снова принимается.
        assert executor.submit(lambda: None) is True
    finally:
        executor._executor.shutdown(wait=True)

def test_bounded_executor_does_not_leave_threads_after_shutdown():
    """После явного shutdown пул не держит фоновых потоков."""
    executor = BoundedDeliveryExecutor(max_workers=2, max_pending=2)
    before = {thread.name for thread in threading.enumerate()}
    done = threading.Event()
    executor.submit(done.set)
    assert done.wait(2) is True

    executor._executor.shutdown(wait=True)
    after = {thread.name for thread in threading.enumerate()}
    assert not {name for name in after - before if name.startswith("webhook-delivery")}
