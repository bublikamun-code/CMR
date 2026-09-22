"""P0-хотфикс 23.09 (часть B): отмена групповой накладной.

Спека — docs/audits/P0-HOTFIX-GROUP-ANNUL-SPEC-2026-09-23.md, дефекты 1 и 2
реестра V2-WORKPLAN-2026-09-22 («Дефекты доски, найденные пунктом 6»):

  1. клиент отменял групповую ТН через DELETE /payments/transactions/{id группы}
     и сносил ПОСТОРОННЮЮ запись реестра, чей номер совпал с id группы.
     Лечение — invoice_transaction_id в ответе issue-invoice (настоящий id
     документа группы) и отдельный серверный роут отмены;
  2. отмена по документу группы (card_id=NULL) не разматывала состояние:
     written_off группы оставался, карточки — в «Закрыто», остатки не
     возвращались, а UI рапортовал «остатки возвращены».
     Лечение — POST /writeoffs/groups/{id}/annul (services.payments.
     annul_group_writeoff), зеркало одиночной отмены delete_transaction.

Гейт роли — тот же, что у одиночной отмены (V11, коммит 5828f2d):
manager / warehouse / documents → 403, admin / superadmin → 200.

Стиль и фикстуры — tests/test_role_gates_v11.py.
"""
import pytest

import models

NON_ADMIN_ROLES = ["manager", "warehouse", "documents"]
ADMIN_ROLES = ["admin", "superadmin"]

STORE = "Богдановича"
GROUP_TN = "ТТН9100"


def _reload(db):
    db.expire_all()


def _headers(make_user, role):
    _, h = make_user(role)
    return h


def _rows(db, card_id):
    """Не-документные записи реестра карточки (остатки и одиночные накладные)."""
    return db.query(models.Transaction).filter(
        models.Transaction.card_id == card_id,
        models.Transaction.is_document == False,  # noqa: E712
    ).order_by(models.Transaction.id.asc()).all()


def _group_doc(db, group_id):
    return db.query(models.Transaction).filter(
        models.Transaction.writeoff_group_id == group_id,
        models.Transaction.is_document == True,  # noqa: E712
    ).first()


def _mk_cards(client, db, make_card, headers, totals, with_registry=True):
    """Карточки одной группы (клиент+магазин совпадают) с записями реестра."""
    cards = []
    for i, total in enumerate(totals):
        c = make_card(title=f"Сделка {i}", total_amount=total, status="Сборка",
                      store_location=STORE)
        if with_registry:
            r = client.post(f"/payments/trigger_from_card/{c.id}", headers=headers,
                            json={"store_location": STORE})
            assert r.status_code == 200, r.text
        cards.append(c)
    return cards


def _mk_issued_group(client, db, make_card, headers, totals=(100.0, 200.0),
                     with_registry=True):
    """Группа + выписанная групповая накладная. Возвращает (group_id, cards, body)."""
    cards = _mk_cards(client, db, make_card, headers, totals, with_registry)
    r = client.post("/writeoffs/groups/", headers=headers,
                    json={"card_ids": [c.id for c in cards], "name": "Группа"})
    assert r.status_code == 200, r.text
    group_id = r.json()["id"]
    inv = client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=headers,
                      json={"invoice_number": GROUP_TN, "invoice_date": "2026-09-20"})
    assert inv.status_code == 200, inv.text
    return group_id, cards, inv.json()


# ---------------------------------------------------------------------------
# 1. invoice_transaction_id — настоящий id документа группы (дефект 1)
# ---------------------------------------------------------------------------

def test_issue_invoice_returns_group_document_id(client, manager, db, make_card):
    """Ответ issue-invoice несёт id записи-документа группы, а не id группы.

    Числовые id группы и документа в свежей базе МОГУТ совпасть (rowid
    переиспользуется после DELETE), поэтому сверяем не «≠ id группы»,
    а что по этому id лежит именно документ группы.
    """
    _, h = manager
    group_id, cards, body = _mk_issued_group(client, db, make_card, h)

    doc_id = body.get("invoice_transaction_id")
    assert doc_id is not None, f"нет invoice_transaction_id в ответе: {body}"

    _reload(db)
    doc = db.query(models.Transaction).filter(models.Transaction.id == doc_id).first()
    assert doc is not None, "по invoice_transaction_id нет записи"
    assert doc.is_document is True, "это обязан быть документ, а не строка реестра"
    assert doc.writeoff_group_id == group_id
    assert doc.card_id is None, "документ группы не привязан к карточке"
    assert (doc.invoice_number or "").strip() == GROUP_TN


def test_invoice_transaction_id_is_adhoc_and_absent_elsewhere(client, manager, db, make_card):
    """Поле есть в схеме группы, но заполняется только в ответе выписки.

    Колонки у writeoff_groups нет (часть B, п.1-2 спеки): в списке и в чтении
    группы поле присутствует и равно None — клиент не спутает его с id группы.
    """
    _, h = manager
    group_id, cards, body = _mk_issued_group(client, db, make_card, h)

    one = client.get(f"/writeoffs/groups/{group_id}", headers=h)
    assert one.status_code == 200, one.text
    assert "invoice_transaction_id" in one.json()
    assert one.json()["invoice_transaction_id"] is None

    lst = client.get("/writeoffs/groups/", headers=h)
    assert lst.status_code == 200, lst.text
    row = next(g for g in lst.json() if g["id"] == group_id)
    assert row["invoice_transaction_id"] is None


# ---------------------------------------------------------------------------
# 2. Матрица доступа: групповая отмена не шире одиночной
# ---------------------------------------------------------------------------

@pytest.mark.access
@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_annul_denied_for_non_admin(client, make_user, manager, db, make_card, role):
    """manager / warehouse / documents групповую ТН не отменяют (V11)."""
    _, mh = manager
    group_id, cards, _ = _mk_issued_group(client, db, make_card, mh)

    h = _headers(make_user, role)
    r = client.post(f"/writeoffs/groups/{group_id}/annul", headers=h)
    assert r.status_code == 403, f"роль {role}: {r.status_code} {r.text}"

    _reload(db)
    g = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first()
    assert g.written_off is True, "после 403 группа обязана остаться закрытой"
    assert _group_doc(db, group_id) is not None, "после 403 документ группы жив"
    for c in cards:
        db.refresh(c)
        assert c.status == "Закрыто", f"после 403 карточка {c.id} не должна выйти из «Закрыто»"


@pytest.mark.access
@pytest.mark.parametrize("role", ADMIN_ROLES)
def test_annul_allowed_for_admin_roles(client, make_user, manager, db, make_card, role):
    _, mh = manager
    group_id, cards, _ = _mk_issued_group(client, db, make_card, mh)

    h = _headers(make_user, role)
    r = client.post(f"/writeoffs/groups/{group_id}/annul", headers=h)
    assert r.status_code == 200, f"роль {role}: {r.text}"
    assert r.json()["written_off"] is False


@pytest.mark.access
def test_annul_requires_authentication(client, manager, db, make_card):
    """Без токена — 401/403, но не 200 и не 500."""
    _, mh = manager
    group_id, _, _ = _mk_issued_group(client, db, make_card, mh)
    assert client.post(f"/writeoffs/groups/{group_id}/annul").status_code in (401, 403)


@pytest.mark.access
def test_annul_unknown_group_is_404(client, make_user):
    h = _headers(make_user, "admin")
    assert client.post("/writeoffs/groups/999999/annul", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# 3. Unwind: что именно разматывается
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_annul_unwinds_group_and_cards(client, make_user, manager, db, make_card,
                                       foreign_key_violations):
    _, mh = manager
    group_id, cards, _ = _mk_issued_group(client, db, make_card, mh,
                                          totals=(100.0, 200.0))
    doc = _group_doc(db, group_id)
    assert doc is not None, "предусловие: документ группы выписан"
    doc_id = doc.id

    h = _headers(make_user, "admin")
    r = client.post(f"/writeoffs/groups/{group_id}/annul", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["written_off"] is False
    assert body["invoice_number"] is None
    assert body["invoice_date"] is None

    _reload(db)
    g = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first()
    assert g is not None, "группа не удаляется — снимается только закрытие"
    assert g.written_off is False
    assert g.invoice_number is None
    assert g.invoice_date is None

    # документ группы удалён
    assert db.query(models.Transaction).filter(
        models.Transaction.id == doc_id).first() is None
    assert _group_doc(db, group_id) is None

    # карточки вышли из «Закрыто» в статус списания, остатки на месте
    for c, total in zip(cards, (100.0, 200.0)):
        db.refresh(c)
        assert c.status != "Закрыто", f"карточка {c.id} осталась в «Закрыто»"
        assert c.status == "На списание", f"карточка {c.id}: {c.status}"
        rows = _rows(db, c.id)
        assert len(rows) == 1, f"карточка {c.id}: записей реестра {len(rows)}, ждали одну"
        assert round(float(rows[0].amount or 0), 2) == round(total, 2)
        assert rows[0].is_warehouse_writeoff is False, "складское списание группы снято"
        assert rows[0].is_invoice_issued is False
        assert rows[0].is_written_off is False, "ручную галочку «Списание» не трогаем"
        assert c.writeoff_group_id == group_id, "карточка остаётся в группе"

    # журнал: по записи на карточку
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.action == "Групповая накладная отменена").all()
    assert {log.card_id for log in logs} == {c.id for c in cards}
    assert all(GROUP_TN in (log.details or "") for log in logs)

    assert foreign_key_violations() == []


@pytest.mark.money
def test_annul_recomputes_remainders_with_single_invoices(client, make_user, manager,
                                                          db, make_card):
    """Одиночная накладная карточки переживает отмену групповой.

    До группировки по карточке А была выписана ТН на 400 из 1000 (остаток 600).
    Групповая выписка подняла флаги на обеих строках; после отмены выписанное
    считается только по настоящим накладным: остаток А = 600, статус «На списание».
    """
    _, mh = manager
    a = make_card(title="A", total_amount=1000.0, status="Сборка", store_location=STORE)
    b = make_card(title="B", total_amount=200.0, status="Сборка", store_location=STORE)
    inv = client.post(f"/payments/cards/{a.id}/issue-invoice", headers=mh,
                      json={"invoice_number": "ТТН400", "amount": 400.0,
                            "invoice_date": "2026-09-18", "store_location": STORE})
    assert inv.status_code == 200, inv.text
    single_id = inv.json()["invoice_id"]

    r = client.post("/writeoffs/groups/", headers=mh,
                    json={"card_ids": [a.id, b.id], "name": "Группа"})
    assert r.status_code == 200, r.text
    group_id = r.json()["id"]
    assert client.post(f"/writeoffs/groups/{group_id}/issue-invoice", headers=mh,
                       json={"invoice_number": GROUP_TN}).status_code == 200

    h = _headers(make_user, "admin")
    assert client.post(f"/writeoffs/groups/{group_id}/annul",
                       headers=h).status_code == 200

    _reload(db)
    db.refresh(a)
    db.refresh(b)
    assert a.status == "На списание", a.status
    assert b.status == "На списание", b.status

    rows_a = _rows(db, a.id)
    assert len(rows_a) == 2, f"у А ждём накладную и остаток, получили {len(rows_a)}"
    single = next(t for t in rows_a if t.id == single_id)
    rest = next(t for t in rows_a if t.id != single_id)
    assert (single.invoice_number or "").strip() == "ТТН400"
    assert round(float(single.amount or 0), 2) == 400.0
    assert single.is_invoice_issued is True, "факт одиночной выписки сохраняется"
    assert single.is_warehouse_writeoff is False, "групповое списание снято"
    assert not (rest.invoice_number or "").strip()
    assert round(float(rest.amount or 0), 2) == 600.0, "остаток = сумма сделки − выписанное"

    rows_b = _rows(db, b.id)
    assert len(rows_b) == 1
    assert round(float(rows_b[0].amount or 0), 2) == 200.0


@pytest.mark.integrity
def test_annul_does_not_touch_foreign_transactions(client, make_user, manager,
                                                   db, make_card):
    """Суть дефекта 1: посторонние записи реестра отмена группы не касается."""
    _, mh = manager
    outsider = make_card(title="Посторонняя", total_amount=777.0, status="Сборка",
                         store_location=STORE)
    assert client.post(f"/payments/trigger_from_card/{outsider.id}", headers=mh,
                       json={"store_location": STORE}).status_code == 200
    group_id, cards, _ = _mk_issued_group(client, db, make_card, mh)

    _reload(db)
    before = db.query(models.Transaction).count()
    outsider_rows = [(t.id, round(float(t.amount or 0), 2)) for t in _rows(db, outsider.id)]

    h = _headers(make_user, "admin")
    assert client.post(f"/writeoffs/groups/{group_id}/annul",
                       headers=h).status_code == 200

    _reload(db)
    # удалён ровно один документ группы
    assert db.query(models.Transaction).count() == before - 1
    assert [(t.id, round(float(t.amount or 0), 2))
            for t in _rows(db, outsider.id)] == outsider_rows
    db.refresh(outsider)
    assert outsider.status == "Сборка"


# ---------------------------------------------------------------------------
# 4. Идемпотентность и устойчивость
# ---------------------------------------------------------------------------

@pytest.mark.integrity
def test_repeated_annul_is_400_not_500(client, make_user, manager, db, make_card):
    """Двойной клик / вторая вкладка: внятный 400, состояние не разматывается дважды."""
    _, mh = manager
    group_id, cards, _ = _mk_issued_group(client, db, make_card, mh)

    h = _headers(make_user, "admin")
    first = client.post(f"/writeoffs/groups/{group_id}/annul", headers=h)
    assert first.status_code == 200, first.text
    _reload(db)
    after_first = {c.id: [(t.id, round(float(t.amount or 0), 2))
                          for t in _rows(db, c.id)] for c in cards}

    second = client.post(f"/writeoffs/groups/{group_id}/annul", headers=h)
    assert second.status_code == 400, f"ждали 400, получили {second.status_code}: {second.text}"
    assert second.json()["detail"].strip(), "у 400 обязана быть причина"

    _reload(db)
    g = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first()
    assert g.written_off is False
    assert {c.id: [(t.id, round(float(t.amount or 0), 2))
                   for t in _rows(db, c.id)] for c in cards} == after_first, \
        "повторный annul не должен плодить записи-остатки"


@pytest.mark.access
def test_annul_of_never_issued_group_is_400(client, make_user, manager, db, make_card):
    """Группа без накладной: отменять нечего — 400, а не 500 и не тишина."""
    _, mh = manager
    cards = _mk_cards(client, db, make_card, mh, (100.0, 200.0))
    r = client.post("/writeoffs/groups/", headers=mh,
                    json={"card_ids": [c.id for c in cards], "name": "Группа"})
    assert r.status_code == 200, r.text
    group_id = r.json()["id"]

    h = _headers(make_user, "admin")
    resp = client.post(f"/writeoffs/groups/{group_id}/annul", headers=h)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"].strip()


@pytest.mark.integrity
def test_annul_unwinds_group_whose_document_was_already_deleted(client, make_user,
                                                               manager, db, make_card):
    """Последствие дефекта 2: документ группы уже снесли общим роутом.

    written_off при этом остался, карточки — в «Закрыто». annul обязан
    доразмотать состояние группы, а не упасть в 500 из-за отсутствующего
    документа.
    """
    _, mh = manager
    group_id, cards, body = _mk_issued_group(client, db, make_card, mh)
    doc_id = body["invoice_transaction_id"]

    admin_h = _headers(make_user, "admin")
    deleted = client.delete(f"/payments/transactions/{doc_id}", headers=admin_h)
    assert deleted.status_code == 200, deleted.text
    _reload(db)
    g = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first()
    assert g.written_off is True, "предусловие: одиночная отмена группу не разматывает"

    r = client.post(f"/writeoffs/groups/{group_id}/annul", headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json()["written_off"] is False

    _reload(db)
    g = db.query(models.WriteoffGroup).filter(
        models.WriteoffGroup.id == group_id).first()
    assert g.written_off is False
    assert g.invoice_number is None
    for c in cards:
        db.refresh(c)
        assert c.status == "На списание", f"карточка {c.id}: {c.status}"

    # повтор — снова 400, не 500
    again = client.post(f"/writeoffs/groups/{group_id}/annul", headers=admin_h)
    assert again.status_code == 400, again.text
