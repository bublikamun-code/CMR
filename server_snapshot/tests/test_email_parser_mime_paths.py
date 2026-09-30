"""Чистые функции разбора письма: MIME, заголовки, пути настроек.

Тесты первой части модуля ``routers/email_parser_router.py``: ни IMAP, ни
сети, ни боевой БД. Работаем напрямую с ``email.message.EmailMessage`` и с
настоящими файлами в ``tmp_path``.

Что фиксируют тесты:

1. ``test_*_part_text_*`` — декодирование текста письма его СОБСТВЕННОЙ
   кодировкой. Раньше байты читались как UTF-8 с ``errors="ignore"``, и
   письма госпочты в windows-1251 / koi8-r приходили «замыленным» — без
   русских букв. Это антирегресс на тот фикс: текст должен совпадать
   посимвольно.
2. ``test_*_attachment_filename_*`` — имя файла вложения в RFC 2047
   encoded-word (так кодирует Outlook в нарушение стандарта),
   переносы строк между частями и битое encoded-word, которое обязано
   дать warning, а не исключение.
3. ``test_save_settings_*`` — ``save_settings`` пишет пароль только в
   Fernet-форме, а ветка ``preserve_encrypted_password`` проверяет
   значение реальной расшифровкой, а не префиксом ``gAAAA``.
4. ``test_*_inside_data_dir_*`` — ``_ensure_inside_data_dir``: настройки и
   ключи не пишутся за пределы ``CRM_DATA_DIR`` (абсолютный путь, ``..``
   после realpath, симлинк наружу).
5. ``test_*_settings_path_*`` — legacy-файл рядом с кодом при ОТСУТСТВИИ
   ``CRM_DATA_DIR`` и безусловная победа каталога данных при его наличии
   (FIX 18.09).
6. ``test_*_attachment_name_*`` / ``test_*_message_id_*`` — ведущие точки,
   обрезка длины, канонизация и отсечка Message-ID.

Маршруты ``POST /sync`` и ``POST /sync-all`` здесь НЕ трогаются: ими
занимаются соседние части.
"""
import base64
import email
import json
import logging
import os
from email.message import EmailMessage

import pytest

import routers.email_parser_router as epr
from email_credentials import (
    InvalidCredentialCiphertextError,
    LegacyPlaintextError,
    decrypt_credential,
    encrypt_credential,
    obtain_fernet,
)

# Настоящая картинка 1x1 PNG — в заголовке имени вложения роли не играет,
# нужна лишь как «тело» части, чтобы get_filename() работал как у письма.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

# Переводы строк — \n: set_content() пересобирает тело по policy.linesep,
# а символы вне таблицы koi8-r («ёлочки», тире, №) в неё не входят, поэтому
# текст письма держим в пересечении трёх кодировок.
RUSSIAN_BODY = (
    "Здравствуйте! Нужна люстра в гостиную и торшер в спальню.\n"
    "Счет на 45 000 рублей, оплата до 15 октября.\n"
    "С уважением, ООО Свет в доме."
)

# Строка, в которой есть и кириллица, и цифры со знаками: именно на ней
# «замыливание» после декодирования как UTF-8 было видно глазом.
CYRILLIC_PROBE = "Ёжик съел яблоко, ёжик доволен (счёт 45/7)"


def _b64(text: str) -> str:
    """RFC 2047 encoded-word в base64 — так Outlook кодирует имена вложений."""
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def _part_with_disposition(disposition):
    """Собрать MIME-часть с СЫРОЙ строкой Content-Disposition.

    Заголовок подставляется вручную, а не через ``add_attachment``: стандартные
    сериализаторы сами экранируют значение, и битое/свёрнутое encoded-word
    до кода не дошло бы — а именно такие письма приходят от Outlook.
    """
    if isinstance(disposition, str):
        disposition = disposition.encode("ascii")
    raw = (
        b"From: klient@example.com\r\n"
        b"Content-Type: application/octet-stream\r\n"
        + disposition
        + b"\r\n\r\n"
        + PNG_BYTES
    )
    return email.message_from_bytes(raw)


def _attachment_part(filename_value: bytes) -> str:
    """Имя файла вложения так, как его вернул бы ``get_filename()``."""
    part = _part_with_disposition(
        b'Content-Disposition: attachment; filename="' + filename_value + b'"'
    )
    return epr._decode_attachment_filename(part)


def _data_dir_env(monkeypatch, tmp_path):
    """Изолированный CRM_DATA_DIR + подменённый database.DATA_DIR.

    ``_get_settings_path`` делает ``from database import DATA_DIR`` при каждом
    вызове (строка 104), поэтому подменять нужно ИМЯ АТРИБУТА модуля
    ``database`` — переменная окружения сама по себе меняет только
    ``_ensure_inside_data_dir``.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CRM_DATA_DIR", str(data_dir))
    monkeypatch.setattr("database.DATA_DIR", str(data_dir))
    return data_dir


# --- 1. _decode_part_text: кодировка письма, а не UTF-8 --------------------


def test_part_without_payload_decodes_to_empty_string():
    """Часть без тела (контейнер multipart) даёт пустую строку, а не падает.

    ``get_payload(decode=True)`` для контейнера возвращает None, и ранний
    выход на ``payload is None`` (строка 199) — единственный способ не
    уронить импорт на письме с вложениями без тела.
    """
    container = email.message_from_string(
        'Content-Type: multipart/mixed; boundary="BB"\n\n'
        "--BB\nContent-Type: application/octet-stream\n\nданные\n--BB--\n"
    )
    assert container.get_payload(decode=True) is None

    assert epr._decode_part_text(container) == ""


def test_empty_body_is_decoded_to_empty_string():
    """Пустое тело в нормальной text/plain-части — тоже пустой текст."""
    part = email.message_from_string("Content-Type: text/plain; charset=utf-8\n\n")
    assert epr._decode_part_text(part) == ""


def test_unknown_charset_falls_back_to_windows_1251():
    """Несуществующий charset в заголовке: фолбэк-цепочка даёт осмысленный текст.

    Заголовок объявляет ``x-unknown-charset-42``: ``bytes.decode`` на нём
    падает LookupError, и функция обязана пройти цепочку
    windows-1251 -> koi8-r -> utf-8 (строки 203-210), а не вернуть пустую
    строку и не уронить импорт.
    """
    message = EmailMessage()
    message["Subject"] = "Заявка"
    message.set_content(RUSSIAN_BODY, charset="windows-1251")
    # Объявляем несуществующую кодировку — байты при этом остаются cp1251.
    message.set_param("charset", "x-unknown-charset-42")
    assert message.get_content_charset() == "x-unknown-charset-42"

    decoded = epr._decode_part_text(message)

    assert decoded.strip(), "битый charset не должен превращать письмо в пустоту"
    assert "люстра" in decoded, decoded
    assert decoded.strip() == RUSSIAN_BODY.strip()


@pytest.mark.parametrize("charset", ["windows-1251", "koi8-r", "utf-8"])
def test_letter_is_decoded_with_its_own_charset(charset):
    """Письмо декодируется своей кодировкой — посимвольно, без потерь.

    Главный антирегресс: раньше байты всегда читались как UTF-8 с
    ``errors="ignore"``, и письма госпочты (windows-1251, koi8-r) приходили
    «замыленными» — с выброшенными русскими буквами. Здесь текст на входе и
    на выходе обязан совпасть посимвольно.
    """
    message = EmailMessage()
    message["Subject"] = "Заявка с сайта"
    message.set_content(RUSSIAN_BODY + "\n" + CYRILLIC_PROBE, charset=charset)
    assert message.get_content_charset() == charset

    decoded = epr._decode_part_text(message)

    # set_content() завершает тело переводом строки по RFC — его не считаем
    # потерей символов, всё остальное обязано совпасть посимвольно.
    assert decoded.rstrip("\n") == RUSSIAN_BODY + "\n" + CYRILLIC_PROBE, (
        f"письмо в {charset} декодировалось с потерями"
    )
    # Ни одного символа-заменителя: errors="replace" маскировал бы потерю.
    assert "�" not in decoded
    assert "люстра" in decoded and "Ёжик" in decoded


def test_mixed_case_charset_in_header_is_accepted():
    """Регистр charset в заголовке не важен: значение нормализуется в нижний."""
    message = EmailMessage()
    message.set_content(RUSSIAN_BODY, charset="windows-1251")
    message.set_param("charset", "Windows-1251")

    assert epr._decode_part_text(message).rstrip("\n") == RUSSIAN_BODY


# --- 2. _decode_attachment_filename: RFC 2047 / битые слова ---------------


def test_part_without_filename_gives_none():
    """Вложение без имени файла не должно давать имя из воздуха."""
    part = email.message_from_string(
        "Content-Type: application/octet-stream\r\n"
        "Content-Disposition: attachment\r\n\r\n"
    )
    assert part.get_filename() is None
    assert epr._decode_attachment_filename(part) is None


def test_outlook_encoded_word_filename_is_decoded():
    """Имя в encoded-word (нарушение стандарта от Outlook) становится текстом.

    ``get_filename()`` раскрывает RFC 2231, но не RFC 2047, поэтому без
    ручного декодирования в БД попадало ``=?UTF-8?B?...?=`` — с base64-мусором.
    """
    part = _part_with_disposition(
        'Content-Disposition: attachment; filename="%s"'
        % _b64("Счёт-фактура.pdf")
    )
    assert "=?" in part.get_filename(), "предусловие: имя сырое, не декодировано"

    assert epr._decode_attachment_filename(part) == "Счёт-фактура.pdf"


def test_encoded_word_filename_without_quote_is_decoded():
    """Тот же Outlook-случай, но значение без кавычек (RFC 2231-вид)."""
    part = _part_with_disposition(
        "Content-Disposition: attachment; filename=" + _b64("Договор.docx")
    )

    assert epr._decode_attachment_filename(part) == "Договор.docx"


def test_broken_encoded_word_does_not_break_import(caplog):
    """Битое encoded-word даёт warning и исходную строку, но не исключение.

    Отправитель не должен ронять импорт письма из-за нечитаемого имени
    вложения: значение возвращается как есть, след остаётся в логе.
    """
    broken = "=?UTF-8?B?0YTQs?="

    with caplog.at_level(logging.WARNING):
        result = _attachment_part(broken.encode("ascii"))

    assert result == broken
    assert "Failed to decode attachment filename" in caplog.text


def test_unknown_encoded_word_charset_does_not_break_import(caplog):
    """Encoded-word с несуществующим charset тоже не роняет функцию."""
    broken = "=?X-NOT-A-CHARSET?B?YWJj?="

    with caplog.at_level(logging.WARNING):
        result = _attachment_part(broken.encode("ascii"))

    assert result == broken
    assert "Failed to decode attachment filename" in caplog.text


def test_tab_inside_filename_collapses_to_one_space():
    """Табуляция внутри значения схлопывается в один пробел (строка 231).

    Такое значение приходит от клиентов, которые пишут в Content-Disposition
    управляющие символы: в БД попадало имя с ``\\t`` посередине, и заголовок
    при скачивании рассыпался.
    """
    part = _part_with_disposition(
        b'Content-Disposition: attachment; filename="=?UTF-8?B?0YTQs?=\tcopy.txt"'
    )

    result = epr._decode_attachment_filename(part)

    assert "\t" not in result
    assert result == "=?UTF-8?B?0YTQs?= copy.txt", result


def test_folded_filename_keeps_no_control_characters():
    """Свёрнутое по RFC 5322 имя не оставляет в значении переносов строк.

    Продолжение строки — это ``CRLF`` + обязательный пробел, поэтому после
    схлопывания управляющих символов между частями остаётся ДВА пробела.
    Тест фиксирует именно наблюдаемое поведение; в отчёте к тесту это отмечено
    как мелкое расхождение с докстрингом функции.
    """
    part = _part_with_disposition(
        b'Content-Disposition: attachment; filename="=?UTF-8?B?0YTQs?=\r\n copy.txt"'
    )

    result = epr._decode_attachment_filename(part)

    assert "\n" not in result and "\r" not in result and "\t" not in result
    assert result == "=?UTF-8?B?0YTQs?=  copy.txt", result
    assert result.endswith("copy.txt")


def test_filename_that_is_only_whitespace_becomes_none():
    """Имя, из которого после обрезки не осталось ничего, — это None.

    Пустая строка попала бы в БД как имя файла и в карточку, и в
    Content-Disposition. Здесь срабатывает ``filename or None`` (строка 232).
    """
    part = _part_with_disposition(
        b'Content-Disposition: attachment; filename="' + _b64(" \t\r\n ").encode() + b'"'
    )

    assert epr._decode_attachment_filename(part) is None


def test_plain_filename_is_returned_unchanged():
    """Обычное имя без encoded-word проходит без изменений."""
    assert _attachment_part("photo.png".encode()) == "photo.png"


# --- 3. save_settings: пароль только в Fernet-форме -----------------------


def test_save_settings_stores_fernet_form_and_loads_back(
    monkeypatch, tmp_path
):
    """Round-trip пароля через настоящий файл настроек.

    На диске plaintext не остаётся ни в каком виде, а ``load_settings``
    возвращает исходный пароль после расшифровки. Это строка 254 —
    единственная точка, где новый пароль уходит в файл.
    """
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    password = "Пароль-Почты-42!@"

    epr.save_settings(
        {
            "email": "sale@svetvdome.by",
            "password": password,
            "imap_server": "imap.example.com",
            "target_status": "Новый запрос",
        }
    )

    settings_file = data_dir / "email_settings.json"
    raw = settings_file.read_text(encoding="utf-8")
    assert password not in raw, "пароль не должен лежать в файле открытым текстом"

    stored = json.loads(raw)
    assert stored["password"] != password
    assert stored["password"].startswith("gAAAA"), stored["password"]
    assert decrypt_credential(stored["password"], obtain_fernet()) == password
    assert settings_file.stat().st_mode & 0o777 == 0o600

    # Читающий путь возвращает то, что положил писатель.
    loaded = epr.load_settings()
    assert epr._get_smtp_password(loaded) == password
    assert loaded["email"] == "sale@svetvdome.by"


def test_save_settings_without_password_keeps_settings_readable(
    monkeypatch, tmp_path
):
    """Настройки без пароля пишутся как есть — ключ шифрования не нужен зря.

    Обновление адреса ящика/статуса не должно требовать перешифровывать
    секрет, которого в словаре нет.
    """
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    monkeypatch.delenv("CRM_EMAIL_SECRET_KEY", raising=False)

    epr.save_settings(
        {"email": "sale@svetvdome.by", "target_status": "В работе"}
    )

    stored = json.loads((data_dir / "email_settings.json").read_text(encoding="utf-8"))
    assert "password" not in stored
    assert stored["target_status"] == "В работе"


def test_preserve_encrypted_password_rejects_fake_fernet_prefix(
    monkeypatch, tmp_path
):
    """Значение, похожее на Fernet, но не зашифрованное, приводит к отказу.

    Префикс ``gAAAA`` НЕ является доказательством: значение обязано пройти
    реальную расшифровку. Иначе битый секрет тихо записался бы в файл и
    закрыл синк с сообщением об ошибке авторизации вместо внятной диагностики.
    """
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    fake = "gAAAA-это-не-настоящий-токен"

    with pytest.raises(InvalidCredentialCiphertextError):
        epr.save_settings(
            {"email": "sale@svetvdome.by", "password": fake},
            preserve_encrypted_password=True,
        )

    assert not (data_dir / "email_settings.json").exists(), (
        "отказ не должен оставлять файл настроек"
    )


def test_preserve_encrypted_password_rejects_legacy_plaintext(
    monkeypatch, tmp_path
):
    """Legacy-plaintext под видом секрета тоже отвергается, а не сохраняется."""
    data_dir = _data_dir_env(monkeypatch, tmp_path)

    with pytest.raises(LegacyPlaintextError):
        epr.save_settings(
            {"email": "sale@svetvdome.by", "password": "открытый-пароль"},
            preserve_encrypted_password=True,
        )

    assert not (data_dir / "email_settings.json").exists()


def test_preserve_encrypted_password_keeps_valid_token_untouched(
    monkeypatch, tmp_path
):
    """Настоящий токен проходит проверку и попадает в файл без изменений."""
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    token = encrypt_credential("mailbox-secret", obtain_fernet())

    epr.save_settings(
        {"email": "sale@svetvdome.by", "password": token, "last_sync": "2026-09-30"},
        preserve_encrypted_password=True,
    )

    stored = json.loads((data_dir / "email_settings.json").read_text(encoding="utf-8"))
    assert stored["password"] == token, "валидный токен не должен перешифровываться"
    assert stored["last_sync"] == "2026-09-30"


# --- 4. _ensure_inside_data_dir: настройки не пишутся наружу ---------------


def test_absolute_path_outside_data_dir_is_rejected(monkeypatch, tmp_path):
    """Абсолютный путь вне CRM_DATA_DIR запрещён."""
    _data_dir_env(monkeypatch, tmp_path)
    outside = tmp_path / "elsewhere" / "email_settings.json"
    outside.parent.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="вне каталога данных"):
        epr._ensure_inside_data_dir(str(outside))


def test_dotdot_escape_is_rejected_after_realpath(monkeypatch, tmp_path):
    """``..``, который после realpath выходит наружу, тоже запрещён.

    Проверка идёт по РАЗРЕШЁННОМУ пути, поэтому ``<data>/../secret`` ловится
    даже когда строка начинается внутри каталога данных.
    """
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    sneaky = os.path.join(str(data_dir), os.pardir, "secret", "key.json")

    with pytest.raises(RuntimeError, match="вне каталога данных"):
        epr._ensure_inside_data_dir(sneaky)


def test_symlink_out_of_data_dir_is_rejected(monkeypatch, tmp_path):
    """Симлинк внутри каталога данных наружу не является безопасным путём."""
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    target = tmp_path / "outside_target.json"
    target.write_text("{}", encoding="utf-8")
    link = data_dir / "link.json"
    link.symlink_to(target)

    with pytest.raises(RuntimeError, match="вне каталога данных"):
        epr._ensure_inside_data_dir(str(link))


def test_path_inside_data_dir_is_allowed(monkeypatch, tmp_path):
    """Файл внутри каталога данных и сам каталог данных — разрешённые пути."""
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    nested = data_dir / "tenants" / "email_settings_7.json"
    nested.parent.mkdir(parents=True)

    assert epr._ensure_inside_data_dir(str(nested)) == os.path.realpath(nested)
    assert epr._ensure_inside_data_dir(str(data_dir)) == os.path.realpath(data_dir)


def test_sibling_directory_with_same_prefix_is_rejected(monkeypatch, tmp_path):
    """Каталог, начинающийся с имени каталога данных, — не «внутри».

    Наивная проверка ``startswith(root)`` без разделителя пропустила бы
    ``<data>_backup/email_settings.json`` — такой обход ищут при подмене
    каталога данных в окружении.
    """
    _data_dir_env(monkeypatch, tmp_path)
    sibling = tmp_path / "data_backup"
    sibling.mkdir()
    probe = sibling / "email_settings.json"

    with pytest.raises(RuntimeError, match="вне каталога данных"):
        epr._ensure_inside_data_dir(str(probe))


# --- 5. _get_settings_path: legacy-ветки при отсутствии CRM_DATA_DIR ------


def _pretend_legacy_file_exists(monkeypatch, app_dir, filename):
    """Считать, что рядом с кодом лежит legacy-файл настроек.

    Писать в каталог приложения тесты не должны, поэтому «существование»
    имитируется подменой ``os.path.exists`` — ровно так, как это делает
    test_email_settings_path.py.
    """
    legacy = os.path.join(app_dir, filename)
    real_exists = os.path.exists

    def _exists(path):
        if str(path) == legacy:
            return True
        return real_exists(path)

    monkeypatch.setattr(epr.os.path, "exists", _exists)
    return legacy


def test_legacy_main_settings_file_is_used_without_data_dir(
    monkeypatch, tmp_path
):
    """Без CRM_DATA_DIR работающий сервер сохраняет свой файл рядом с кодом.

    Голый metal без каталога данных не должен внезапно терять настроенный
    ящик только из-за того, что путь пересчитался.
    """
    monkeypatch.delenv("CRM_DATA_DIR", raising=False)
    data_dir = tmp_path / "data"
    monkeypatch.setattr("database.DATA_DIR", str(data_dir))
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(epr.__file__)))
    legacy = _pretend_legacy_file_exists(monkeypatch, app_dir, "email_settings.json")

    assert epr._get_settings_path(None) == legacy


def test_legacy_tenant_settings_file_is_used_without_data_dir(
    monkeypatch, tmp_path
):
    """То же для файла tenant'а: tenants/ рядом с кодом, а не в data dir."""
    monkeypatch.delenv("CRM_DATA_DIR", raising=False)
    data_dir = tmp_path / "data"
    monkeypatch.setattr("database.DATA_DIR", str(data_dir))
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(epr.__file__)))
    legacy = _pretend_legacy_file_exists(
        monkeypatch, app_dir, os.path.join("tenants", "email_settings_7.json")
    )

    assert epr._get_settings_path(7) == legacy


@pytest.mark.parametrize("tenant_id", [None, 7])
def test_without_legacy_file_settings_go_to_data_dir(
    monkeypatch, tmp_path, tenant_id
):
    """Нет CRM_DATA_DIR и нет legacy-файла — пишем в каталог данных.

    Ветка «каталог данных» тут берётся из ``database.DATA_DIR`` (строка 110
    или 122), поэтому проверяем и сам путь, и то, что каталог создан.
    """
    monkeypatch.delenv("CRM_DATA_DIR", raising=False)
    data_dir = tmp_path / "data"
    monkeypatch.setattr("database.DATA_DIR", str(data_dir))
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(epr.__file__)))
    real_exists = os.path.exists

    monkeypatch.setattr(
        epr.os.path,
        "exists",
        lambda path: False if str(path).startswith(app_dir) else real_exists(path),
    )

    path = epr._get_settings_path(tenant_id)

    if tenant_id:
        assert path == str(data_dir / "tenants" / f"email_settings_{tenant_id}.json")
        assert (data_dir / "tenants").is_dir()
    else:
        assert path == str(data_dir / "email_settings.json")
        assert data_dir.is_dir()


@pytest.mark.parametrize("tenant_id", [None, 7])
def test_data_dir_always_wins_over_legacy_file(
    monkeypatch, tmp_path, tenant_id
):
    """При CRM_DATA_DIR legacy-файл рядом с кодом не перекрывает каталог данных.

    FIX 18.09: чтение шло в legacy-файл, а guard запрещал туда писать — синк
    импортировал письма и падал на сохранении last_sync. Здесь проверяется
    полный контракт, а не только возврат пути: реальная запись и чтение
    проходят по каталогу данных.
    """
    data_dir = _data_dir_env(monkeypatch, tmp_path)
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(epr.__file__)))
    stored_name = (
        "email_settings.json" if tenant_id is None
        else f"email_settings_{tenant_id}.json"
    )
    legacy_name = (
        stored_name if tenant_id is None
        else os.path.join("tenants", stored_name)
    )
    _pretend_legacy_file_exists(monkeypatch, app_dir, legacy_name)

    path = epr._get_settings_path(tenant_id)
    assert not path.startswith(app_dir + os.sep), path
    assert path.startswith(str(data_dir)), path

    epr.save_settings({"email": "sale@svetvdome.by", "target_status": "Новый запрос"},
                      tenant_id)
    loaded = epr.load_settings(tenant_id)
    assert loaded["email"] == "sale@svetvdome.by"

    subdir = data_dir / "tenants" if tenant_id else data_dir
    assert (subdir / stored_name).exists(), "запись обязана идти в каталог данных"


# --- 6. Санитайзер имени вложения и Message-ID ---------------------------


def test_leading_dots_are_stripped_from_attachment_name():
    """Ведущие точки убираются: ``.htaccess`` иначе стал бы скрытым файлом."""
    assert epr._sanitize_attachment_name(".htaccess") == "htaccess"
    # Точки убираются только ведущие: расширение и внутренние точки целы.
    assert epr._sanitize_attachment_name("...invoice.v2.pdf") == "invoice.v2.pdf"


def test_attachment_name_is_truncated_to_120_chars():
    """Слишком длинное имя обрезается до 120 символов (лимит заголовков/ФС)."""
    long_name = "a" * 200 + ".pdf"

    result = epr._sanitize_attachment_name(long_name)

    assert len(result) == 120, len(result)
    assert result == long_name[:120]


def test_attachment_name_path_traversal_and_control_chars_are_stripped():
    """Путь, слеши и управляющие символы не должны дожить до имени файла."""
    assert "/" not in epr._sanitize_attachment_name("../../etc/passwd.png")
    assert epr._sanitize_attachment_name("../../etc/passwd.png") == "passwd.png"
    assert "\n" not in epr._sanitize_attachment_name("отчёт\n\r\tвредный.pdf")


def test_empty_attachment_name_falls_back_to_placeholder():
    """Пустое имя после очистки заменяется заглушкой, а не пустой строкой."""
    assert epr._sanitize_attachment_name("") == "attachment"
    assert epr._sanitize_attachment_name(None) == "attachment"
    assert epr._sanitize_attachment_name("...") == "attachment"


def test_message_id_is_canonicalized_to_local_at_domain():
    """Из ``<local@domain>`` остаётся только ``local@domain`` — без скобок."""
    assert epr._normalize_message_id("<letter-1@example.com>") == "letter-1@example.com"
    assert epr._normalize_message_id("  <a.b@mail.example.com>  ") == (
        "a.b@mail.example.com"
    )


def test_message_id_without_brackets_is_kept_as_is():
    """Письмо без скобок не выдумывает ключ и не отбрасывается целиком."""
    assert epr._normalize_message_id("bare-id@example.com") == "bare-id@example.com"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_message_id_is_none(raw):
    """Пустой Message-ID — это None: дедупликация не применяется."""
    assert epr._normalize_message_id(raw) is None


def test_overlong_message_id_is_rejected_at_255():
    """Message-ID длиннее 255 символов отбрасывается: он не влезает в колонку."""
    assert epr._normalize_message_id("m" * 255) == "m" * 255
    assert epr._normalize_message_id("m" * 256) is None
    assert epr._normalize_message_id("<" + "m" * 300 + "@example.com>") is None


def test_message_id_of_other_type_is_stringified():
    """Не-строковое значение заголовка не должно ронять дедупликацию."""
    assert epr._normalize_message_id(12345) == "12345"
