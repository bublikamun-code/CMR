"""Регрессии дефектов безопасности и денежных гонок в card_details/workflows.

Покрыты дефекты, найденные 26.09.2026:

1. Проверка баланса клиента при закрытии счёта «из баланса» была обычным
   SELECT: два параллельных PATCH /cards/{id}/payment с from_balance оба
   видели один и тот же остаток и оба увеличивали Σ paid_amount — кредит
   клиента уходил в минус.
2. ``_resolve_file_path`` сравнивал realpath с каталогом загрузок БЕЗ os.sep
   на конце, поэтому файл из соседнего каталога uploads_evil считался
   «своим» и отдавался на скачивание.
3. Имя файла счёта уходило в ответ сырым: не-ASCII имя роняло ответ 500 на
   кодировке заголовка, спецсимволы позволяли подменить filename.
4. Ручки триггеров/шагов/запусков воркфлоу читали и удаляли записи по
   глобальному id без tenant-фильтра.
5. Отказ записи уведомлений (откат commit'а) был полностью немым.

Каждый тест на границу tenant создаёт строки с ЯВНЫМ tenant_id, поэтому
маскирующий слушатель ``conftest._set_test_tenant`` (проставляет 101 при
None) ничего не подменяет. Что тесты действительно ловят дефект, проверялось
вручную: фикс откатывался, тесты падали, фикс возвращался.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote, unquote

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

import database
import models
import routers.card_details_router as card_router
import routers.clients_router as clients_router
from conftest import DEFAULT_TEST_TENANT_ID, SCHEMA_PROFILE

pytestmark = pytest.mark.skipif(
    SCHEMA_PROFILE != "prod",
    reason="Гонки и файловые проверки требуют профиль фактического production DDL",
)

OTHER_TENANT_ID = 202


@pytest.fixture
def other_tenant(db):
    """Реальная строка tenants для второго tenant (FK users.tenant_id включён)."""
    import models_tenant

    tenant = db.query(models_tenant.Tenant).filter(
        models_tenant.Tenant.id == OTHER_TENANT_ID
    ).first()
    if tenant is None:
        tenant = models_tenant.Tenant(id=OTHER_TENANT_ID,
                                      name="Второй тестовый tenant",
                                      db_path=f"tests/crm_{OTHER_TENANT_ID}.db")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    return tenant


def _request(method, path, headers, json=None):
    import main

    with TestClient(main.app, raise_server_exceptions=False) as client:
        return client.request(method, path, headers=headers, json=json)


def _concurrent(specs):
    """Несколько настоящих HTTP-запросов, стартующих одновременно.

    spec — dict с ключами method/path/headers/json (как у TestClient.request).
    """
    barrier = threading.Barrier(len(specs))

    def worker(spec):
        barrier.wait(timeout=10)
        return _request(**spec)

    with ThreadPoolExecutor(max_workers=len(specs)) as executor:
        futures = [executor.submit(worker, spec) for spec in specs]
        return [future.result(timeout=30) for future in futures]


def _spec(method, path, headers, **extra):
    return {"method": method, "path": path, "headers": headers, **extra}


@pytest.fixture
def make_client(db):
    def _factory(name="Клиент гонки", tenant_id=DEFAULT_TEST_TENANT_ID):
        c = models.Client(name=name, tenant_id=tenant_id)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c
    return _factory


# ---------------------------------------------------------------------------
# 1. Гонка на балансе клиента (денежный дефект)
# ---------------------------------------------------------------------------

def test_concurrent_close_from_balance_cannot_double_spend(
        client, manager, db, make_card, make_client, monkeypatch):
    """Два параллельных закрытия «из баланса» не могут списать больше кассы.

    Барьер внутри чтения баланса детерминированно вскрывает старое окно
    check-then-write: без резервирования writer slot оба запроса читают
    один и тот же остаток и оба проходят проверку. С резервированием второй
    запрос до барьера не доходит (он ждёт commit первого), таймаут короткий,
    а BrokenBarrierError безопасен — тот же приём, что в
    tests/test_payments_money.py.
    """
    _, headers = manager
    cl = make_client()
    card_a = make_card(title="Счёт А", total_amount=600.0, paid_amount=0.0,
                       payment_status="Не оплачен", client_id=cl.id)
    card_b = make_card(title="Счёт Б", total_amount=600.0, paid_amount=0.0,
                       payment_status="Не оплачен", client_id=cl.id)
    assert client.post(f"/clients/{cl.id}/payments", headers=headers,
                       json={"amount": 1000.0, "note": "предоплата"}).status_code == 200

    barrier = threading.Barrier(2)
    real_balance = clients_router._client_balance_payload

    def synchronized_balance(session, client_id, current_user):
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return real_balance(session, client_id, current_user)

    monkeypatch.setattr(clients_router, "_client_balance_payload", synchronized_balance)

    body = {"paid_amount": 600.0, "payment_status": "Оплачен", "from_balance": True}
    responses = _concurrent([
        _spec("PATCH", f"/cards/{card_a.id}/payment", headers, json=body),
        _spec("PATCH", f"/cards/{card_b.id}/payment", headers, json=body),
    ])

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 400], [response.text for response in responses]
    rejected = [r for r in responses if r.status_code == 400][0]
    assert "не хватает" in rejected.json()["detail"], rejected.text

    db.expire_all()
    covered = round(sum(float(c.paid_amount or 0) for c in db.query(models.Card).filter(
        models.Card.id.in_([card_a.id, card_b.id])).all()), 2)
    assert covered == 600.0, f"списано {covered} при кассе 1000 — двойное списание"
    bal = real_balance(db, cl.id, manager[0])
    assert bal["paid_on_cards"] <= bal["payments_total"], (
        f"кредит ушёл в минус: покрытие {bal['paid_on_cards']} > кассы {bal['payments_total']}"
    )


def test_real_busy_client_balance_is_503_with_retry_after(manager, db, make_card, make_client):
    """Реальная занятость SQLite на резервировании — управляемый 503.

    Тот же контракт, что у групп списания: клиент получает Retry-After,
    а не 500, и карточка не остаётся полуизменённой.
    """
    _, headers = manager
    cl = make_client("Клиент под замком")
    card = make_card(title="Счёт", total_amount=400.0, paid_amount=0.0,
                     payment_status="Не оплачен", client_id=cl.id)
    db.add(models.ClientPayment(client_id=cl.id, amount=400.0,
                                tenant_id=DEFAULT_TEST_TENANT_ID))
    db.commit()

    def set_short_busy_timeout(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=50")
        cursor.close()

    event.listen(database.engine, "connect", set_short_busy_timeout)
    database.engine.dispose()
    locker = database.engine.raw_connection()
    try:
        locker.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request, "PATCH", f"/cards/{card.id}/payment", headers,
                json={"paid_amount": 400.0, "payment_status": "Оплачен",
                      "from_balance": True},
            )
            # ^ прямой вызов, а не _concurrent: здесь нужен один запрос,
            #  ждущий освобождения writer slot из другого соединения.
            response = future.result(timeout=30)
    finally:
        locker.rollback()
        locker.close()
        event.remove(database.engine, "connect", set_short_busy_timeout)
        database.engine.dispose()

    assert response.status_code == 503, response.text
    assert response.headers.get("Retry-After") == "1"
    db.expire_all()
    assert float(db.get(models.Card, card.id).paid_amount or 0) == 0.0


# ---------------------------------------------------------------------------
# 2. Выход за пределы каталога загрузок
# ---------------------------------------------------------------------------

def test_resolve_file_path_rejects_sibling_directory(tmp_path, monkeypatch):
    """uploads_evil не проходит проверку «внутри uploads».

    Без os.sep на конце ``/…/uploads_evil/файл`` начинается с ``/…/uploads``
    и проходил как свой файл.
    """
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    evil = tmp_path / "uploads_evil"
    evil.mkdir()
    secret = evil / "passwd.txt"
    secret.write_text("секрет", encoding="utf-8")
    inside = uploads / "own.txt"
    inside.write_text("свой", encoding="utf-8")
    monkeypatch.setattr(card_router, "UPLOAD_DIR", str(uploads))

    assert card_router._resolve_file_path(str(secret)) == ""
    assert card_router._resolve_file_path(str(inside)) == str(inside)


def test_path_inside_uploads_helper(tmp_path, monkeypatch):
    """Тот же инвариант в помощнике, которым пользуются все три загрузчика."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(card_router, "UPLOAD_DIR", str(uploads))

    assert card_router._path_inside_uploads(str(uploads / "1_a.pdf")) is True
    assert card_router._path_inside_uploads(
        str(tmp_path / "uploads_evil" / "1_a.pdf")) is False
    assert card_router._path_inside_uploads(
        str(tmp_path / "uploads.bak" / "1_a.pdf")) is False
    # Сам каталог — не файл, даже realpath без разделителя совпал бы.
    assert card_router._path_inside_uploads(str(uploads)) is False


def test_download_of_file_outside_uploads_is_404(client, manager, db, make_card,
                                                 tmp_path, monkeypatch):
    """Скачивание вложения с путём наружу не отдаёт файл — 404.

    Сквозная проверка через HTTP: путь из БД указывает в соседний каталог.
    """
    _, headers = manager
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(card_router, "UPLOAD_DIR", str(uploads))
    evil = tmp_path / "uploads_evil"
    evil.mkdir()
    secret = evil / "личные_данные.txt"
    secret.write_text("секрет", encoding="utf-8")

    card = make_card(title="Сделка", total_amount=100.0)
    attachment = models.CardAttachment(card_id=card.id,
                                       file_name="личные_данные.txt",
                                       file_path=str(secret))
    db.add(attachment)
    db.commit()
    attachment_id = attachment.id

    response = client.get(f"/attachments/{attachment_id}/download", headers=headers)
    assert response.status_code == 404, response.text
    assert "секрет" not in response.text


# ---------------------------------------------------------------------------
# 3. Имя файла при скачивании
# ---------------------------------------------------------------------------

def _attachment(db, make_card, file_name, file_path):
    card = make_card(title="Сделка", total_amount=100.0)
    attachment = models.CardAttachment(card_id=card.id, file_name=file_name,
                                       file_path=file_path)
    db.add(attachment)
    db.commit()
    return attachment.id


def _checklist_invoice(db, make_card, file_name, file_path):
    card = make_card(title="Сделка", total_amount=100.0)
    item = models.CardChecklist(card_id=card.id, company_name="Поставщик",
                                invoice_file_name=file_name,
                                invoice_file_path=file_path)
    db.add(item)
    db.commit()
    return item.id


def test_invoice_download_survives_non_ascii_name(client, manager, db, make_card,
                                                  tmp_path, monkeypatch):
    """Не-ASCII имя счёта больше не роняет ответ 500.

    Значения заголовков Starlette кодируются в latin-1, поэтому сырое
    «Счёт №5 от поставщика» давало UnicodeEncodeError при сборке ответа.
    """
    _, headers = manager
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(card_router, "UPLOAD_DIR", str(uploads))
    stored = uploads / "chk1_aaaa1111_schet.txt"
    stored.write_text("счёт", encoding="utf-8")

    name = 'Счёт № 5 от поставщика\r\nX-Injected: 1".txt'
    item_id = _checklist_invoice(db, make_card, name, str(stored))

    response = client.get(f"/checklists/{item_id}/invoice/download", headers=headers)
    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.startswith("attachment; ")
    # RFC 5987: не-ASCII имя уходит в filename*, ASCII-дубль — в filename.
    assert "filename*=UTF-8''" in disposition
    encoded = disposition.split("filename*=UTF-8''", 1)[1].split(";", 1)[0].strip()
    decoded = unquote(encoded)
    assert "Счёт" in decoded, disposition
    # Инъекция не должна ни разорвать заголовок, ни стать отдельным
    # заголовком ответа: CRLF вычищены, а остатки имени — часть filename*.
    assert "\r" not in decoded and "\n" not in decoded and '"' not in decoded
    assert "x-injected" not in {key.lower() for key in response.headers}
    assert len(response.headers.get_list("content-disposition")) == 1
    assert response.content == "счёт".encode("utf-8")


def test_sanitize_download_name_strips_paths_and_quotes():
    """Только базовое имя и безопасные символы."""
    assert card_router._sanitize_download_name("../../etc/passwd") == "passwd"
    assert card_router._sanitize_download_name("C:\\windows\\evil.exe") == "evil.exe"
    assert card_router._sanitize_download_name('a"b.txt') == "a_b.txt"
    assert card_router._sanitize_download_name("  ") == "attachment"
    assert card_router._sanitize_download_name("") == "attachment"
    assert card_router._sanitize_download_name("счёт.pdf") == "счёт.pdf"


def test_content_disposition_is_ascii_only():
    """Заголовок обязан быть чисто ASCII: UTF-8 несёт filename*."""
    disposition = card_router._content_disposition("Счёт № 5.pdf")
    disposition.encode("latin-1")  # не должно бросить UnicodeEncodeError
    assert "filename*=UTF-8''" + quote("Счёт № 5.pdf", safe="") in disposition
    assert 'filename="' in disposition


def test_attachment_download_uses_same_header_rules(client, manager, db, make_card,
                                                    tmp_path, monkeypatch):
    _, headers = manager
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(card_router, "UPLOAD_DIR", str(uploads))
    stored = uploads / "1_aaaa1111_file.txt"
    stored.write_text("данные", encoding="utf-8")

    attachment_id = _attachment(db, make_card, "Договор «Альфа».txt", str(stored))

    response = client.get(f"/attachments/{attachment_id}/download", headers=headers)
    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")
    encoded = disposition.split("filename*=UTF-8''", 1)[1].split(";", 1)[0].strip()
    assert "Договор" in unquote(encoded)
    assert response.content == "данные".encode("utf-8")


# ---------------------------------------------------------------------------
# 4. Tenant-изоляция воркфлоу
# ---------------------------------------------------------------------------

@pytest.fixture
def foreign_workflow(db, make_user, other_tenant):
    """Воркфлоу чужого tenant со всеми дочерними строками.

    Все tenant_id проставлены ЯВНО: маскирующий слушатель conftest их не
    трогает, поэтому тест видит настоящее cross-tenant чтение/удаление.
    """
    foreign_admin, _ = make_user("admin", username="wf_admin_b",
                                tenant_id=OTHER_TENANT_ID)
    workflow = models.Workflow(name="Чужой сценарий", created_by=foreign_admin.id,
                               tenant_id=OTHER_TENANT_ID)
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    trigger = models.WorkflowTrigger(workflow_id=workflow.id, trigger_type="manual",
                                     config='{"secret": "чужая конфигурация"}',
                                     tenant_id=OTHER_TENANT_ID)
    step = models.WorkflowStep(workflow_id=workflow.id, step_type="action",
                               config='{"url": "https://example.com"}',
                               position=0, tenant_id=OTHER_TENANT_ID)
    run = models.WorkflowRun(workflow_id=workflow.id, status="failed",
                             error="чужая ошибка запуска",
                             tenant_id=OTHER_TENANT_ID)
    db.add_all([trigger, step, run])
    db.commit()
    return {
        "workflow_id": workflow.id,
        "trigger_id": trigger.id,
        "step_id": step.id,
        "run_id": run.id,
    }


def test_workflow_reads_do_not_cross_tenants(client, admin, foreign_workflow):
    """Админ своего tenant не читает конфигурацию чужого воркфлоу."""
    _, own = admin
    wf_id = foreign_workflow["workflow_id"]
    for path in (f"/workflows/{wf_id}/triggers", f"/workflows/{wf_id}/steps",
                 f"/workflows/{wf_id}/runs"):
        response = client.get(path, headers=own)
        assert response.status_code == 404, f"{path}: {response.text}"
        assert "чужая" not in response.text


def test_workflow_deletes_do_not_cross_tenants(client, admin, db, foreign_workflow):
    """Чужой триггер и шаг по глобальному id удаляются только с 404."""
    _, own = admin
    for path in (f"/workflows/triggers/{foreign_workflow['trigger_id']}",
                 f"/workflows/steps/{foreign_workflow['step_id']}"):
        response = client.delete(path, headers=own)
        assert response.status_code == 404, f"{path}: {response.text}"

    db.expire_all()
    assert db.get(models.WorkflowTrigger, foreign_workflow["trigger_id"]) is not None
    assert db.get(models.WorkflowStep, foreign_workflow["step_id"]) is not None


def test_workflow_patch_and_delete_do_not_cross_tenants(client, admin, db, foreign_workflow):
    """PATCH и DELETE самого воркфлоу — тоже честный 404."""
    _, own = admin
    wf_id = foreign_workflow["workflow_id"]
    assert client.patch(f"/workflows/{wf_id}", headers=own,
                        json={"name": "Переименован"}).status_code == 404
    assert client.delete(f"/workflows/{wf_id}", headers=own).status_code == 404
    db.expire_all()
    assert db.get(models.Workflow, wf_id).name == "Чужой сценарий"
    assert db.query(models.WorkflowTrigger).count() == 1
    assert db.query(models.WorkflowStep).count() == 1
    assert db.query(models.WorkflowRun).count() == 1


def test_workflow_own_tenant_still_works(client, admin):
    """Контроль в другую сторону: свой tenant работает как раньше."""
    _, own = admin
    created = client.post("/workflows/", headers=own, json={"name": "Мой сценарий"})
    assert created.status_code == 200, created.text
    wf_id = created.json()["id"]

    trigger = client.post(f"/workflows/{wf_id}/triggers", headers=own,
                          json={"trigger_type": "manual", "config": {"a": 1}})
    assert trigger.status_code == 200, trigger.text
    listed = client.get(f"/workflows/{wf_id}/triggers", headers=own).json()
    assert listed == [{"id": trigger.json()["id"], "trigger_type": "manual",
                       "config": {"a": 1}}]
    assert client.delete(f"/workflows/triggers/{trigger.json()['id']}",
                         headers=own).status_code == 200
    assert client.get(f"/workflows/{wf_id}/triggers", headers=own).json() == []
    assert client.get(f"/workflows/{wf_id}/steps", headers=own).json() == []
    assert client.get(f"/workflows/{wf_id}/runs", headers=own).json() == []


def test_workflow_children_get_requesting_tenant(client, admin, db, foreign_workflow):
    """Новые строки получают tenant вызывающего, а не значение из БД чужого."""
    _, own = admin
    created = client.post("/workflows/", headers=own, json={"name": "Свой"})
    assert created.status_code == 200, created.text
    wf_id = created.json()["id"]
    step = client.post(f"/workflows/{wf_id}/steps", headers=own,
                       json={"step_type": "action", "config": {}})
    assert step.status_code == 200, step.text
    trigger = client.post(f"/workflows/{wf_id}/triggers", headers=own,
                          json={"trigger_type": "manual", "config": {}})
    assert trigger.status_code == 200, trigger.text

    db.expire_all()
    owner_tenant = db.get(models.Workflow, wf_id).tenant_id
    assert owner_tenant == DEFAULT_TEST_TENANT_ID
    assert db.get(models.WorkflowStep, step.json()["id"]).tenant_id == owner_tenant
    assert db.get(models.WorkflowTrigger, trigger.json()["id"]).tenant_id == owner_tenant


# ---------------------------------------------------------------------------
# 5. Отказ записи уведомлений виден вызывающему
# ---------------------------------------------------------------------------

class _CommitFailsSession:
    """Сессия, у которой проходит SELECT пользователей, но падает commit."""

    def __init__(self):
        self.rolled_back = False
        self.added = []

    def query(self, model):
        class _Users:
            def filter(self, *args, **kwargs):
                return self

            def all(self):
                return [models.User(id=1, username="u", hashed_password="x",
                                    role="manager", tenant_id=DEFAULT_TEST_TENANT_ID)]

        return _Users()

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        raise RuntimeError("диск заполнен")

    def rollback(self):
        self.rolled_back = True


def test_notify_reports_failed_commit(caplog):
    """Откат commit'а в notify виден, а не проглочен.

    Раньше возвращалось created > 0 при полном отсутствии результата.
    """
    from notify import notify as notify_func

    session = _CommitFailsSession()
    result = notify_func(session, [1], type="test", title="Проверка")

    assert int(result) == 0
    assert result.failed is True
    assert "диск заполнен" in result.error
    assert session.rolled_back is True
    assert "Не удалось записать уведомления" in caplog.text


def test_notify_success_still_returns_count(db, manager):
    """Контракт для обычных вызовов не изменился: число записанных строк."""
    from notify import notify as notify_func

    user, _ = manager
    result = notify_func(db, [user.id], type="test", title="Проверка",
                         entity_type="card", entity_id=1)
    assert result == 1
    assert result.failed is False
    assert result.error is None
    assert db.query(models.Notification).count() == 1


def test_sync_overdue_reports_errors(client, cron_headers, db, manager, monkeypatch):
    """Сбой рассылки виден в ответе cron, а не как тихий created: 0."""
    import routers.notifications_router as notifications_router
    from notify import NotifyResult

    owner, _ = manager
    # Дата — datetime.date: sync_overdue сравнивает due_date со строковым
    # day_key, поэтому хранимое значение должно быть того же типа.
    db.add(models.Card(title="Просроченная", due_date=date(2020, 1, 1),
                       owner_id=owner.id, status="Новый запрос",
                       tenant_id=DEFAULT_TEST_TENANT_ID))
    db.commit()

    def failing_notify(db, recipients, **kwargs):
        return NotifyResult(0, failed=True, error="диск заполнен")

    monkeypatch.setattr(notifications_router, "notify", failing_notify)
    response = client.post("/notifications/sync-overdue", headers=cron_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created"] == 0
    assert payload["errors"] == 1, payload
    assert payload["error"] == "диск заполнен", payload


def test_sync_overdue_breaks_are_logged(client, cron_headers, db, manager,
                                        monkeypatch, caplog):
    """Исключение в теле рассылки тоже попадает в лог и в счётчик."""
    import routers.notifications_router as notifications_router

    owner, _ = manager
    db.add(models.Card(title="Просроченная", due_date=date(2020, 1, 1),
                       owner_id=owner.id, status="Новый запрос",
                       tenant_id=DEFAULT_TEST_TENANT_ID))
    db.commit()

    def exploding_statuses(session):
        raise ValueError("справочник статусов недоступен")

    monkeypatch.setattr(notifications_router, "_done_status_names",
                        exploding_statuses)
    with caplog.at_level("ERROR", logger=notifications_router.__name__):
        response = client.post("/notifications/sync-overdue", headers=cron_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["errors"] == 1, payload
    assert "справочник статусов недоступен" in payload["error"], payload
    assert "Синхронизация просрочек прервана" in caplog.text
