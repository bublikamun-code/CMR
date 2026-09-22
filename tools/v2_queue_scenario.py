#!/usr/bin/env python3
"""Стенд-сценарий очередей списания v2 (пункт 6 плана + дефект 7 реестра).

Прогоняет на стенде с копией прод-БД:
  1) очередь «На списание» → частичная выписка ТН → отмена ТН:
     серверные коды, жизненный цикл документа, возврат карточки в очередь
     (провал последнего = дефект 7 реестра V2-WORKPLAN-2026-09-22);
  2) «ТН у нас»: персистентность флага оригинала через перезагрузку
     + скриншоты обеих тем (замерный пробел пункта 2).

Запуск (браузерное окно должно быть свободным — правило 2 раздела
«Параллелизация пунктов» V2-WORKPLAN):
    CRM_USER=... CRM_PASS=... python3 tools/v2_queue_scenario.py [--base URL]
    SKIP_PART1=1 — только часть 2 (без мутаций выписки/отмены)
    SKIP_PART2=1 — только часть 1

Мутации стенда нетто-нулевые: частичная выписка плюс её отмена; флаг
оригинала возвращается в исходное состояние. Карточка-жертва берётся
первой из очереди «На списание» с остатком.
"""
import os
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get("V2_BASE", "http://127.0.0.1:8126/v2/")
USER = os.environ.get("CRM_USER")
PASS = os.environ.get("CRM_PASS")
SHOTS = str(REPO / "gui-test-screenshots")

if not USER or not PASS:
    print("Нужны CRM_USER и CRM_PASS в окружении", file=sys.stderr)
    sys.exit(2)

results = []
def check(name, ok, note=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(note)) if note else ""))

def close_dialog(page):
    if page.locator("#kb-dialog[open]").count():
        page.click("#kb-dialog .kb-detail-close")
        page.wait_for_timeout(250)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "#board", wait_until="domcontentloaded")
    page.wait_for_selector("#login-username", state="visible", timeout=10000)
    page.fill("#login-username", USER)
    page.fill("#login-password", PASS)
    page.click("#login-submit")
    page.wait_for_function("() => document.getElementById('login-overlay')?.hidden !== false", timeout=15000)
    page.wait_for_load_state("networkidle")

    # --- часть 1: очередь «На списание» -> частичная выписка -> отмена
    skip1 = os.environ.get("SKIP_PART1") == "1"
    page.click("#kb-q-pending")
    page.wait_for_selector(".kb-q-row[data-card]", timeout=10000)
    ids = [] if skip1 else page.eval_on_selector_all(".kb-q-row[data-card]", "els => els.map(e => e.getAttribute('data-card'))")
    issued = False
    number = "VERIFY06-%d" % (int(time.time()) % 100000)
    for cid in ids[:6]:
        page.click('.kb-q-row[data-card="%s"]' % cid)
        page.wait_for_selector("#kb-dialog[open]", timeout=5000)
        if page.locator("#kb-issue").count():
            issued = True
            break
        close_dialog(page)
    check("очередь «На списание»: найдена карточка с остатком", issued, cid if issued else ids[:6])
    if issued:
        page.click("#kb-issue")
        page.wait_for_selector("#kb-issue-form", timeout=5000)
        amount_full = page.input_value("#kb-amount")
        half = str(round(float(amount_full.replace(" ", "").replace(",", ".")) / 2, 2)).replace(".", ",")
        page.fill("#kb-date", date.today().isoformat())
        page.fill("#kb-series", "ТСТ")
        page.fill("#kb-number", number)
        page.fill("#kb-amount", half)
        t0 = time.time()
        with page.expect_response(lambda r: "issue-invoice" in r.url and r.request.method == "POST", timeout=60000) as resp_issue:
            page.click("#kb-issue-form button[type=submit]")
        print("INFO issue-invoice latency, s: %s status: %s" % (round(time.time() - t0, 1), resp_issue.value.status))
        t1 = time.time()
        try:
            page.wait_for_function("() => document.getElementById('kb-dialog')?.open !== true || !document.getElementById('kb-issue-form')", timeout=15000)
            form_gone = True
        except Exception:
            form_gone = False
        print("INFO dialog close wait, s: %s" % round(time.time() - t1, 1))
        check("частичная выписка прошла (диалог формы закрыт)", form_gone)
        close_dialog(page)
        still = page.locator('.kb-q-row[data-card="%s"]' % cid).count()
        if not still:
            page.click("#kb-q-pending")
            page.wait_for_timeout(400)
            still = page.locator('.kb-q-row[data-card="%s"]' % cid).count()
        check("после частичной выписки карточка осталась в очереди", still > 0)
        page.click('.kb-q-row[data-card="%s"]' % cid)
        page.wait_for_selector("#kb-dialog[open]", timeout=5000)
        page.click("#kb-tab-invoices")
        page.wait_for_timeout(400)
        row = page.locator(".kb-detail-doc", has_text=number)
        check("новая ТН видна в «Накладных»", row.count() == 1)
        row.locator("[data-doc-cancel]").click()
        page.wait_for_timeout(300)
        t0 = time.time()
        with page.expect_response(lambda r: "/payments/transactions/" in r.url and r.request.method == "DELETE", timeout=60000) as resp_del:
            row.locator("[data-doc-cancel]").click()
        print("INFO annul latency, s: %s status: %s" % (round(time.time() - t0, 1), resp_del.value.status))
        page.wait_for_timeout(1200)
        check("отмена ТН: строка исчезла", page.locator(".kb-detail-doc", has_text=number).count() == 0)
        close_dialog(page)
        page.wait_for_timeout(800)
        page.click("#kb-q-pending")
        page.wait_for_timeout(600)
        in_pending = page.locator('.kb-q-row[data-card="%s"]' % cid).count() > 0
        where = "pending" if in_pending else ""
        if not in_pending:
            page.click("#kb-q-history")
            page.wait_for_timeout(600)
            where = "history" if page.locator('.kb-q-row[data-card="%s"]' % cid).count() else "нигде в очередях"
        check("после отмены карточка снова в очереди «На списание» (дефект 7)", in_pending, where)
        diag = page.evaluate("""(cid) => {
            const c = (window.KBData.cards || []).find(x => String(x.id) === String(cid));
            if (!c) return 'no card in KBData';
            return {stage: c.stage, issued: c.issued, remaining_kop: c.remaining_kop,
                    issued_total: c.issued_total,
                    docs: (c.docs || []).map(d => d.series + ' ' + d.number + ' ' + d.amount)};
        }""", cid)
        print("INFO card state after cancel:", diag)
        page.screenshot(path=SHOTS + "/queue-scenario-after-cancel.png")

    # --- часть 2: «ТН у нас» персистентность + темы
    skip2 = os.environ.get("SKIP_PART2") == "1"
    page.click("#kb-q-history")
    page.wait_for_timeout(600)
    card_ids = [] if skip2 else page.eval_on_selector_all(".kb-q-row[data-card]", "els => els.map(e => e.getAttribute('data-card'))")
    row_sel = '.kb-q-row[data-card="%s"]'
    if not card_ids and not skip2:
        page.click("#kb-q-board")
        page.wait_for_timeout(400)
        card_ids = page.eval_on_selector_all(".kb-card[data-card]", "els => els.map(e => e.getAttribute('data-card'))")
        row_sel = '.kb-card[data-card="%s"]'
    found = False
    for cc in card_ids[:10]:
        page.click(row_sel % cc)
        page.wait_for_selector("#kb-dialog[open]", timeout=5000)
        page.click("#kb-tab-invoices")
        page.wait_for_timeout(400)
        if page.locator("[data-originals]").count():
            found = True
            break
        close_dialog(page)
    if not skip2:
        check("найдена карточка с документами и чекбоксом оригинала", found, cc if found else "")
    if found:
        box = page.locator("[data-originals]").first
        before = box.is_checked()
        box.click()
        page.wait_for_timeout(800)
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".kb-card[data-card], .kb-q-row[data-card]", state="attached", timeout=10000)
        page.wait_for_timeout(800)
        if page.locator(row_sel % cc).count() == 0:
            page.click("#kb-q-board")
            page.wait_for_timeout(500)
        if page.locator(row_sel % cc).count() == 0:
            page.click("#kb-q-history")
            page.wait_for_timeout(600)
        page.click(row_sel % cc)
        page.wait_for_selector("#kb-dialog[open]", timeout=5000)
        page.click("#kb-tab-invoices")
        page.wait_for_timeout(400)
        after = page.locator("[data-originals]").first.is_checked()
        check("флаг «ТН у нас» пережил перезагрузку", after != before, (before, after))
        page.screenshot(path=SHOTS + "/tnhere-light.png")
        page.evaluate("() => { localStorage.setItem('kb-theme', 'dark'); document.documentElement.dataset.theme = 'dark'; }")
        page.wait_for_timeout(300)
        page.screenshot(path=SHOTS + "/tnhere-dark.png")
        page.evaluate("() => { localStorage.setItem('kb-theme', 'light'); document.documentElement.dataset.theme = 'light'; }")
        # вернуть флаг в исходное состояние, стенд не засоряем
        page.locator("[data-originals]").first.click()
        page.wait_for_timeout(800)
        close_dialog(page)

    check("нет pageerror за прогон", not errors, errors[:3])
    browser.close()
sys.exit(0 if all(results) else 1)
