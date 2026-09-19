#!/usr/bin/env python3
"""Перепись ошибок фронта v2 на РЕАЛЬНЫХ данных.

Мотив: на локальной dev-БД v2 работает, на прод-данных сыпется. Значит нужен
инструмент, который проходит по всем разделам v2 и фиксирует три класса проблем:
  1. ошибки JS (pageerror / console.error) — где код падает на форме реальных данных;
  2. сетевые провалы (status >= 400 или ошибка транспорта) — контракты клиент↔бэк;
  3. аномалии отображения и данных — «undefined» / «NaN» / «[object Object]» в DOM
     и дыры в window.KBData (null/undefined/NaN в полях, которые UI считает числами).

Запуск (логин — переменные окружения, в отчёт не попадают):
    CRM_USER=... CRM_PASS=... python3 tools/v2_error_census.py --base http://127.0.0.1:8125/v2/
    CRM_USER=... CRM_PASS=... python3 tools/v2_error_census.py --base http://87-232-64-12.nip.io/v2/ --read-only

--read-only (по умолчанию для прода) не кликает кнопки, которые могут что-то
изменить; без него дополнительно проверяются безопасные открытия (карточка,
строка реестра, вкладки). Скриншоты и JSON-отчёт — в --out-dir (по умолчанию
/tmp/crm_census).
"""
import argparse
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

VIEWS = ["board", "fin", "clients", "suppliers", "tasks", "day", "admin"]

# Текст кнопок, которые что-то меняют: в read-only их не трогаем.
MUTATING = re.compile(
    r"удалит|delete|сохран|save|оплатит|выписа|списа|создат|добав|отправ|импорт|"
    r"очист|восстанов|примен|подтверд|закрыть сделку|перенести|назнач|register|apply",
    re.I,
)

# Поля, которые UI трактует как деньги/числа: NaN/undefined здесь = баг отображения.
NUMERIC_HINTS = re.compile(r"amount|sum|total|paid|price|cost|remainder|balance|qty|count|position", re.I)

ANOMALY_TEXT = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]|\bnull\b")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8125/v2/")
    ap.add_argument("--out-dir", default="/tmp/crm_census")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--viewport", default="1600x950")
    ap.add_argument("--dark", action="store_true", help="пройти ещё и тёмной темой")
    ap.add_argument("--timeout", type=int, default=20000)
    args = ap.parse_args()

    user = os.environ.get("CRM_USER")
    password = os.environ.get("CRM_PASS")
    if not user or not password:
        print("Нужны CRM_USER и CRM_PASS в окружении", file=sys.stderr)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    base = args.base.rstrip("/") + "/"
    w, h = (int(x) for x in args.viewport.split("x"))

    js_errors = []      # (view, stage, text)
    net_failures = []   # dict: url, method, status, body
    api_calls = []      # dict: url, method, status, ms
    dom_anomalies = []  # (view, selector, text)
    data_anomalies = [] # (view, path, value)
    steps = []          # что делали и что увидели

    view = "login"
    stage = "init"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": w, "height": h})
        page.set_default_timeout(args.timeout)

        def on_pageerror(e):
            js_errors.append({"view": view, "stage": stage, "text": str(e)[:500]})

        def on_console(m):
            if m.type == "error":
                js_errors.append({"view": view, "stage": stage, "text": ("console: " + m.text)[:500]})

        def on_response(r):
            req = r.request
            url = r.url
            if "/v2/" in url and url.endswith((".js", ".css", ".html", ".png", ".woff2")):
                pass  # статика тоже интересна, но отдельно
            try:
                status = r.status
            except Exception:
                return
            entry = {"url": url, "method": req.method, "status": status, "view": view}
            api_calls.append(entry)
            if status >= 400:
                body = ""
                try:
                    body = r.text()[:400]
                except Exception:
                    body = "<no body>"
                net_failures.append({**entry, "body": body})

        def on_requestfailed(r):
            net_failures.append({
                "url": r.url, "method": r.method, "status": 0,
                "body": "transport: " + (r.failure or "?"), "view": view,
            })

        page.on("pageerror", on_pageerror)
        page.on("console", on_console)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)

        def snap(name):
            try:
                page.screenshot(path=os.path.join(args.out_dir, f"{name}.png"), full_page=False)
            except Exception as e:
                steps.append(f"скриншот {name} не снят: {e}")

        def click_soft(loc, what, ms=5000):
            """Клик с коротким таймаутом; если элемент перекрыт — диспатчим событие.

            Без этого один перекрытый элемент вешает обход раздела на 20 секунд.
            """
            try:
                loc.click(timeout=ms)
                return True
            except Exception:
                pass
            try:
                loc.dispatch_event("click")
                steps.append(f"{what}: обычный клик не прошёл, использован dispatch_event")
                return True
            except Exception as e:
                steps.append(f"{what}: клик не удался — {str(e)[:120]}")
                return False

        def scan_dom():
            """Видимые тексты с undefined/NaN/[object Object]."""
            try:
                found = page.evaluate(
                    """() => {
                        const bad = [];
                        const rx = /\\bundefined\\b|\\bNaN\\b|\\[object Object\\]/;
                        const els = document.querySelectorAll('#shell-content *, #kb-board *, .modal *, [id^=view-] *');
                        for (const el of els) {
                            if (el.children.length) continue;
                            const t = (el.textContent || '').trim();
                            if (!t || t.length > 200) continue;
                            if (!rx.test(t)) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 && r.height === 0) continue;
                            bad.push({sel: (el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+(el.className||'').toString().split(' ')[0]), text: t.slice(0,120)});
                            if (bad.length > 60) break;
                        }
                        return bad;
                    }"""
                )
                for f in found:
                    dom_anomalies.append({"view": view, **f})
            except Exception as e:
                steps.append(f"DOM-скан в {view} не выполнен: {str(e)[:120]}")

        def scan_data():
            """Дыры в KBData: числа, которые стали null/undefined/NaN."""
            try:
                found = page.evaluate(
                    """(hintSrc) => {
                        const hint = new RegExp(hintSrc, 'i');
                        const out = [];
                        const D = window.KBData;
                        if (!D) return [{path: 'KBData', value: 'отсутствует'}];
                        const walk = (obj, path, depth) => {
                            if (depth > 3 || obj === null || typeof obj !== 'object') return;
                            const items = Array.isArray(obj) ? obj.slice(0, 400) : null;
                            if (items) {
                                let n = 0;
                                for (const it of items) {
                                    if (it && typeof it === 'object') {
                                        for (const [k, v] of Object.entries(it)) {
                                            if (!hint.test(k)) continue;
                                            if (v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))) {
                                                out.push({path: path + '[].' + k, value: String(v), id: it.id});
                                                if (++n > 12) return;
                                            }
                                            if (typeof v === 'string' && hint.test(k) && v.trim() === '') {
                                                out.push({path: path + '[].' + k, value: "''", id: it.id});
                                                if (++n > 12) return;
                                            }
                                        }
                                    }
                                }
                                return;
                            }
                            for (const [k, v] of Object.entries(obj)) {
                                if (v && typeof v === 'object') walk(v, path + '.' + k, depth + 1);
                            }
                        };
                        walk(D, 'KBData', 0);
                        return out.slice(0, 80);
                    }""",
                    NUMERIC_HINTS.pattern,
                )
                for f in found:
                    data_anomalies.append({"view": view, **f})
            except Exception as e:
                steps.append(f"KBData-скан в {view} не выполнен: {str(e)[:120]}")

        # --- вход ---
        page.goto(base + "#board", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        try:
            page.wait_for_selector("#login-username", state="visible", timeout=10000)
            page.fill("#login-username", user)
            page.fill("#login-password", password)
            stage = "login"
            page.click("#login-submit")
            page.wait_for_function("() => document.getElementById('login-overlay')?.hidden !== false", timeout=15000)
        except Exception as e:
            steps.append(f"вход: {str(e)[:200]}")
        try:
            page.wait_for_function("() => !!window.KBData", timeout=25000)
            steps.append("KBData появился после входа")
        except Exception as e:
            steps.append(f"KBData НЕ появился: {str(e)[:160]}")
        page.wait_for_timeout(1200)
        try:
            src = page.text_content("#shell-source") or ""
            steps.append("индикатор источника: " + src.strip()[:80])
        except Exception:
            pass
        snap("00-login")

        # --- обход разделов ---
        themes = ["light"] + (["dark"] if args.dark else [])
        for theme in themes:
            if theme == "dark":
                page.evaluate("() => document.documentElement.setAttribute('data-theme','dark')")
            for v in VIEWS:
                view, stage = v, "navigate"
                try:
                    page.goto(base + "#" + v, wait_until="domcontentloaded")
                except Exception as e:
                    steps.append(f"{v}: goto {str(e)[:120]}")
                page.wait_for_timeout(1500)
                try:
                    vis = page.is_visible(f"#view-{v}")
                except Exception:
                    vis = False
                steps.append(f"{v}: виден={vis} тема={theme}")
                scan_dom()
                scan_data()
                snap(f"{theme}-{v}")

                if args.read_only:
                    continue

                # безопасные открытия внутри раздела
                stage = "interact"
                if v == "board":
                    # Несколько карточек подряд: на реальных данных формы записей
                    # разные (пустой клиент, нулевая сумма, старая дата) — падает
                    # обычно не первая.
                    for sel, label, howmany in (
                        (".kb-card", "карточка доски", 5),
                        (".kb-q-row", "строка очереди", 3),
                    ):
                        try:
                            n = page.locator(sel).count()
                        except Exception:
                            n = 0
                        if not n:
                            steps.append(f"board: {label} — нет элементов ({sel})")
                            continue
                        for i in range(min(howmany, n)):
                            loc = page.locator(sel).nth(i)
                            if not click_soft(loc, f"board: {label} #{i}"):
                                continue
                            page.wait_for_timeout(1000)
                            scan_dom()
                            scan_data()
                            snap(f"{theme}-board-{label}-{i}")
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(500)
                        steps.append(f"board: открыто {min(howmany, n)} из {n} — {label}")
                else:
                    # вкладки/переключатели внутри раздела — только не мутирующие
                    clicked = 0
                    try:
                        els = page.locator(
                            f"#view-{v} [data-handler], #view-{v} .tab, #view-{v} .kb-tab, #view-{v} button"
                        ).all()[:40]
                    except Exception as e:
                        els = []
                        steps.append(f"{v}: список кнопок не получен: {str(e)[:120]}")
                    for el in els:
                        try:
                            txt = (el.inner_text() or "").strip()[:40]
                            if not txt or MUTATING.search(txt) or not el.is_visible():
                                continue
                        except Exception:
                            continue
                        if not click_soft(el, f"{v}: «{txt}»"):
                            continue
                        page.wait_for_timeout(700)
                        clicked += 1
                        scan_dom()
                        if clicked >= 8:
                            break
                    steps.append(f"{v}: сделано безопасных кликов — {clicked}")
                    snap(f"{theme}-{v}-after")

        view, stage = "final", "dump"
        browser.close()

    report = {
        "base": base,
        "read_only": args.read_only,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": steps,
        "js_errors": js_errors,
        "net_failures": net_failures,
        "dom_anomalies": dom_anomalies,
        "data_anomalies": data_anomalies,
        "api_calls": api_calls,
    }
    out = os.path.join(args.out_dir, "census.json")
    with open(out, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # --- человекочитаемая сводка ---
    def uniq(items, key):
        seen, res = set(), []
        for it in items:
            k = key(it)
            if k in seen:
                continue
            seen.add(k)
            res.append(it)
        return res

    print("=" * 70)
    print(f"ПЕРЕПИСЬ ОШИБОК v2 — {base} (read_only={args.read_only})")
    print("=" * 70)
    print(f"\n-- Ошибки JS: {len(js_errors)} (уникальных {len(uniq(js_errors, lambda e: e['text']))}) --")
    for e in uniq(js_errors, lambda e: e["text"])[:25]:
        print(f"  [{e['view']}/{e['stage']}] {e['text'][:200]}")
    print(f"\n-- Сетевые провалы: {len(net_failures)} --")
    for f_ in uniq(net_failures, lambda x: (x['method'], x['url'], x['status']))[:30]:
        print(f"  {f_['status']} {f_['method']} {f_['url'][:120]} [{f_['view']}] :: {f_['body'][:160]}")
    print(f"\n-- Аномалии DOM (undefined/NaN): {len(dom_anomalies)} --")
    for a in uniq(dom_anomalies, lambda x: (x['view'], x['text']))[:25]:
        print(f"  [{a['view']}] {a.get('sel')} :: {a['text'][:110]}")
    print(f"\n-- Дыры в KBData: {len(data_anomalies)} --")
    for a in uniq(data_anomalies, lambda x: x['path'])[:25]:
        print(f"  [{a['view']}] {a['path']} = {a.get('value')} (id={a.get('id')})")
    print(f"\n-- Всего API-вызовов: {len(api_calls)} --")
    codes = {}
    for c in api_calls:
        codes[c["status"]] = codes.get(c["status"], 0) + 1
    print("  коды:", dict(sorted(codes.items())))
    print("\n-- Шаги --")
    for s in steps:
        print("  " + s[:200])
    print(f"\nПолный отчёт: {out}\nСкриншоты: {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
