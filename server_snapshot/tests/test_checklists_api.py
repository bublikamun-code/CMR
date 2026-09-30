"""Чек-листы сделки и вложения: контракт API (routers/card_details_router.py).

До этого файла функционал почти не был покрыт: маршруты
POST /cards/{id}/checklists, PATCH|DELETE /checklists/{id},
POST|DELETE /checklists/{id}/invoice и DELETE /attachments/{id} показывались
в отчёте tests/route_coverage.py как MISS, а загрузчики вложений были
задеты ровно одним smoke-тестом (tests/test_package_b_money.py проверяет
только код ответа 200/400 у /files/attach/{id}, без лимитов, magic bytes и
границ тенанта). Файлы счетов и вложений никто не удалял и никто не
проверял на выход за пределы каталога загрузок.

Что здесь закрыто:

* связка «поставщик из справочника → company_name» при создании и правке
  пункта чек-листа, включая отказ при пустом названии;
* загрузка счёта (multipart) и вложения: белый список расширений, проверка
  magic bytes, лимит размера, невозможность выйти из каталога загрузок
  именем файла, удаление предыдущей версии файла при повторной загрузке;
* удаление пункта чек-листа вместе с файлом счёта, удаление вложения
  вместе с файлом — обе операции проверяются и на диске, и в БД;
* скачивание: имя файла с кириллицей и спецсимволами не должно ломать
  заголовок Content-Disposition;
* границы тенанта на всех маршрутах группы (чужой объект неотличим от
  отсутствующего — 404).

Каталог загрузок во всех файловых тестах подменяется на tmp_path
(monkeypatch атрибута card_details_router.UPLOAD_DIR): проверки
_path_inside_uploads/_resolve_file_path читают именно этот атрибут, а
реальный боевой uploads тесты не должны затрагивать.
"""
from pathlib import Path
from urllib.parse import unquote

import pytest

import models
import routers.card_details_router as card_router
from conftest import DEFAULT_TEST_TENANT_ID

# Минимальные валидные содержимое: _validate_upload_content смотрит первые
# 16 байт, поэтому хватает заголовка формата.
PDF_BYTES = b"%PDF-1.4\n% schet\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ---------------------------------------------------------------------------
# Фикстуры данных
# ---------------------------------------------------------------------------

@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """Изолированный каталог загрузок вместо CRM_DATA_DIR/uploads."""
    directory = tmp_path / "uploads"
    directory.mkdir()
    monkeypatch.setattr(card_router, "UPLOAD_DIR", str(directory))
    return directory


@pytest.fixture
def make_supplier(db):
    def _factory(name="ООО «Ромашка»", tenant_id=DEFAULT_TEST_TENANT_ID):
        supplier = models.Supplier(name=name, tenant_id=tenant_id)
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        return supplier
    return _factory


@pytest.fixture
def make_checklist(db, make_card):
    def _factory(card=None, **kw):
        card = card if card is not None else make_card()
        defaults = {"company_name": "Поставщик", "amount": 0.0}
        defaults.update(kw)
        item = models.CardChecklist(card_id=card.id, **defaults)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    return _factory


@pytest.fixture
def make_attachment(db, make_card):
    def _factory(card=None, **kw):
        card = card if card is not None else make_card()
        defaults = {"file_name": "файл.pdf", "file_path": "/uploads/файл.pdf"}
        defaults.update(kw)
        attachment = models.CardAttachment(card_id=card.id, **defaults)
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        return attachment
    return _factory


def _pdf(name="schet.pdf", content=PDF_BYTES):
    """Multipart-параметр для клиента TestClient."""
    return {"file": (name, content, "application/pdf")}


def _upload(client, url, headers, name, content, content_type="application/pdf"):
    return client.post(url, headers=headers,
                       files={"file": (name, content, content_type)})


def _stored(uploads_dir, name, content=b"x"):
    """Создать файл внутри каталога загрузок и вернуть путь строкой."""
    path = uploads_dir / name
    path.write_bytes(content)
    return str(path)


# ---------------------------------------------------------------------------
# 1. POST /cards/{card_id}/checklists
# ---------------------------------------------------------------------------

def test_create_checklist_takes_company_name_from_supplier(
        client, manager, db, make_card, make_supplier):
    """supplier_id подтягивает название из справочника поставщиков."""
    _, headers = manager
    card = make_card()
    supplier = make_supplier("ООО «Ромашка»")

    response = client.post(f"/cards/{card.id}/checklists", headers=headers, json={
        "supplier_id": supplier.id,
        "amount": 120.5,
        "note": "первая партия",
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["company_name"] == "ООО «Ромашка»"
    assert payload["supplier_id"] == supplier.id
    assert payload["amount"] == 120.5
    assert payload["card_id"] == card.id
    assert payload["note"] == "первая партия"
    # Значения по умолчанию не должны молча выключаться при создании.
    assert payload["is_paid"] is False
    assert payload["ordered"] is False
    assert payload["received"] is False
    assert payload["supplier"]["name"] == "ООО «Ромашка»"
    assert payload["invoice_file_path"] is None

    db.expire_all()
    stored = db.get(models.CardChecklist, payload["id"])
    assert stored.company_name == "ООО «Ромашка»"
    assert stored.card_id == card.id


def test_create_checklist_keeps_manual_company_name_without_supplier(
        client, manager, make_card):
    """Старые записи без supplier_id заводятся вручную — это разрешено."""
    _, headers = manager
    card = make_card()

    response = client.post(f"/cards/{card.id}/checklists", headers=headers,
                           json={"company_name": "Частный мастер", "amount": 10.0})

    assert response.status_code == 200, response.text
    assert response.json()["company_name"] == "Частный мастер"
    assert response.json()["supplier_id"] is None


@pytest.mark.parametrize("company_name", [None, "", "   "])
def test_create_checklist_without_company_name_is_400(
        client, manager, db, make_card, company_name):
    """Ни поставщика, ни названия — создать пункт нечем."""
    _, headers = manager
    card = make_card()

    response = client.post(f"/cards/{card.id}/checklists", headers=headers,
                           json={"company_name": company_name, "amount": 0})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Укажите поставщика"
    db.expire_all()
    assert db.query(models.CardChecklist).count() == 0


def test_create_checklist_unknown_supplier_is_404(client, manager, db, make_card):
    """Несуществующий поставщик — 404, а не запись с битым supplier_id."""
    _, headers = manager
    card = make_card()

    response = client.post(f"/cards/{card.id}/checklists", headers=headers,
                           json={"supplier_id": 999999, "amount": 0})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Поставщик не найден"
    db.expire_all()
    assert db.query(models.CardChecklist).count() == 0


@pytest.mark.access
def test_create_checklist_on_foreign_tenant_card_is_404(
        client, two_tenant_users, db, make_card):
    """Сделка чужого tenant не видна: 404 и никакой новой записи."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])

    response = client.post(f"/cards/{card.id}/checklists",
                           headers=two_tenant_users["first_headers"],
                           json={"company_name": "Чужой поставщик", "amount": 5})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    db.expire_all()
    assert db.query(models.CardChecklist).count() == 0


@pytest.mark.access
def test_create_checklist_with_supplier_of_other_tenant_is_404(
        client, two_tenant_users, db, make_card, make_supplier):
    """Справочник поставщиков tenant-scoped: чужой поставщик — 404."""
    card = make_card()
    foreign_supplier = make_supplier("Чужой поставщик",
                                     tenant_id=two_tenant_users["second_tenant_id"])

    response = client.post(f"/cards/{card.id}/checklists",
                           headers=two_tenant_users["first_headers"],
                           json={"supplier_id": foreign_supplier.id, "amount": 5})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Поставщик не найден"
    db.expire_all()
    assert db.query(models.CardChecklist).count() == 0


# ---------------------------------------------------------------------------
# 2. PATCH /checklists/{checklist_id}
# ---------------------------------------------------------------------------

def test_update_checklist_supplier_refreshes_company_name(
        client, manager, db, make_checklist, make_supplier):
    """Смена поставщика переписывает снимок названия."""
    _, headers = manager
    item = make_checklist(company_name="ООО «Ромашка»")
    new_supplier = make_supplier("ЗАО «Восход»")

    response = client.patch(f"/checklists/{item.id}", headers=headers,
                            json={"supplier_id": new_supplier.id})

    assert response.status_code == 200, response.text
    assert response.json()["company_name"] == "ЗАО «Восход»"
    assert response.json()["supplier_id"] == new_supplier.id

    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert stored.company_name == "ЗАО «Восход»"
    assert stored.supplier_id == new_supplier.id


def test_update_checklist_is_partial_and_keeps_other_fields(
        client, manager, db, make_checklist, make_supplier):
    """Точечная правка не затирает остальные поля пункта."""
    _, headers = manager
    supplier = make_supplier("ООО «Ромашка»")
    item = make_checklist(company_name="ООО «Ромашка»", supplier_id=supplier.id,
                          amount=250.0, note="старое примечание",
                          is_paid=True, ordered=True, received=True,
                          is_secondary_check=True)

    response = client.patch(f"/checklists/{item.id}", headers=headers,
                            json={"is_paid": False})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["is_paid"] is False
    assert payload["amount"] == 250.0
    assert payload["note"] == "старое примечание"
    assert payload["company_name"] == "ООО «Ромашка»"
    assert payload["supplier_id"] == supplier.id
    assert payload["ordered"] is True
    assert payload["received"] is True
    assert payload["is_secondary_check"] is True

    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert float(stored.amount) == 250.0
    assert stored.note == "старое примечание"
    assert stored.is_paid is False


def test_update_checklist_unknown_supplier_is_404(
        client, manager, db, make_checklist):
    """Ссылка на несуществующего поставщика отклоняется до записи."""
    _, headers = manager
    item = make_checklist(company_name="ООО «Ромашка»")

    response = client.patch(f"/checklists/{item.id}", headers=headers,
                            json={"supplier_id": 999999})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Поставщик не найден"
    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert stored.company_name == "ООО «Ромашка»"
    assert stored.supplier_id is None


def test_update_checklist_unknown_id_is_404(client, manager):
    _, headers = manager

    response = client.patch("/checklists/999999", headers=headers,
                            json={"is_paid": True})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Пункт чек-листа не найден"


@pytest.mark.access
def test_update_checklist_of_foreign_tenant_is_404(
        client, two_tenant_users, db, make_card, make_checklist):
    """Чужой пункт не читается и не меняется."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    item = make_checklist(card=card, company_name="Чужой поставщик")

    response = client.patch(f"/checklists/{item.id}",
                            headers=two_tenant_users["first_headers"],
                            json={"company_name": "Взломан"})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    db.expire_all()
    assert db.get(models.CardChecklist, item.id).company_name == "Чужой поставщик"


# ---------------------------------------------------------------------------
# 3. DELETE /checklists/{checklist_id}
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_delete_checklist_item_removes_row_and_invoice_file(
        client, manager, db, make_checklist, uploads_dir):
    """Удаление пункта уносит с собой и файл счёта с диска."""
    _, headers = manager
    invoice = uploads_dir / "chk1_aaaaaaaa_schet.pdf"
    invoice.write_bytes(PDF_BYTES)
    item = make_checklist(invoice_file_name="schet.pdf",
                          invoice_file_path=str(invoice))
    item_id = item.id
    assert invoice.exists()

    response = client.delete(f"/checklists/{item_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert not invoice.exists()
    db.expire_all()
    assert db.query(models.CardChecklist).filter_by(id=item_id).count() == 0


def test_delete_checklist_item_unknown_id_is_404(client, manager):
    _, headers = manager

    response = client.delete("/checklists/999999", headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Пункт чек-листа не найден"


@pytest.mark.integrity
@pytest.mark.access
def test_delete_checklist_item_of_foreign_tenant_is_404(
        client, two_tenant_users, db, make_card, make_checklist, uploads_dir):
    """Чужой пункт не удаляется, и его файл счёта остаётся на месте."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    invoice = uploads_dir / "chk_foreign.pdf"
    invoice.write_bytes(PDF_BYTES)
    item = make_checklist(card=card, invoice_file_name="schet.pdf",
                          invoice_file_path=str(invoice))

    response = client.delete(f"/checklists/{item.id}",
                             headers=two_tenant_users["first_headers"])

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    assert invoice.exists()
    db.expire_all()
    assert db.get(models.CardChecklist, item.id) is not None


# ---------------------------------------------------------------------------
# 4. POST /checklists/{checklist_id}/invoice
# ---------------------------------------------------------------------------

def test_invoice_upload_saves_file_and_fills_checklist(
        client, manager, db, make_checklist, uploads_dir):
    """Успешная загрузка: файл на диске, поля пункта заполнены."""
    _, headers = manager
    item = make_checklist(company_name="ООО «Ромашка»")

    response = client.post(f"/checklists/{item.id}/invoice", headers=headers,
                           files=_pdf("schet-2026.pdf"))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["invoice_file_name"] == "schet-2026.pdf"
    stored_path = Path(payload["invoice_file_path"])
    assert stored_path.exists()
    assert stored_path.read_bytes() == PDF_BYTES
    # safe-имя служебное, каталог — строго uploads теста.
    assert stored_path.parent == uploads_dir
    assert stored_path.name.startswith(f"chk{item.id}_")

    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert stored.invoice_file_name == "schet-2026.pdf"
    assert stored.invoice_file_path == payload["invoice_file_path"]


def test_invoice_upload_rejects_disallowed_extension(
        client, manager, db, make_checklist, uploads_dir):
    """.exe не в белом списке ALLOWED_EXTS — 400, файл не создаётся."""
    _, headers = manager
    item = make_checklist()

    response = _upload(client, f"/checklists/{item.id}/invoice", headers,
                       "payload.exe", b"MZ\x90\x00", "application/octet-stream")

    assert response.status_code == 400, response.text
    assert ".exe" in response.json()["detail"]
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.get(models.CardChecklist, item.id).invoice_file_path is None


def test_invoice_upload_rejects_magic_bytes_mismatch(
        client, manager, db, make_checklist, uploads_dir):
    """PDF под именем .jpg — 400: тип проверяется по содержимому."""
    _, headers = manager
    item = make_checklist()

    response = _upload(client, f"/checklists/{item.id}/invoice", headers,
                       "photo.jpg", PDF_BYTES, "image/jpeg")

    assert response.status_code == 400, response.text
    assert "не соответствует" in response.json()["detail"]
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.get(models.CardChecklist, item.id).invoice_file_path is None


def test_invoice_upload_rejects_empty_file(
        client, manager, make_checklist, uploads_dir):
    """Пустой файл — 400 с внятным сообщением, а не «не соответствует типу»."""
    _, headers = manager
    item = make_checklist()

    response = _upload(client, f"/checklists/{item.id}/invoice", headers,
                       "empty.pdf", b"", "application/pdf")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Файл пустой"
    assert list(uploads_dir.iterdir()) == []


def test_invoice_upload_over_limit_is_413_and_leaves_no_garbage(
        client, manager, db, make_checklist, uploads_dir, monkeypatch):
    """Превышение MAX_UPLOAD_BYTES — 413, и частично записанный файл удалён.

    Лимит уменьшается monkeypatch'ем, чтобы не гонять 25 МБ через TestClient;
    проверяется ровно та же ветка кода с тем же условием size > MAX_UPLOAD_BYTES.
    """
    _, headers = manager
    monkeypatch.setattr(card_router, "MAX_UPLOAD_BYTES", 1024)
    item = make_checklist()
    payload = b"%PDF-1.4\n" + b"x" * 4096

    response = _upload(client, f"/checklists/{item.id}/invoice", headers,
                       "big.pdf", payload)

    assert response.status_code == 413, response.text
    assert "слишком большой" in response.json()["detail"]
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.get(models.CardChecklist, item.id).invoice_file_path is None


def test_invoice_upload_name_cannot_escape_uploads_dir(
        client, manager, make_checklist, uploads_dir, tmp_path):
    """Имя вида ../evil.pdf не выносит запись за пределы uploads."""
    _, headers = manager
    item = make_checklist()

    response = _upload(client, f"/checklists/{item.id}/invoice", headers,
                       "../evil.pdf", PDF_BYTES)

    assert response.status_code == 200, response.text
    stored_path = Path(response.json()["invoice_file_path"])
    assert stored_path.parent == uploads_dir
    assert stored_path.exists()
    assert Path(response.json()["invoice_file_path"]).is_relative_to(uploads_dir)
    # Исходное имя сохраняется в базе как есть, в имя файла попадает basename.
    assert response.json()["invoice_file_name"] == "evil.pdf"
    assert not (tmp_path / "evil.pdf").exists()
    assert list(tmp_path.iterdir()) == [uploads_dir]


def test_invoice_upload_replaces_previous_file(
        client, manager, db, make_checklist, uploads_dir):
    """Повторная загрузка удаляет прежний файл и меняет путь в пункте."""
    _, headers = manager
    item = make_checklist()

    first = client.post(f"/checklists/{item.id}/invoice", headers=headers,
                        files=_pdf("schet-1.pdf"))
    assert first.status_code == 200, first.text
    first_path = Path(first.json()["invoice_file_path"])
    assert first_path.exists()

    second = client.post(f"/checklists/{item.id}/invoice", headers=headers,
                         files=_pdf("schet-2.pdf"))
    assert second.status_code == 200, second.text
    second_path = Path(second.json()["invoice_file_path"])

    assert second_path != first_path
    assert not first_path.exists()
    assert second_path.exists()
    assert second_path.read_bytes() == PDF_BYTES
    assert [p.name for p in uploads_dir.iterdir()] == [second_path.name]

    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert stored.invoice_file_path == str(second_path)
    assert stored.invoice_file_name == "schet-2.pdf"


def test_invoice_upload_unknown_checklist_is_404(client, manager, uploads_dir):
    _, headers = manager

    response = client.post("/checklists/999999/invoice", headers=headers,
                           files=_pdf())

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Пункт чек-листа не найден"
    assert list(uploads_dir.iterdir()) == []


@pytest.mark.access
def test_invoice_upload_to_foreign_tenant_checklist_is_404(
        client, two_tenant_users, db, make_card, make_checklist, uploads_dir):
    """Счёт чужой сделки не загружается и на диск не попадает."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    item = make_checklist(card=card)

    response = client.post(f"/checklists/{item.id}/invoice",
                           headers=two_tenant_users["first_headers"],
                           files=_pdf())

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.get(models.CardChecklist, item.id).invoice_file_path is None


# ---------------------------------------------------------------------------
# 5. DELETE /checklists/{checklist_id}/invoice
# ---------------------------------------------------------------------------

def test_delete_invoice_clears_fields_and_removes_file(
        client, manager, db, make_checklist, uploads_dir):
    """Счёт отцепляется от пункта, файл уходит с диска."""
    _, headers = manager
    invoice = uploads_dir / "chk1_bbbbbbbb_schet.pdf"
    invoice.write_bytes(PDF_BYTES)
    item = make_checklist(invoice_file_name="schet.pdf",
                          invoice_file_path=str(invoice))

    response = client.delete(f"/checklists/{item.id}/invoice", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["invoice_file_name"] is None
    assert response.json()["invoice_file_path"] is None
    assert not invoice.exists()
    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert stored.invoice_file_name is None
    assert stored.invoice_file_path is None


def test_delete_invoice_without_file_is_noop(client, manager, db, make_checklist):
    """Пункт без счёта — повторный DELETE не ошибка, а успех."""
    _, headers = manager
    item = make_checklist()

    response = client.delete(f"/checklists/{item.id}/invoice", headers=headers)

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(models.CardChecklist, item.id) is not None


def test_delete_invoice_unknown_checklist_is_404(client, manager):
    _, headers = manager

    response = client.delete("/checklists/999999/invoice", headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Пункт чек-листа не найден"


@pytest.mark.access
def test_delete_invoice_of_foreign_tenant_is_404(
        client, two_tenant_users, db, make_card, make_checklist, uploads_dir):
    """Чужой счёт не отцепляется, файл остаётся."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    invoice = uploads_dir / "chk_foreign_invoice.pdf"
    invoice.write_bytes(PDF_BYTES)
    item = make_checklist(card=card, invoice_file_name="schet.pdf",
                          invoice_file_path=str(invoice))

    response = client.delete(f"/checklists/{item.id}/invoice",
                             headers=two_tenant_users["first_headers"])

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    assert invoice.exists()
    db.expire_all()
    stored = db.get(models.CardChecklist, item.id)
    assert stored.invoice_file_path == str(invoice)


# ---------------------------------------------------------------------------
# 6. GET /checklists/{checklist_id}/invoice/download
# ---------------------------------------------------------------------------

def test_download_invoice_returns_file_bytes(
        client, manager, make_checklist, uploads_dir):
    """Скачивание счёта отдаёт байты файла как есть."""
    _, headers = manager
    stored = _stored(uploads_dir, "chk1_cccccccc_schet.pdf", PDF_BYTES)
    item = make_checklist(invoice_file_name="schet.pdf",
                          invoice_file_path=stored)

    response = client.get(f"/checklists/{item.id}/invoice/download",
                          headers=headers)

    assert response.status_code == 200, response.text
    assert response.content == PDF_BYTES
    assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_download_invoice_name_with_cyrillic_keeps_header_valid(
        client, manager, make_checklist, uploads_dir):
    """Кириллица и спецсимволы в имени не ломают Content-Disposition.

    Проверяем ровно то, что обещает _content_disposition: заголовок
    целиком влезает в latin-1, ASCII-фолбэк присутствует и не содержит
    сырых кавычек, а CRLF из имени не попадают ни в заголовок, ни в
    отдельный заголовок ответа.
    """
    _, headers = manager
    stored = _stored(uploads_dir, "chk1_dddddddd_schet.pdf", PDF_BYTES)
    hostile_name = 'Счёт (договор) "Правда"\r\nX-Injected: 1.pdf'
    item = make_checklist(invoice_file_name=hostile_name,
                          invoice_file_path=stored)

    response = client.get(f"/checklists/{item.id}/invoice/download",
                          headers=headers)

    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")  # значение заголовка кодируется в latin-1
    assert disposition.startswith("attachment; ")
    assert "\r" not in disposition and "\n" not in disposition
    assert len(response.headers.get_list("content-disposition")) == 1

    quoted = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert quoted, disposition
    assert quoted.isascii(), quoted
    assert '"' not in quoted and "\r" not in quoted and "\n" not in quoted

    assert "filename*=UTF-8''" in disposition
    encoded = disposition.split("filename*=UTF-8''", 1)[1].split(";", 1)[0]
    decoded = unquote(encoded)
    assert "Счёт" in decoded and "Правда" in decoded, decoded
    assert '"' not in decoded and "\r" not in decoded and "\n" not in decoded
    assert "x-injected" not in {key.lower() for key in response.headers}
    assert response.content == PDF_BYTES


def test_download_invoice_missing_file_on_disk_is_404(
        client, manager, make_checklist, uploads_dir):
    """Файл счёта убран с диска вручную — 404, а не 500."""
    _, headers = manager
    item = make_checklist(invoice_file_name="schet.pdf",
                          invoice_file_path=str(uploads_dir / "нет-такого.pdf"))

    response = client.get(f"/checklists/{item.id}/invoice/download",
                          headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Файл счёта не найден на сервере"


def test_download_invoice_not_attached_is_404(client, manager, make_checklist):
    """Пункт без счёта: скачивать нечего — 404, не 500."""
    _, headers = manager
    item = make_checklist()

    response = client.get(f"/checklists/{item.id}/invoice/download",
                          headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Счёт не прикреплён"


@pytest.mark.access
def test_download_invoice_of_foreign_tenant_is_404(
        client, two_tenant_users, make_card, make_checklist, uploads_dir):
    """Счёт чужой сделки не отдаётся даже при прямой ссылке."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    stored = _stored(uploads_dir, "chk_foreign_invoice.pdf", "чужие данные".encode("utf-8"))
    item = make_checklist(card=card, invoice_file_name="schet.pdf",
                          invoice_file_path=stored)

    response = client.get(f"/checklists/{item.id}/invoice/download",
                          headers=two_tenant_users["first_headers"])

    assert response.status_code == 404, response.text
    assert "чужие данные" not in response.text


# ---------------------------------------------------------------------------
# 7. POST /cards/{card_id}/attachments и POST /files/attach/{card_id}
# ---------------------------------------------------------------------------

# v2-совместимый дубль объявлен как отдельный маршрут, поэтому контракт
# проверяется один раз на оба URL.
ATTACH_URLS = [
    "/cards/{card_id}/attachments",
    "/files/attach/{card_id}",
]


@pytest.mark.parametrize("url_template", ATTACH_URLS)
def test_attachment_upload_saves_file_and_row(
        client, manager, db, make_card, uploads_dir, url_template):
    """Успешная загрузка вложения: строка в card_attachments и файл на диске."""
    _, headers = manager
    card = make_card()

    response = _upload(client, url_template.format(card_id=card.id), headers,
                       "smeta.pdf", PDF_BYTES)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["file_name"] == "smeta.pdf"
    assert payload["card_id"] == card.id
    stored_path = Path(payload["file_path"])
    assert stored_path.exists()
    assert stored_path.read_bytes() == PDF_BYTES
    assert stored_path.parent == uploads_dir
    assert stored_path.name.startswith(f"{card.id}_")

    db.expire_all()
    stored = db.get(models.CardAttachment, payload["id"])
    assert stored.file_name == "smeta.pdf"
    assert stored.file_path == payload["file_path"]
    assert stored.uploaded_at is not None


@pytest.mark.parametrize("url_template", ATTACH_URLS)
def test_attachment_upload_rejects_disallowed_extension(
        client, manager, db, make_card, uploads_dir, url_template):
    """.exe запрещён белым списком ALLOWED_EXTENSIONS."""
    _, headers = manager
    card = make_card()

    response = _upload(client, url_template.format(card_id=card.id), headers,
                       "virus.exe", b"MZ\x90\x00", "application/octet-stream")

    assert response.status_code == 400, response.text
    assert ".exe" in response.json()["detail"]
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0


@pytest.mark.parametrize("url_template", ATTACH_URLS)
def test_attachment_upload_rejects_magic_bytes_mismatch(
        client, manager, db, make_card, uploads_dir, url_template):
    """PDF под именем .png не проходит проверку по первым байтам."""
    _, headers = manager
    card = make_card()

    response = _upload(client, url_template.format(card_id=card.id), headers,
                       "poddelka.png", PDF_BYTES, "image/png")

    assert response.status_code == 400, response.text
    assert "не соответствует" in response.json()["detail"]
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0


@pytest.mark.parametrize("url_template", ATTACH_URLS)
def test_attachment_upload_over_limit_is_413(
        client, manager, db, make_card, uploads_dir, monkeypatch, url_template):
    """Лимит размера на вложениях такой же, как на счетах: 413 без мусора."""
    _, headers = manager
    monkeypatch.setattr(card_router, "MAX_UPLOAD_BYTES", 1024)
    card = make_card()

    response = _upload(client, url_template.format(card_id=card.id), headers,
                       "big.pdf", b"%PDF-1.4\n" + b"x" * 4096)

    assert response.status_code == 413, response.text
    assert "слишком большой" in response.json()["detail"]
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0


@pytest.mark.parametrize("url_template", ATTACH_URLS)
def test_attachment_upload_accepts_png_with_matching_magic(
        client, manager, make_card, uploads_dir, url_template):
    """Контрольная сторона проверки: настоящий PNG принимается."""
    _, headers = manager
    card = make_card()

    response = _upload(client, url_template.format(card_id=card.id), headers,
                       "photo.png", PNG_BYTES, "image/png")

    assert response.status_code == 200, response.text
    stored_path = Path(response.json()["file_path"])
    assert stored_path.parent == uploads_dir
    assert stored_path.read_bytes() == PNG_BYTES


@pytest.mark.access
@pytest.mark.parametrize("url_template", ATTACH_URLS)
def test_attachment_upload_to_foreign_tenant_card_is_404(
        client, two_tenant_users, db, make_card, uploads_dir, url_template):
    """Вложение к сделке чужого tenant не загружается."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])

    response = _upload(client, url_template.format(card_id=card.id),
                       two_tenant_users["first_headers"],
                       "secret.pdf", PDF_BYTES)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    assert list(uploads_dir.iterdir()) == []
    db.expire_all()
    assert db.query(models.CardAttachment).count() == 0


def test_attachment_upload_unknown_card_is_404(client, manager, uploads_dir):
    _, headers = manager

    response = _upload(client, "/cards/999999/attachments", headers,
                       "smeta.pdf", PDF_BYTES)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    assert list(uploads_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 8. DELETE /attachments/{attachment_id}
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_delete_attachment_removes_row_and_file(
        client, manager, db, uploads_dir, make_attachment, foreign_key_violations):
    """Удаление вложения чистит и БД, и диск."""
    _, headers = manager
    stored = uploads_dir / "1_eeeeeeee_smeta.pdf"
    stored.write_bytes(PDF_BYTES)
    attachment = make_attachment(file_name="smeta.pdf", file_path=str(stored))
    attachment_id = attachment.id

    response = client.delete(f"/attachments/{attachment_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert not stored.exists()
    db.expire_all()
    assert db.get(models.CardAttachment, attachment_id) is None
    assert foreign_key_violations() == []


def test_delete_attachment_unknown_id_is_404(client, manager):
    _, headers = manager

    response = client.delete("/attachments/999999", headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Файл не найден"


@pytest.mark.access
@pytest.mark.integrity
def test_delete_attachment_of_foreign_tenant_is_404(
        client, two_tenant_users, db, make_card, uploads_dir, make_attachment):
    """Чужое вложение не удаляется ни из БД, ни с диска."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    stored = uploads_dir / "foreign_smeta.pdf"
    stored.write_bytes("чужие данные".encode("utf-8"))
    attachment = make_attachment(card=card, file_name="smeta.pdf",
                                 file_path=str(stored))
    attachment_id = attachment.id

    response = client.delete(f"/attachments/{attachment_id}",
                             headers=two_tenant_users["first_headers"])

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Карточка не найдена"
    assert stored.read_bytes() == "чужие данные".encode("utf-8")
    db.expire_all()
    assert db.get(models.CardAttachment, attachment_id) is not None


# ---------------------------------------------------------------------------
# 9. GET /attachments/{attachment_id}/download
# ---------------------------------------------------------------------------

def test_download_attachment_returns_file_bytes(
        client, manager, uploads_dir, make_attachment):
    """Скачивание вложения отдаёт байты файла."""
    _, headers = manager
    stored = uploads_dir / "1_ffffffff_smeta.pdf"
    stored.write_bytes(PDF_BYTES)
    attachment = make_attachment(file_name="smeta.pdf", file_path=str(stored))

    response = client.get(f"/attachments/{attachment.id}/download",
                          headers=headers)

    assert response.status_code == 200, response.text
    assert response.content == PDF_BYTES
    assert "filename*=UTF-8''smeta.pdf" in response.headers["content-disposition"]


def test_download_attachment_missing_file_on_disk_is_404(
        client, manager, uploads_dir, make_attachment):
    """Вложение есть в базе, файла на диске нет — 404, а не 500."""
    _, headers = manager
    attachment = make_attachment(
        file_name="smeta.pdf",
        file_path=str(uploads_dir / "удалённый_файл.pdf"),
    )

    response = client.get(f"/attachments/{attachment.id}/download",
                          headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Файл не найден на сервере"


@pytest.mark.access
def test_download_attachment_of_foreign_tenant_is_404(
        client, two_tenant_users, make_card, uploads_dir, make_attachment):
    """Вложение чужой сделки не отдаётся по прямой ссылке."""
    card = make_card(tenant_id=two_tenant_users["second_tenant_id"])
    stored = uploads_dir / "foreign_dogovor.pdf"
    stored.write_bytes("чужие данные".encode("utf-8"))
    attachment = make_attachment(card=card, file_name="dogovor.pdf",
                                 file_path=str(stored))

    response = client.get(f"/attachments/{attachment.id}/download",
                          headers=two_tenant_users["first_headers"])

    assert response.status_code == 404, response.text
    assert "чужие данные" not in response.text
