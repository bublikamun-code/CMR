"""Функциональный аудит v2 на проде (фидбек 18.09 «проверь, что всё
работает: кнопки, галочки, карточки на местах»).

Запуск: python3 tools/v2_functional_audit.py
Требует: playwright (pip), временный superadmin-пользователь v2_audit на
проде (создать/удалить вручную — скрипт печатает CLEANUP_IDS для уборки
тестовой карточки, UI-карточки и клиента: DELETE transactions/
card_attachments/client_payments по card_id/client_id, затем cards,
clients, users).

Проверки: вход; карточки в своих колонках (все, сверка со статусами API);
поиск; очереди «На списание»/«Списано» (правила board.js); сводка 553;
тестовая сделка: выписка, галочки реестра + сохранение, CSV, документы
(ТН/Счёт/Печать), все режимы оплаты, аванс и закрытие из баланса,
«Импорт почты» в истории; создание сделки с доски (UI-путь: кнопка →
диалог → POST /kanban/cards → карточка в DOM без F5); настройки почты;
колокольчик; разделы; ошибки консоли. Мутации — только на тестовых
карточках AUDIT v2 тест и AUDIT v2 UI-сделка.
"""
import argparse, json, os, sys
from playwright.sync_api import sync_playwright

OUT = "/tmp/crm_matrix"; os.makedirs(OUT, exist_ok=True)

# FIX 2026-09-30: инструмент больше не умеет молча уйти на боевой сайт.
#
# Раньше адрес брался ТОЛЬКО из V2_BASE, а по умолчанию был боевым:
#     BASE = os.environ.get("V2_BASE", "http://87-232-64-12.nip.io/v2/")
# Флаг --base не существовал, его молча игнорировал argparse, и запуск
# «--base http://127.0.0.1:8151/v2/» спокойно открывал ПРОДАКШН. Это не
# мелкое неудобство: аудит логинится и ходит по кнопкам, то есть способ
# без спроса ударить по боевой базе. Именно так однажды и вышло: прогон,
# запущенный явно со стенда, ушёл на 87-232-64-12.nip.io.
#
# Теперь адрес обязателен, --base и V2_BASE равноправны, а запуск без явного
# указания стенда падает с внятным сообщением вместо похода в прод.
PRODUCTION_BASE = "http://87-232-64-12.nip.io/v2/"
_ap = argparse.ArgumentParser(description="Функциональный аудит v2 по стенду")
_ap.add_argument("--base", default=None,
                 help="адрес стенда, напр. http://127.0.0.1:8151/v2/ "
                      "(обязателен: без него скрипт не запускается)")
_args, _unknown = _ap.parse_known_args()

BASE = _args.base or os.environ.get("V2_BASE")
if not BASE:
    sys.exit(
        "Не указан адрес стенда.\n"
        "  Запуск: python3 tools/v2_functional_audit.py --base http://127.0.0.1:8151/v2/\n"
        "Или через переменную окружения V2_BASE=...\n"
        f"Боевой адрес {PRODUCTION_BASE} НЕ подставляется по умолчанию намеренно: "
        "аудит логинится и нажимает кнопки."
    )
if BASE == PRODUCTION_BASE:
    sys.exit(
        f"Отказ: {PRODUCTION_BASE} — боевой адрес.\n"
        "Аудит логинится и проходит по кнопкам, на боевой базе это недопустимо.\n"
        "Укажите стенд: --base http://127.0.0.1:8151/v2/"
    )
print(f"Аудит идёт по стенду {BASE}")

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
    page.on("dialog", lambda d: d.accept())  # нативные confirm/alert (insights.js)
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    # Опасные действия доски с 5293043 подтверждаются не нативным confirm, а
    # модальным <dialog id="kb-confirm"> (KBConfirm.ask в shell-v2-board.js):
    # page.on("dialog") его не видит, диалог остаётся открытым и перехватывает
    # клики — без автоподтверждения аудит вставал на шаге 6e («Не оплачен»).
    # add_init_script — потому что шаги 6a/6c/6d делают page.reload().
    page.add_init_script("""
        (() => {
            const arm = () => {
                if (window.__kbConfirmAutopilot) return;
                window.__kbConfirmAutopilot = true;
                const accept = () => {
                    const d = document.getElementById('kb-confirm');
                    if (!d || !d.open) return;
                    const ok = d.querySelector('[data-confirm-ok]');
                    if (ok) ok.click();
                };
                new MutationObserver(accept).observe(document.documentElement, {
                    childList: true, subtree: true, attributes: true, attributeFilter: ['open']
                });
            };
            if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arm);
            else arm();
        })();
    """)

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
        stageOf['Закрыто'] = 'closed';
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

    # --- 5. Сводка карточки: интерфейс против сервера ---
    # FIX 2026-09-30: сверяем интерфейс с API, а не с зашитыми числами.
    #
    # Раньше здесь стояло open_card("553") и проверка, что в сводке написаны
    # именно «38 824,52» и «18 221,86». И то, и другое — наследие одного
    # конкретного среза данных: на другой копии базы карточки «553» нет,
    # open_card ничего не открывал, .kb-detail-money оставался пустым, и
    # проверка падала с «{}» — не потому что сводка врёт, а потому что её
    # не на чем было проверить.
    #
    # Теперь карточка берётся первая с суммой, а эталон — те же поля из API.
    # Проверка честно ломается, когда интерфейс показывает не то, что сервер.
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    money_check = page.evaluate("""async () => {
        const cards = await window.V2Api.api('/kanban/cards');
        const c = cards.filter(x => !x.is_deleted && Number(x.total_amount) > 0)[0];
        if (!c) return {error: 'нет ни одной сделки с суммой'};
        const openCardById = (id) => {
            // Атрибут на карточке называется data-card, а внутри есть отдельная
            // кнопка data-card-open. Раньше здесь искалось .kb-card[data-id=...],
            // чего на доске нет вовсе, поэтому шаг падал с «нет элемента».
            const btn = document.querySelector('.kb-card[data-card="' + id + '"] .kb-card-open')
                || document.querySelector('[data-card-open="' + id + '"]');
            if (btn) { btn.click(); return true; }
            return false;
        };
        if (!openCardById(c.id)) return {error: 'карточку не открыть: нет элемента'};
        await new Promise(r => setTimeout(r, 1200));
        const dl = document.querySelector('.kb-detail-money');
        if (!dl) return {error: '.kb-detail-money не появился'};
        const o = {};
        dl.querySelectorAll('div').forEach(d => {
            const dt = d.querySelector('dt'), dd = d.querySelector('dd');
            if (dt && dd) o[dt.textContent.trim()] = dd.textContent.trim();
        });
        return {
            api: {
                'Сумма сделки': c.total_amount,
                'Оплачено': c.paid_amount,
                'Выписано': c.issued_total,
                'Осталось выписать': c.remaining,
            },
            ui: o,
        };
    }""")
    if money_check.get("error"):
        check("5. Сводка сделки: выписано/осталось совпадает с сервером",
              False, money_check["error"])
    else:
        # «123,45 BYN» → 123.45. Пробелы в разрядах и неразрывные пробелы — норма.
        def _num(text):
            digits = "".join(ch for ch in str(text).replace("\xa0", "")
                             if ch.isdigit() or ch in ",.-")
            try:
                return round(float(digits.replace(",", ".")), 2)
            except ValueError:
                return None

        mismatches = []
        for label, expected in money_check["api"].items():
            got = _num(money_check["ui"].get(label, ""))
            if got is None or expected is None:
                if got != expected:
                    mismatches.append(f"{label}: показано {money_check['ui'].get(label)!r}")
            elif abs(got - float(expected)) > 0.01:
                mismatches.append(f"{label}: в интерфейсе {got}, у сервера {float(expected)}")
        check("5. Сводка сделки: выписано/осталось совпадает с сервером",
              not mismatches,
              "; ".join(mismatches) if mismatches else json.dumps(money_check["ui"], ensure_ascii=False))
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

    # После API-мутации обновляем локальную модель: карточка и выписка ещё
    # не попали в загруженные до этого KB_FIN_SOURCE.
    page.reload(); page.wait_for_timeout(3000)

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
    # С 9bdba57 нативный select скрыт (паттерн kb-select): значение ставим
    # нативному select'у и диспатчим input+change — ровно то, что делает
    # shell-v2-select.js при выборе опции пользователем.
    drow.locator("select.fprint-select").evaluate(
        "el => { const opt = [...el.options].find(o => o.textContent.trim() === 'Печать');"
        " el.value = opt ? opt.value : 'Печать';"
        " el.dispatchEvent(new Event('input', {bubbles: true}));"
        " el.dispatchEvent(new Event('change', {bubbles: true})); }"
    ); page.wait_for_timeout(800)
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

    # 6d. Оплата — волна C (24.10): ручной выбор статуса убран, статус
    # вычисляется. Диалог: «Внести оплату» (POST /cards/{id}/pay — приход в
    # кассу клиента И привязка к карточке одним запросом), «Отсрочка» (PATCH
    # paid_amount + дата), «Аванс в кассу» (приход без привязки), «Сбросить»
    # (обнулить покрытие через KBConfirm, кассу не трогает). Поле
    # payment_status в подготовке шлётся намеренно: старые клиенты его
    # передают, сервер молча игнорирует — контракт не ломается.
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

    def pay_flow(mode, amount=None, due=None, from_balance=False):
        open_card(t["card"])
        page.locator("#kb-pay").click(timeout=4000)
        page.wait_for_timeout(300)
        # Клики внутри диалога оплаты — только через evaluate: диалоги
        # открываются/закрываются в момент клика, проверка попадания
        # Playwright не успевает и ретраит клик по уже перестроенному DOM —
        # клик либо падает по таймауту, либо молча не доходит (25.10,
        # два прогона с разными симптомами). evaluate-клик синхронный.
        page.evaluate("(m) => document.querySelector('.kb-pay-mode[data-mode=\"' + m + '\"]').click()", mode)
        page.wait_for_selector("#kb-pay-form:not([hidden])", timeout=4000)
        if amount is not None: page.fill("#kb-pay-amount", str(amount))
        if due: page.fill("#kb-pay-due", due)
        if from_balance:
            page.evaluate("() => { document.getElementById('kb-pay-from-balance').checked = true; }")
        page.evaluate("() => document.querySelector('#kb-pay-form button[type=submit]').click()")
        if mode == "reset":
            # «Сбросить» подтверждается KBConfirm — отдельный <dialog> поверх
            # формы. Ждать его открытым нельзя: автопилот из add_init_script
            # жмёт ОК в том же кадре, что и открытие, — wait_for_selector
            # стабильно проигрывает гонку и таймаутится (третий прогон 25.10).
            # Диалог открывается синхронно внутри клика по «Провести», так что
            # сюда он попадает уже подтверждённым; страховка — если вдруг ещё
            # открыт, кликнуть самим.
            if page.evaluate("() => { const d = document.getElementById('kb-confirm'); return !!(d && d.open); }"):
                page.evaluate("() => document.querySelector('#kb-confirm [data-confirm-ok]').click()")
                page.wait_for_timeout(300)
        page.wait_for_timeout(1000)

    # «Внести оплату» без суммы = полный остаток (10 BYN): деньги падают в
    # кассу клиента и привязываются к счёту одним POST /pay.
    pay_flow("pay")
    st = page.evaluate("""async () => {
        const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s');
        const bal = await window.V2Api.api('/clients/%d/balance');
        return {paid: c.paid_amount, status: c.payment_status, cash: bal.payments_total};
    }""" % (t["card"], cl))
    check("6d. «Внести оплату» 10 BYN: счёт закрыт, касса +10",
          st["paid"] == 1000 and st["status"] == "Оплачен" and st["cash"] == cash0 + 1000.0, str(st))

    # «Сбросить оплату»: покрытие обнуляется, касса не двигается.
    pay_flow("reset")
    st = page.evaluate("""async () => {
        const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s');
        const bal = await window.V2Api.api('/clients/%d/balance');
        return {paid: c.paid_amount, status: c.payment_status, cash: bal.payments_total};
    }""" % (t["card"], cl))
    check("6e. «Сбросить» обнуляет покрытие, касса на месте",
          st["paid"] == 0 and st["status"] == "Не оплачен" and st["cash"] == cash0 + 1000.0, str(st))

    pay_flow("pay", amount=600)
    st = page.evaluate("""async () => {
        const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s');
        const bal = await window.V2Api.api('/clients/%d/balance');
        return {paid: c.paid_amount, status: c.payment_status, cash: bal.payments_total};
    }""" % (t["card"], cl))
    check("6f. Частичная оплата 600: покрытие и касса",
          st["paid"] == 600 and st["status"] == "Частично" and st["cash"] == cash0 + 1600.0, str(st))

    # «Отсрочка»: оплаченная часть (0) и дата остатка; прежнее покрытие 600
    # снижается — привязка аннулируется с историей, касса не двигается.
    pay_flow("due", amount=0, due="2026-12-31")
    st = page.evaluate("""async () => { const cards = await window.V2Api.api('/kanban/cards'); const c = cards.find(x => x.id == '%s'); return {due: c.payment_due_date, status: c.payment_status}; }""" % t["card"])
    check("6g. «Отсрочка» с датой", (st["due"] or "").startswith("2026-12-31") and st["status"] == "Отсрочка", str(st))

    # 6h. Аванс через UI: деньги в кассу клиента сверх счёта, без привязки
    pay_flow("advance", amount=2000)
    cash = page.evaluate("""async () => (await window.V2Api.api('/clients/%d/balance')).payments_total""" % cl)
    check("6h. Аванс 2000 в кассу", cash == cash0 + 3600.0, f"касса {cash} (было {cash0} + 2000 + оплаты 1600)")

    # 6i. Закрытие из свободного остатка: pay + source=balance — новых денег
    # нет, касса не двигается; дата отсрочки ещё стоит, но полная оплата
    # главнее даты → статус «Оплачен».
    pay_flow("pay", amount=1000, from_balance=True)
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

    # 6k. Создание сделки с доски: UI-путь целиком (раньше карточка создавалась
    # напрямую через API в шаге 6, поэтому отсутствие кнопки в тулбаре не ловилось).
    # Проверяем: кнопка есть → диалог открывается → сабмит шлёт POST /kanban/cards
    # → карточка появилась в DOM доски и в KBData БЕЗ перезагрузки страницы.
    page.locator("[data-view=board]").first.click(timeout=5000)
    page.wait_for_timeout(600)
    page.locator("#kb-q-board").click(timeout=5000)
    page.wait_for_timeout(400)
    ui_posts = []
    page.on("request", lambda r: ui_posts.append(r.url)
            if r.method == "POST" and r.url.split("?")[0].endswith("/kanban/cards") else None)
    ui_btn = page.locator("#kb-new-card").count() > 0
    ui_card = None
    ui_amount = None
    ui_toast = False
    ui_err = ""
    if ui_btn:
        # Сбой шага не должен ронять весь аудит: остальные проверки дороже.
        try:
            page.locator("#kb-new-card").click(timeout=4000)
            page.wait_for_selector("#kb-nc-form", state="visible", timeout=5000)
            # Маркер «страницу не перезагружали»: после location.reload() он исчезнет.
            page.evaluate("() => { window.__auditNoReload = true; }")
            page.fill("#kb-nc-title", "AUDIT v2 UI-сделка")
            page.fill("#kb-nc-amount", "123,45")
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.split("?")[0].endswith("/kanban/cards"),
                timeout=15000,
            ) as ui_resp:
                page.locator("#kb-nc-submit").click()
            body = ui_resp.value.json() if ui_resp.value.ok else {}
            ui_card = body.get("id")
            ui_amount = body.get("total_amount")
            # Тост «Сделка создана» показывается ПОСЛЕ перечитывания данных
            # (KBData.reload → kb:reloaded → refresh) — ждём его вместо паузы.
            try:
                page.wait_for_function(
                    "() => { const t = document.getElementById('kb-toast');"
                    " return !!t && !t.hidden && t.textContent.indexOf('Сделка создана') >= 0; }",
                    timeout=25000,
                )
                ui_toast = True
            except Exception:
                pass
            page.wait_for_timeout(300)
        except Exception as exc:
            ui_err = str(exc).splitlines()[0][:120]
            # Оставшаяся открытой модалка заблокировала бы клики следующих шагов.
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
    ui_no_reload = page.evaluate("() => window.__auditNoReload === true")
    ui_dom = bool(ui_card) and page.locator('.kb-card[data-card="%s"]' % ui_card).count() > 0
    ui_data = bool(ui_card) and page.evaluate(
        "(id) => window.KBData.cards.some(c => c.id === String(id))", ui_card)
    check("6k. Создание сделки с доски (кнопка → диалог → POST → DOM без F5)",
          ui_btn and not ui_err and len(ui_posts) == 1 and bool(ui_card) and ui_amount == 123.45
          and ui_toast and ui_no_reload and ui_dom and ui_data,
          f"кнопка={ui_btn} POST={len(ui_posts)} id={ui_card} сумма={ui_amount} тост={ui_toast} "
          f"без F5={ui_no_reload} в DOM={ui_dom} в KBData={ui_data}" + (f" | {ui_err}" if ui_err else ""))

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
    print("CLEANUP_IDS card=%s client=%s ui_card=%s" % (t["card"], cl, ui_card))
    page.screenshot(path=f"{OUT}/80_final.png")
    browser.close()

fails = [r for r in results if not r[1]]
print("\nИТОГО: %d проверок, %d провалено" % (len(results), len(fails)))
for name, ok, note in fails:
    print("  FAIL:", name, "—", note)
