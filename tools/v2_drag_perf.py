#!/usr/bin/env python3
"""Замер стоимости перетаскивания плитки на доске v2 (drag&drop).

Мотив: пункт плана «Drag&drop: троттлинг и обратная связь» — нужно не на глаз,
а числами показать, что перетаскивание больше не дёргает layout пачками, что
статус уходит на сервер одним запросом и что пользователь видит подтверждение.
Скрипт сравнивается сам с собой: прогон ДО и ПОСЛЕ правки одним и тем же
набором метрик (см. --label), поэтому числа кладутся в JSON и диффятся.

Что меряется, двумя прогонами:
  1. «функциональный» — настоящий HTML5-DnD через locator.drag_to(): состав
     колонок изменился, ровно один PATCH .../kanban/cards/{id}/status, тост
     непустой. Внимание: Playwright внутри drag_to сам дёргает
     getBoundingClientRect (проверка перекрывания), поэтому счётчик чтений
     геометрии в этом прогоне завышен одинаково для ДО и ПОСЛЕ. Порядок величин
     показывает прогон 2.
  2. «шторм» — синтетическая пачка событий dragover (несколько событий на кадр)
     без участия Playwright в диспатче: чистая стоимость кадра. Дополнительно
     считается число кадров (rAF) и вызовов document.elementFromPoint — это
     прямая проверка «не чаще одного placeDrop() на кадр».

Метрики кадра берутся через CDP Performance.getMetrics (дельты LayoutCount,
LayoutDuration, RecalcStyleCount, RecalcStyleDuration, ScriptDuration),
long tasks — через PerformanceObserver, перерисовки доски — через
MutationObserver, сеть — через слушатели запросов Playwright.

Запуск (логин — аргументы или переменные окружения, в отчёт не попадают):
    CRM_PASS=... python3 tools/v2_drag_perf.py --base http://127.0.0.1:8126/v2/ \
        --label baseline --out /tmp/v2-item4/drag-baseline.json
"""
import argparse
import collections
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

# Дельты этих счётчиков CDP и показывают «пачки layout-пересчётов».
METRIC_KEYS = (
    "LayoutCount",
    "LayoutDuration",
    "RecalcStyleCount",
    "RecalcStyleDuration",
    "ScriptDuration",
    "TaskDuration",
)

STATUS_PATCH = re.compile(r"/kanban/cards/(\d+)/status$")

# Хуки страницы: long tasks + запросы (сеть дополнительно ловит Playwright).
HOOK_JS = """() => {
  if (window.__dragPerfHooked) return 'already';
  window.__dragPerfHooked = true;
  window.__lt = [];
  try {
    new PerformanceObserver(function (l) {
      l.getEntries().forEach(function (e) {
        window.__lt.push({t: Math.round(e.startTime), dur: Math.round(e.duration)});
      });
    }).observe({entryTypes: ['longtask']});
  } catch (e) { window.__ltErr = String(e.message || e); }
  return 'hooked';
}"""

# Наблюдение за перерисовками доски на время одного прогона.
WATCH_ON = """() => {
  window.__mw = {mut: 0, added: 0, removed: 0};
  if (window.__mo) window.__mo.disconnect();
  window.__lt = [];
  const board = document.getElementById('kb-board');
  window.__mo = new MutationObserver(function (ms) {
    for (const m of ms) {
      window.__mw.mut++;
      window.__mw.added += m.addedNodes.length;
      window.__mw.removed += m.removedNodes.length;
    }
  });
  window.__mo.observe(board, {childList: true, subtree: true, attributes: true, characterData: true});
  return 'watching';
}"""

WATCH_OFF = """() => {
  if (window.__mo) window.__mo.disconnect();
  window.__mo = null;
  const lt = window.__lt.slice();
  return {mutations: window.__mw.mut, added: window.__mw.added, removed: window.__mw.removed,
          longtasks: lt,
          longtasks_count: lt.length,
          longtasks_gt200: lt.filter(function (x) { return x.dur > 200; }).length,
          max_longtask_ms: lt.length ? Math.max.apply(null, lt.map(function (x) { return x.dur; })) : 0};
}"""

# Временный monkey-patch прототипов: считаем чтения геометрии и хит-тесты.
# Снимается сразу после замера, чтобы не влиять на остальные прогоны.
GBC_ON = """() => {
  if (window.__gbcPatched) return 'already';
  window.__gbcPatched = true;
  window.__gbc = 0; window.__efp = 0;
  window.__origGBC = Element.prototype.getBoundingClientRect;
  window.__origEFP = Document.prototype.elementFromPoint;
  Element.prototype.getBoundingClientRect = function () {
    window.__gbc++;
    return window.__origGBC.apply(this, arguments);
  };
  Document.prototype.elementFromPoint = function () {
    window.__efp++;
    return window.__origEFP.apply(this, arguments);
  };
  return 'patched';
}"""

GBC_RESET = "() => { window.__gbc = 0; window.__efp = 0; }"

GBC_READ = "() => ({getBoundingClientRect: window.__gbc | 0, elementFromPoint: window.__efp | 0})"

GBC_OFF = """() => {
  if (!window.__gbcPatched) return 'clean';
  Element.prototype.getBoundingClientRect = window.__origGBC;
  Document.prototype.elementFromPoint = window.__origEFP;
  window.__gbcPatched = false;
  return 'restored';
}"""

# Счётчик кадров: позволяет разделить «вызовов на кадр» и «вызовов всего».
FRAMES_ON = """() => {
  window.__frames = 0; window.__frameStop = false;
  const tick = function () {
    if (window.__frameStop) return;
    window.__frames++;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return 'counting';
}"""

FRAMES_OFF = "() => { window.__frameStop = true; return window.__frames | 0; }"

# --- синтетический шторм: начало, пачка dragover, конец ---
STORM_START = """(arg) => {
  const el = document.querySelector(arg.sel);
  if (!el) return 'no-source';
  const dt = new DataTransfer();
  window.__stormDT = dt;
  el.dispatchEvent(new DragEvent('dragstart', {bubbles: true, cancelable: true,
    dataTransfer: dt, clientX: arg.x, clientY: arg.y}));
  return 'dragstart';
}"""

STORM_BATCH = """(arg) => {
  const el = document.querySelector(arg.sel) || document.body;
  const dt = window.__stormDT;
  let n = 0;
  for (const p of arg.points) {
    el.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true,
      dataTransfer: dt, clientX: p[0], clientY: p[1]}));
    n++;
  }
  return n;
}"""

STORM_END = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return 'no-source';
  el.dispatchEvent(new DragEvent('dragend', {bubbles: true, cancelable: true,
    dataTransfer: window.__stormDT}));
  return 'dragend';
}"""

BOARD_SHAPE = """() => [...document.querySelectorAll('.kb-col')].map(function (c) {
  return {stage: c.dataset.stage,
          title: ((c.querySelector('.kb-col-title') || {}).textContent || '').trim(),
          cards: c.querySelectorAll('.kb-card').length};
})"""

TOAST_JS = """() => {
  const t = document.getElementById('kb-toast');
  if (!t) return {exists: false};
  return {exists: true, hidden: t.hidden, text: (t.innerText || '').trim(),
          has_see_history: !!document.getElementById('kb-see-history')};
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    # 8126 — общий стенд копии прод-БД по V2-WORKPLAN (8125 занят чужим старым стендом).
    ap.add_argument("--base", default="http://127.0.0.1:8126/v2/")
    ap.add_argument("--out", default="/tmp/v2-item4/drag_perf.json")
    ap.add_argument("--label", default="run", help="метка прогона в отчёте (baseline/after)")
    ap.add_argument("--user", default=os.environ.get("CRM_USER", "audit_admin"))
    ap.add_argument("--password", default=os.environ.get("CRM_PASS", ""))
    ap.add_argument("--shots", default="/tmp/v2-item4/shots")
    ap.add_argument("--viewport", default="1600x950")
    ap.add_argument("--storm-events", type=int, default=120, help="сколько dragover dispatчить в шторме")
    ap.add_argument("--storm-per-frame", type=int, default=8, help="событий dragover на одну пачку (кадр)")
    ap.add_argument("--storm-step", type=float, default=40.0,
                    help="шаг курсора между событиями, px: больше половины высоты "
                         "карточки — чтобы каждое событие переносило плейсхолдер")
    ap.add_argument("--frame-ms", type=int, default=32, help="пауза между пачками, мс")
    ap.add_argument("--timeout", type=int, default=25000)
    ap.add_argument("--strict", action="store_true",
                    help="ненулевой код возврата, если критерии пункта не выполнены "
                         "(для baseline-прогона не нужен: тоста там ещё нет)")
    args = ap.parse_args()

    if not args.password:
        print("Нужен пароль: --password или CRM_PASS в окружении", file=sys.stderr)
        return 2

    os.makedirs(args.shots, exist_ok=True)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    base = args.base.rstrip("/") + "/"
    w, h = (int(x) for x in args.viewport.split("x"))

    steps = []
    pageerrors = []
    console_errors = []
    bad_responses = []   # status >= 400: чтобы посторонние 404 было видно в отчёте
    requests = []          # все запросы страницы (Playwright — авторитетно)

    def log(msg):
        line = str(msg)
        steps.append(line)
        print(line, flush=True)

    report = {
        "base": base,
        "label": args.label,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "storm": {"events": args.storm_events, "per_frame": args.storm_per_frame,
                  "frame_ms": args.frame_ms, "step_px": args.storm_step},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        page.set_default_timeout(args.timeout)
        client = ctx.new_cdp_session(page)
        client.send("Performance.enable")

        page.on("pageerror", lambda e: pageerrors.append(str(e)[:400]))
        page.on("console", lambda m: console_errors.append(m.text[:400]) if m.type == "error" else None)

        def on_request(req):
            requests.append({"method": req.method, "url": req.url, "t": round(time.time() * 1000)})

        def on_response(resp):
            try:
                if resp.status >= 400:
                    bad_responses.append({"status": resp.status, "url": resp.url[:200]})
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", lambda r: bad_responses.append(
            {"status": 0, "url": (r.url[:160] + " :: " + str(r.failure)[:80])}))

        def metrics():
            got = client.send("Performance.getMetrics")
            return {m["name"]: m["value"] for m in got.get("metrics", [])}

        def delta(before, after):
            out = {}
            for k in METRIC_KEYS:
                d = (after.get(k, 0.0) or 0.0) - (before.get(k, 0.0) or 0.0)
                out[k] = round(d, 6)
            # LayoutDuration/RecalcStyleDuration/ScriptDuration CDP отдаёт в секундах
            for k in ("LayoutDuration", "RecalcStyleDuration", "ScriptDuration", "TaskDuration"):
                out[k + "_ms"] = round(out[k] * 1000.0, 2)
            return out

        def shot(name):
            try:
                path = os.path.join(args.shots, name + ".png")
                page.screenshot(path=path, full_page=False)
                return path
            except Exception as e:
                log("скриншот %s не снят: %s" % (name, str(e)[:120]))
                return None

        def status_patches(since_index):
            """PATCH .../kanban/cards/{id}/status среди запросов после метки."""
            hits = []
            for r in requests[since_index:]:
                m = STATUS_PATCH.search(r["url"].split("?")[0])
                if m and r["method"] == "PATCH":
                    hits.append({"card": m.group(1), "url": r["url"]})
            return hits

        def net_since(since_index):
            return collections.Counter(
                r["method"] + " " + r["url"].split("?")[0].split("/api/")[-1]
                for r in requests[since_index:]
            )

        # ---------- вход ----------
        page.goto(base + "#board", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        try:
            page.wait_for_selector("#login-username", state="visible", timeout=10000)
            page.fill("#login-username", args.user)
            page.fill("#login-password", args.password)
            page.click("#login-submit")
            page.wait_for_function(
                "() => document.getElementById('login-overlay')?.hidden !== false", timeout=20000)
        except Exception as e:
            log("вход: %s" % str(e)[:200])
        page.wait_for_selector(".kb-card", timeout=30000)
        page.wait_for_timeout(2000)
        page.evaluate(HOOK_JS)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        shape = page.evaluate(BOARD_SHAPE)
        total_cards = page.evaluate("document.querySelectorAll('.kb-card').length")
        report["board"] = {"columns": shape, "cards_total": total_cards,
                           "kbdata_cards": page.evaluate(
                               "() => (window.KBData && window.KBData.cards || []).length")}
        log("вход выполнен; плиток на доске=%d, KBData.cards=%s" % (
            total_cards, report["board"]["kbdata_cards"]))
        log("колонки: %s" % json.dumps(shape, ensure_ascii=False))

        # ---------- выбор пары колонок ----------
        src_col = next((c for c in shape if c["cards"] > 0), None)
        dst_col = next((c for c in shape if src_col and c["stage"] != src_col["stage"]), None)
        if not src_col or not dst_col:
            log("НЕ УДАЛОСЬ выбрать пару колонок — замер невозможен")
            browser.close()
            return 3
        src_stage, dst_stage = src_col["stage"], dst_col["stage"]
        log("перенос: «%s» (%s) → «%s» (%s)" % (
            src_col["title"], src_stage, dst_col["title"], dst_stage))

        page.evaluate(GBC_ON)

        # ================= ПРОГОН 1: функциональный (настоящий HTML5-DnD) =================
        log("\n=== ПРОГОН 1: функциональный перенос (locator.drag_to) ===")
        func = {"src_stage": src_stage, "dst_stage": dst_stage, "error": None}
        src_loc = page.locator(".kb-col[data-stage='%s'] .kb-card" % src_stage).first
        dst_loc = page.locator(".kb-col[data-stage='%s'] .kb-cards" % dst_stage).first
        card_id = None
        try:
            card_id = src_loc.get_attribute("data-card")
        except Exception as e:
            log("data-card не прочитан: %s" % str(e)[:120])
        func["card_id"] = card_id

        net_mark = len(requests)
        before_shape = page.evaluate(BOARD_SHAPE)
        page.evaluate(WATCH_ON)
        page.evaluate(GBC_RESET)
        page.evaluate(FRAMES_ON)
        m_before = metrics()
        t0 = time.time()
        try:
            src_loc.drag_to(dst_loc)
        except Exception as e:
            func["error"] = str(e)[:250]
            log("ОШИБКА drag_to: %s" % func["error"])
        page.wait_for_timeout(2000)
        func["drop_ms"] = int((time.time() - t0) * 1000)
        m_after = metrics()
        frames = page.evaluate(FRAMES_OFF)
        counts = page.evaluate(GBC_READ)
        watch = page.evaluate(WATCH_OFF)
        after_shape = page.evaluate(BOARD_SHAPE)
        toast = page.evaluate(TOAST_JS)

        func.update({
            "metrics_delta": delta(m_before, m_after),
            "frames": frames,
            "dom_reads": counts,
            "board_mutations": watch["mutations"],
            "nodes_added": watch["added"],
            "nodes_removed": watch["removed"],
            "longtasks_count": watch["longtasks_count"],
            "longtasks_gt200": watch["longtasks_gt200"],
            "max_longtask_ms": watch["max_longtask_ms"],
            "longtasks": watch["longtasks"],
            "shape_before": before_shape,
            "shape_after": after_shape,
            "moved": before_shape != after_shape,
            "toast": toast,
            "status_patches": status_patches(net_mark),
            "requests": dict(net_since(net_mark)),
        })
        shot("%s-01-functional-toast" % args.label)
        func["screenshot"] = os.path.join(args.shots, "%s-01-functional-toast.png" % args.label)
        report["functional"] = func

        log("время drop=%d мс; состав колонок изменился=%s" % (func["drop_ms"], func["moved"]))
        log("  ДО : %s" % json.dumps([c["cards"] for c in before_shape]))
        log("  ПОСЛЕ: %s" % json.dumps([c["cards"] for c in after_shape]))
        log("  PATCH .../status: %d %s" % (
            len(func["status_patches"]), json.dumps(func["status_patches"], ensure_ascii=False)))
        log("  тост: hidden=%s текст=%r кнопка«Списано»=%s" % (
            toast.get("hidden"), toast.get("text"), toast.get("has_see_history")))
        log("  метрики CDP: %s" % json.dumps(func["metrics_delta"], ensure_ascii=False))
        log("  кадров=%d, getBoundingClientRect=%d, elementFromPoint=%d" % (
            frames, counts["getBoundingClientRect"], counts["elementFromPoint"]))
        log("  перерисовок доски(mutation records)=%d (+%d/-%d), long tasks=%d (>200мс=%d, макс %s мс)" % (
            watch["mutations"], watch["added"], watch["removed"], watch["longtasks_count"],
            watch["longtasks_gt200"], watch["max_longtask_ms"]))
        log("  сеть за прогон: %s" % json.dumps(func["requests"], ensure_ascii=False)[:400])

        # возврат карточки, чтобы следующий прогон стартовал с того же состава
        if func["moved"] and card_id:
            back_mark = len(requests)
            try:
                page.locator(".kb-col[data-stage='%s'] .kb-card[data-card='%s']" % (dst_stage, card_id)).first.drag_to(
                    page.locator(".kb-col[data-stage='%s'] .kb-cards" % src_stage).first)
                page.wait_for_timeout(2000)
                back_shape = page.evaluate(BOARD_SHAPE)
                log("  возврат карточки %s: состав=%s (совпадает с ДО=%s), PATCH=%d" % (
                    card_id, json.dumps([c["cards"] for c in back_shape]),
                    [c["cards"] for c in back_shape] == [c["cards"] for c in before_shape],
                    len(status_patches(back_mark))))
                report["functional"]["restore"] = {
                    "shape": back_shape,
                    "same_as_before": [c["cards"] for c in back_shape] == [c["cards"] for c in before_shape],
                    "status_patches": len(status_patches(back_mark)),
                }
            except Exception as e:
                log("  возврат не удался: %s" % str(e)[:160])
                report["functional"]["restore"] = {"error": str(e)[:200]}
        page.wait_for_timeout(1200)

        # ================= ПРОГОН 2: шторм dragover =================
        log("\n=== ПРОГОН 2: шторм dragover (%d событий по %d на кадр) ===" % (
            args.storm_events, args.storm_per_frame))
        storm = {"events": args.storm_events, "per_frame": args.storm_per_frame,
                 "frame_ms": args.frame_ms, "error": None}
        try:
            src_box = page.locator(".kb-col[data-stage='%s'] .kb-card" % src_stage).first.bounding_box()
            dst_box = page.locator(".kb-col[data-stage='%s'] .kb-cards" % dst_stage).first.bounding_box()
        except Exception as e:
            src_box = dst_box = None
            storm["error"] = "bounding_box: " + str(e)[:160]
            log("ОШИБКА: %s" % storm["error"])
        if src_box and dst_box:
            # Профиль шторма: список целевой колонки предварительно прокручен
            # (в «В работе» 168 карточек — при прокрутке placeDrop читает rect
            # десятков плиток сверху от курсора), курсор ходит зигзагом с шагом
            # ~40px, то есть почти каждое событие переносит плейсхолдер и
            # заставляет искать соседей заново. Краёв списка (48px) не касаемся —
            # иначе включится автоскролл и будет инвалидировать кэш сам по себе.
            page.evaluate("""(stage) => {
                const list = document.querySelector(".kb-col[data-stage='" + stage + "'] .kb-cards");
                if (!list) return 0;
                list.scrollTop = Math.round((list.scrollHeight - list.clientHeight) * 0.4);
                return {scrollTop: list.scrollTop, scrollHeight: list.scrollHeight,
                        clientHeight: list.clientHeight, cards: list.querySelectorAll('.kb-card').length};
            }""", dst_stage)
            scroll_info = page.evaluate("""(stage) => {
                const list = document.querySelector(".kb-col[data-stage='" + stage + "'] .kb-cards");
                return {scrollTop: list.scrollTop, scrollHeight: list.scrollHeight,
                        clientHeight: list.clientHeight, cards: list.querySelectorAll('.kb-card').length};
            }""", dst_stage)
            storm["target_list"] = scroll_info
            log("  целевой список: %s" % json.dumps(scroll_info))
            # рабочая область — середина видимой части списка
            x0 = src_box["x"] + src_box["width"] / 2.0
            y0 = src_box["y"] + min(20.0, src_box["height"] / 2.0)
            x1 = dst_box["x"] + dst_box["width"] / 2.0
            top = dst_box["y"] + dst_box["height"] * 0.30
            span = dst_box["height"] * 0.40
            step = args.storm_step
            n = max(1, args.storm_events)
            approach = max(4, n // 10)

            net_mark = len(requests)
            page.evaluate(WATCH_ON)
            page.evaluate(GBC_RESET)
            page.evaluate(FRAMES_ON)
            m_before = metrics()
            t0 = time.time()

            started = page.evaluate(STORM_START, {
                "sel": ".kb-col[data-stage='%s'] .kb-card" % src_stage, "x": x0, "y": y0})
            storm["dragstart"] = started
            batches = 0
            sent = 0
            # подход: от исходной плитки к целевой колонке (диспатч в исходном списке)
            for i in range(1, approach + 1):
                pts = [[round(x0 + (x1 - x0) * i / float(approach), 1),
                        round(y0 + (top + span / 2.0 - y0) * i / float(approach), 1)]]
                sent += page.evaluate(STORM_BATCH, {
                    "sel": ".kb-col[data-stage='%s'] .kb-cards" % src_stage, "points": pts})
                batches += 1
                page.wait_for_timeout(args.frame_ms)
            # шторм внутри целевой колонки: зигзаг с шагом step, по несколько
            # событий на кадр. Каждый переход через середину карточки = перенос
            # плейсхолдера = полный перебор соседей в placeDrop().
            rest = n - approach
            per = max(1, args.storm_per_frame)
            chunk = []
            y_cur = top + span / 2.0
            direction = 1.0
            for i in range(rest):
                y_cur += direction * step
                if y_cur > top + span:
                    y_cur = top + span
                    direction = -1.0
                elif y_cur < top:
                    y_cur = top
                    direction = 1.0
                px = round(x1 + (dst_box["width"] * 0.12) * (1 if (i // 4) % 2 else -1), 1)
                py = round(y_cur, 1)
                chunk.append([px, py])
                if len(chunk) >= per:
                    sent += page.evaluate(STORM_BATCH, {
                        "sel": ".kb-col[data-stage='%s'] .kb-cards" % dst_stage, "points": chunk})
                    chunk = []
                    batches += 1
                    page.wait_for_timeout(args.frame_ms)
            if chunk:
                sent += page.evaluate(STORM_BATCH, {
                    "sel": ".kb-col[data-stage='%s'] .kb-cards" % dst_stage, "points": chunk})
                batches += 1
                page.wait_for_timeout(args.frame_ms)
            # dragend слушается на document — шлём на исходную плитку, а если её
            # уже нет в этой колонке, на body (обработчику всё равно, откуда пузырь)
            end_sel = ".kb-col[data-stage='%s'] .kb-card" % src_stage
            if not page.locator(end_sel).count():
                end_sel = "body"
            ended = page.evaluate(STORM_END, end_sel)
            storm["dragend"] = ended
            storm["wall_ms"] = int((time.time() - t0) * 1000)
            # счётчики снимаем СРАЗУ после шторма: очистка (cleanupDrag) гасит
            # rAF-петлю, а лишний простой размыл бы значения «на кадр»
            frames = page.evaluate(FRAMES_OFF)
            m_after = metrics()
            counts = page.evaluate(GBC_READ)
            watch = page.evaluate(WATCH_OFF)
            page.wait_for_timeout(600)

            storm.update({
                "sent_events": sent,
                "batches": batches,
                "metrics_delta": delta(m_before, m_after),
                "frames": frames,
                "dom_reads": counts,
                "board_mutations": watch["mutations"],
                "nodes_added": watch["added"],
                "nodes_removed": watch["removed"],
                "longtasks_count": watch["longtasks_count"],
                "longtasks_gt200": watch["longtasks_gt200"],
                "max_longtask_ms": watch["max_longtask_ms"],
                "longtasks": watch["longtasks"],
                "requests": dict(net_since(net_mark)),
                "shape_after": page.evaluate(BOARD_SHAPE),
            })
            # производные: сколько хит-тестов (= вызовов placeDrop) и чтений
            # геометрии пришлось на один кадр
            if frames:
                storm["elementFromPoint_per_frame"] = round(counts["elementFromPoint"] / float(frames), 2)
                storm["getBoundingClientRect_per_frame"] = round(counts["getBoundingClientRect"] / float(frames), 2)
            shot("%s-02-storm" % args.label)
            storm["screenshot"] = os.path.join(args.shots, "%s-02-storm.png" % args.label)
        report["storm"] = dict(report["storm"], **storm)

        log("отправлено dragover=%d пачками=%d; wall=%s мс; кадров=%s" % (
            storm.get("sent_events"), storm.get("batches"), storm.get("wall_ms"), storm.get("frames")))
        log("  getBoundingClientRect=%s, elementFromPoint=%s (за кадр: %s / %s)" % (
            storm.get("dom_reads", {}).get("getBoundingClientRect"),
            storm.get("dom_reads", {}).get("elementFromPoint"),
            storm.get("getBoundingClientRect_per_frame"),
            storm.get("elementFromPoint_per_frame")))
        log("  метрики CDP: %s" % json.dumps(storm.get("metrics_delta", {}), ensure_ascii=False))
        log("  перерисовок доски=%d (+%s/-%s), long tasks=%s (>200мс=%s, макс %s мс)" % (
            storm.get("board_mutations", -1), storm.get("nodes_added"), storm.get("nodes_removed"),
            storm.get("longtasks_count"), storm.get("longtasks_gt200"), storm.get("max_longtask_ms")))
        log("  сеть за шторм (PATCH не ожидается): %s" % json.dumps(
            storm.get("requests", {}), ensure_ascii=False)[:300])
        log("  состав колонок после шторма: %s" % json.dumps(
            [c["cards"] for c in storm.get("shape_after", [])]))

        page.evaluate(GBC_OFF)

        report["pageerrors"] = pageerrors
        report["console_errors"] = console_errors
        report["bad_responses"] = collections.Counter(
            "%s %s" % (b["status"], b["url"].split("?")[0]) for b in bad_responses).most_common(20)
        log("\n=== ИТОГ ===")
        log("pageerror=%d, console.error=%d" % (len(pageerrors), len(console_errors)))
        for e in pageerrors[:8]:
            log("  pageerror: %s" % e[:200])
        for e in console_errors[:8]:
            log("  console: %s" % e[:200])
        if report["bad_responses"]:
            log("ответы >=400 (посторонние, не относятся к drag&drop): %s" % json.dumps(
                report["bad_responses"][:10], ensure_ascii=False)[:600])
        browser.close()

    report["steps"] = steps

    # ---------- критерии пункта плана ----------
    func = report.get("functional", {})
    patches = len(func.get("status_patches", []))
    toast_text = (func.get("toast") or {}).get("text") or ""
    checks = {
        "состав колонок изменился": bool(func.get("moved")),
        "ровно один PATCH .../kanban/cards/{id}/status": patches == 1,
        "тост после переноса непустой": bool(toast_text.strip()),
        "нет pageerror": not pageerrors,
    }
    report["checks"] = checks
    print("\n-- КРИТЕРИИ ПУНКТА --")
    for name, ok in checks.items():
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))

    with open(args.out, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("отчёт записан: %s" % args.out)

    if args.strict and not all(checks.values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
