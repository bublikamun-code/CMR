"""Контракты панели уведомлений v2 (пункт 10 плана 22.09, аудит F9).

Колокольчик в v2 больше не помечает уведомления прочитанными при раскрытии:
раньше фронт на каждое открытие слал `POST /notifications/read {ids: []}`, и
лента фактически исчезала, не будучи прочитанной пользователем. Теперь
прочтение и удаление — только явные действия. Тесты фиксируют серверную
сторону этих контрактов, на которые опирается shell-v2-management.js:

1. GET /notifications — чтение без побочных эффектов (иначе критерий
   «открытие колокольчика не меняет статусы» не выполняется даже при
   правильном фронте);
2. POST /read с пустым ids помечает ВСЕ непрочитанные текущего пользователя
   и не трогает чужие — это кнопка «Прочитать все»;
3. POST /read с одним id помечает только его — это клик по уведомлению;
4. DELETE /notifications/{id} удаляет только своё уведомление.

Своё/чужое разделяется по user_id, а не по роли: лента адресная, и админ не
должен ни видеть, ни удалять чужие уведомления.
"""
import models


def _note(db, user_id, title="Просрочена сделка: Тест", *, type="card_overdue",
          entity_type="card", entity_id=1, is_read=False):
    """Уведомление напрямую в БД: поведение ленты, а не логика notify()."""
    note = models.Notification(user_id=user_id, type=type, title=title,
                               entity_type=entity_type, entity_id=entity_id,
                               is_read=is_read)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def _fresh(db):
    """API работает своей сессией — сбрасываем кеш identity map перед чтением."""
    db.expire_all()
    return db.query(models.Notification).order_by(models.Notification.id).all()


def test_list_notifications_does_not_mark_read(client, manager, db):
    """Раскрытие колокольчика ничего не помечает: GET идемпотентен.

    Двойной GET — намеренно: именно повторные открытия панели и стирали
    ленту, когда прочтение висело на загрузке списка.
    """
    user, headers = manager
    _note(db, user.id, "Первое")
    _note(db, user.id, "Второе", type="task_overdue", entity_type="task")

    for _ in range(2):
        r = client.get("/notifications", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["unread_count"] == 2, "открытие панели изменило счётчик непрочитанных"
        assert [i["is_read"] for i in body["items"]] == [False, False]

    assert [n.is_read for n in _fresh(db)] == [False, False], \
        "GET /notifications отмечает прочитанным — авто-прочтение вернулось"
    assert client.post("/notifications/read", json={}, headers=headers).status_code == 422, \
        "ids обязательны: пустое тело не должно молча помечать всё"


def test_read_all_marks_only_own_unread(client, manager, make_user, db):
    """«Прочитать все» = ids: [] — все свои непрочитанные, чужие не трогает."""
    user, headers = manager
    other, other_headers = make_user("manager", username="manager_other")
    _note(db, user.id, "Своё непрочитанное 1")
    _note(db, user.id, "Своё непрочитанное 2")
    _note(db, user.id, "Своё уже прочитанное", is_read=True)
    foreign_id = _note(db, other.id, "Чужое непрочитанное").id

    r = client.post("/notifications/read", json={"ids": []}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 2, "помечены должны быть ровно непрочитанные"

    notes = {n.id: n for n in _fresh(db)}
    assert foreign_id in notes and notes[foreign_id].is_read is False, \
        "«Прочитать все» задело чужое уведомление"
    assert client.get("/notifications", headers=headers).json()["unread_count"] == 0
    # Чужая лента не изменилась: счётчик смотрит по user_id.
    assert client.get("/notifications", headers=other_headers).json()["unread_count"] == 1


def test_read_single_id_marks_only_that_one(client, manager, db):
    """Клик по уведомлению помечает одно: ids: [id], а не всю ленту."""
    user, headers = manager
    first_id = _note(db, user.id, "Открытая").id
    second_id = _note(db, user.id, "Не открытая").id

    r = client.post("/notifications/read", json={"ids": [first_id]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 1

    notes = {n.id: n for n in _fresh(db)}
    assert notes[first_id].is_read is True and notes[first_id].read_at is not None
    assert notes[second_id].is_read is False, "прочтение одного пометило соседа"
    assert client.get("/notifications", headers=headers).json()["unread_count"] == 1


def test_delete_own_notification(client, manager, db):
    """Удаление одного уведомления (кнопка-корзина в строке панели)."""
    user, headers = manager
    note = _note(db, user.id, "Удаляемое")
    kept = _note(db, user.id, "Остаётся")
    # id снимаем до вызова API: _fresh() гасит кеш сессии, а обращение к
    # атрибуту удалённой строки закончилось бы ObjectDeletedError.
    note_id, kept_id = note.id, kept.id

    r = client.delete(f"/notifications/{note_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    left = {n.id for n in _fresh(db)}
    assert note_id not in left, "уведомление не удалено из БД"
    assert kept_id in left, "удаление задело соседнее уведомление"
    listed = {i["id"] for i in client.get("/notifications", headers=headers).json()["items"]}
    assert note_id not in listed and kept_id in listed


def test_delete_foreign_notification_is_404(client, manager, make_user, db):
    """Чужое уведомление не удаляется и не раскрывает своё существование."""
    user, _ = manager
    other, other_headers = make_user("manager", username="manager_victim")
    foreign_id = _note(db, user.id, "Чужое").id

    r = client.delete(f"/notifications/{foreign_id}", headers=other_headers)
    assert r.status_code == 404, f"ожидался 404, получено {r.status_code} {r.text}"
    assert foreign_id in {n.id for n in _fresh(db)}, "чужое уведомление удалено"


def test_notification_endpoints_require_auth(client, db):
    """Без токена лента не отдаётся и не меняется."""
    user_row = models.User(username="noauth_probe", hashed_password="x", role="manager")
    db.add(user_row)
    db.commit()
    note_id = _note(db, user_row.id, "Чужое без токена").id

    assert client.get("/notifications").status_code == 401
    assert client.post("/notifications/read", json={"ids": []}).status_code == 401
    assert client.delete(f"/notifications/{note_id}").status_code == 401
    assert note_id in {n.id for n in _fresh(db)}
