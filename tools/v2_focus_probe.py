#!/usr/bin/env python3
"""Ловец консольных сообщений браузера со стеком — для точечных вопросов
вроде «An invalid form control … is not focusable».

Инструмент НЕ в гейт: гейтом остаётся tools/v2_error_census.py, который
считает pageerror и аномалии. Этот нужен только когда перепись дала
console-сообщение без стека и надо понять, чей это код.

Запуск (только по одному прогону за раз — стенд режет 10 логинов в минуту):

    CRM_USER=... CRM_PASS=... python3 tools/v2_focus_probe.py \
        --base http://127.0.0.1:8140/v2/ --view admin

--limit N — остановиться после N совпадений. --pattern — что ловить
(по умолчанию любое console-сообщение, кроме info/debug).
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

# Те же безопасные селекторы, что и в переписи: обычный click в этом стенде
# зависает (известная особенность), поэтому событие диспатчится напрямую.
CLICKABLE = "button, [role=button], a[href], summary, [data-nav], [data-tab], th[role=button]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8140/v2/")
    ap.add_argument("--user", default=os.environ.get("CRM_USER", ""))
    ap.add_argument("--password", default=os.environ.get("CRM_PASS", ""))
    ap.add_argument("--view", default="admin", help="хэш вида, например admin")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--pattern", default="", help="подстрока; пусто — любое")
    ap.add_argument("--click", default="", help="кликать только цели с этим текстом")
    args = ap.parse_args()

    if not args.user or not args.password:
        print("нужны CRM_USER и CRM_PASS", file=sys.stderr)
        return 2

    hits: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        def on_console(msg) -> None:
            if len(hits) >= args.limit:
                return
            if msg.type not in ("error", "warning"):
                return
            text = msg.text
            if args.pattern and args.pattern not in text:
                return
            loc = msg.location or {}
            hits.append({
                "type": msg.type,
                "text": text,
                "file": loc.get("url", ""),
                "line": loc.get("lineNumber"),
            })

        def on_error(exc) -> None:
            if len(hits) < args.limit:
                hits.append({"type": "pageerror", "text": str(exc)})

        page.on("console", on_console)
        page.on("pageerror", on_error)

        page.goto(args.base, wait_until="domcontentloaded")
        page.evaluate(
            """([u, p]) => {
                document.getElementById('login-username').value = u;
                document.getElementById('login-password').value = p;
                document.querySelector('#login-username').form.requestSubmit();
            }""",
            [args.user, args.password],
        )
        page.wait_for_function("() => !!document.querySelector('#kb-board, #view-board')", timeout=30000)
        page.evaluate("v => { location.hash = '#' + v; }", args.view)
        page.wait_for_timeout(1500)

        # Один безопасный клик по каждой доступной цели — ровно то, что делает
        # перепись, но без её длинного обхода: нужен только стек.
        target = args.click
        count = page.evaluate(
            """([sel, want]) => {
                const pick = (list) => want
                    ? list.filter(e => (e.textContent || '').trim().includes(want))
                    : list;
                const host = document.querySelector('#view-admin.active') || document;
                let els = Array.from(host.querySelectorAll(sel))
                    .filter(e => e.offsetParent !== null && !e.disabled);
                els = pick(els);
                els.slice(0, 12).forEach(e => e.dispatchEvent(
                    new MouseEvent('click', {bubbles: true, cancelable: true})));
                return els.length;
            }""",
            [CLICKABLE, target],
        )
        page.wait_for_timeout(2500)
        print(f"вид #{args.view}: прокликано целей — {count}")
        browser.close()

    print(f"\nсообщений: {len(hits)}")
    for h in hits:
        print(json.dumps(h, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
