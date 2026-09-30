"""Сбои и обработка ошибок при синхронизации почты.

Вторая часть набора по ``routers/email_parser_router.py``: чистые функции
разбора MIME и HTTP-ручки настроек покрыты соседними файлами, здесь только
путь синка и его отказоустойчивость.

Что фиксируют тесты:

1. ``test_imap_error_text_with_secrets_never_reaches_http_response`` и
   ``test_sync_endpoint_returns_sanitized_detail_on_imap_error`` — аудит С7
   от 06.09: текст исключения почтового сервера уходил клиенту в ``detail``
   прямо, а в нём бывают имя хоста и данные сессии.
2. ``test_*credential*`` — ветка ошибок учётных данных не выдаёт наружу ни тип
   исключения, ни ключ шифрования, ни текст шифра.
3. ``test_timeout_*`` / ``test_unexpected_error_*`` — таймаут и любая
   неожиданная ошибка дают обобщённую формулировку без деталей.
4. ``test_logout_*`` — аудит 06.09: ``logout()`` обязан вызываться и при
   ошибке, иначе соединение с IMAP висело до таймаута; исключение из самого
   ``logout()`` подавляется.
5. ``test_fetch_*`` — не-OK от ``fetch`` и не-tuple в ``msg_data``.
6. ``test_search_unseen_*`` — ``SEARCH`` с статусом != OK.
7. ``test_imap4_ssl_without_timeout_kwarg_*`` — запасной путь подключения
   для Python < 3.9.
8. ``test_save_settings_failure_keeps_successful_import`` — антирегресс на
   фикс 18.09: сбой записи ``last_sync`` не перечёркивает выполненный импорт.
9. ``test_mark_seen_*`` — сбой ``STORE`` не откатывает уже зафиксированную
   карточку.
10. ``test_store_attachment_*`` — ``OSError`` при записи вложения пробрасывается
    и не оставляет частичного файла; небезопасное имя даёт ``None``.

Сеть не используется: ``IMAP4_SSL`` подменяется фейком, ``save_settings`` —
заглушкой, поэтому реальный JSON настроек тесты не пишут.
"""
import hashlib
import imaplib
import json
import logging
import os
from email.message import EmailMessage

import pytest

import models
import routers.email_parser_router as email_parser_router

# Настоящая картинка 1x1 в формате PNG — проходит magic-bytes проверку.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

# Секреты внутри текста исключения IMAP. Реальные сообщения почтовых серверов
# выглядят именно так: хост, логин, пароль и идентификатор сессии.
SECRET_HOST = "mail.internal.example.com"
SECRET_PASSWORD = "hunter2"
SECRET_SESSION = "A1B2C3D4"
SECRETS = (SECRET_HOST, SECRET_PASSWORD, SECRET_SESSION)
IMAP_ERROR_TEXT = (
    f"Login failed to {SECRET_HOST}:993 user=box "
    f"password={SECRET_PASSWORD} session={SECRET_SESSION}"
)

# Ожидаемые наружу формулировки: в них нет ни деталей, ни имён исключений.
GENERIC_IMAP_ERROR = (
    "Ошибка авторизации на почтовом сервере: проверьте логин и пароль ящика"
)
GENERIC_ERROR = "Ошибка подключения к почтовому серверу"
TIMEOUT_ERROR_TEXT = "Превышено время ожидания при подключении к почте"
CREDENTIAL_ERROR_TEXT = (
    "Не удалось безопасно использовать сохранённый пароль почты"
)

# Значения из настроек, которые не должны просочиться наружу.
BROKEN_CIPHERTEXT = "gAAAA-битый-шифр-совсем-секретный-01"
LEGACY_PLAINTEXT = "старый-пароль-открытым-текстом"


# --- фейковый IMAP ---------------------------------------------------------


class _FakeImapClient:
    """IMAP-клиент с программируемыми сбоями на каждом шаге протокола.

    Счётчики методов нужны тестам: по ним проверяется, что ``logout``
    вызывается и при ошибке, а ``store`` — после успешной фиксации карточки.
    """

    def __init__(self, messages=(), *, search_status="OK", search_ids=None,
                 login_error=None, select_error=None, fetch_status="OK",
                 fetch_result=None, fetch_error=None, store_error=None,
                 logout_error=None):
        self._messages = list(messages)
        self._search_status = search_status
        self._search_ids = search_ids
        self._login_error = login_error
        self._select_error = select_error
        self._fetch_status = fetch_status
        self._fetch_result = fetch_result
        self._fetch_error = fetch_error
        self._store_error = store_error
        self._logout_error = logout_error

        self.login_calls: list[tuple] = []
        self.select_calls: list[str] = []
        self.search_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []
        self.store_calls: list[tuple] = []
        self.logout_calls = 0
        self.socket_calls = 0
        self.settimeout_calls: list = []

    # Запасной путь подключения для Python < 3.9 дёргает socket().settimeout().
    def socket(self):
        self.socket_calls += 1
        return self

    def settimeout(self, value):
        self.settimeout_calls.append(value)

    def login(self, email_addr, password):
        self.login_calls.append((email_addr, password))
        if self._login_error is not None:
            raise self._login_error
        return "OK", [b""]

    def select(self, mailbox):
        self.select_calls.append(mailbox)
        if self._select_error is not None:
            raise self._select_error
        return "OK", [str(len(self._messages)).encode()]

    def search(self, charset, criteria):
        self.search_calls.append((charset, criteria))
        if self._search_status != "OK":
            return self._search_status, [b""]
        if self._search_ids is not None:
            return "OK", [self._search_ids]
        ids = b" ".join(
            str(i + 1).encode() for i in range(len(self._messages))
        )
        return "OK", [ids]

    def fetch(self, message_id, query):
        self.fetch_calls.append((message_id, query))
        if self._fetch_error is not None:
            raise self._fetch_error
        if self._fetch_result is not None:
            return self._fetch_result
        if self._fetch_status != "OK":
            return self._fetch_status, [b""]
        raw = self._messages[int(message_id) - 1]
        return "OK", [(message_id, raw)]

    def store(self, message_id, command, flags):
        self.store_calls.append((message_id, command, flags))
        if self._store_error is not None:
            raise self._store_error
        return "OK", [b""]

    def logout(self):
        self.logout_calls += 1
        if self._logout_error is not None:
            raise self._logout_error
        return "BYE", [b""]


class _ImapHarness:
    """Подмена ``IMAP4_SSL``: выдаёт заданные клиенты и помнит их.

    ``reject_timeout`` имитирует Python < 3.9: конструктор без параметра
    ``timeout`` — так синк обязан откатиться на путь с ``socket().settimeout``.
    """

    def __init__(self, messages=(), *, reject_timeout=False, **client_kwargs):
        self._messages = list(messages)
        self._client_kwargs = client_kwargs
        self.reject_timeout = reject_timeout
        self.clients: list[_FakeImapClient] = []
        self.connect_calls: list[dict] = []

    def factory(self, host, *args, **kwargs):
        self.connect_calls.append({"host": host, "args": args, "kwargs": kwargs})
        if self.reject_timeout and "timeout" in kwargs:
            raise TypeError(
                "IMAP4_SSL() got an unexpected keyword argument 'timeout'"
            )
        client = _FakeImapClient(self._messages, **self._client_kwargs)
        self.clients.append(client)
        return client


# --- помощники -------------------------------------------------------------


def _mail(subject="Тестовое письмо", sender="sender@example.com",
           body="Нужна люстра в гостиную.",
           message_id="<letter-1@example.com>", attachments=()):
    """Собрать сырое письмо. attachments — кортежи (имя, данные, maintype, subtype)."""
    message = EmailMessage()
    if subject is not None:
        message["Subject"] = subject
    message["From"] = sender
    message["To"] = "sale@example.com"
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body)
    for name, data, maintype, subtype in attachments:
        message.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=name
        )
    return message.as_bytes()


def _sync(db, settings, tenant_id=None):
    return email_parser_router._sync_tenant_emails(tenant_id, settings, db)


def _upload_dir_files() -> set:
    return set(os.listdir(email_parser_router.UPLOAD_DIR))


def _assert_no_secrets(text):
    for secret in SECRETS:
        assert secret not in text, f"секрет {secret!r} утёк наружу: {text!r}"


@pytest.fixture
def mailbox(monkeypatch):
    """Подставить мок IMAP и заглушку сохранения last_sync.

    Если настройки содержат сохранённый пароль, переменная-подмена
    ``CRM_SMTP_PASSWORD`` снимается: иначе она перекрыла бы путь к ключу
    шифрования, и ветки ошибок учётных данных были бы недостижимы.
    """
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "test-password")
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    monkeypatch.setattr(
        email_parser_router, "save_settings",
        lambda settings, tenant_id, **kwargs: None,
    )

    def _factory(messages=(), settings=None, **client_kwargs):
        """Подменить IMAP и вернуть (harness, настройки синка).

        Аргумент ``settings`` переопределяет поля настроек ящика, остальные
        именованные аргументы уходят в фейковый IMAP-клиент.
        """
        harness = _ImapHarness(messages, **client_kwargs)
        monkeypatch.setattr(
            email_parser_router.imaplib, "IMAP4_SSL", harness.factory
        )
        box = {
            "email": "sale@example.com",
            "password": "",
            "imap_server": "imap.example.com",
            "target_status": "Новый запрос",
        }
        box.update(settings or {})
        if box.get("password"):
            # Иначе подмена CRM_SMTP_PASSWORD перекрыла бы путь к ключу
            # шифрования, и ветки ошибок учётных данных были бы недостижимы.
            monkeypatch.delenv("CRM_SMTP_PASSWORD", raising=False)
        return harness, box

    return _factory


def _write_settings_file(monkeypatch, tmp_path, **overrides) -> str:
    """Положить файл настроек в tmp и подменить путь к нему."""
    settings_file = tmp_path / "email_settings.json"
    payload = {
        "email": "sale@example.com",
        "password": "",
        "imap_server": "imap.example.com",
        "target_status": "Новый запрос",
    }
    payload.update(overrides)
    settings_file.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        email_parser_router, "_get_settings_path",
        lambda tenant_id=None: str(settings_file),
    )
    return str(settings_file)


# --- 1. Утечка данных IMAP в ответ ----------------------------------------


def test_imap_error_text_with_secrets_never_reaches_http_response(
    db, mailbox, caplog
):
    """Текст исключения IMAP с секретами не попадает в результат синка.

    Причина фиксации: в тексте исключения почтового сервера бывают имя хоста
    и данные сессии, и раньше они уходили клиенту прямо в ``detail``
    (аудит С7 от 06.09). Наружу обязана выходить обобщённая формулировка.
    """
    harness, settings = mailbox(
        login_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT)
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == GENERIC_IMAP_ERROR, result
    assert result["count"] == 0, result
    _assert_no_secrets(result["error"])
    assert IMAP_ERROR_TEXT not in result["error"]


def test_sync_endpoint_returns_sanitized_detail_on_imap_error(
    client, admin, db, tmp_path, monkeypatch, caplog
):
    """HTTP-ручка /sync отдаёт санированный detail, а не текст исключения.

    Путь через ручку важен отдельно: ``sync_emails`` превращает ``error``
    результата в ``HTTPException.detail``, то есть утечка могла бы вернуться
    и на этом шаге.
    """
    # Настройки без сохранённого пароля: ручка возьмёт его из окружения.
    _write_settings_file(monkeypatch, tmp_path)
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "test-password")
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    harness = _ImapHarness(
        login_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT)
    )
    monkeypatch.setattr(
        email_parser_router.imaplib, "IMAP4_SSL", harness.factory
    )
    monkeypatch.setattr(
        email_parser_router, "save_settings",
        lambda settings, tenant_id, **kwargs: None,
    )

    with caplog.at_level(logging.ERROR):
        response = client.post("/email-parser/sync", headers=admin[1])

    assert response.status_code == 500, response.text
    assert response.json()["detail"] == GENERIC_IMAP_ERROR, response.text
    _assert_no_secrets(response.text)
    assert IMAP_ERROR_TEXT not in response.text
    assert "IMAP4.error" not in response.text


def test_imap_error_details_go_to_internal_log_only(
    db, mailbox, caplog
):
    """Внутренний лог сохраняет детали для администратора, ответ — нет.

    ВАЖНО ДЛЯ РЕВЬЮ: ``logger.exception`` пишет traceback целиком, поэтому
    ТЕКСТ исключения IMAP (вместе с хостом, паролем и session-id) ПОПАДАЕТ В
    ЛОГ СЕРВЕРА. Это соответствует комментарию FIX 06.09 («наружу только
    обобщённая формулировка, детали в логе выше») и означает, что требование
    «секрет не должен утекать в логи» для этого случая НЕ выполнено.
    Тест фиксирует фактическое поведение: если владелец решит убрать секрет
    и из лога, менять надо прод-код (``logger.exception`` → ``logger.error``
    без ``exc_info``) и одновременно переворачивать это утверждение.
    """
    harness, settings = mailbox(
        login_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT)
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    failures = [
        record for record in caplog.records
        if "IMAP error for tenant" in record.getMessage()
    ]
    assert failures, [r.getMessage() for r in caplog.records]
    assert IMAP_ERROR_TEXT in caplog.text, (
        "ожидается, что детали исключения пишутся во внутренний лог "
        "(см. докстринг: это осознанное поведение, а не проверенный факт)"
    )
    # Ответ при этом остаётся санированным.
    _assert_no_secrets(result["error"])


def test_imap_error_on_later_stage_also_keeps_details_private(
    db, mailbox, caplog
):
    """Утечка не зависит от того, на каком шаге упал IMAP.

    Ошибка на ``select``/``fetch`` приходит после успешного ``login`` — тот же
    класс исключения и тот же обобщённый ответ, секреты наружу не выходят.
    """
    harness, settings = mailbox(
        [_mail()],
        select_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT),
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == GENERIC_IMAP_ERROR, result
    _assert_no_secrets(result["error"])


# --- 2. Ошибки учётных данных ---------------------------------------------


def test_invalid_ciphertext_password_is_reported_without_leaking_material(
    db, mailbox, caplog
):
    """Битый Fernet-шифр не выдаёт наружу ни шифр, ни имя исключения."""
    harness, settings = mailbox(
        settings={"password": BROKEN_CIPHERTEXT}
    )

    with caplog.at_level(logging.DEBUG):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == CREDENTIAL_ERROR_TEXT, result
    assert result["count"] == 0, result
    assert BROKEN_CIPHERTEXT not in result["error"]
    assert "InvalidCredentialCiphertextError" not in result["error"]
    assert BROKEN_CIPHERTEXT not in caplog.text


def test_legacy_plaintext_password_is_reported_without_leaking_material(
    db, mailbox, caplog
):
    """Старый пароль открытым текстом не выдаётся наружу и не попадает в лог."""
    harness, settings = mailbox(settings={"password": LEGACY_PLAINTEXT})

    with caplog.at_level(logging.DEBUG):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == CREDENTIAL_ERROR_TEXT, result
    assert LEGACY_PLAINTEXT not in result["error"]
    assert "LegacyPlaintextError" not in result["error"]
    assert LEGACY_PLAINTEXT not in caplog.text


def test_email_encryption_config_error_is_reported_without_key_material(
    db, mailbox, monkeypatch, caplog
):
    """Ошибка конфигурации ключа не раскрывает ключ и его источник."""

    def _boom(*args, **kwargs):
        raise email_parser_router.EmailEncryptionConfigError(
            "Ключ шифрования почты имеет неверный формат"
        )

    monkeypatch.setattr(email_parser_router, "obtain_fernet", _boom)
    harness, settings = mailbox(settings={"password": BROKEN_CIPHERTEXT})

    with caplog.at_level(logging.DEBUG):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == CREDENTIAL_ERROR_TEXT, result
    assert "EmailEncryptionConfigError" not in result["error"]
    assert "Ключ шифрования" not in result["error"]
    assert BROKEN_CIPHERTEXT not in caplog.text
    # До IMAP дело не дошло: сбой на разборе секрета, а не на подключении.
    assert harness.clients == []


# --- 3. TimeoutError и неожиданная ошибка ----------------------------------


def test_timeout_is_reported_without_server_details(db, mailbox, caplog):
    """Таймаут подключения даёт обобщённый текст без хоста и адреса."""
    harness, settings = mailbox(
        login_error=TimeoutError(f"timed out connecting to {SECRET_HOST}:993")
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == TIMEOUT_ERROR_TEXT, result
    assert result["count"] == 0, result
    _assert_no_secrets(result["error"])


def test_unexpected_error_is_reported_without_details(db, mailbox, caplog):
    """Любая неожиданная ошибка не выдаёт наружу ни текст, ни имя класса."""
    harness, settings = mailbox(
        select_error=RuntimeError(
            f"соединение с {SECRET_HOST} разорвано, password={SECRET_PASSWORD}"
        )
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == GENERIC_ERROR, result
    assert result["count"] == 0, result
    _assert_no_secrets(result["error"])
    assert "RuntimeError" not in result["error"]


def test_unexpected_error_is_logged_for_diagnosis(db, mailbox, caplog):
    """Общая ветка всё же оставляет след в логе — иначе отказ не диагностируется."""
    harness, settings = mailbox(select_error=RuntimeError("сломалось соединение"))

    with caplog.at_level(logging.ERROR):
        _sync(db, settings)

    failures = [
        record for record in caplog.records
        if "Email sync error for tenant" in record.getMessage()
    ]
    assert failures, [r.getMessage() for r in caplog.records]


# --- 4. finally: logout при ошибке -----------------------------------------


def test_logout_is_called_even_when_sync_fails(db, mailbox):
    """Соединение закрывается и на ошибке.

    Аудит 06.09: ``logout`` был только на успехе, и при падении посреди
    выборки соединение с IMAP висело до таймаута.
    """
    harness, settings = mailbox(
        login_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT)
    )

    result = _sync(db, settings)

    assert result["success"] is False, result
    assert len(harness.clients) == 1, harness.clients
    assert harness.clients[0].logout_calls == 1, (
        "IMAP-соединение обязано закрываться и при ошибке"
    )


def test_logout_is_called_after_error_inside_letter_loop(db, mailbox, caplog):
    """Падение на середине выборки тоже закрывает соединение ровно один раз."""
    harness, settings = mailbox(
        [_mail()],
        fetch_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT),
    )

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    # Сбой одного письма перехвачен внутри цикла: синк в целом успешен.
    assert result["success"] is True, result
    assert result["count"] == 0, result
    assert harness.clients[0].logout_calls == 1
    # Текст исключения IMAP не всплыл в результате синка.
    _assert_no_secrets(json.dumps(result, ensure_ascii=False))


def test_logout_failure_does_not_break_error_response(db, mailbox):
    """Исключение из самого ``logout`` подавляется и не ломает ответ."""
    harness, settings = mailbox(
        login_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT),
        logout_error=OSError(f"сокет {SECRET_HOST} уже закрыт"),
    )

    result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == GENERIC_IMAP_ERROR, result
    assert harness.clients[0].logout_calls == 1


def test_logout_failure_does_not_break_successful_import(db, mailbox):
    """Тот же контракт на успешном пути: импорт не перечёркивается."""
    harness, settings = mailbox(
        [_mail()],
        logout_error=OSError("соединение уже разорвано сервером"),
    )

    result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    assert harness.clients[0].logout_calls == 1


# --- 5. fetch: не-OK и мусор в msg_data -----------------------------------


def test_letter_not_returned_by_server_is_skipped(db, mailbox, caplog):
    """Письмо, не отданное сервером, пропускается: синк не падает."""
    harness, settings = mailbox([_mail()], fetch_status="NO")
    files_before = _upload_dir_files()

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 0, result
    assert harness.clients[0].store_calls == [], (
        "письмо, которое сервер не отдал, нельзя помечать прочитанным"
    )
    db.expire_all()
    assert db.query(models.Card).count() == 0
    assert _upload_dir_files() == files_before
    assert "не отдано сервером" in caplog.text


def test_non_tuple_chunks_of_fetch_response_are_ignored(db, mailbox):
    """Служебные куски ответа FETCH (не tuple) пропускаются молча."""
    raw = _mail()
    harness, settings = mailbox(
        [_mail()],
        fetch_result=("OK", [b"1 (RFC822 BODY[] {123}", (b"1", raw), b")"]),
    )

    result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    db.expire_all()
    assert db.query(models.Card).count() == 1


def test_fetch_response_without_any_tuple_imports_nothing(db, mailbox):
    """Ответ FETCH без единой полезной пары не роняет синк и не создаёт мусор."""
    harness, settings = mailbox(
        [],
        fetch_result=("OK", [b"1 (RFC822 BODY[] {0}", b")"]),
    )

    result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 0, result
    db.expire_all()
    assert db.query(models.Card).count() == 0
    assert db.query(models.ActivityLog).count() == 0


# --- 6. _search_unseen: статус != OK ---------------------------------------


def test_search_unseen_raises_controlled_http_exception():
    """Статус != OK поднимает контролируемый HTTPException 500."""
    client_stub = _FakeImapClient(search_status="NO")

    with pytest.raises(email_parser_router.HTTPException) as caught:
        email_parser_router._search_unseen(client_stub, "30-Sep-2026")

    assert caught.value.status_code == 500
    assert "Не удалось получить список писем" in caught.value.detail


def test_failed_search_does_not_leak_out_of_sync(db, mailbox, caplog):
    """Сбой SEARCH даёт контролируемую ошибку синка, а не исключение наружу."""
    harness, settings = mailbox(
        [_mail()],
        search_status="NO",
        search_ids=b"1",
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["count"] == 0, result
    assert result["error"] == GENERIC_ERROR, result
    db.expire_all()
    assert db.query(models.Card).count() == 0
    # Соединение закрыто даже при отказе на поиске.
    assert harness.clients[0].logout_calls == 1


# --- 7. Запасной путь подключения без параметра timeout --------------------


def test_imap4_ssl_without_timeout_kwarg_falls_back_to_socket_timeout(
    db, mailbox
):
    """Python < 3.9: TypeError на ``timeout`` → подключение через socket().

    Таймаут на самом TCP-connect обязателен: иначе «зависший» IMAP-хост
    подвешивал запрос или cron на системный таймаут (минуты).
    """
    harness, settings = mailbox([_mail()], reject_timeout=True)

    result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    # Первая попытка с timeout упала TypeError, вторая — без него.
    assert len(harness.connect_calls) == 2, harness.connect_calls
    assert harness.connect_calls[0]["kwargs"] == {"timeout": 15}
    assert harness.connect_calls[1]["kwargs"] == {}
    assert harness.connect_calls[1]["host"] == "imap.example.com"
    client = harness.clients[0]
    assert client.socket_calls == 1
    assert client.settimeout_calls == [15]
    assert client.logout_calls == 1


def test_fallback_connection_error_is_not_leaked(db, mailbox, caplog):
    """Ошибка запасного подключения тоже даёт обобщённый ответ без деталей."""
    harness, settings = mailbox(
        reject_timeout=True,
        login_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT),
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is False, result
    assert result["error"] == GENERIC_IMAP_ERROR, result
    _assert_no_secrets(result["error"])


# --- 8. Сбой записи last_sync не перечёркивает импорт ----------------------


def test_save_settings_failure_keeps_successful_import(db, mailbox, monkeypatch, caplog):
    """Антирегресс на фикс 18.09: сбой last_sync — бухгалтерия, не импорт.

    Раньше исключение перечёркивало успешный синк: письма уже были
    обработаны, а ответ уходил «Ошибка подключения к почтовому серверу»
    каждую итерацию watchdog.
    """

    def _boom(settings, tenant_id=None, **kwargs):
        raise OSError("диск только для чтения")

    harness, settings = mailbox([_mail()])
    # Порядок важен: фикстура mailbox ставит свою заглушку save_settings.
    monkeypatch.setattr(email_parser_router, "save_settings", _boom)

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    assert [card["title"] for card in result["cards"]] == ["Письмо: Тестовое письмо"], result
    db.expire_all()
    assert db.query(models.Card).count() == 1
    assert harness.clients[0].logout_calls == 1
    assert "Не удалось сохранить last_sync" in caplog.text


def test_last_sync_is_still_recorded_on_success(db, mailbox, monkeypatch):
    """Контроль пары: при рабочем save_settings метка last_sync обновляется."""
    saved: list = []

    def _record(settings, tenant_id=None, **kwargs):
        saved.append(dict(settings))

    harness, settings = mailbox([_mail()])
    # Подмена save_settings обязана быть ПОСЛЕДУЮщей: фикстура сама ставит
    # заглушку, и обратный порядок вернул бы no-op.
    monkeypatch.setattr(email_parser_router, "save_settings", _record)

    result = _sync(db, settings)

    assert result["success"] is True, result
    assert len(saved) == 1, saved
    assert saved[0]["last_sync"], saved[0]
    assert saved[0]["email"] == "sale@example.com"


# --- 9. Сбой _mark_seen не прерывает импорт --------------------------------


def test_store_failure_does_not_break_imported_letter(db, mailbox, caplog):
    """Ошибка STORE не откатывает уже зафиксированную карточку.

    Письмо просто вернётся в выборку при следующем запуске, а дедупликация
    по Message-ID не даст создать вторую карточку.
    """
    harness, settings = mailbox(
        [_mail()],
        store_error=imaplib.IMAP4.error(IMAP_ERROR_TEXT),
    )

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    assert harness.clients[0].store_calls == [(b"1", "+FLAGS", "\\Seen")]
    db.expire_all()
    assert db.query(models.Card).count() == 1
    assert db.query(models.ActivityLog).count() == 1
    assert "Не удалось пометить письмо" in caplog.text
    # Исключение STORE глушится — в результате синка его текста нет.
    _assert_no_secrets(json.dumps(result, ensure_ascii=False))


def test_store_failure_still_closes_connection(db, mailbox):
    """Глушение ошибки STORE не отменяет закрытие IMAP-соединения."""
    harness, settings = mailbox(
        [_mail()],
        store_error=RuntimeError("соединение оборвано"),
    )

    result = _sync(db, settings)

    assert result["success"] is True, result
    assert harness.clients[0].logout_calls == 1


# --- 10. _store_attachment: сбой записи и небезопасное имя -----------------


def test_store_attachment_removes_partial_file_and_reraises(monkeypatch):
    """OSError при записи вложения пробрасывается, частичного файла нет.

    Файл мог остаться наполовину записанным: без уборки на диске копились
    «сироты», на которые ссылается ни одна строка БД.
    """
    # card_id намеренно выбит: имя файла — это digest от (card_id, имя),
    # и совпадение с вложением соседнего теста сделало бы снимок каталога
    # зависимым от порядка прогонов.
    card_id = 987654
    target_name = "photo.png"
    digest = hashlib.md5(
        f"{card_id}_{target_name}".encode(), usedforsecurity=False
    ).hexdigest()[:8]
    safe_name = digest + "_" + email_parser_router._sanitize_attachment_name(
        target_name
    )
    target_path = os.path.join(email_parser_router.UPLOAD_DIR, safe_name)
    files_before = _upload_dir_files()

    class _PartialWritePath:
        """Пишет половину файла и падает — как полный диск."""

        def __init__(self, path):
            self._path = str(path)

        def write_bytes(self, data):
            with open(self._path, "wb") as handle:
                handle.write(data[: len(data) // 2])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(email_parser_router, "Path", _PartialWritePath)

    with pytest.raises(OSError):
        email_parser_router._store_attachment(card_id, target_name, PNG_BYTES)

    assert not os.path.exists(target_path), "частичный файл не убран с диска"
    assert _upload_dir_files() == files_before


def test_attachment_write_failure_does_not_break_letter_import(
    db, mailbox, monkeypatch, caplog
):
    """Падение записи вложения не теряет само письмо и не оставляет сирот."""
    files_before = _upload_dir_files()

    class _FailingWritePath:
        """Запись вложения всегда падает — диск или каталог недоступны."""

        def __init__(self, path):
            self._path = str(path)

        def write_bytes(self, data):
            raise OSError(30, "Read-only file system")

    monkeypatch.setattr(email_parser_router, "Path", _FailingWritePath)
    # Имя вложения уникально для набора: имя файла на диске — это digest от
    # (card_id, имя), и при card_id=1 чужое «photo.png» из соседнего теста
    # совпало бы (его уборка после сбоя записи ломала бы снимок каталога).
    harness, settings = mailbox(
        [_mail(attachments=[("writefail_probe.png", PNG_BYTES, "image", "png")])]
    )

    with caplog.at_level(logging.ERROR):
        result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    db.expire_all()
    card = db.query(models.Card).one()
    assert db.query(models.CardAttachment).count() == 0
    assert card.id is not None
    assert _upload_dir_files() == files_before, "на диске остался мусор"
    assert "Не удалось сохранить вложение" in caplog.text


def test_store_attachment_rejects_path_escaping_uploads_dir(tmp_path):
    """Имя, уводящее файл за UPLOAD_DIR, даёт None, а не запись на диск.

    Проверяется подмена подготовленного имени файла: если по этому пути уже
    лежит ссылка наружу (например, оставшаяся от прошлой сборки каталога),
    realpath уводит запись за пределы UPLOAD_DIR.
    """
    card_id = 4242
    name = "photo.png"
    stored = email_parser_router._store_attachment(card_id, name, PNG_BYTES)
    assert stored is not None and os.path.isfile(stored)

    outside = tmp_path / "утечка.png"
    outside.write_bytes("секретный файл".encode("utf-8"))
    os.remove(stored)
    os.symlink(outside, stored)
    try:
        assert email_parser_router._store_attachment(card_id, name, PNG_BYTES) is None
        assert outside.read_bytes() == "секретный файл".encode("utf-8"), (
            "файл вне каталога перезаписан"
        )
    finally:
        if os.path.islink(stored):
            os.remove(stored)
