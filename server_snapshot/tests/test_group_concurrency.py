"""BA-10: конкурентные HTTP-операции групп списания на реальном SQLite DDL.

Каждый поток открывает собственный TestClient и получает собственную сессию
FastAPI. Проверки идут через production-DDL профиль, а не через упрощённые
прямые вызовы обработчиков.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

import database
import models
import routers.writeoff_groups_router as groups_router
from conftest import SCHEMA_PROFILE


pytestmark = pytest.mark.skipif(
    SCHEMA_PROFILE != "prod",
    reason="BA-10 требует профиль фактического production DDL",
)

STORE = "Матусевича 72"


def _request(method, path, headers, json=None, *, started=None):
    import main

    if started is not None:
        started.set()
    with TestClient(main.app, raise_server_exceptions=False) as client:
        return client.request(method, path, headers=headers, json=json)


def _concurrent(requests):
    """Два настоящих HTTP-запроса, стартующих одновременно."""
    barrier = threading.Barrier(len(requests))

    def worker(spec):
        barrier.wait(timeout=5)
        return _request(*spec)

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = [executor.submit(worker, spec) for spec in requests]
        return [future.result(timeout=20) for future in futures]


def _make_group(client, manager, make_card, totals=(100.0, 200.0)):
    _, headers = manager
    cards = []
    for index, total in enumerate(totals):
        card = make_card(
            title=f"Сделка {index}",
            total_amount=total,
            status="Сборка",
            store_location=STORE,
        )
        response = client.post(
            f"/payments/trigger_from_card/{card.id}",
            headers=headers,
            json={"store_location": STORE},
        )
        assert response.status_code == 200, response.text
        cards.append(card)

    response = client.post(
        "/writeoffs/groups/",
        headers=headers,
        json={"card_ids": [card.id for card in cards], "name": "Группа BA-10"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"], cards, headers


def _group_state(db, group_id, card_ids):
    db.expire_all()
    group = db.get(models.WriteoffGroup, group_id)
    cards = [db.get(models.Card, card_id) for card_id in card_ids]
    documents = db.query(models.Transaction).filter(
        models.Transaction.writeoff_group_id == group_id,
        models.Transaction.is_document == True,  # noqa: E712
    ).all()
    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.action.in_(["Групповая накладная", "Групповая накладная отменена"])
    ).all()
    return group, cards, documents, logs


@pytest.mark.parametrize(
    "first_number,second_number",
    [
        ("ТТН8200", "ТТН8200"),
        ("ТТН8201", "ТТН8202"),
    ],
    ids=["same-invoice-number", "different-invoice-number"],
)
def test_two_issue_requests_create_one_document(
        first_number, second_number, client, manager, db, make_card):
    group_id, cards, headers = _make_group(client, manager, make_card)

    responses = _concurrent([
        ("POST", f"/writeoffs/groups/{group_id}/issue-invoice", headers,
         {"invoice_number": first_number}),
        ("POST", f"/writeoffs/groups/{group_id}/issue-invoice", headers,
         {"invoice_number": second_number}),
    ])

    assert sorted(response.status_code for response in responses) == [200, 400]
    assert all(response.status_code < 500 for response in responses)
    winner = next(response for response in responses if response.status_code == 200)

    group, group_cards, documents, logs = _group_state(
        db, group_id, [card.id for card in cards]
    )
    assert group.written_off is True
    assert group.invoice_number == winner.json()["invoice_number"]
    assert group.total_amount == 300.0
    assert len(documents) == 1
    assert documents[0].invoice_number == winner.json()["invoice_number"]
    assert float(documents[0].amount) == 300.0
    assert len(logs) == len(cards)
    assert {log.card_id for log in logs} == {card.id for card in cards}
    assert all(log.action == "Групповая накладная" for log in logs)
    assert all(winner.json()["invoice_number"] in (log.details or "") for log in logs)
    assert all(card.status == "Закрыто" for card in group_cards)


@pytest.mark.parametrize("membership_action", ["add", "remove"])
def test_issue_wins_against_add_or_remove(
        membership_action, client, manager, db, make_card, monkeypatch):
    group_id, cards, headers = _make_group(client, manager, make_card)
    extra = None
    if membership_action == "add":
        extra = make_card(
            title="Дополнительная",
            total_amount=50.0,
            status="Сборка",
            store_location=STORE,
        )
        membership_path = f"/writeoffs/groups/{group_id}/cards/{extra.id}"
        membership_spec = ("POST", membership_path, headers, None)
        touched_card_id = extra.id
    else:
        membership_spec = (
            "DELETE", f"/writeoffs/groups/{group_id}/cards/{cards[0].id}", headers, None
        )
        touched_card_id = cards[0].id

    claim_done = threading.Event()
    release_issue = threading.Event()
    real_claim = groups_router._claim_group_issue

    def blocking_claim(session, candidate_group_id):
        claimed = real_claim(session, candidate_group_id)
        if claimed:
            claim_done.set()
            assert release_issue.wait(timeout=5)
        return claimed

    monkeypatch.setattr(groups_router, "_claim_group_issue", blocking_claim)
    with ThreadPoolExecutor(max_workers=2) as executor:
        issue_future = executor.submit(
            _request,
            "POST",
            f"/writeoffs/groups/{group_id}/issue-invoice",
            headers,
            json={"invoice_number": "ТТН8300"},
        )
        assert claim_done.wait(timeout=5)
        membership_future = executor.submit(_request, *membership_spec)
        release_issue.set()
        issue = issue_future.result(timeout=20)
        membership = membership_future.result(timeout=20)

    assert issue.status_code == 200, issue.text
    assert membership.status_code == 400, membership.text
    expected_card_ids = [card.id for card in cards]
    group, group_cards, documents, logs = _group_state(
        db, group_id, expected_card_ids
    )
    assert group.written_off is True
    assert float(group.total_amount) == 300.0
    assert len(group_cards) == 2
    assert all(card.status == "Закрыто" for card in group_cards)
    assert len(documents) == 1
    assert len(logs) == 2
    touched = db.get(models.Card, touched_card_id)
    expected_group = group_id if membership_action == "remove" else None
    assert touched.writeoff_group_id == expected_group


def test_issue_and_annul_are_serialized(
        client, make_user, manager, db, make_card, monkeypatch):
    group_id, cards, manager_headers = _make_group(client, manager, make_card)
    _, admin_headers = make_user("admin")

    annul_reserved = threading.Event()
    release_annul = threading.Event()
    real_reserve = groups_router._reserve_group_write
    calls = 0

    def blocking_first_reserve(session, candidate_group_id, **kwargs):
        nonlocal calls
        group = real_reserve(session, candidate_group_id, **kwargs)
        calls += 1
        if calls == 1:
            annul_reserved.set()
            assert release_annul.wait(timeout=5)
        return group

    monkeypatch.setattr(groups_router, "_reserve_group_write", blocking_first_reserve)
    with ThreadPoolExecutor(max_workers=2) as executor:
        annul_future = executor.submit(
            _request,
            "POST",
            f"/writeoffs/groups/{group_id}/annul",
            admin_headers,
        )
        assert annul_reserved.wait(timeout=5)
        issue_future = executor.submit(
            _request,
            "POST",
            f"/writeoffs/groups/{group_id}/issue-invoice",
            manager_headers,
            json={"invoice_number": "ТТН8400"},
        )
        release_annul.set()
        annul = annul_future.result(timeout=20)
        issue = issue_future.result(timeout=20)

    assert annul.status_code == 400, annul.text
    assert issue.status_code == 200, issue.text
    group, group_cards, documents, logs = _group_state(
        db, group_id, [card.id for card in cards]
    )
    assert group.written_off is True
    assert group.invoice_number == "ТТН8400"
    assert len(documents) == 1
    assert len(logs) == len(cards)
    assert all(log.action == "Групповая накладная" for log in logs)
    assert all(card.status == "Закрыто" for card in group_cards)


def test_issue_and_disband_are_serialized(
        client, make_user, manager, db, make_card, monkeypatch):
    group_id, cards, manager_headers = _make_group(client, manager, make_card)
    _, admin_headers = make_user("admin")

    disband_reserved = threading.Event()
    release_disband = threading.Event()
    real_reserve = groups_router._reserve_group_write
    calls = 0

    def blocking_first_reserve(session, candidate_group_id, **kwargs):
        nonlocal calls
        group = real_reserve(session, candidate_group_id, **kwargs)
        calls += 1
        if calls == 1:
            disband_reserved.set()
            assert release_disband.wait(timeout=5)
        return group

    monkeypatch.setattr(groups_router, "_reserve_group_write", blocking_first_reserve)
    with ThreadPoolExecutor(max_workers=2) as executor:
        disband_future = executor.submit(
            _request,
            "DELETE",
            f"/writeoffs/groups/{group_id}",
            admin_headers,
        )
        assert disband_reserved.wait(timeout=5)
        issue_future = executor.submit(
            _request,
            "POST",
            f"/writeoffs/groups/{group_id}/issue-invoice",
            manager_headers,
            json={"invoice_number": "ТТН8500"},
        )
        release_disband.set()
        disband = disband_future.result(timeout=20)
        issue = issue_future.result(timeout=20)

    assert disband.status_code == 200, disband.text
    assert issue.status_code == 404, issue.text
    db.expire_all()
    assert db.get(models.WriteoffGroup, group_id) is None
    assert all(db.get(models.Card, card.id).writeoff_group_id is None for card in cards)
    assert db.query(models.Transaction).filter(
        models.Transaction.writeoff_group_id == group_id
    ).count() == 0
    assert db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id.in_([card.id for card in cards])
    ).count() == 0


def test_two_add_operations_keep_both_memberships(
        client, manager, db, make_card):
    group_id, cards, headers = _make_group(client, manager, make_card)
    extras = [
        make_card(
            title=f"Добавленная {index}",
            total_amount=50.0 + index,
            status="Сборка",
            store_location=STORE,
        )
        for index in range(2)
    ]

    responses = _concurrent([
        ("POST", f"/writeoffs/groups/{group_id}/cards/{extras[0].id}", headers, None),
        ("POST", f"/writeoffs/groups/{group_id}/cards/{extras[1].id}", headers, None),
    ])
    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.status_code < 500 for response in responses)

    db.expire_all()
    group = db.get(models.WriteoffGroup, group_id)
    assert group is not None
    assert float(group.total_amount) == 401.0
    assert all(db.get(models.Card, card.id).writeoff_group_id == group_id for card in extras)
    assert db.query(models.Card).filter(
        models.Card.id.in_([card.id for card in extras])
    ).count() == 2
    assert group.cards
    assert len({card.id for card in group.cards}) == 4


def test_two_add_one_card_operations_assign_the_card_once(
        client, manager, db, make_card):
    group_a, _, headers = _make_group(client, manager, make_card)
    group_b, _, _ = _make_group(client, manager, make_card)
    contested = make_card(
        title="Общая карточка",
        total_amount=50.0,
        status="Сборка",
        store_location=STORE,
    )

    responses = _concurrent([
        ("POST", f"/writeoffs/groups/{group_a}/cards/{contested.id}", headers, None),
        ("POST", f"/writeoffs/groups/{group_b}/cards/{contested.id}", headers, None),
    ])
    assert sorted(response.status_code for response in responses) == [200, 400]
    assert all(response.status_code < 500 for response in responses)

    db.expire_all()
    assigned_group = db.get(models.Card, contested.id).writeoff_group_id
    assert assigned_group in (group_a, group_b)
    winner = db.get(models.WriteoffGroup, assigned_group)
    loser = db.get(
        models.WriteoffGroup, group_b if assigned_group == group_a else group_a
    )
    assert float(winner.total_amount) == 350.0
    assert float(loser.total_amount) == 300.0
    assert winner.cards
    assert len({card.id for card in winner.cards}) == 3
    assert len({card.id for card in loser.cards}) == 2


def test_real_busy_timeout_is_503_with_retry_after(manager, db, make_card):
    group_id, cards, headers = _make_group_from_standalone_manager(
        manager, make_card
    )

    def set_short_busy_timeout(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=50")
        cursor.close()

    event.listen(database.engine, "connect", set_short_busy_timeout)
    database.engine.dispose()
    locker = database.engine.raw_connection()
    started = threading.Event()
    try:
        locker.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"/writeoffs/groups/{group_id}/issue-invoice",
                headers,
                json={"invoice_number": "ТТН8600"},
                started=started,
            )
            assert started.wait(timeout=5)
            response = future.result(timeout=20)
    finally:
        locker.rollback()
        locker.close()
        event.remove(database.engine, "connect", set_short_busy_timeout)
        database.engine.dispose()

    assert response.status_code == 503, response.text
    assert response.headers.get("Retry-After") == "1"
    group, group_cards, documents, logs = _group_state(
        db, group_id, [card.id for card in cards]
    )
    assert group.written_off is False
    assert group.invoice_number is None
    assert all(card.status == "Сборка" for card in group_cards)
    assert documents == []
    assert logs == []


def _make_group_from_standalone_manager(manager, make_card):
    """Готовит группу HTTP-клиентом без долгоживущего общего TestClient."""
    import main

    _, headers = manager
    with TestClient(main.app) as client:

        return _make_group(client, manager, make_card)[:2] + (headers,)
