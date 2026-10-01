"""Фоновая синхронизация почты: полнота прогона и защита от наложения.

Регрессии аудита 2026-09-30:
  * Ф-1 — /email-parser/sync-all рапортовал ``success: True, count: 0`` при
    полной потере писем: упавшее письмо уходило в warning, цикл продолжался.
  * Ф-2 — синк запускался поверх себя, когда не укладывался в 5-минутный
    интервал cron'а, и два прогона делали по карточке на одно письмо.
  * Ф-6 — выборка непрочитанных писем не была ограничена, и разросшийся ящик
    порождал ровно тот наложенный прогон, что и Ф-2.
"""

import pytest

from routers import email_parser_router
from services import single_instance

# Обвязка письма переиспользуется из соседнего модуля. Фикстуру mailbox
# импортировать нельзя: pytest перестаёт её разрешать и падает с «Fixture
# called directly». Поэтому здесь своя фикстура, повторяющая её тело один в
# один, а класс харнесса и сборщик письма берутся напрямую.
from tests.test_email_import_hardening import (  # noqa: F401
    _ImapHarness,
    _mail,
    _sync,
)


@pytest.fixture
def mailbox(monkeypatch):
    """Подставить мок IMAP и заглушку сохранения last_sync (как в hardening)."""
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


# ---------------------------------------------------------------------------
# Ф-1: счётчик ошибок
# ---------------------------------------------------------------------------

def test_sync_reports_failed_letters_instead_of_silent_success(db, mailbox, monkeypatch):
    """Падение на одном письме видно в ответе, а не прячется за count: 0."""
    harness, settings = mailbox([
        _mail(subject="Хорошее", message_id="<good-1@example.com>"),
        _mail(subject="Плохое", message_id="<bad-2@example.com>"),
    ])

    real = email_parser_router._import_email_message
    calls = {"n": 0}

    def flaky(db_, msg, e_id, **kwargs):
        calls["n"] += 1
        if "bad-2" in str(msg.get("Message-ID")):
            raise ValueError("искусственная ошибка разбора письма")
        return real(db_, msg, e_id, **kwargs)

    monkeypatch.setattr(email_parser_router, "_import_email_message", flaky)

    result = _sync(db, settings)

    # Одно письмо импортировано, одно упало — и это видно.
    assert result["count"] == 1, result
    assert result["errors"] == 1, (
        f"падение письма обязано попасть в счётчик: {result}"
    )
    # В тексте — IMAP-номер письма и тип исключения, а не Message-ID: на этом
    # уровне известен только e_id, и подставлять заголовок письма в отчёт не
    # нужно. Важно, что причина названа, а не «ошибка импорта».
    assert result["error"] and "ValueError" in result["error"], result
    assert "искусственная ошибка" in result["error"], result
    # Упавшее письмо осталось непрочитанным и вернётся при следующем прогоне:
    # фиксировать его как \Seen нельзя, иначе потеря необратима.
    flagged = {e_id for e_id, _ in harness.stored_flags}
    assert len(flagged) == 1, f"отмечено писем {len(flagged)}, ожидалось 1"


def test_all_letters_failing_is_reported_as_errors(db, mailbox, monkeypatch):
    """Полная потеря почты больше не выглядит как «писем не было»."""
    harness, settings = mailbox([
        _mail(message_id="<a-1@example.com>"),
        _mail(message_id="<b-2@example.com>"),
    ])

    def always_fail(db_, msg, e_id, **kwargs):
        raise RuntimeError("IMAP отвалился")

    monkeypatch.setattr(
        email_parser_router, "_import_email_message", always_fail)

    result = _sync(db, settings)

    assert result["count"] == 0, result
    assert result["errors"] == 2, result
    # Раньше это был ``{"success": True, "count": 0}`` — неотличимо от ящика,
    # в котором действительно ничего не было.
    assert result["success"] is True, "сам прогон состоялся, ошибки — про письма"


def test_healthy_sync_reports_zero_errors(db, mailbox, monkeypatch):
    """Штатный прогон не должен ложно пугать счётчиком."""
    harness, settings = mailbox([_mail(message_id="<ok-1@example.com>")])

    result = _sync(db, settings)

    assert result["count"] == 1, result
    assert result["errors"] == 0, result
    assert result["error"] is None, result


# ---------------------------------------------------------------------------
# Ф-6: потолок на выборку
# ---------------------------------------------------------------------------

class _MailboxStub:
    """Заглушка IMAP, отдающая заранее заданный список писем."""

    def __init__(self, ids):
        self.ids = ids
        self.search_args = None

    def search(self, charset, criteria):
        self.search_args = criteria
        return "OK", [b" ".join(self.ids)]


def test_unseen_search_is_capped(monkeypatch):
    """Разросшийся ящик не превращает синк в тысячи fetch'ей за раз."""
    monkeypatch.setattr(
        email_parser_router, "MAX_UNSEEN_PER_RUN", 5, raising=False)

    stub = _MailboxStub([b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8"])
    ids = email_parser_router._search_unseen(stub, "30-Sep-2026")

    assert len(ids) == 5, ids
    # Обрезаем с конца: больший UID — более позднее письмо, значит в выборку
    # попадают актуальные, а хвост вернётся следующим прогоном.
    assert ids == [b"4", b"5", b"6", b"7", b"8"], ids


def test_unseen_search_under_cap_is_untouched(monkeypatch):
    """Обычный объём не обрезается."""
    monkeypatch.setattr(
        email_parser_router, "MAX_UNSEEN_PER_RUN", 5, raising=False)

    stub = _MailboxStub([b"1", b"2", b"3"])
    ids = email_parser_router._search_unseen(stub, "30-Sep-2026")

    assert ids == [b"1", b"2", b"3"], ids


def test_unseen_search_raises_on_bad_status(monkeypatch):
    """Прежнее поведение при ошибке сервера сохранено."""
    class Bad:
        def search(self, charset, criteria):
            return "NO", [b""]

    with pytest.raises(Exception):
        email_parser_router._search_unseen(Bad(), "30-Sep-2026")


# ---------------------------------------------------------------------------
# Ф-2: блокировка от наложения прогонов
# ---------------------------------------------------------------------------

def test_single_instance_blocks_second_holder(tmp_path):
    """Второй вход в секцию отклоняется, первый — проходит."""
    with single_instance.single_instance("проба", data_dir=str(tmp_path)):
        with pytest.raises(single_instance.LockBusy):
            with single_instance.single_instance("проба", data_dir=str(tmp_path)):
                pass


def test_single_instance_releases_on_exit(tmp_path):
    """Блокировка снимается и после нормального выхода, и после исключения."""
    with single_instance.single_instance("проба", data_dir=str(tmp_path)):
        pass
    with single_instance.single_instance("проба", data_dir=str(tmp_path)):
        pass

    with pytest.raises(ValueError):
        with single_instance.single_instance("проба", data_dir=str(tmp_path)):
            raise ValueError("падение внутри секции")

    # После падения секция снова доступна — иначе одна ошибка навсегда
    # остановила бы фоновые задачи.
    with single_instance.single_instance("проба", data_dir=str(tmp_path)):
        pass


def test_different_names_do_not_block_each_other(tmp_path):
    """Локи независимы: почта не должна блокировать другие фоновые задачи."""
    with single_instance.single_instance("почта", data_dir=str(tmp_path)):
        with single_instance.single_instance("ротация", data_dir=str(tmp_path)):
            pass


def test_sync_all_skips_when_already_running(client, db, monkeypatch, tmp_path):
    """Занятая блокировка даёт явный «пропущено», а не ложный успех.

    Без флага skipped ответ выглядел бы как «син�� прошёл, писем не нашлось».
    """
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))
    calls = {"n": 0}

    def would_sync(*args, **kwargs):
        calls["n"] += 1
        return {"success": True, "count": 0, "errors": 0, "results": []}

    monkeypatch.setattr(email_parser_router, "_run_sync_all", would_sync)

    with single_instance.single_instance("crm-email-sync", data_dir=str(tmp_path)):
        r = client.post("/email-parser/sync-all",
                        headers={"X-Cron-Token": _cron_token()})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("skipped") == 1, body
    assert calls["n"] == 0, "второй синк не должен был запускаться"


def test_sync_all_runs_when_free(client, db, monkeypatch, tmp_path):
    """Без конкуренции синк выполняется штатно."""
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))
    calls = {"n": 0}

    def would_sync(*args, **kwargs):
        calls["n"] += 1
        return {"success": True, "total_count": 3, "tenants_processed": 1,
                "tenants_failed": 0, "errors": 0, "results": []}

    monkeypatch.setattr(email_parser_router, "_run_sync_all", would_sync)

    r = client.post("/email-parser/sync-all", headers={"X-Cron-Token": _cron_token()})

    assert r.status_code == 200, r.text
    assert calls["n"] == 1
    assert r.json().get("total_count") == 3, r.text


def _cron_token() -> str:
    from auth import CRON_TOKEN
    return CRON_TOKEN
