# Покрытие HTTP-маршрутов тестами

Снимок прогона `pytest -p tests.route_coverage`. Число в скобках —
сколько раз маршрут вызвали тесты за прогон: единица означает, что
ручка задета, но почти не проверена.

- Всего маршрутов (метод+путь): **155**
- Покрыто: **155**
- Не покрыто: **0**
- Из покрытых задеты только отказом (4xx/5xx): **0**

## Не покрыто

| Маршрут | Почему |
| --- | --- |
| — | все маршруты покрыты |

## Покрыто, но только отказом

Маршрут вызывали, и каждый ответ был 4xx/5xx — успешная ветка ручки
не выполнялась ни разу (обычно тест проверяет только 404 чужого
объекта или 403 для чужой роли).

| Маршрут | Вызовов |
| --- | --- |
| — | таких нет |

## Покрыто

| Маршрут | Вызовов | Успешных |
| --- | --- | --- |
| `GET /` | 6 | 6 |
| `GET /activity` | 10 | 6 |
| `DELETE /activity/{entry_id}` | 6 | 2 |
| `PATCH /activity/{entry_id}` | 9 | 2 |
| `GET /admin` | 5 | 5 |
| `GET /api/version` | 1 | 1 |
| `DELETE /attachments/{attachment_id}` | 3 | 1 |
| `GET /attachments/{attachment_id}/download` | 6 | 2 |
| `POST /auth/login` | 10 | 6 |
| `POST /auth/logout` | 14 | 14 |
| `GET /auth/me` | 35 | 10 |
| `PUT /auth/me/password` | 1 | 1 |
| `GET /auth/users` | 2 | 2 |
| `POST /auth/users` | 9 | 3 |
| `DELETE /auth/users/{user_id}` | 8 | 4 |
| `PATCH /auth/users/{user_id}` | 25 | 11 |
| `PATCH /cards/{card_id}` | 88 | 65 |
| `POST /cards/{card_id}/attachments` | 8 | 2 |
| `POST /cards/{card_id}/checklists` | 8 | 2 |
| `PATCH /cards/{card_id}/payment` | 47 | 33 |
| `DELETE /checklists/{checklist_id}` | 3 | 1 |
| `PATCH /checklists/{checklist_id}` | 5 | 2 |
| `DELETE /checklists/{checklist_id}/invoice` | 4 | 2 |
| `POST /checklists/{checklist_id}/invoice` | 10 | 4 |
| `GET /checklists/{checklist_id}/invoice/download` | 7 | 3 |
| `GET /clients` | 10 | 5 |
| `POST /clients` | 5 | 5 |
| `DELETE /clients/payments/{payment_id}` | 5 | 1 |
| `DELETE /clients/{client_id}` | 8 | 3 |
| `GET /clients/{client_id}` | 3 | 2 |
| `PATCH /clients/{client_id}` | 2 | 1 |
| `GET /clients/{client_id}/balance` | 12 | 11 |
| `GET /clients/{client_id}/cards` | 4 | 3 |
| `POST /clients/{client_id}/payments` | 11 | 8 |
| `GET /clients/{client_id}/versions` | 5 | 3 |
| `DELETE /custom/fields/{field_id}` | 5 | 3 |
| `PATCH /custom/fields/{field_id}` | 37 | 14 |
| `GET /custom/objects` | 6 | 6 |
| `POST /custom/objects` | 69 | 62 |
| `DELETE /custom/objects/{obj_id}` | 2 | 1 |
| `PATCH /custom/objects/{obj_id}` | 33 | 15 |
| `GET /custom/objects/{obj_id}/fields` | 3 | 3 |
| `POST /custom/objects/{obj_id}/fields` | 40 | 35 |
| `GET /custom/objects/{obj_id}/records` | 5 | 5 |
| `POST /custom/objects/{obj_id}/records` | 9 | 8 |
| `DELETE /custom/records/{record_id}` | 3 | 2 |
| `GET /dictionaries/statuses` | 5 | 5 |
| `POST /dictionaries/statuses` | 10 | 5 |
| `PATCH /dictionaries/statuses/{status_id}` | 7 | 3 |
| `GET /dictionaries/stores` | 3 | 2 |
| `POST /dictionaries/stores` | 8 | 3 |
| `PATCH /dictionaries/stores/{store_id}` | 9 | 3 |
| `GET /docs` | 5 | 2 |
| `POST /email-parser/link/{card_id}` | 9 | 4 |
| `GET /email-parser/related/{card_id}` | 4 | 2 |
| `GET /email-parser/settings` | 2 | 1 |
| `POST /email-parser/settings` | 10 | 4 |
| `POST /email-parser/sync` | 11 | 2 |
| `POST /email-parser/sync-all` | 6 | 4 |
| `POST /files/attach/{card_id}` | 8 | 3 |
| `GET /health` | 4 | 4 |
| `GET /health/live` | 1 | 1 |
| `GET /health/ready` | 2 | 1 |
| `GET /kanban/cards` | 37 | 25 |
| `POST /kanban/cards` | 17 | 8 |
| `PATCH /kanban/cards/reorder` | 9 | 7 |
| `DELETE /kanban/cards/{card_id}` | 2 | 2 |
| `GET /kanban/cards/{card_id}` | 23 | 20 |
| `DELETE /kanban/cards/{card_id}/permanent` | 15 | 8 |
| `PATCH /kanban/cards/{card_id}/restore` | 2 | 2 |
| `PATCH /kanban/cards/{card_id}/status` | 17 | 15 |
| `GET /kanban/cards/{card_id}/versions` | 6 | 4 |
| `GET /kanban/export/csv` | 7 | 7 |
| `GET /kanban/trash` | 6 | 5 |
| `GET /legacy` | 4 | 4 |
| `GET /legacy/admin` | 2 | 2 |
| `GET /manifest.json` | 3 | 3 |
| `GET /nakladnye` | 30 | 28 |
| `POST /nakladnye` | 29 | 17 |
| `GET /nakladnye/bot/check-duplicate` | 20 | 16 |
| `POST /nakladnye/bot/create` | 43 | 27 |
| `PATCH /nakladnye/bot/{nak_id}` | 8 | 4 |
| `POST /nakladnye/bot/{nak_id}/photos` | 7 | 4 |
| `GET /nakladnye/export/products-excel` | 14 | 13 |
| `GET /nakladnye/photos/{filename}` | 6 | 2 |
| `DELETE /nakladnye/{nak_id}` | 11 | 6 |
| `PATCH /nakladnye/{nak_id}` | 20 | 10 |
| `GET /nakladnye/{nak_id}/excel` | 11 | 7 |
| `POST /nakladnye/{nak_id}/photos` | 15 | 6 |
| `GET /notifications` | 9 | 7 |
| `POST /notifications/read` | 4 | 2 |
| `POST /notifications/sync-overdue` | 9 | 6 |
| `DELETE /notifications/{notification_id}` | 3 | 1 |
| `GET /openapi.json` | 5 | 2 |
| `GET /payments/cards/{card_id}/invoices` | 2 | 1 |
| `POST /payments/cards/{card_id}/invoices` | 5 | 2 |
| `POST /payments/cards/{card_id}/issue-invoice` | 65 | 51 |
| `POST /payments/cards/{card_id}/sync-writeoff-status` | 12 | 11 |
| `DELETE /payments/cards/{card_id}/writeoff` | 8 | 4 |
| `GET /payments/cards/{card_id}/writeoff-status` | 4 | 3 |
| `GET /payments/documents` | 4 | 3 |
| `POST /payments/repair-writeoffs` | 8 | 4 |
| `GET /payments/transactions` | 50 | 42 |
| `DELETE /payments/transactions/{transaction_id}` | 23 | 15 |
| `PATCH /payments/transactions/{transaction_id}` | 31 | 16 |
| `POST /payments/transactions/{transaction_id}/duplicate_as_document` | 1 | 1 |
| `POST /payments/trigger_from_card/{card_id}` | 113 | 111 |
| `GET /redoc` | 4 | 1 |
| `GET /suppliers` | 13 | 12 |
| `POST /suppliers` | 3 | 3 |
| `DELETE /suppliers/{supplier_id}` | 11 | 6 |
| `GET /suppliers/{supplier_id}` | 3 | 1 |
| `PATCH /suppliers/{supplier_id}` | 11 | 8 |
| `GET /suppliers/{supplier_id}/purchases` | 5 | 3 |
| `GET /tags` | 2 | 1 |
| `POST /tags` | 2 | 2 |
| `DELETE /tags/cards/{card_id}/tags/{tag_id}` | 1 | 1 |
| `POST /tags/cards/{card_id}/tags/{tag_id}` | 2 | 1 |
| `DELETE /tags/{tag_id}` | 6 | 3 |
| `GET /tasks` | 4 | 2 |
| `POST /tasks` | 7 | 6 |
| `GET /tasks/assignees` | 3 | 2 |
| `DELETE /tasks/checklist/{item_id}` | 5 | 1 |
| `PATCH /tasks/checklist/{item_id}` | 7 | 4 |
| `DELETE /tasks/{task_id}` | 5 | 3 |
| `PATCH /tasks/{task_id}` | 17 | 9 |
| `POST /tasks/{task_id}/checklist` | 3 | 3 |
| `GET /webhooks/` | 3 | 3 |
| `POST /webhooks/` | 15 | 15 |
| `GET /webhooks/events` | 4 | 2 |
| `DELETE /webhooks/{wh_id}` | 4 | 1 |
| `PATCH /webhooks/{wh_id}` | 22 | 8 |
| `POST /webhooks/{wh_id}/test` | 5 | 2 |
| `GET /workflows/` | 2 | 2 |
| `POST /workflows/` | 16 | 13 |
| `DELETE /workflows/steps/{step_id}` | 3 | 1 |
| `DELETE /workflows/triggers/{trigger_id}` | 4 | 2 |
| `DELETE /workflows/{wf_id}` | 4 | 2 |
| `PATCH /workflows/{wf_id}` | 20 | 8 |
| `GET /workflows/{wf_id}/runs` | 3 | 1 |
| `GET /workflows/{wf_id}/steps` | 3 | 1 |
| `POST /workflows/{wf_id}/steps` | 3 | 2 |
| `GET /workflows/{wf_id}/triggers` | 7 | 5 |
| `POST /workflows/{wf_id}/triggers` | 3 | 3 |
| `GET /writeoffs/groups/` | 3 | 2 |
| `POST /writeoffs/groups/` | 36 | 33 |
| `DELETE /writeoffs/groups/{group_id}` | 13 | 4 |
| `GET /writeoffs/groups/{group_id}` | 1 | 1 |
| `POST /writeoffs/groups/{group_id}/annul` | 18 | 9 |
| `DELETE /writeoffs/groups/{group_id}/cards/{card_id}` | 7 | 5 |
| `POST /writeoffs/groups/{group_id}/cards/{card_id}` | 8 | 5 |
| `POST /writeoffs/groups/{group_id}/issue-invoice` | 29 | 23 |
| `GET /writeoffs/pending` | 3 | 2 |
| `POST /writeoffs/{card_id}/execute` | 5 | 2 |
| `POST /writeoffs/{card_id}/finish_assembly` | 7 | 2 |
