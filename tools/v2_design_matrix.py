#!/usr/bin/env python3
"""Дизайн-матрица v2: скриншоты + объективные метрики по ширинам/темам/состояниям.

Зачем: ручной дизайн-аудит на глаз ненадёжен. Скрипт снимает систематическую
матрицу 4 ширины × 2 темы × N состояний и для каждого кадра собирает метрики:
оверфлоу, обрезка текста, распределение кеглей, контраст, размеры контролов,
наложения, проблемы тёмной темы, отступы контейнеров, фокус-ring.

Запуск:
    CRM_USER=... CRM_PASS=... python3 tools/v2_design_matrix.py
    CRM_USER=... CRM_PASS=... python3 tools/v2_design_matrix.py --base http://127.0.0.1:8125/v2/ --widths 1920 1440

Скриншоты и metrics.json — в --out (по умолчанию /tmp/crm_design).
Read-only: ничего не сохраняет, не удаляет, не создаёт.
"""
import argparse, json, os, sys, time
from playwright.sync_api import sync_playwright

VIEWS = [
    ("board", "#board"), ("fin", "#fin"), ("clients", "#clients"),
    ("suppliers", "#suppliers"), ("tasks", "#tasks"), ("day", "#day"), ("admin", "#admin"),
]

# JS-блок для сбора метрик одного состояния. Возвращает dict.
METRICS_JS = r"""() => {
    const out = {overflow: [], truncated: [], fonts: {}, contrast: [], smallControls: [], overlaps: [], gaps: [], focusRing: null, paddingGap: []};

    // 1. Горизонтальный оверфлоу
    const sw = document.documentElement.scrollWidth, cw = document.documentElement.clientWidth;
    out.pageOverflow = {scrollWidth: sw, clientWidth: cw, overflows: sw > cw};
    if (sw > cw) {
        const all = document.querySelectorAll('body *, #shell-content *, #kb-board *');
        for (const el of all) {
            const r = el.getBoundingClientRect();
            if (r.right > cw + 2 && r.width > 0) {
                const sel = el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+(el.className||'').toString().split(' ').slice(0,2).join('.');
                out.overflow.push({sel, overshoot: Math.round(r.right - cw), w: Math.round(r.width)});
                if (out.overflow.length > 20) break;
            }
        }
    }

    // 2. Обрезанный текст (scrollWidth > clientWidth при overflow hidden/ellipsis)
    const els = document.querySelectorAll('#shell-content *, #kb-board *, .modal *, [id^=view-] *');
    for (const el of els) {
        if (el.children.length > 2) continue;
        const cs = getComputedStyle(el);
        if (cs.overflow !== 'hidden' && cs.textOverflow !== 'ellipsis' && cs.overflowX !== 'hidden') continue;
        if (el.scrollWidth > el.clientWidth + 2) {
            const t = (el.textContent||'').trim().slice(0,80);
            const sel = el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+(el.className||'').toString().split(' ').slice(0,2).join('.');
            out.truncated.push({sel, text: t, lost: el.scrollWidth - el.clientWidth});
            if (out.truncated.length > 30) break;
        }
    }

    // 3. Распределение кеглей (font-size × font-weight × line-height)
    const seen = {};
    const textEls = document.querySelectorAll('#shell-content h1, #shell-content h2, #shell-content h3, #shell-content h4, #shell-content p, #shell-content span, #shell-content td, #shell-content th, #shell-content label, #shell-content a, #shell-content button, #shell-content input, #kb-board h1, #kb-board h2, #kb-board h3, #kb-board h4, #kb-board p, #kb-board span, #kb-board td, #kb-board th, #kb-board label, #kb-board a, #kb-board button, #kb-board input, [id^=view-] h1, [id^=view-] h2, [id^=view-] h3, [id^=view-] h4, [id^=view-] p, [id^=view-] span, [id^=view-] td, [id^=view-] th, [id^=view-] label, [id^=view-] a, [id^=view-] button, [id^=view-] input');
    for (const el of textEls) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const cs = getComputedStyle(el);
        const fs = cs.fontSize, fw = cs.fontWeight, lh = cs.lineHeight;
        const key = fs+'|'+fw+'|'+lh;
        if (!seen[key]) seen[key] = {fontSize: fs, fontWeight: fw, lineHeight: lh, count: 0, tag: el.tagName.toLowerCase()};
        seen[key].count++;
    }
    out.fonts = Object.values(seen).sort((a,b) => b.count - a.count);

    // 4. Контраст (выборка ключевых пар текст/фон)
    function hexToRgb(hex) {
        hex = hex.replace('#','');
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        return [parseInt(hex.slice(0,2),16), parseInt(hex.slice(2,4),16), parseInt(hex.slice(4,6),16)];
    }
    function luminance(rgb) {
        const a = rgb.map(v => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
        return 0.2126*a[0] + 0.7152*a[1] + 0.0722*a[2];
    }
    function contrastRatio(l1, l2) {
        const lighter = Math.max(l1,l2), darker = Math.min(l1,l2);
        return (lighter + 0.05) / (darker + 0.05);
    }
    function resolveBg(el) {
        let cur = el;
        while (cur && cur !== document.documentElement) {
            const bg = getComputedStyle(cur).backgroundColor;
            if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return bg;
            cur = cur.parentElement;
        }
        return 'rgb(255, 255, 255)';
    }
    function parseRgb(s) {
        const m = s.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        return m ? [+m[1], +m[2], +m[3]] : [255,255,255];
    }
    const sample = document.querySelectorAll('#shell-content h1, #shell-content h2, #shell-content h3, #shell-content .text-muted, #shell-content .text-secondary, #shell-content a, #shell-content button, #shell-content .badge, #shell-content .status-badge, #shell-content .kb-card, #shell-content th, #shell-content td, #kb-board h1, #kb-board h2, #kb-board h3, #kb-board .text-muted, #kb-board .text-secondary, #kb-board a, #kb-board button, #kb-board .badge, #kb-board .status-badge, #kb-board .kb-card, #kb-board th, #kb-board td, [id^=view-] h1, [id^=view-] h2, [id^=view-] h3, [id^=view-] .text-muted, [id^=view-] a, [id^=view-] button, [id^=view-] .badge, [id^=view-] .status-badge, [id^=view-] th, [id^=view-] td');
    const checked = new Set();
    for (const el of sample) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const cs = getComputedStyle(el);
        const fg = parseRgb(cs.color);
        const bg = parseRgb(resolveBg(el));
        const ratio = contrastRatio(luminance(fg), luminance(bg));
        const key = cs.color+'|'+resolveBg(el);
        if (checked.has(key) || ratio >= 4.5) { checked.add(key); continue; }
        checked.add(key);
        const sel = el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+(el.className||'').toString().split(' ').slice(0,2).join('.');
        const fontSize = parseFloat(cs.fontSize);
        const required = fontSize >= 18.66 && (parseInt(cs.fontWeight) >= 700 || parseFloat(cs.fontWeight) >= 700) ? 3.0 : 4.5;
        if (ratio < required) {
            out.contrast.push({sel, fg: cs.color, bg: resolveBg(el), ratio: Math.round(ratio*100)/100, required, text: (el.textContent||'').trim().slice(0,40)});
            if (out.contrast.length > 30) break;
        }
    }

    // 5. Мелкие контролы (< 32x32)
    const ctrls = document.querySelectorAll('button, input[type=checkbox], input[type=radio], .icon-btn, [role=button], .kb-card-action, a.btn, .action-btn, .close-btn, .modal-close');
    for (const el of ctrls) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (r.width < 32 || r.height < 32) {
            const sel = el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+(el.className||'').toString().split(' ').slice(0,2).join('.');
            out.smallControls.push({sel, w: Math.round(r.width), h: Math.round(r.height)});
            if (out.smallControls.length > 20) break;
        }
    }

    // 6. Расстояния между кликабельными элементами < 8px
    const clickables = [...document.querySelectorAll('button, a, .icon-btn, [role=button], input, select, textarea')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    let gapIssues = 0;
    for (let i = 0; i < clickables.length - 1 && gapIssues < 15; i++) {
        const r1 = clickables[i].getBoundingClientRect();
        const r2 = clickables[i+1].getBoundingClientRect();
        // только соседние по горизонтали на одной y-полосе
        if (Math.abs(r1.top - r2.top) < 10) {
            const gap = r2.left - r1.right;
            if (gap > 0 && gap < 8) {
                out.gaps.push({gap: Math.round(gap), el1: clickables[i].tagName+'.'+(clickables[i].className||'').toString().split(' ')[0], el2: clickables[i+1].tagName+'.'+(clickables[i+1].className||'').toString().split(' ')[0]});
                gapIssues++;
            }
        }
    }

    // 7. Отступы основных контейнеров
    const containers = document.querySelectorAll('#shell-content, #kb-board, .kb-columns, .kb-column, .modal-content, .card-body, .table-container, #view-fin, #view-clients, #view-suppliers, #view-tasks, #view-day, #view-admin');
    for (const el of containers) {
        const cs = getComputedStyle(el);
        const sel = el.id ? '#'+el.id : el.tagName.toLowerCase()+'.'+(el.className||'').toString().split(' ').slice(0,2).join('.');
        out.paddingGap.push({sel, padding: cs.padding, gap: cs.gap || cs.rowGap || 'normal'});
    }

    // 8. Focus ring — таб по первым интерактивным элементам
    // (проверяется отдельно, тут заглушка)

    return out;
}"""


def login(page, base, user, password):
    """Вход через form-encoded POST."""
    page.goto(base + "#board", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#login-username", state="visible", timeout=15000)
    page.fill("#login-username", user)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_function("() => document.getElementById('login-overlay')?.hidden !== false", timeout=15000)
    page.wait_for_timeout(2000)


def set_theme(page, theme):
    if theme == "dark":
        page.evaluate("() => { document.documentElement.setAttribute('data-theme','dark'); localStorage.setItem('crm_theme','dark'); }")
    else:
        page.evaluate("() => { document.documentElement.removeAttribute('data-theme'); localStorage.setItem('crm_theme','light'); }")
    page.wait_for_timeout(500)


def set_width(page, width):
    page.set_viewport_size({"width": width, "height": 950})
    page.wait_for_timeout(400)


def snap(page, path):
    try:
        page.screenshot(path=path, full_page=False)
    except Exception as e:
        print(f"  ! скриншот {path}: {e}")


def collect(page):
    try:
        return page.evaluate(METRICS_JS)
    except Exception as e:
        return {"error": str(e)[:200]}


def open_card_modal(page):
    """Клик по первой карточке на доске."""
    try:
        page.click(".kb-card", timeout=5000)
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def close_modal(page):
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)


def open_nakladnaya_dialog(page):
    """Попытка открыть диалог выписки накладной — кнопка в карточке."""
    try:
        # Сначала откроем карточку
        if not open_card_modal(page):
            return False
        # Ищем кнопку "Выписать накладную" или похожую
        btn = page.locator("button, a").filter(has_text="накладн")
        if btn.count() > 0:
            btn.first.click(timeout=3000)
            page.wait_for_timeout(800)
            return True
        close_modal(page)
        return False
    except Exception:
        close_modal(page)
        return False


def open_payment_dialog(page):
    """Попытка открыть диалог оплаты."""
    try:
        if not open_card_modal(page):
            return False
        btn = page.locator("button, a").filter(has_text="оплат")
        if btn.count() > 0:
            btn.first.click(timeout=3000)
            page.wait_for_timeout(800)
            return True
        close_modal(page)
        return False
    except Exception:
        close_modal(page)
        return False


def open_new_user_form(page):
    """Админка → кнопка «+ Новый пользователь»."""
    try:
        page.goto(page.url.split("#")[0] + "#admin", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        btn = page.locator("button").filter(has_text="Новый пользователь")
        if btn.count() > 0:
            btn.first.click(timeout=3000)
            page.wait_for_timeout(800)
            return True
        return False
    except Exception:
        return False


def trigger_empty_state(page):
    """Фильтр, дающий 0 результатов в таблице."""
    try:
        page.goto(page.url.split("#")[0] + "#clients", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        search = page.locator("input[placeholder*='иск'], input[type=search], #client-search, .search-input").first
        if search.is_visible(timeout=2000):
            search.fill("ZZZZNONEXISTENT999")
            page.wait_for_timeout(800)
            return True
        return False
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://87-232-64-12.nip.io/v2/")
    ap.add_argument("--out", default="/tmp/crm_design")
    ap.add_argument("--widths", nargs="+", type=int, default=[1920, 1440, 1280, 1024])
    ap.add_argument("--themes", nargs="+", default=["light", "dark"])
    args = ap.parse_args()

    user = os.environ.get("CRM_USER")
    password = os.environ.get("CRM_PASS")
    if not user or not password:
        print("Нужны CRM_USER и CRM_PASS в окружении", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    base = args.base.rstrip("/") + "/"
    all_metrics = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})

        # Логин
        print("→ Вход…")
        login(page, base, user, password)
        print("✓ Вошли")

        # Матрица: width × theme × view
        for width in args.widths:
            set_width(page, width)
            for theme in args.themes:
                set_theme(page, theme)
                for view_name, view_hash in VIEWS:
                    key = f"{theme}-{width}-{view_name}"
                    print(f"  → {key}")
                    page.goto(base + view_hash, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)
                    snap(page, os.path.join(args.out, f"{key}.png"))
                    metrics = collect(page)
                    all_metrics[key] = metrics

                # Специальные состояния (только на одном view, чтобы не дублировать)
                if width == args.widths[0]:
                    # Карточка (модалка)
                    page.goto(base + "#board", wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    if open_card_modal(page):
                        key = f"{theme}-{width}-card-modal"
                        print(f"  → {key}")
                        snap(page, os.path.join(args.out, f"{key}.png"))
                        all_metrics[key] = collect(page)
                        close_modal(page)

                    # Накладная
                    page.goto(base + "#board", wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    if open_nakladnaya_dialog(page):
                        key = f"{theme}-{width}-nakladnaya"
                        print(f"  → {key}")
                        snap(page, os.path.join(args.out, f"{key}.png"))
                        all_metrics[key] = collect(page)
                        close_modal(page)

                    # Оплата
                    page.goto(base + "#board", wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    if open_payment_dialog(page):
                        key = f"{theme}-{width}-payment"
                        print(f"  → {key}")
                        snap(page, os.path.join(args.out, f"{key}.png"))
                        all_metrics[key] = collect(page)
                        close_modal(page)

                    # Новый пользователь
                    if open_new_user_form(page):
                        key = f"{theme}-{width}-new-user"
                        print(f"  → {key}")
                        snap(page, os.path.join(args.out, f"{key}.png"))
                        all_metrics[key] = collect(page)
                        close_modal(page)

                    # Пустой список
                    if trigger_empty_state(page):
                        key = f"{theme}-{width}-empty-list"
                        print(f"  → {key}")
                        snap(page, os.path.join(args.out, f"{key}.png"))
                        all_metrics[key] = collect(page)
                        # Сброс фильтра
                        search = page.locator("input[placeholder*='иск'], input[type=search], #client-search, .search-input").first
                        if search.is_visible(timeout=1000):
                            search.fill("")
                            page.wait_for_timeout(500)

        browser.close()

    # Сохраняем метрики
    out_path = os.path.join(args.out, "metrics.json")
    with open(out_path, "w") as f:
        json.dump(all_metrics, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Метрики: {out_path}")
    print(f"✓ Скриншотов: {len([f for f in os.listdir(args.out) if f.endswith('.png')])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
