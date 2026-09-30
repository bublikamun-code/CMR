# Покрытие HTTP-маршрутов тестами

Снимок прогона `pytest -p tests.route_coverage`. Число в скобках —
сколько раз маршрут вызвали тесты за прогон: единица означает, что
ручка задета, но почти не проверена.

- Всего маршрутов (метод+путь): **155**
- Покрыто: **155**
- Не покрыто: **0**

## Не покрыто

| Маршрут | Почему |
| --- | --- |
| — | все маршруты покрыты |

## Покрыто

| Маршрут | Вызовов |
| --- | --- |
| `GET /` | 6 |
| `GET /activity` | 10 |
| `DELETE /activity/{entry_id}` | 6 |
| `PATCH /activity/{entry_id}` | 9 |
| `GET /admin` | 5 |
| `GET /api/version` | 1 |
| `DELETE /attachments/{attachment_id}` | 3 |
| `GET /attachments/{attachment_id}/download` | 6 |
| `POST /auth/login` | 10 |
| `POST /auth/logout` | 14 |
| `GET /auth/me` | 35 |
| `PUT /auth/me/password` | 1 |
| `GET /auth/users` | 2 |
| `POST /auth/users` | 9 |
| `DELETE /auth/users/{user_id}` | 8 |
| `PATCH /auth/users/{user_id}` | 25 |
| `PATCH /cards/{card_id}` | 88 |
| `POST /cards/{card_id}/attachments` | 8 |
| `POST /cards/{card_id}/checklists` | 8 |
| `PATCH /cards/{card_id}/payment` | 47 |
| `DELETE /checklists/{checklist_id}` | 3 |
| `PATCH /checklists/{checklist_id}` | 5 |
| `DELETE /checklists/{checklist_id}/invoice` | 4 |
| `POST /checklists/{checklist_id}/invoice` | 10 |
| `GET /checklists/{checklist_id}/invoice/download` | 7 |
| `GET /clients` | 10 |
| `POST /clients` | 1 |
| `DELETE /clients/payments/{payment_id}` | 5 |
| `DELETE /clients/{client_id}` | 8 |
| `GET /clients/{client_id}` | 3 |
| `PATCH /clients/{client_id}` | 2 |
| `GET /clients/{client_id}/balance` | 12 |
| `GET /clients/{client_id}/cards` | 1 |
| `POST /clients/{client_id}/payments` | 11 |
| `GET /clients/{client_id}/versions` | 5 |
| `DELETE /custom/fields/{field_id}` | 1 |
| `PATCH /custom/fields/{field_id}` | 37 |
| `GET /custom/objects` | 6 |
| `POST /custom/objects` | 66 |
| `DELETE /custom/objects/{obj_id}` | 2 |
| `PATCH /custom/objects/{obj_id}` | 33 |
| `GET /custom/objects/{obj_id}/fields` | 1 |
| `POST /custom/objects/{obj_id}/fields` | 36 |
| `GET /custom/objects/{obj_id}/records` | 4 |
| `POST /custom/objects/{obj_id}/records` | 7 |
| `DELETE /custom/records/{record_id}` | 3 |
| `GET /dictionaries/statuses` | 5 |
| `POST /dictionaries/statuses` | 10 |
| `PATCH /dictionaries/statuses/{status_id}` | 7 |
| `GET /dictionaries/stores` | 3 |
| `POST /dictionaries/stores` | 8 |
| `PATCH /dictionaries/stores/{store_id}` | 9 |
| `GET /docs` | 5 |
| `POST /email-parser/link/{card_id}` | 9 |
| `GET /email-parser/related/{card_id}` | 4 |
| `GET /email-parser/settings` | 2 |
| `POST /email-parser/settings` | 10 |
| `POST /email-parser/sync` | 7 |
| `POST /email-parser/sync-all` | 6 |
| `POST /files/attach/{card_id}` | 8 |
| `GET /health` | 4 |
| `GET /health/live` | 1 |
| `GET /health/ready` | 2 |
| `GET /kanban/cards` | 37 |
| `POST /kanban/cards` | 17 |
| `PATCH /kanban/cards/reorder` | 9 |
| `DELETE /kanban/cards/{card_id}` | 2 |
| `GET /kanban/cards/{card_id}` | 23 |
| `DELETE /kanban/cards/{card_id}/permanent` | 15 |
| `PATCH /kanban/cards/{card_id}/restore` | 2 |
| `PATCH /kanban/cards/{card_id}/status` | 17 |
| `GET /kanban/cards/{card_id}/versions` | 6 |
| `GET /kanban/export/csv` | 7 |
| `GET /kanban/trash` | 6 |
| `GET /legacy` | 4 |
| `GET /legacy/admin` | 2 |
| `GET /manifest.json` | 3 |
| `GET /nakladnye` | 7 |
| `POST /nakladnye` | 22 |
| `GET /nakladnye/bot/check-duplicate` | 14 |
| `POST /nakladnye/bot/create` | 39 |
| `PATCH /nakladnye/bot/{nak_id}` | 1 |
| `POST /nakladnye/bot/{nak_id}/photos` | 1 |
| `GET /nakladnye/export/products-excel` | 1 |
| `GET /nakladnye/photos/{filename}` | 6 |
| `DELETE /nakladnye/{nak_id}` | 9 |
| `PATCH /nakladnye/{nak_id}` | 13 |
| `GET /nakladnye/{nak_id}/excel` | 1 |
| `POST /nakladnye/{nak_id}/photos` | 3 |
| `GET /notifications` | 9 |
| `POST /notifications/read` | 4 |
| `POST /notifications/sync-overdue` | 9 |
| `DELETE /notifications/{notification_id}` | 3 |
| `GET /openapi.json` | 5 |
| `GET /payments/cards/{card_id}/invoices` | 2 |
| `POST /payments/cards/{card_id}/invoices` | 5 |
| `POST /payments/cards/{card_id}/issue-invoice` | 65 |
| `POST /payments/cards/{card_id}/sync-writeoff-status` | 12 |
| `DELETE /payments/cards/{card_id}/writeoff` | 8 |
| `GET /payments/cards/{card_id}/writeoff-status` | 4 |
| `GET /payments/documents` | 4 |
| `POST /payments/repair-writeoffs` | 8 |
| `GET /payments/transactions` | 50 |
| `DELETE /payments/transactions/{transaction_id}` | 23 |
| `PATCH /payments/transactions/{transaction_id}` | 31 |
| `POST /payments/transactions/{transaction_id}/duplicate_as_document` | 1 |
| `POST /payments/trigger_from_card/{card_id}` | 113 |
| `GET /redoc` | 4 |
| `GET /suppliers` | 3 |
| `POST /suppliers` | 1 |
| `DELETE /suppliers/{supplier_id}` | 9 |
| `GET /suppliers/{supplier_id}` | 2 |
| `PATCH /suppliers/{supplier_id}` | 6 |
| `GET /suppliers/{supplier_id}/purchases` | 1 |
| `GET /tags` | 2 |
| `POST /tags` | 2 |
| `DELETE /tags/cards/{card_id}/tags/{tag_id}` | 1 |
| `POST /tags/cards/{card_id}/tags/{tag_id}` | 2 |
| `DELETE /tags/{tag_id}` | 6 |
| `GET /tasks` | 4 |
| `POST /tasks` | 7 |
| `GET /tasks/assignees` | 3 |
| `DELETE /tasks/checklist/{item_id}` | 5 |
| `PATCH /tasks/checklist/{item_id}` | 7 |
| `DELETE /tasks/{task_id}` | 5 |
| `PATCH /tasks/{task_id}` | 17 |
| `POST /tasks/{task_id}/checklist` | 3 |
| `GET /webhooks/` | 3 |
| `POST /webhooks/` | 15 |
| `GET /webhooks/events` | 4 |
| `DELETE /webhooks/{wh_id}` | 4 |
| `PATCH /webhooks/{wh_id}` | 22 |
| `POST /webhooks/{wh_id}/test` | 5 |
| `GET /workflows/` | 2 |
| `POST /workflows/` | 16 |
| `DELETE /workflows/steps/{step_id}` | 3 |
| `DELETE /workflows/triggers/{trigger_id}` | 4 |
| `DELETE /workflows/{wf_id}` | 4 |
| `PATCH /workflows/{wf_id}` | 20 |
| `GET /workflows/{wf_id}/runs` | 3 |
| `GET /workflows/{wf_id}/steps` | 3 |
| `POST /workflows/{wf_id}/steps` | 3 |
| `GET /workflows/{wf_id}/triggers` | 7 |
| `POST /workflows/{wf_id}/triggers` | 3 |
| `GET /writeoffs/groups/` | 3 |
| `POST /writeoffs/groups/` | 36 |
| `DELETE /writeoffs/groups/{group_id}` | 13 |
| `GET /writeoffs/groups/{group_id}` | 1 |
| `POST /writeoffs/groups/{group_id}/annul` | 18 |
| `DELETE /writeoffs/groups/{group_id}/cards/{card_id}` | 7 |
| `POST /writeoffs/groups/{group_id}/cards/{card_id}` | 8 |
| `POST /writeoffs/groups/{group_id}/issue-invoice` | 29 |
| `GET /writeoffs/pending` | 3 |
| `POST /writeoffs/{card_id}/execute` | 5 |
| `POST /writeoffs/{card_id}/finish_assembly` | 7 |
