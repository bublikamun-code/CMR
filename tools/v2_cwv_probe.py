#!/usr/bin/env python3
"""Замер Core Web Vitals (LCP / CLS / INP) для CRM v2.

Read-only harness: разрешены GET/HEAD и единственный POST /auth/login,
остальные мутации блокируются ДО отправки в CRM. Принимает только локальный
стенд (127.0.0.1 / localhost) — случайно упасть на прод нельзя.

Полевых данных (CrUX p75) здесь нет: ключ API не задан, поэтому скрипт
меряет ЛАБОРАТОРНЫЕ значения и называет их лабораторными.

Запуск:
    CRM_USER=... CRM_PASS=... python3 tools/v2_cwv_probe.py \
        --base http://127.0.0.1:8125/v2/ --out /tmp/cwv.json \
        --label desktop --repeat 5
"""
import argparse
import json
import os
import statistics
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Метрики Google: good / needs-work / poor.
THRESHOLDS = {
    "lcp_ms": (2500, 4000),
    "cls": (0.1, 0.25),
    "inp_ms": (200, 500),
    "ttfb_ms": (800, 1800),
}

# Профили сети/CPU. Медленный профиль нужен отдельно: локальный стенд по
# умолчанию отдаёт мгновенно, и без троттлинга все метрики выглядят
# неправдоподобно хорошо — а прод у клиентов по мобильному каналу.
PROFILES = {
    "desktop": {
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1,
        "cpu_rate": 1,
        "network": None,
    },
    "mobile": {
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 2,
        # «Быстрый 4G» из https://web.dev/articles/vitals — то, что Google
        # считает медленной мобильной сетью по умолчанию.
        "cpu_rate": 4,
        "network": {
            "offline": False,
            "latency": 150,
            "downloadThroughput": 1.6 * 1024 * 1024 / 8,
            "uploadThroughput": 750 * 1024 / 8,
        },
    },
}

# Сценарий взаимодействий для INP. Каждый пункт — реальное действие
# пользователя, а не искусственный таймер: INP меряется по event timing,
# который сам делит задержку на input/processing/presentation.
INTERACTIONS = ("open-card", "switch-fin-tab", "open-drawer", "toggle-filter")

INIT_SCRIPT = r"""
(() => {
  const state = window.__cwv = {
    lcp: null,
    lcpElement: null,
    cls: 0,
    clsSources: [],
    fcp: null,
    inp: 0,
    inpInteraction: null,
    interactions: [],
    longTasks: [],
    longTaskTotal: 0,
    errors: [],
    resourceCount: 0,
    transferBytes: 0,
    phases: [],
  };
  const describe = (el) => {
    if (!el || !el.tagName) return '(нет элемента)';
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.className && typeof el.className === 'string') {
      s += '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.');
    }
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
    if (text) s += ' «' + text.slice(0, 40) + '»';
    return s;
  };
  try {
    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      const last = entries[entries.length - 1];
      state.lcp = last.startTime;
      state.lcpElement = describe(last.element || (last.url ? '(url: ' + last.url + ')' : null));
    }).observe({type: 'largest-contentful-paint', buffered: true});
  } catch (e) { state.errors.push('lcp: ' + e.message); }
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) {
          state.cls += entry.value;
          for (const src of (entry.sources || [])) {
            state.clsSources.push({
              value: entry.value,
              node: describe(src.node),
              previous: src.previousRect ? [src.previousRect.x, src.previousRect.y,
                                            src.previousRect.width, src.previousRect.height] : null,
              current: src.currentRect ? [src.currentRect.x, src.currentRect.y,
                                          src.currentRect.width, src.currentRect.height] : null,
            });
          }
        }
      }
    }).observe({type: 'layout-shift', buffered: true});
  } catch (e) { state.errors.push('cls: ' + e.message); }
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        state.fcp = entry.startTime;
      }
    }).observe({type: 'paint', buffered: true});
  } catch (e) { state.errors.push('fcp: ' + e.message); }
  // INP: event timing. duration в нём уже включает presentation delay
  // (округление до 8 мс — это округление спецификации, не наша потеря).
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.interactionId) continue;
        const rec = {
          name: entry.name,
          duration: entry.duration,
          inputDelay: entry.processingStart - entry.startTime,
          processing: entry.processingEnd - entry.processingStart,
          presentation: entry.startTime + entry.duration - entry.processingEnd,
          at: entry.startTime,
        };
        state.interactions.push(rec);
        if (entry.duration > state.inp) {
          state.inp = entry.duration;
          state.inpInteraction = rec;
        }
      }
    }).observe({type: 'event', buffered: true, durationThreshold: 16});
  } catch (e) { state.errors.push('event: ' + e.message); }
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        state.longTasks.push({start: entry.startTime, duration: entry.duration});
        state.longTaskTotal += entry.duration;
      }
    }).observe({type: 'longtask', buffered: true});
  } catch (e) { state.errors.push('longtask: ' + e.message); }
  try {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) state.ttfb = nav.responseStart;
  } catch (e) { state.errors.push('nav: ' + e.message); }
  const rc = performance.getEntriesByType('resource');
  state.resourceCount = rc.length;
  for (const r of rc) state.transferBytes += r.transferSize || 0;
  window.addEventListener('error', (e) => {
    state.errors.push('pageerror: ' + (e.message || String(e.error || '')));
  });
  window.addEventListener('unhandledrejection', (e) => {
    state.errors.push('rejection: ' + (e.reason && e.reason.message || e.reason || ''));
  });
  // Слушаем document, а не window: приложение диспатчит kb:* через
  // new CustomEvent(...) без bubbles, и на window такие события не доходят
  // вовсе — пустой массив фаз выглядел бы как «событий не было».
  // Регистрации явные, а не циклом по именам: вариант с циклом молча не
  // срабатывал (проверено маркером рядом — сам маркер писался, callback нет).
  const notePhase = (name) => { state.phases.push({name, at: performance.now()}); };
  document.addEventListener('kb:cards-appended', () => notePhase('kb:cards-appended'));
  document.addEventListener('kb:reloaded', () => notePhase('kb:reloaded'));
  document.addEventListener('v2:role', () => notePhase('v2:role'));
  document.addEventListener('kb:documents', () => notePhase('kb:documents'));
  document.addEventListener('kb:groups', () => notePhase('kb:groups'));
  document.addEventListener('kb:phase2', () => notePhase('kb:phase2'));
  document.addEventListener('kb:initial', () => notePhase('kb:initial'));
  // Сводка по ресурсам считается в момент ЧТЕНИЯ, а не на старте: init-скрипт
  // выполняется до того, как что-либо успело загрузиться, и счётчик всегда
  // был бы нулём. Плюс самые тяжёлые файлы — их и надо называть по имени.
  state.resourceSummary = () => {
    const rows = performance.getEntriesByType('resource').map((r) => ({
      name: r.name.replace(location.origin, ''),
      start: Math.round(r.startTime),
      duration: Math.round(r.duration),
      transfer: r.transferSize || 0,
      kind: r.initiatorType,
    }));
    rows.sort((a, b) => b.duration - a.duration);
    const paint = performance.getEntriesByType('paint')
      .map((p) => ({name: p.name, at: Math.round(p.startTime)}));
    const nav = performance.getEntriesByType('navigation')[0];
    return {
      count: rows.length,
      transfer_kb: Math.round(rows.reduce((s, r) => s + r.transfer, 0) / 1024),
      slowest: rows.slice(0, 12),
      paints: paint,
      responseStart: nav ? Math.round(nav.responseStart) : null,
      domContentLoaded: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
      loadEvent: nav ? Math.round(nav.loadEventEnd) : null,
    };
  };
})();
"""


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("ожидается положительное целое")
    return number


def grade(metric, value):
    """good / needs-improvement / poor по порогам Google."""
    if value is None:
        return "нет данных"
    good, poor = THRESHOLDS[metric]
    if value <= good:
        return "good"
    if value <= poor:
        return "needs-improvement"
    return "poor"


def check_local(base):
    host = urlsplit(base).hostname
    if host not in ALLOWED_HOSTS:
        raise SystemExit(
            f" refusing: --base {base!r} не локальный стенд. "
            "Инструмент read-only и блокирует мутации, но на прод пусть "
            "не заходит вообще."
        )


def install_guards(page, on_blocked):
    """Запрет всего, что меняет данные, кроме входа."""
    def handler(route):
        request = route.request
        if request.method in ("GET", "HEAD"):
            return route.continue_()
        parsed = urlsplit(request.url)
        login = request.method == "POST" and parsed.path.rstrip("/").endswith("/auth/login")
        if login:
            return route.continue_()
        on_blocked.append(f"{request.method} {parsed.path}")
        return route.abort()

    page.route("**/*", handler)


def login(page, base, user, password, timeout_ms=25000):
    """Вход через штатную форму логина.

    Именно форма, а не fetch: /auth/login принимает OAuth2PasswordRequestForm
    (form-encoded, путь от корня сайта, а не от /v2/), и только она ставит
    HttpOnly-cookie, которую потом наследуют остальные вкладки контекста.
    """
    # Обработчик отправки формы вешается в showLogin() — асинхронно, уже после
    # разбора DOM (boot.js стартует по DOMContentLoaded, showLogin приходит из
    # ответа /auth/me). Клик по кнопке до этого момента уходит в пустоту, и
    # вход «зависает» без ошибки. Надёжный признак готовности — только сам
    # результат, поэтому шлём requestSubmit повторно, пока не появится cookie;
    # каждый раз дозаполняя поля, потому что нативный сабмит без обработчика
    # перезагружает страницу и стирает ввод.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        page.wait_for_selector("#login-username", state="visible", timeout=10000)
    except Exception:
        pass
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            page.fill("#login-username", user)
            page.fill("#login-password", password)
        except Exception:
            pass  # поля ещё не в DOM — следующая итерация
        try:
            page.evaluate("() => document.getElementById('login-form')?.requestSubmit()")
        except Exception:
            pass
        page.wait_for_timeout(700)
        try:
            if page.evaluate("() => document.getElementById('login-overlay')?.hidden === true"):
                return True
        except Exception:
            continue
    return False


def do_interaction(page, kind):
    """Одно типовое действие пользователя.

    Селекторы взяты из ЖИВОГО DOM, а не из догадок: `.kb-card` без
    `:visible` цепляет и скрытые представления, и клик по невидимому узлу
    молча ничего не делает — INP тогда измеряет пустоту. Отсюда `:visible`
    везде и `tbody tr`, а не просто `tr` (первой строкой таблицы клиентов
    идёт `<th>`-шапка, клик по ней — не действие пользователя).
    """
    if kind == "open-card":
        # Сколько реально ждать доску — сам по себе полезный факт, поэтому
        # время до готовности пишется в результат, а не теряется.
        started = time.time()
        try:
            page.wait_for_selector(".kb-card-open:visible", timeout=25000, state="visible")
        except Exception:
            return "доска не отрисовалась за 25 с (карточек нет)"
        waited = round(time.time() - started, 1)
        page.locator(".kb-card-open:visible").first.click(timeout=10000)
        page.wait_for_timeout(1200)
        page.keyboard.press("Escape")
        page.wait_for_timeout(900)
        return f"открытие карточки (доска готова через {waited} с) и закрытие"

    if kind == "switch-fin-tab":
        page.evaluate("() => { location.hash = '#fin'; }")
        page.wait_for_timeout(2500)
        tab = page.locator("#view-fin button:visible", has_text="Документы").first
        if tab.count() == 0:
            return "переход на #fin (вкладка «Документы» не найдена)"
        tab.click(timeout=10000)
        page.wait_for_timeout(1800)
        return "переход на #fin и вкладка «Документы»"

    if kind == "open-drawer":
        page.evaluate("() => { location.hash = '#clients'; }")
        page.wait_for_timeout(2500)
        row = page.locator("#view-clients tbody tr:visible").first
        if row.count() == 0:
            return "в разделе «Клиенты» нет строк"
        row.click(timeout=10000)
        page.wait_for_timeout(1800)
        return "открытие карточки клиента (дровер)"

    if kind == "toggle-filter":
        page.evaluate("() => { location.hash = '#board'; }")
        page.wait_for_timeout(1500)
        control = page.locator("#kb-filters-toggle:visible").first
        if control.count() == 0:
            return "тумблер фильтров не найден"
        control.click(timeout=10000)
        page.wait_for_timeout(1500)
        return "открытие панели фильтров"

    return None


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main():
    parser = argparse.ArgumentParser(description="Замер Core Web Vitals v2")
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", default="/tmp/v2_cwv.json")
    parser.add_argument("--label", default="run")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="desktop")
    parser.add_argument("--repeat", type=positive_int, default=5)
    parser.add_argument("--settle-ms", type=positive_int, default=9000)
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    args = parser.parse_args()

    check_local(args.base)
    user = os.environ.get("CRM_USER")
    password = os.environ.get("CRM_PASS")
    if not user or not password:
        raise SystemExit("нужны CRM_USER и CRM_PASS в окружении (стенд, не прод)")

    profile = PROFILES[args.profile]
    runs, load_runs = [], []
    blocked_all, errors_all = [], []
    # Тема ставится ДО любой навигации в контексте, иначе первый кадр успевает
    # отрисоваться в светлой, а потом переключиться — это лишний CLS в замере.
    theme_init = f"try {{ localStorage.setItem('kb-theme', {args.theme!r}); }} catch (e) {{}}"

    def make_page(context):
        page = context.new_page()
        if profile["network"]:
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", profile["network"])
        if profile["cpu_rate"] > 1:
            cdp = context.new_cdp_session(page)
            cdp.send("Emulation.setCPUThrottlingRate",
                     {"rate": profile["cpu_rate"]})
        blocked = []
        install_guards(page, blocked)
        page.add_init_script(theme_init)
        return page, blocked

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for index in range(args.repeat):
            context = browser.new_context(
                viewport=profile["viewport"],
                device_scale_factor=profile["device_scale_factor"],
            )

            # 1) Вход в отдельной вкладке: cookie становится HttpOnly и общей
            #    для контекста, а сама вкладка в замер не попадает.
            auth_page = context.new_page()
            auth_page.goto(args.base, wait_until="commit", timeout=60000)
            if not login(auth_page, args.base, user, password):
                raise SystemExit("вход не удался: оверлей логина не скрылся "
                                 "(проверьте CRM_USER/CRM_PASS и состояние стенда)")
            auth_page.close()

            # 2) Навигация с наблюдателями, только загрузка — источник
            #    LCP/CLS/TTFB/FCP (без единого клика, чтобы наши собственные
            #    клики не попали в layout-shift).
            load_page, load_blocked = make_page(context)
            load_page.add_init_script(INIT_SCRIPT)
            load_page.goto(args.base + "#board", wait_until="commit", timeout=60000)
            load_page.wait_for_load_state("domcontentloaded", timeout=60000)
            load_page.wait_for_timeout(args.settle_ms)
            load_data = load_page.evaluate("() => window.__cwv")
            load_data["resources"] = load_page.evaluate("() => window.__cwv.resourceSummary()")
            load_runs.append(load_data)
            blocked_all.extend(load_blocked)
            errors_all.extend(load_data.get("errors", []))
            load_page.close()

            # 3) Та же навигация + типовые взаимодействия — источник INP.
            int_page, int_blocked = make_page(context)
            int_page.add_init_script(INIT_SCRIPT)
            int_page.goto(args.base + "#board", wait_until="commit", timeout=60000)
            int_page.wait_for_load_state("domcontentloaded", timeout=60000)
            int_page.wait_for_timeout(6000)
            done = []
            for kind in INTERACTIONS:
                try:
                    done.append({"kind": kind, "result": do_interaction(int_page, kind)})
                except Exception as exc:  # noqa: BLE001
                    int_blocked.append(f"interaction {kind}: {exc}")
                    done.append({"kind": kind, "result": "ОШИБКА: " + str(exc)[:120]})
            int_page.wait_for_timeout(1200)
            int_data = int_page.evaluate("() => window.__cwv")
            int_data["interactions_done"] = done
            runs.append(int_data)
            blocked_all.extend(int_blocked)
            errors_all.extend(int_data.get("errors", []))

            print(f"  прогон {index + 1}/{args.repeat}: "
                  f"LCP={load_data.get('lcp')} CLS={load_data.get('cls')} "
                  f"INP={int_data.get('inp')}", flush=True)
            context.close()
        browser.close()

    lcp_values = [d.get("lcp") for d in load_runs if d.get("lcp")]
    cls_values = [d.get("cls") for d in load_runs if d.get("cls") is not None]
    inp_values = [d.get("inp") for d in runs if d.get("inp")]
    ttfb_values = [d.get("ttfb") for d in load_runs if d.get("ttfb")]
    fcp_values = [d.get("fcp") for d in load_runs if d.get("fcp")]

    report = {
        "label": args.label,
        "base": args.base,
        "profile": args.profile,
        "theme": args.theme,
        "repeat": args.repeat,
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": ("ЛАБОРАТОРНЫЕ значения на локальном стенде. Полевых данных "
                 "(CrUX p75) нет: ключ API не задан. Один прогон — одна "
                 "вкладка, лабораторный p75 ниже НЕ равен полевому."),
        "summary": {
            "lcp_ms": {"p75": percentile(lcp_values, 0.75), "median": statistics.median(lcp_values) if lcp_values else None,
                       "worst": max(lcp_values) if lcp_values else None, "grade": grade("lcp_ms", percentile(lcp_values, 0.75))},
            "cls": {"p75": percentile(cls_values, 0.75), "median": statistics.median(cls_values) if cls_values else None,
                    "worst": max(cls_values) if cls_values else None, "grade": grade("cls", percentile(cls_values, 0.75))},
            "inp_ms": {"p75": percentile(inp_values, 0.75), "median": statistics.median(inp_values) if inp_values else None,
                       "worst": max(inp_values) if inp_values else None, "grade": grade("inp_ms", percentile(inp_values, 0.75))},
            "ttfb_ms": {"p75": percentile(ttfb_values, 0.75), "median": statistics.median(ttfb_values) if ttfb_values else None},
            "fcp_ms": {"p75": percentile(fcp_values, 0.75), "median": statistics.median(fcp_values) if fcp_values else None},
        },
        "lcp_elements": sorted({d.get("lcpElement") for d in load_runs if d.get("lcpElement")}),
        "cls_sources": [s for d in load_runs for s in d.get("clsSources", [])][:15],
        "long_tasks": {
            "total_ms": [round(d.get("longTaskTotal", 0), 1) for d in load_runs],
            "worst": [
                {"start": round(t["start"], 1), "duration": round(t["duration"], 1)}
                for d in load_runs for t in sorted(d.get("longTasks", []),
                                                    key=lambda x: -x["duration"])[:5]
            ],
        },
        "resources": {
            "count": [d.get("resources", {}).get("count") for d in load_runs],
            "transfer_kb": [d.get("resources", {}).get("transfer_kb") for d in load_runs],
            "slowest_first_run": (load_runs[0].get("resources", {}).get("slowest")
                                  if load_runs else []),
            "paints_first_run": (load_runs[0].get("resources", {}).get("paints")
                                 if load_runs else []),
            "load_event_first_run": {
                "responseStart": load_runs[0].get("resources", {}).get("responseStart") if load_runs else None,
                "domContentLoaded": load_runs[0].get("resources", {}).get("domContentLoaded") if load_runs else None,
                "loadEvent": load_runs[0].get("resources", {}).get("loadEvent") if load_runs else None,
            },
        },
        "interactions": [i for d in runs for i in d.get("interactions", [])][:40],
        "interactions_done": [i for d in runs for i in d.get("interactions_done", [])],
        "kb_events": [d.get("phases", []) for d in load_runs],
        "page_errors": errors_all,
        "blocked_requests": blocked_all,
        "raw": {"load": load_runs, "interacted": runs},
    }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    summary = report["summary"]
    print()
    print(f"=== CWV · {args.label} · профиль {args.profile} · {args.repeat} прогонов ===")
    for key, name in (("lcp_ms", "LCP"), ("inp_ms", "INP"), ("cls", "CLS"),
                      ("ttfb_ms", "TTFB"), ("fcp_ms", "FCP")):
        entry = summary[key]
        digits = 4 if key == "cls" else 0
        def fmt(value):
            return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "н/д"
        print(f"  {name:5s} p75={fmt(entry.get('p75')):>9s}  "
              f"медиана={fmt(entry.get('median')):>9s}  "
              f"худший={fmt(entry.get('worst')):>9s}  {entry.get('grade', '')}")
    print(f"\n  LCP-элементы: {report['lcp_elements']}")
    if report["cls_sources"]:
        print("  Источники CLS:")
        for source in report["cls_sources"][:5]:
            print(f"    {source['value']:.4f}  {source['node']}")
    if report["page_errors"]:
        print(f"  Ошибки страницы: {report['page_errors'][:5]}")
    if blocked_all:
        print(f"  Заблокировано мутаций (ожидаемо): {len(blocked_all)}")
    print(f"\n  Отчёт: {args.out}")


if __name__ == "__main__":
    main()
