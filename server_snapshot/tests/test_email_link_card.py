"""Связка письма-дубля с существующей сделкой — POST /email-parser/link/{card_id}.

Регрессия на недостижимое тело обработчика (найдено при правке пункта 9, 23.09):
блок переноса текста лежал ВНУТРИ ветки `if not src or not dst:` после `raise`,
то есть выполнялся только тогда, когда карточки не найдены, — а значит никогда.
При успешном запросе функция возвращала None, FastAPI отдавал 200 с `null`, и ни
текст письма не переносился, ни ActivityLog не писался, ни дубль не помечался
удалённым. Оба клиента (v2 shell-v2-board.js `linkEmailToCard`, legacy
card_modal.js) показывали «успех», а в базе не менялось ничего — то есть
существование теста, который просто проверяет 200, эту ситуацию не ловит:
проверяем именно состояние строк.
"""
import pytest

import models

pytestmark = pytest.mark.integrity


@pytest.fixture
def letter_pair(db):
    """Карточка-письмо и карточка-сделка, с которой письмо сливают."""
    src = models.Card(title="Заявка: люстра 6 рожков",
                      description="Добрый день, нужна люстра в гостиную.",
                      status="Новый запрос", total_amount=0.0, paid_amount=0.0,
                      payment_status="Не оплачен", is_deleted=False,
                      sender_email="klient@example.com")
    dst = models.Card(title="Клиент Иван (повторная)",
                      description="Уже работаем.",
                      status="В работе", total_amount=1200.0, paid_amount=0.0,
                      payment_status="Не оплачен", is_deleted=False,
                      sender_email="klient@example.com")
    db.add_all([src, dst])
    db.commit()
    db.refresh(src)
    db.refresh(dst)
    return src, dst


def test_link_moves_letter_into_target_and_retires_duplicate(client, manager, db, letter_pair):
    """Успешная связка: текст перенесён, дубль в корзине, событие в ленте, ответ success."""
    _, h = manager
    src, dst = letter_pair

    r = client.post(f"/email-parser/link/{src.id}", headers=h,
                    json={"target_card_id": dst.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body is not None, "обработчик вернул null — тело запроса не исполнилось"
    assert body["success"] is True, body
    assert body["target_card_id"] == dst.id, body

    db.expire_all()
    fresh_dst = db.query(models.Card).filter(models.Card.id == dst.id).first()
    fresh_src = db.query(models.Card).filter(models.Card.id == src.id).first()

    # Метка + заголовок + тело письма дописаны ПОСЕРФ исходного описания,
    # а не заменили его.
    assert "--- Письмо от" in fresh_dst.description, fresh_dst.description
    assert "Заявка: люстра 6 рожков" in fresh_dst.description
    assert "нужна люстра в гостиную" in fresh_dst.description
    assert fresh_dst.description.startswith("Уже работаем."), "исходное описание затёрто"

    # Дубль уходит в корзину (is_deleted), а не стирается: карточка остаётся.
    assert bool(fresh_src.is_deleted) is True
    assert fresh_src.id == src.id

    entry = db.query(models.ActivityLog).filter(
        models.ActivityLog.card_id == dst.id,
        models.ActivityLog.action == "Связано письмо",
    ).first()
    assert entry is not None, "запись в ленте активности не создана"
    assert f"#{src.id}" in (entry.details or "")


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_link_works_for_any_authenticated_role(client, make_user, db, letter_pair, role):
    """Связка — операционное действие карточки, ролевой гейт на ней не ставится."""
    _, h = make_user(role)
    src, dst = letter_pair
    r = client.post(f"/email-parser/link/{src.id}", headers=h,
                    json={"target_card_id": dst.id})
    assert r.status_code == 200 and r.json()["success"] is True, r.text


def test_link_unknown_target_returns_404_and_changes_nothing(client, manager, db, letter_pair):
    _, h = manager
    src, dst = letter_pair

    r = client.post(f"/email-parser/link/{src.id}", headers=h,
                    json={"target_card_id": 999_999})
    assert r.status_code == 404, r.text

    db.expire_all()
    assert not db.query(models.Card).filter(models.Card.id == src.id).first().is_deleted
    assert db.query(models.ActivityLog).count() == 0


def test_link_unknown_source_returns_404(client, manager, db, letter_pair):
    _, h = manager
    src, dst = letter_pair

    r = client.post("/email-parser/link/999999", headers=h,
                    json={"target_card_id": dst.id})
    assert r.status_code == 404, r.text

    db.expire_all()
    assert db.query(models.Card).filter(models.Card.id == dst.id).first().description == "Уже работаем."


def test_link_requires_authentication(client, letter_pair):
    """Без токена — 401/403, а не тихий 200."""
    src, dst = letter_pair
    code = client.post(f"/email-parser/link/{src.id}",
                       json={"target_card_id": dst.id}).status_code
    assert code in (401, 403), code
