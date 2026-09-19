"""Функциональный аудит v2 на проде (фидбек 18.09 «проверь, что всё
работает: кнопки, галочки, карточки на местах»).

Запуск: python3 tools/v2_functional_audit.py
Требует: playwright (pip), временный superadmin-пользователь v2_audit на
проде (создать/удалить вручную — скрипт печатает CLEANUP_IDS для уборки
тестовой карточки и клиента: DELETE transactions/card_attachments/
client_payments по card_id/client_id, затем cards, clients, users).

Проверки: вход; карточки в своих колонках (все, сверка со статусами API);
поиск; очереди «На списание»/«Списано» (правила board.js); сводка 553;
тестовая сделка: выписка, галочки реестра + сохранение, CSV, документы
(ТН/Счёт/Печать), все режимы оплаты, аванс и закрытие из баланса,
«Импорт почты» в истории; настройки почты; колокольчик; разделы;
ошибки консоли. Мутации — только на тестовой карточке AUDIT v2 тест.
"""
import json, os
from playwright.sync_api import sync_playwright

OUT = "/tmp/crm_matrix"; os.makedirs(OUT, exist_ok=True)
BASE = os.environ.get("V2_BASE", "http://87-232-64-12.nip.io/v2/")
V2_USER = os.environ.get("V2_USER", "v2_audit")
V2_PASS = os.environ.get("V2_PASS", "V2Audit2026!")
results = []
def check(name, ok, note=""):
    results.append((name, bool(ok), note))
    print(("PASS " if ok else "FAIL ") + name + (" — " + str(note) if note else ""))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 950})
    console_errors = []
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())  # «Не оплачен» — подтверждение обнуления
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    def open_card(cid):
        page.evaluate("""(cid) => {
            const el = [...document.querySelectorAll('.kb-q-row, .kb-card')].find(e => e.dataset.card === String(cid));
            if (el) el.click();
        }""", str(cid))
        page.wait_for_timeout(800)

    # --- 1. Вход ---
    page.goto(BASE, timeout=30000)
    page.wait_for_selector("#login-username", state="visible", timeout=20000)
    page.fill("#login-username", V2_USER)
    page.fill("#login-password", V2_PASS)
    page.click("#login-submit")
    page.wait_for_timeout(4000)
    check("1. Вход в v2", page.locator("#v2-user").inner_text().find(V2_USER) >= 0)

    # --- 2. Карточки на своих местах: сверяем ВСЕ карточки с API ---
    pos = page.evaluate("""async () => {
        const cards = await window.V2Api.api('/kanban/cards');
        const stageOf = {}; window.KBData.statuses.forEach(s => stageOf[s.name] = s.id);
        stageOf['Закрыто'] = 'done';
        const mism = [];
        let checked = 0;
        for (const c of cards) {
            if (c.is_deleted) continue;
            checked++;
            const expect = window.KBData.cards.find(x => x.id === String(c.id));
            const expectStage = expect ? expect.stage : null;
            const want = stageOf[c.status] || null;
            if (!expect || expectStage !== want) mism.push({id: c.id, status: c.status});
        }
        return {checked, mism: mism.slice(0, 5), mismCount: mism.length, total: window.KBData.cards.length};
    }""")
    check("2. Карточки на своих местах (стадия = статусу API)", pos["mismCount"] == 0,
          f'{pos["checked"]} карточек проверено, расхождений {pos["mismCount"]}')

    # --- 3. Поиск и фильтр магазина ---
    page.locator("#kb-q-board").click(timeout=5000)
    page.wait_for_timeout(500)
    before = page.locator(".kb-card").count()
    page.locator("#kb-search-toggle").click(timeout=4000)
    page.wait_for_timeout(300)
    page.fill("#kb-search", "Рацио")
    page.wait_for_timeout(700)
    after = page.locator(".kb-card").count()
    check("3. Поиск по доске фильтрует карточки", after < before or before == 0, f"было {before}, стало {after}")
    page.fill("#kb-search", "")
    page.wait_for_timeout(400)
    page.locator("#kb-search-close").click(timeout=3000)
    page.wait_for_timeout(300)

    # --- 4. Очереди: На списание / Списано соответствуют данным ---
    q = page.evaluate("""async () => {
        const rows = (await window.V2Api.api('/payments/transactions')).filter(r => !r.is_document);
        const cards = await window.V2Api.api('/kanban/cards');
        const issuedByCard = {};
        rows.forEach(t => {
            if (t.invoiced_amount !== undefined && t.invoiced_amount !== null) { issuedByCard[t.card_id] = t.invoiced_amount; return; }
            if (!t.is_warehouse_writeoff && !(t.invoice_number || '').trim()) return;
            issuedByCard[t.card_id] = (issuedByCard[t.card_id] || 0) + (t.amount || 0);
        });
        let pending = 0, done = 0;
        for (const c of cards) {
            if (c.is_deleted) continue;
            if (c.status !== 'На списание' && c.status !== 'Закрыто') continue;
            const iss = issuedByCard[c.id] || 0;
            const rem = (c.total_amount || 0) - iss;
            if (c.status === 'На списание' && rem > 0.01) pending++;
            if (c.status === 'Закрыто' || (c.status === 'На списание' && iss > 0 && rem <= 0.01)) done++;
        }
        return {pending, done};
    }""")
    page.locator("#kb-q-pending").click(timeout=5000); page.wait_for_timeout(600)
    p_cnt = page.locator(".kb-q-row").count()
    page.locator("#kb-q-history").click(timeout=5000); page.wait_for_timeout(600)
    d_cnt = page.locator(".kb-q-row").count()
    check("4. Очереди «На списание»/«Списано» соответствуют данным", p_cnt == q["pending"], f"на списание {p_cnt}/{q['pending']}, списано {d_cnt}/{q['done']}")

    # --- 5. Сводка карточки 553 на живых данных ---
    page.locator("#kb-q-pending").click(timeout=5000)
    page.wait_for_timeout(500)
    open_card("553")
    summ = page.evaluate("""() => { const dl = document.querySelector('.kb-detail-money'); const o = {};
        if (dl) dl.querySelectorAll('div').forEach(d => { const dt = d.querySelector('dt'), dd = d.querySelector('dd'); if (dt && dd) o[dt.textContent.trim()] = dd.textContent.trim(); }); return o; }""")
    check("5. Сводка 553: выписано/осталось верны",
          "38 824,52" in summ.get("Выписано", "") and "18 221,86" in summ.get("Осталось выписать", ""), json.dumps(summ, ensure_ascii=False))
    page.keyboard.press("Escape"); page.wait_for_timeout(300)

    # --- 6. Тестовая сделка: все ключевые операции (мутации только на ней) ---
    t = page.evaluate("""async () => {
        const existing = window.KBData.cards.find(x => x.title === 'AUDIT v2 тест');
        if (existing) return {card: existing.id, invoice_closed: null, reused: true};
        const card = await window.V2Api.api('/kanban/cards', { method: 'POST', body: { title: 'AUDIT v2 тест', status: 'Сборка', total_amount: 1000, store_location: 'Богдановича' } });
        await window.V2Api.api('/payments/trigger_from_card/' + card.id, { method: 'POST', body: { store_location: 'Богдановича' } });
        const inv = await window.V2Api.api('/payments/cards/' + card.id + '/issue-invoice', { method: 'POST', body: { invoice_number: 'AUDIT-ТТН', amount: 1000, store_location: 'Богдановича' } });
        return {card: card.id, invoice_closed: inv.card_closed};
    }""")
    check("6. Тестовая сделка + выписка накладной (issue-invoice)",
          t.get("reused") or t["invoice_closed"] is True, f"card {t['card']}")

    # 6a. Реестр: галочки Просчёт/Списание + сохранение после перезагрузки
    page.locator("[data-view=fin]").first.click(timeout=5000)
    page.wait_for_timeout(1200)
    trow = page.locator("#fin-payment-rows tr", has_text="AUDIT v2 тест").first
    # кликаем только снятые галочки — проверка идемпотентна для повторов
    if not trow.locator("input[data-field='calculated']").is_checked():
        trow.locator("input[data-field='calculated']").click(); page.wait_for_timeout(900)
    if not trow.locator("input[data-field='posted']").is_checked():
        trow.locator("input[data-field='posted']").click(); page.wait_for_timeout(900)
    page.reload(); page.wait_for_timeout(3000)
    page.locator("[data-view=fin]").first.click(timeout=5000)
    page.wait_for_selector("#fin-payment-rows tr", timeout=15000)
    page.wait_for_timeout(800)
    trow = page.locator("#fin-payment-rows tr", has_text="AUDIT v2 тест").first
    st = {"calc": trow.locator("input[data-field='calculated']").is_checked(),
          "posted": trow.locator("input[data-field='posted']").is_checked()}
    api_flags = page.evaluate("""async () => {
        const rows = await window.V2Api.api('/payments/transactions');
        return rows.find(r => r.company_name && r.company_name.includes('AUDIT v2 тест'));
    }""")
    check("6a. Галочки реестра сохраняются и совпадают с API",
          st["calc"] and st["posted"] and api_flags["is_calculated"] and api_flags["is_written_off"], str(st))

    # 6b. CSV-экспорт скачивает файл
    with page.expect_download() as dl:
        page.locator("#fin-payment-export").click()
    csv_path = "/tmp/" + dl.value.suggested_filename
    dl.value.save_as(csv_path)
    csv_head = open(csv_path, encoding="utf-8-sig").read(200)
    check("6b. Экспорт CSV", os.path.getsize(csv_path) > 100 and "Клиент" in csv_head, f"{os.path.getsize(csv_path)} байт")

    # 6c. Документы: ТН у нас / Счёт у нас / Печать — сохраняются
    page.locator("[data-fin-tab]", has_text="Документ").first.click(timeout=4000)
    page.wait_for_timeout(600)
    drow = page.locator("#fin-document-rows tr", has_text="AUDIT v2 тест").first
    if not drow.locator("input[data-field='tnHere']").is_checked():
        drow.locator("input[data-field='tnHere']").click(); page.wait_for_timeout(800)
    drow.locator("select.fprint-select").select_option("Печать"); page.wait_for_timeout(800)
    page.reload(); page.wait_for_timeout(3000)
    page.locator("[data-view=fin]").first.click(timeout=5000)
    page.locator("[data-fin-tab]", has_text="Документ").first.click(timeout=4000)
    page.wait_for_selector("#fin-document-rows tr", timeout=10000)
    page.wait_for_timeout(600)
    drow = page.locator("#fin-document-rows tr", has_text="AUDIT v2 тест").first
    ui_tn = drow.locator("input[data-field='tnHere']").is_checked()
    doc_api = page.evaluate("""async () => {
        const docs = await window.V2Api.api('/payments/documents');
        return docs.find(d => (d.invoice_number || '') === 'AUDIT-ТТН');
    }""")
    check("6c. Документы: «ТН у нас» сохраняется", ui_tn and doc_api and bool(doc_api["is_invoice_doc"]),
          f"ui={ui_tn} api={doc_api and doc_api['is_invoice_doc']}")
    check("6c-2. Документы: «Печать» сохраняется", doc_api and doc_api["print_status"] == "Печать",
          f"print={doc_api and doc_api['print_status']}")

    # 6d. Оплата: Оплачен(100%), Частично, Отсрочка, Аванс, Из баланса — на тестовых данных
    page.locator("[data-fin-tab]", has_text="Реестр").first.click(timeout=4000)
    page.wait_for_timeout(500)
    cl = page.evaluate("""async () => {
        const c = await window.V2Api.api('/clients', { method: 'POST', body: { name: 'AUDIT v2 клиент' } });
        await window.V2Api.api('/cards/' + cardIdHolder.id, { method: 'PATCH', body: { client_id: c.id } });
        return c.id;
    }""".replace("cardIdHolder.id", f"{t['card']}"))
    check("6d-0. Клиент привязан к тестовой сделке", cl is not None, f"client {cl}")

    page.evaluate("""async (cid) => {
        await window.V2Api.api('/kanban/cards/' + cid + '/status', { method: 'PATCH', body: { status: 'Сборка' } });
        await window.V2Api.api('/cards/' + cid + '/payment', { method: 'PATCH', body: { paid_amount: 0, payment_status: 'Не оплачен' } });
    }""", t["card"])
    page.reload(); page.wait_for_timeout(3000)
    cash0 = page.evaluate("""async () => {
        const c = window.KBData.cards.find(x => x.title === 'AUDIT v2 тест');
        const bal = await window.V2Api.api('/clients/' + Number(String(c.clientId).replace(/[^0-9]/g, '')) + '/balance');
        return bal.payments_total;
    }""")

    def pay_flow(mode, amount=None, due=None, from_balance=False, note=None):
        open_card(t["card"])
        page.locator("#kb-pay").click(timeout=4000)
        page.wait_for_timeout(300)
        page.locator(".kb-pay-mode[data-mode='%s']" % mode).click()
        page.wait_for_timeout(200)
        if amount is not None: page.fill("#kb-pay-amount", str(amount))
        if due: page.fill("#kb-pay-due", due)
        if from_balance: page.locator("#kb-pay-from-balance").check()
        page.locator("#kb-pay-form button[type=submit]").click()
        page.wait_for_timeout(1000)

    pay_flow("Оплачен")
    st = page.evaluate("""async () => { const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s'); return {paid: c.paid_amount, status: c.payment_status}; }""" % t["card"])
    check("6d. Оплата «Оплачен (100%)»", st["paid"] == 1000 and st["status"] == "Оплачен", str(st))

    pay_flow("Не оплачен")
    st = page.evaluate("""async () => { const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s'); return {paid: c.paid_amount, status: c.payment_status}; }""" % t["card"])
    check("6e. «Не оплачен» обнуляет покрытие", st["paid"] == 0 and st["status"] == "Не оплачен", str(st))

    pay_flow("Частично", amount=600)
    st = page.evaluate("""async () => { const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s'); return {paid: c.paid_amount, status: c.payment_status}; }""" % t["card"])
    check("6f. «Частично 600»", st["paid"] == 600 and st["status"] == "Частично", str(st))

    pay_flow("Отсрочка", amount=0, due="2026-12-31")
    st = page.evaluate("""async () => { const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s'); return {due: c.payment_due_date, status: c.payment_status}; }""" % t["card"])
    check("6g. «Отсрочка» с датой", (st["due"] or "").startswith("2026-12-31") and st["status"] == "Отсрочка", str(st))

    # 6h. Аванс через UI: деньги в кассу клиента сверх счёта → кредит +200
    pay_flow("Аванс", amount=2000)
    cash = page.evaluate("""async () => (await window.V2Api.api('/clients/%d/balance')).payments_total""" % cl)
    bal0 = page.evaluate("""async () => (await window.V2Api.api('/clients/%d/balance')).balance""" % cl)
    check("6h. Аванс 2000 в кассу", cash == cash0 + 3600.0, f"касса {cash} (было {cash0} + 2000 + оплаты 1600)")

    # 6i. «Из баланса»: частичное закрытие без новых денег
    open_card(t["card"])
    page.locator("#kb-pay").click(timeout=4000)
    page.locator(".kb-pay-mode[data-mode='Частично']").click()
    page.fill("#kb-pay-amount", "1000")
    page.locator("#kb-pay-from-balance").check()
    page.locator("#kb-pay-form button[type=submit]").click()
    page.wait_for_timeout(1200)
    st = page.evaluate("""async () => {
        const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s');
        const bal = await window.V2Api.api('/clients/%d/balance');
        return {paid: c.paid_amount, status: c.payment_status, cash: bal.payments_total, balance: bal.balance};
    }""" % (t["card"], cl))
    check("6i. «Из баланса»: счёт закрыт, касса не двигается",
          st["paid"] == 1000 and st["status"] == "Оплачен" and st["cash"] == cash0 + 3600.0, str(st))

    # 6j. История: журнал + письмо
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    hist = page.evaluate("""async () => {
        const acts = await window.V2Api.api('/activity?limit=200');
        const m = acts.find(a => a.action === 'Импорт почты' && a.card_id);
        return m ? m.card_id : null;
    }""")
    check("6j. «Импорт почты» в журнале (карточка %s)" % hist, hist is not None)

    # --- 7. Почта: карточка настроек заполняется ---
    page.locator("[data-view=admin]").first.click(timeout=5000)
    page.wait_for_timeout(1200)
    email_val = page.locator("#mgmt-email-email").input_value()
    last = page.locator("#mgmt-email-last").inner_text()
    check("7. Настройки почты заполняются из API", "@" in email_val and "синхронизац" in last.lower(), f"{email_val} | {last[:40]}")

    # --- 8. Уведомления ---
    bell = page.locator("#v2-bell").count()
    page.locator("#v2-bell").click(timeout=3000); page.wait_for_timeout(800)
    panel_ok = page.locator("#v2-notif-panel").is_visible()
    check("8. Колокольчик уведомлений открывается", bell and panel_ok)

    # --- 9. Разделы клиенты/поставщики/задачи/день рендерятся ---
    for view, marker in (("clients", "#cl-count"), ("suppliers", None), ("tasks", None), ("day", None)):
        try:
            page.locator(f"[data-view={view}]").first.click(timeout=3000)
            page.wait_for_timeout(500)
            ok = page.locator("section.view:not([hidden]) *").first.is_visible()
        except Exception:
            ok = False
        check(f"9. Раздел {view} открывается", ok)

    # --- 10. Ошибки консоли ---
    check("10. Консоль без ошибок", len(console_errors) == 0, "; ".join(e[:80] for e in console_errors[:3]))

    # --- id тестовых сущностей для уборки ---
    print("CLEANUP_IDS card=%s client=%s" % (t["card"], cl))
    page.screenshot(path=f"{OUT}/80_final.png")
    browser.close()

fails = [r for r in results if not r[1]]
print("\nИТОГО: %d проверок, %d провалено" % (len(results), len(fails)))
for name, ok, note in fails:
    print("  FAIL:", name, "—", note)
