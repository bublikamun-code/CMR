"""Регрессии устойчивости импорта писем: откат, идемпотентность, вложения, tenant.

Что фиксируют тесты:

1. ``test_one_bad_letter_does_not_kill_the_batch`` — после IntegrityError
   сессия остаётся в pending-rollback, и ВСЕ следующие письма того же запуска
   падали каскадно: синк умирал на первом плохом письме.
2. ``test_failed_letter_is_not_committed_with_the_next_one`` — обработка одного
   письма атомарна: частично применённое письмо не уезжает в БД вместе со
   следующим.
3. ``test_repeated_sync_creates_single_card`` / ``..._is_marked_seen`` — окно
   между commit карточки и флагом \\Seen создавало вторую карточку-дубль при
   повторном прогоне синка на то же письмо.
4. ``test_*_attachment_*`` — вложение из письма недоверенный ввод: без белого
   списка расширений, magic bytes и лимита размера отправитель письма писал на
   сервер .html/.svg (Stored XSS при отдаче) или файл произвольного размера.
5. ``test_related/link_*_foreign_tenant_*`` — ручки работали по глобальному
   card_id и отдавали (и позволяли править) объекты чужой компании.
6. ``test_broken_settings_file_is_not_silently_replaced`` — нечитаемый файл
   настроек молча превращался в пустые настройки, и следующая запись затирала
   адрес ящика.

Сеть не используется: IMAP мокается (сеть/файловая система боевых писем не
трогаются). tenant во всех проверках задаётся явно, поэтому маскирующий
слушатель conftest ничего не скрывает, а тесты соседнего
test_email_sync_tenant.py с его снятием продолжают работать как раньше.
"""
import logging
import os
from email.message import EmailMessage

import pytest
from sqlalchemy.exc import IntegrityError

import models
import models_tenant
import routers.email_parser_router as email_parser_router

# Настоящая картинка 1x1 в формате PNG — проходит magic-bytes проверку.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _SyncSession:
    """Сессия-обёртка: считает commit/rollback и умеет ронять фиксацию.

    ``fail_commits`` — номера фиксаций, которые падают IntegrityError так же,
    как это делает СУБД: транзакция сессии остаётся открытой и непригодной,
    пока не будет отката.
    """

    def __init__(self, session, fail_commits=()):
        self._session = session
        self.fail_commits = set(fail_commits)
        self.commits = 0
        self.rollbacks = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    def commit(self):
        self.commits += 1
        if self.commits in self.fail_commits:
            raise IntegrityError(
                "INSERT INTO cards ...",
                {},
                Exception("FOREIGN KEY constraint failed"),
            )
        return self._session.commit()

    def rollback(self):
        self.rollbacks += 1
        return self._session.rollback()


class _FakeImapClient:
    """Мок IMAP с заранее заданными письмами и учётом выставленных флагов."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.stored_flags: list[tuple[bytes, str]] = []
        self.logged_out = False

    def login(self, email_addr, password):
        return "OK", [b""]

    def select(self, mailbox):
        return "OK", [str(len(self._messages)).encode()]

    def search(self, charset, criteria):
        ids = b" ".join(
            str(i + 1).encode() for i in range(len(self._messages))
        )
        return "OK", [ids]

    def fetch(self, message_id, query):
        return "OK", [(message_id, self._messages[int(message_id) - 1])]

    def store(self, message_id, command, flags):
        self.stored_flags.append((message_id, flags))
        return "OK", [b""]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


class _ImapHarness:
    """Подмена IMAP4_SSL: отдаёт заданные письма и запоминает STORE."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.clients: list[_FakeImapClient] = []

    def factory(self, host, timeout=15):
        client = _FakeImapClient(self.messages)
        self.clients.append(client)
        return client

    @property
    def stored_flags(self) -> list[tuple[bytes, str]]:
        return [flag for client in self.clients for flag in client.stored_flags]


def _mail(
    subject="Тестовое письмо",
    sender="sender@example.com",
    body="Нужна люстра в гостиную.",
    message_id="<letter-1@example.com>",
    attachments=(),
):
    """Собрать сырое письмо. attachments — кортежи (имя, данные, maintype, subtype).

    subject=None — письмо БЕЗ заголовка Subject вовсе (не пустая строка):
    именно такие письма роняли decode_header.
    """
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


@pytest.fixture
def mailbox(monkeypatch):
    """Подставить мок IMAP и заглушку сохранения last_sync."""
    monkeypatch.setenv("CRM_SMTP_PASSWORD", "test-password")
    monkeypatch.delenv("CRM_LEGACY_TENANT_ID", raising=False)
    monkeypatch.setattr(
        email_parser_router, "save_settings",
        lambda settings, tenant_id, **kwargs: None,
    )

    def _factory(messages, **overrides):
        harness = _ImapHarness(messages)
        monkeypatch.setattr(
            email_parser_router.imaplib, "IMAP4_SSL", harness.factory
        )
        settings = {
            "email": "sale@example.com",
            "password": "",
            "imap_server": "imap.example.com",
            "target_status": "Новый запрос",
        }
        settings.update(overrides)
        return harness, settings

    return _factory


def _sync(db, settings, tenant_id=None):
    return email_parser_router._sync_tenant_emails(tenant_id, settings, db)


def _upload_dir_files() -> set[str]:
    return set(os.listdir(email_parser_router.UPLOAD_DIR))


# --- 0. Отсутствующий заголовок Subject ------------------------------------


def test_letter_without_subject_is_imported(db, mailbox, caplog):
    """Письмо без заголовка Subject импортируется, а не теряется.

    На проде 29.09 письмо b'3137' роняло весь цикл синка на
    ``decode_header(None)`` -> TypeError: expected string or bytes-like
    object, got 'NoneType'. Карточка не создавалась, письмо терялось.
    Заголовок карточки собирается из отправителя.
    """
    messages = [_mail(subject=None, message_id="<no-subject@example.com>")]
    harness, settings = mailbox(messages)

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    assert "TypeError" not in caplog.text

    db.expire_all()
    card = db.query(models.Card).one()
    assert card.title == "Письмо без темы от sender@example.com", card.title
    # Письмо без темы — обычное, а не битое: оно отмечается прочитанным.
    assert {flags for _, flags in harness.stored_flags} == {"\\Seen"}


# --- 1. Откат сессии: одно плохое письмо не убивает синк -------------------


def test_one_bad_letter_does_not_kill_the_batch(db, mailbox, caplog):
    """Ошибка БД на письме откатывается, и следующие письма обрабатываются.

    Настройки указывают на несуществующий tenant, поэтому вставка карточки
    падает по внешнему ключу — та же ошибка, что и в бою после неудачной
    правки данных. Раньше отката не было: второе письмо падало уже с
    PendingRollbackError, а сессия оставалась непригодной для вызывающего.
    """
    messages = [
        _mail(subject="Первое", message_id="<one@example.com>"),
        _mail(subject="Второе", sender="two@example.com",
              message_id="<two@example.com>"),
    ]
    harness, settings = mailbox(messages, tenant_id="999")

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    assert result["success"] is True, result
    assert result["count"] == 0, result
    # Сессия после синка обязана остаться пригодной: в сломанном коде она
    # оставалась в pending-rollback и любой следующий запрос падал.
    assert db.query(models_tenant.Tenant).count() >= 1
    # Каскада нет: каждое письмо падает по своей причине, а не по
    # PendingRollbackError от предыдущего.
    assert "PendingRollbackError" not in caplog.text
    failures = [
        record for record in caplog.records
        if "Не удалось обработать письмо" in record.getMessage()
    ]
    assert len(failures) == 2, [r.getMessage() for r in failures]
    assert all("IntegrityError" in r.getMessage() for r in failures)
    db.expire_all()
    assert db.query(models.Card).count() == 0


# --- 2. Атомарность обработки одного письма --------------------------------


def test_failed_letter_is_not_committed_with_the_next_one(db, mailbox, caplog):
    """Падение фиксации письма не оставляет полукарточку в следующем commit.

    Если отката нет, незафиксированная карточка первого письма уезжает в БД
    вместе со вторым письмом — пользователь видит два «письма», из которых
    одно никогда не дошло до конца. Заодно проверяется уборка файла вложения,
    который уже лег на диск, но в БД не попал.
    """
    messages = [
        _mail(subject="С вложением", message_id="<with-att@example.com>",
              attachments=[("photo.png", PNG_BYTES, "image", "png")]),
        _mail(subject="Без вложений", sender="second@example.com",
              message_id="<second@example.com>"),
    ]
    harness, settings = mailbox(messages)
    files_before = _upload_dir_files()
    probe = _SyncSession(db, fail_commits=[1])

    with caplog.at_level(logging.WARNING):
        result = _sync(probe, settings)

    assert result["success"] is True, result
    assert result["count"] == 1, result
    assert probe.rollbacks == 1, "сессия должна быть откачена после падения"
    db.expire_all()
    cards = db.query(models.Card).order_by(models.Card.id).all()
    assert [c.title for c in cards] == ["Письмо: Без вложений"], [
        c.title for c in cards
    ]
    # Оборванное письмо не оставило ни карточки, ни записи в ленте, ни файла.
    assert db.query(models.ActivityLog).count() == 1
    assert _upload_dir_files() == files_before


def test_one_letter_is_committed_once(db, mailbox):
    """Одно письмо — одна фиксация: карточка, вложения и лента применяются вместе."""
    messages = [
        _mail(attachments=[("photo.png", PNG_BYTES, "image", "png")]),
    ]
    harness, settings = mailbox(messages)
    probe = _SyncSession(db)

    result = _sync(probe, settings)

    assert result["success"] is True, result
    assert probe.commits == 1, (
        f"ожидалась одна фиксация на письмо, получено {probe.commits}"
    )


# --- 3. Идемпотентность повторного синка -----------------------------------


def test_repeated_sync_creates_single_card(db, mailbox):
    """Повторная обработка того же письма не создаёт вторую карточку.

    Сценарий боя: процесс упал между commit карточки и флагом \\Seen, либо
    сессия IMAP вернула письмо снова (письмо осталось UNSEEN, флаг не
    применился, письмо вернулось в выборку следующего прогона).
    """
    messages = [_mail(message_id="<idempotent@example.com>")]
    harness, settings = mailbox(messages)

    first = _sync(db, settings)
    second = _sync(db, settings)

    assert first["count"] == 1, first
    assert second["success"] is True, second
    assert second["count"] == 0, second
    db.expire_all()
    assert db.query(models.Card).count() == 1
    assert db.query(models.ActivityLog).count() == 1
    # Повторный прогон не плодит карточек даже после нескольких попыток.
    _sync(db, settings)
    db.expire_all()
    assert db.query(models.Card).count() == 1


def test_duplicate_letter_is_marked_seen(db, mailbox):
    """Письмо-дубль помечается прочитанным, чтобы IMAP больше его не отдавал."""
    messages = [_mail(message_id="<seen@example.com>")]
    harness, settings = mailbox(messages)

    _sync(db, settings)
    _sync(db, settings)

    assert len(harness.stored_flags) == 2, harness.stored_flags
    assert {flags for _, flags in harness.stored_flags} == {"\\Seen"}
    db.expire_all()
    assert db.query(models.Card).count() == 1


def test_message_id_of_another_tenant_does_not_block_import(db, mailbox):
    """Ключ идемпотентности не должен переезжать между компаниями."""
    db.add(models_tenant.Tenant(
        id=202, name="Второй тестовый tenant", db_path="tests/crm_202.db"
    ))
    db.commit()
    db.add(models.Card(
        title="Письмо чужого tenant", status="Новый запрос", total_amount=0.0,
        paid_amount=0.0, payment_status="Не оплачен", is_deleted=False,
        tenant_id=202,
    ))
    db.commit()
    foreign_card = db.query(models.Card).filter(
        models.Card.tenant_id == 202
    ).one()
    db.add(models.ActivityLog(
        user_id=None, card_id=foreign_card.id, action="Импорт почты",
        details="Message-ID: <shared@example.com>\nОт: klient@example.com",
        # FIX 2026-09-30 (К-1): ключ держится в колонке, а не в тексте.
        # Без неё тест проверял бы уже не то и проходил вхолостую.
        email_message_id="shared@example.com",
        tenant_id=202,
    ))
    db.commit()

    harness, settings = mailbox(
        [_mail(message_id="<shared@example.com>")], tenant_id="101"
    )
    result = _sync(db, settings, tenant_id=101)

    assert result["count"] == 1, result
    db.expire_all()
    assert db.query(models.Card).count() == 2


def test_reply_quoting_original_message_id_is_still_imported(db, mailbox):
    """Оригинал, чей Message-ID процитирован в ответе, НЕ теряется.

    Регрессия на аудит 2026-09-30 (К-1) — самый дорогой дефект в отчёте.
    Раньше ключ искался подстрокой в details, а туда пишется ТЕЛО письма.

    Сценарий потери. Приходит ответ, в теле которого процитированы заголовки
    оригинала вместе с его Message-ID (Outlook и Thunderbird цитируют их по
    умолчанию). Ответ импортируется, и его запись ленты теперь СОДЕРИТ
    Message-ID оригинала. Дальше доходит очередь самого оригинала — а он
    ещё не импортирован. Старый LIKE искал его Message-ID в details и
    находил его в записи ЧУЖОГО, уже импортированного ответа: оригинал
    объявлялся «уже импортированным», карточка не создавалась, письмо
    помечалось флагом \\Seen — и исходная заявка клиента пропадала навсегда.
    Обратной дороги нет: непрочитанным письмо больше не станет.

    Порядок в списке ИМЕННО ТАКОЙ — ответ раньше оригинала. Это не
    искусственное условие: синк берёт непрочитанные письма, а \\Seen ставится
    только после успешного commit, причём ошибка STORE проглатывается
    (_mark_seen). Оригинал, оставшийся непрочитанным, попадает в выборку
    позже ответа — например, если флаг не записался.
    """
    reply_body = (
        "Спасибо!\n"
        "От: klient@example.com\n"
        "Тема: Люстра\n"
        "Message-ID: <original-1@example.com>\n"
        "Дата: 30 сентября 2026\n"
    )
    harness, settings = mailbox([
        _mail(subject="Re: Люстра", message_id="<reply-2@example.com>",
              body=reply_body),
        _mail(subject="Люстра", message_id="<original-1@example.com>"),
    ])

    result = _sync(db, settings)

    assert result["count"] == 2, (
        f"ожидались обе заявки, импортировано {result['count']}: {result}"
    )
    db.expire_all()
    cards = db.query(models.Card).all()
    assert len(cards) == 2, "оригинал заявки обязан стать отдельной карточкой"
    # Ключи импорта у карточек РАЗНЫЕ, а не совпали по цитате в теле.
    keys = {row.email_message_id for row in db.query(models.ActivityLog).all()}
    assert keys == {"original-1@example.com", "reply-2@example.com"}, keys


def test_long_reply_still_keeps_its_own_card(db, mailbox):
    """Ответ с процитированным заголовком импортируется сам по себе.

    Обратная сторона того же дефекта: чистильщик режет цитату, если маркер
    «От:» стоит дальше 40-го символа (email_cleaner.py:54), и тогда старый
    LIKE срабатывал в другую сторону — терялся ответ. Здесь цитата длинная и
    срезается, Message-ID оригинала в details не попадает, и оба письма
    импортируются. Тест фиксирует, что переход на точное сравнение ничего не
    сломал в обычном случае.
    """
    reply_body = (
        "Спасибо, всё получили, отправляем оплату сегодня до конца дня.\n"
        "От: klient@example.com\n"
        "Тема: Люстра\n"
        "Message-ID: <original-1@example.com>\n"
    )
    harness, settings = mailbox([
        _mail(subject="Re: Люстра", message_id="<reply-2@example.com>",
              body=reply_body),
        _mail(subject="Люстра", message_id="<original-1@example.com>"),
    ])

    result = _sync(db, settings)

    assert result["count"] == 2, result
    db.expire_all()
    assert db.query(models.Card).count() == 2


def test_repeated_sync_does_not_duplicate_cards(db, mailbox):
    """Повторный прогон по тем же письмам не плодит карточки.

    Точное сравнение по колонке обязано вести себя как прежний LIKE во всём,
    кроме ложных срабатываний, — иначе починить К-1 ценой дублей было бы
    плохим разменом.
    """
    harness, settings = mailbox([_mail(message_id="<once@example.com>")])

    _sync(db, settings)
    _sync(db, settings)

    db.expire_all()
    assert db.query(models.Card).count() == 1
    assert len(harness.stored_flags) >= 1


def test_letters_without_message_id_are_not_merged(db, mailbox):
    """Без Message-ID дедупликация не применяется: разные письма не склеиваются.

    Ключа нет — сравнивать нечего, и молча склеивать такие письма значило бы
    потерять реальные заявки.
    """
    messages = [
        _mail(subject="Первое", message_id=None),
        _mail(subject="Второе", message_id=None),
    ]
    harness, settings = mailbox(messages)

    result = _sync(db, settings)

    assert result["count"] == 2, result
    db.expire_all()
    assert db.query(models.Card).count() == 2


# --- 4. Вложения: белый список, magic bytes, размер, путь -------------------


def test_allowed_attachment_is_saved_inside_uploads_dir(db, mailbox):
    """Разрешённое вложение сохраняется и ссылается на файл в каталоге загрузок."""
    messages = [_mail(attachments=[("схема.png", PNG_BYTES, "image", "png")])]
    harness, settings = mailbox(messages)

    result = _sync(db, settings)

    assert result["count"] == 1, result
    db.expire_all()
    attachment = db.query(models.CardAttachment).one()
    assert attachment.file_name == "схема.png"
    uploads = os.path.realpath(email_parser_router.UPLOAD_DIR)
    assert os.path.realpath(
        os.path.join(uploads, os.path.basename(attachment.file_path))
    ).startswith(uploads + os.sep)
    stored = os.path.join(uploads, os.path.basename(attachment.file_path))
    assert os.path.isfile(stored)
    with open(stored, "rb") as handle:
        assert handle.read() == PNG_BYTES


@pytest.mark.parametrize("name,maintype,subtype", [
    ("страница.html", "text", "html"),
    ("логотип.svg", "image", "svg+xml"),
    ("скрипт.php", "application", "octet-stream"),
])
def test_forbidden_attachment_type_is_skipped(db, mailbox, caplog, name, maintype, subtype):
    """html/svg/php из письма не пишутся на диск (Stored XSS при отдаче)."""
    payload = b"<script>alert(1)</script>" if maintype != "application" else b"<?php echo 1;"
    messages = [_mail(attachments=[(name, payload, maintype, subtype)])]
    harness, settings = mailbox(messages)
    files_before = _upload_dir_files()

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    # Письмо импортировано, вложение — нет, и синк не упал.
    assert result["success"] is True, result
    assert result["count"] == 1, result
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0
    assert _upload_dir_files() == files_before
    assert "не разрешён" in caplog.text


def test_attachment_content_must_match_extension(db, mailbox, caplog):
    """Переименованный исполняемый файл под видом .png отбрасывается."""
    messages = [_mail(attachments=[
        ("картинка.png", b"MZ\x90\x00 executable", "image", "png"),
    ])]
    harness, settings = mailbox(messages)
    files_before = _upload_dir_files()

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    assert result["count"] == 1, result
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0
    assert _upload_dir_files() == files_before
    assert "не соответствует типу" in caplog.text


def test_oversized_attachment_is_skipped(db, mailbox, monkeypatch, caplog):
    """Вложение сверх лимита не пишется на диск (DoS через почту)."""
    monkeypatch.setattr(email_parser_router, "MAX_ATTACHMENT_BYTES", 32)
    messages = [_mail(attachments=[
        ("doc.pdf", b"%PDF-1.7" + b"0" * 4096, "application", "pdf"),
    ])]
    harness, settings = mailbox(messages)
    files_before = _upload_dir_files()

    with caplog.at_level(logging.WARNING):
        result = _sync(db, settings)

    assert result["count"] == 1, result
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0
    assert _upload_dir_files() == files_before
    assert "превышает лимит" in caplog.text


def test_attachment_name_cannot_escape_uploads_dir(db, mailbox):
    """Имя вложения из письма не может вынести файл за пределы каталога."""
    messages = [_mail(attachments=[
        ("../../../../etc/passwd.png", PNG_BYTES, "image", "png"),
    ])]
    harness, settings = mailbox(messages)

    result = _sync(db, settings)

    assert result["count"] == 1, result
    db.expire_all()
    attachment = db.query(models.CardAttachment).one()
    uploads = os.path.realpath(email_parser_router.UPLOAD_DIR)
    stored = os.path.realpath(
        os.path.join(uploads, os.path.basename(attachment.file_path))
    )
    assert stored.startswith(uploads + os.sep), stored
    assert ".." not in os.path.basename(stored)
    assert os.path.isfile(stored)


# --- 5. Tenant-изоляция ручек link и related -------------------------------


def test_related_returns_404_for_foreign_tenant_card(client, two_tenant_users, db):
    """Карточка чужой компании не отличается от несуществующей."""
    first_headers = two_tenant_users["first_headers"]
    foreign = models.Card(
        title="Сделка чужого tenant", status="Новый запрос", total_amount=0.0,
        paid_amount=0.0, payment_status="Не оплачен", is_deleted=False,
        sender_email="klient@example.com",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add(foreign)
    db.commit()
    db.refresh(foreign)

    foreign_response = client.get(
        f"/email-parser/related/{foreign.id}", headers=first_headers
    )
    missing_response = client.get(
        "/email-parser/related/999999", headers=first_headers
    )

    assert foreign_response.status_code == 404, foreign_response.text
    assert missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json() == {
        "detail": "Карточка не найдена"
    }


def test_link_rejects_foreign_tenant_card(client, two_tenant_users, db):
    """Нельзя перелить письмо чужой компании в свою сделку и убрать её в корзину."""
    first_headers = two_tenant_users["first_headers"]
    foreign = models.Card(
        title="Письмо чужого tenant", description="текст письма",
        status="Новый запрос", total_amount=0.0, paid_amount=0.0,
        payment_status="Не оплачен", is_deleted=False,
        sender_email="klient@example.com",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    own = models.Card(
        title="Моя сделка", description="уже работаем",
        status="В работе", total_amount=1200.0, paid_amount=0.0,
        payment_status="Не оплачен", is_deleted=False,
        sender_email="klient@example.com",
        tenant_id=two_tenant_users["first_tenant_id"],
    )
    db.add_all([foreign, own])
    db.commit()
    db.refresh(foreign)
    db.refresh(own)

    response = client.post(
        f"/email-parser/link/{foreign.id}", headers=first_headers,
        json={"target_card_id": own.id},
    )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Карточка не найдена"}
    db.expire_all()
    assert db.query(models.Card).filter(
        models.Card.id == foreign.id
    ).one().is_deleted is False
    assert db.query(models.Card).filter(
        models.Card.id == own.id
    ).one().description == "уже работаем"
    assert db.query(models.ActivityLog).count() == 0


def test_link_rejects_foreign_tenant_target(client, two_tenant_users, db):
    """Целевая карточка чужой компании недоступна так же, как несуществующая."""
    first_headers = two_tenant_users["first_headers"]
    own = models.Card(
        title="Письмо своего tenant", description="текст письма",
        status="Новый запрос", total_amount=0.0, paid_amount=0.0,
        payment_status="Не оплачен", is_deleted=False,
        sender_email="klient@example.com",
        tenant_id=two_tenant_users["first_tenant_id"],
    )
    foreign = models.Card(
        title="Сделка чужого tenant", description="",
        status="В работе", total_amount=1200.0, paid_amount=0.0,
        payment_status="Не оплачен", is_deleted=False,
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add_all([own, foreign])
    db.commit()
    db.refresh(own)
    db.refresh(foreign)

    response = client.post(
        f"/email-parser/link/{own.id}", headers=first_headers,
        json={"target_card_id": foreign.id},
    )

    assert response.status_code == 404, response.text
    db.expire_all()
    assert db.query(models.Card).filter(
        models.Card.id == own.id
    ).one().is_deleted is False


def test_related_lists_only_same_tenant_cards(client, two_tenant_users, db):
    """Своя карточка видна, но чужие сделки того же отправителя — нет."""
    first_headers = two_tenant_users["first_headers"]
    own = models.Card(
        title="Моя сделка", status="Новый запрос", total_amount=0.0,
        paid_amount=0.0, payment_status="Не оплачен", is_deleted=False,
        sender_email="klient@example.com",
        tenant_id=two_tenant_users["first_tenant_id"],
    )
    foreign = models.Card(
        title="Сделка чужого tenant", status="В работе", total_amount=500.0,
        paid_amount=0.0, payment_status="Не оплачен", is_deleted=False,
        sender_email="klient@example.com",
        tenant_id=two_tenant_users["second_tenant_id"],
    )
    db.add_all([own, foreign])
    db.commit()
    db.refresh(own)

    response = client.get(
        f"/email-parser/related/{own.id}", headers=first_headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sender_email"] == "klient@example.com"
    # Единственная сделка того же отправителя принадлежит чужому tenant —
    # в выдаче её быть не должно, а ручка обязана ответить, а не 404.
    assert body["related"] == []


# --- 6. Настройки почты: отказ явный, файл не затирается --------------------


def test_broken_settings_file_is_not_silently_replaced(client, admin, db, tmp_path, monkeypatch, caplog):
    """Нечитаемый JSON настроек — 503 и traceback, а не пустые настройки.

    Раньше исключение глоталось, возвращались пустые настройки, и следующий
    POST /settings затирал файл — адрес ящика исчезал без единого следа.
    """
    settings_file = tmp_path / "email_settings.json"
    broken = '{"email": "sale@svetvdome.by", "password": "gAAAA-broken'
    settings_file.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(
        email_parser_router, "_get_settings_path", lambda tenant_id=None: str(settings_file)
    )

    with caplog.at_level(logging.ERROR):
        read_response = client.get("/email-parser/settings", headers=admin[1])
        write_response = client.post(
            "/email-parser/settings", headers=admin[1],
            json={
                "email": "new@svetvdome.by",
                "password": "новый-пароль",
                "imap_server": "imap.example.com",
                "target_status": "Новый запрос",
            },
        )

    assert read_response.status_code == 503, read_response.text
    assert write_response.status_code == 503, write_response.text
    # Файл на диске не тронут — адрес и пароль не потеряны.
    assert settings_file.read_text(encoding="utf-8") == broken
    assert "Не удалось прочитать настройки почты" in caplog.text


def test_broken_settings_file_fails_sync_explicitly(db, mailbox, tmp_path, monkeypatch, caplog):
    """cron sync-all не объявляет успех при недоступном файле настроек."""
    settings_file = tmp_path / "email_settings.json"
    settings_file.write_text("не json вовсе", encoding="utf-8")
    monkeypatch.setattr(
        email_parser_router, "_get_settings_path", lambda tenant_id=None: str(settings_file)
    )
    harness, _ = mailbox([_mail()])

    with caplog.at_level(logging.ERROR):
        result = email_parser_router.sync_all_tenants(db=db)

    body = result["results"][0]
    assert body["success"] is False, body
    assert "недоступен" in body["error"], body
    assert result["tenants_failed"] == 1
    assert result["total_count"] == 0
    assert harness.clients == [], "к IMAP не должны ходить при битых настройках"


# --- 7. Привязка к клиенту не пересекает tenant ---------------------------

def test_sender_is_not_matched_to_client_of_another_tenant(db, mailbox):
    """Одноимённый email у двух компаний: карточка не должна достаться чужой.

    Адрес в CRM не ключ. Раньше клиент искался по `Client.email` без учёта
    tenant, поэтому письмо компании A привязывалось к клиенту компании B.
    """
    first_tenant = db.query(models_tenant.Tenant.id).order_by(
        models_tenant.Tenant.id
    ).scalar()
    second = models_tenant.Tenant(
        id=909, name="Чужой tenant", db_path="tests/crm_909.db"
    )
    db.add(second)
    db.commit()

    db.add(models.Client(name="Клиент чужой компании", email="sender@example.com",
                         tenant_id=second.id))
    db.commit()

    harness, settings = mailbox([_mail()])
    result = _sync(db, settings, tenant_id=first_tenant)
    assert result["success"] is True, result

    db.expire_all()
    card = db.query(models.Card).filter(
        models.Card.sender_email == "sender@example.com"
    ).one()
    assert card.client_id is None, "письмо привязалось к клиенту чужого tenant"


def test_sender_is_matched_to_client_of_same_tenant(db, mailbox):
    """Свой tenant по-прежнему связывается — изоляция не ломает импорт."""
    tenant_id = db.query(models_tenant.Tenant.id).order_by(
        models_tenant.Tenant.id
    ).scalar()
    own = models.Client(name="Свой клиент", email="sender@example.com",
                        tenant_id=tenant_id)
    db.add(own)
    db.commit()
    own_id = own.id

    # Второй tenant с тем же адресом: выбор должен быть однозначным.
    second = models_tenant.Tenant(
        id=910, name="Второй tenant", db_path="tests/crm_910.db"
    )
    db.add(second)
    db.commit()
    db.add(models.Client(name="Чужой", email="sender@example.com",
                         tenant_id=second.id))
    db.commit()

    harness, settings = mailbox([_mail()])
    result = _sync(db, settings, tenant_id=tenant_id)
    assert result["success"] is True, result

    db.expire_all()
    card = db.query(models.Card).filter(
        models.Card.sender_email == "sender@example.com"
    ).one()
    assert card.client_id == own_id
