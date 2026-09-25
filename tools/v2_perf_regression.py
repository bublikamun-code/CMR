#!/usr/bin/env python3
"""Read-only harness для регрессий производительности CRM v2.

Собирает DOM/Layout/Script CDP-метрики, стоимость переключения финансовых
вкладок, скрытые DOM-mutations, 30 циклов навигации, события initial/phase2 и
ленивую загрузку фотографий накладных. Разрешён только локальный стенд; все
POST/PATCH/PUT/DELETE, кроме POST /auth/login, блокируются до CRM.

Запуск:
    CRM_USER=... CRM_PASS=... python3 tools/v2_perf_regression.py \
        --base http://127.0.0.1:8125/v2/ --out /tmp/v2-perf.json --label baseline
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright


FIN_TABS = ("payments", "documents", "incoming", "control")
ROUTE_ORDER = ("board", "fin")
CDP_KEYS = (
    "Nodes",
    "LayoutCount",
    "LayoutDuration",
    "RecalcStyleCount",
    "RecalcStyleDuration",
    "ScriptDuration",
    "TaskDuration",
)
EVENT_NAMES = ("kb:reloaded", "kb:documents", "kb:groups", "kb:cards-appended")
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
PHOTO_PATH = re.compile(r"/nakladnye/photos/")
SECRET_VALUES = []


INIT_SCRIPT = r"""
((theme) => {
  const state = window.__v2PerfRegression || {
    phase: 'initial',
    events: [],
    longTasks: [],
    longTaskSupported: false,
    longTaskError: null,
    tabClicks: []
  };
  window.__v2PerfRegression = state;

  try { localStorage.setItem('kb-theme', theme); } catch (e) { /* режим без storage */ }
  const applyTheme = () => {
    if (document.documentElement) document.documentElement.dataset.theme = theme;
  };
  applyTheme();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyTheme, {once: true});
  }

  for (const name of __EVENT_NAMES__) {
    document.addEventListener(name, () => {
      state.events.push({name, phase: state.phase, t: Math.round(performance.now() * 100) / 100});
    });
  }

  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        state.longTasks.push({
          start_ms: Math.round(entry.startTime * 100) / 100,
          duration_ms: Math.round(entry.duration * 100) / 100
        });
      }
    });
    observer.observe({entryTypes: ['longtask']});
    state.longTaskSupported = true;
  } catch (error) {
    state.longTaskError = String(error && (error.message || error));
  }

  document.addEventListener('click', (event) => {
    const button = event.target && event.target.closest && event.target.closest('[data-fin-tab]');
    if (!button) return;
    const start = performance.now();
    state.pendingTabClick = {
      tab: button.dataset.finTab || '',
      start_ms: Math.round(start * 100) / 100
    };
  }, true);

  document.addEventListener('click', (event) => {
    const pending = state.pendingTabClick;
    const button = event.target && event.target.closest && event.target.closest('[data-fin-tab]');
    if (!pending || !button || button.dataset.finTab !== pending.tab) return;
    const panel = document.getElementById(button.getAttribute('aria-controls'));
    const end = performance.now();
    pending.synchronous = button.getAttribute('aria-pressed') === 'true'
      && !!panel && !panel.hidden && panel.getAttribute('aria-hidden') !== 'true';
    pending.outcome_ms = Math.round(end * 100) / 100;
    pending.duration_ms = Math.round((end - pending.start_ms) * 100) / 100;
    state.tabClicks.push(pending);
    state.pendingTabClick = null;
  }, false);
})(__V2_THEME__);
"""


DOM_METRICS = r"""
() => {
  const root = document.getElementById('view-fin');
  const board = document.getElementById('kb-board');
  return {
    total_nodes: document.getElementsByTagName('*').length,
    finance_nodes: root ? root.getElementsByTagName('*').length : 0,
    board_nodes: board ? board.getElementsByTagName('*').length : 0,
    finance_table_rows: root ? root.querySelectorAll('table tbody tr').length : 0,
    board_cards: board ? board.querySelectorAll('.kb-card').length : 0
  };
}
"""


PAGE_STATE = r"""
() => {
  const state = window.__v2PerfRegression;
  if (!state) return null;
  return {
    phase: state.phase,
    events: state.events.slice(),
    long_tasks: state.longTasks.slice(),
    long_task_supported: state.longTaskSupported,
    long_task_error: state.longTaskError,
    tab_clicks: state.tabClicks.slice()
  };
}
"""


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("нужно положительное целое число")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("нужно неотрицательное целое число")
    return number


def validate_base(raw_base):
    try:
        parsed = urlsplit(raw_base)
        port = parsed.port
    except ValueError as error:
        raise ValueError("некорректный URL: %s" % error)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("разрешены только http:// и https://")
    if parsed.username or parsed.password:
        raise ValueError("логин и пароль в --base не допускаются")
    if parsed.query or parsed.fragment:
        raise ValueError("query и fragment в --base не допускаются")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError("hostname должен быть localhost, 127.0.0.1 или [::1]")
    if not host:
        raise ValueError("в --base отсутствует hostname")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("порт должен быть 1..65535")
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/") + "/", "", ""))


def safe_url(raw_url):
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = "[%s]" % host
        if parsed.port:
            host += ":%d" % parsed.port
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:
        return "<invalid-url>"


def origin_key(raw_url):
    parsed = urlsplit(raw_url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, host, port


def safe_text(value, limit=500):
    text = str(value or "")
    for secret in sorted((item for item in SECRET_VALUES if item), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", text)
    text = re.sub(
        r"(?i)((?:access_token|refresh_token|token|password|secret|authorization)\s*[:=]\s*[\"']?)[^\"',}\s]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(r"(?i)((?:token|password|secret|authorization)=)[^&\s]+", r"\1<redacted>", text)
    return text[:limit]


def request_key(request):
    """Ключ запроса без приватного внутреннего API Playwright."""
    return (request.method.upper(), request.url, request.post_data or "")


def first_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def median_or_zero(values):
    return round(float(statistics.median(values)), 2) if values else 0.0


def frame_settle(page, frames=2):
    page.evaluate(
        """(count) => Promise.race([
          new Promise((resolve) => {
            const tick = () => { if (--count <= 0) resolve(); else requestAnimationFrame(tick); };
            tick();
          }),
          new Promise((resolve, reject) => setTimeout(() => reject(new Error('frame settle timeout')), 2500))
        ])""",
        frames,
    )


def wait_network_quiet(page, timeout_ms=5000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        page.wait_for_timeout(250)
        return True
    except Exception:
        return False


def login(page, base, user, password, timeout_ms):
    page.goto(base + "#board", wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_selector("#login-username", state="visible", timeout=min(timeout_ms, 15000))
    except Exception:
        if page.locator("#login-overlay").get_attribute("hidden") is False:
            raise RuntimeError("логин недоступен на локальном стенде")
    else:
        page.fill("#login-username", user)
        page.fill("#login-password", password)
        page.click("#login-submit")
        page.wait_for_function(
            "() => document.getElementById('login-overlay')?.hidden !== false",
            timeout=timeout_ms,
        )
    page.wait_for_function("() => !!window.KBData", timeout=timeout_ms)
    page.wait_for_selector("#view-board", state="attached", timeout=timeout_ms)
    return wait_network_quiet(page, min(timeout_ms, 5000))


def navigate(page, route, timeout_ms=25000):
    selector = '.rail [data-view="%s"]' % route
    page.locator(selector).click(timeout=timeout_ms)
    page.wait_for_function(
        "(route) => { const view = document.getElementById('view-' + route); return !!view && !view.hidden && view.classList.contains('active'); }",
        route,
        timeout=timeout_ms,
    )
    frame_settle(page)


def cdp_snapshot(client):
    raw = client.send("Performance.getMetrics").get("metrics", [])
    values = {item["name"]: item.get("value", 0) for item in raw}
    return {key: values.get(key, 0) for key in CDP_KEYS}


def cdp_delta(before, after):
    result = {}
    for key in CDP_KEYS:
        if key == "Nodes":
            result["Nodes_current_change"] = round(float(after.get(key, 0)) - float(before.get(key, 0)), 6)
            continue
        delta = float(after.get(key, 0)) - float(before.get(key, 0))
        result[key] = round(delta, 6)
        if key.endswith("Duration"):
            result[key + "_ms"] = round(delta * 1000.0, 2)
    return result


def measure_tabs(page):
    results = []
    # Установка состояния до observer нужна для симметричного чтения курсора.
    page.evaluate("() => { window.__v2PerfMutationState = {mutations: []}; }")
    page.evaluate(
        """() => {
            const root = document.getElementById('view-fin');
          const state = {mutations: []};
          const observer = new MutationObserver((records) => {
            for (const record of records) {
              const element = record.target && record.target.nodeType === Node.ELEMENT_NODE
                ? record.target : record.target && record.target.parentElement;
              const panel = element && element.closest ? element.closest('[data-fin-panel]') : null;
              const table = element && element.closest ? element.closest('table, tbody') : null;
              state.mutations.push({
                t: Math.round(performance.now() * 100) / 100,
                type: record.type,
                target: element ? (element.id ? '#' + element.id : element.tagName.toLowerCase()) : 'non-element',
                added: record.addedNodes.length,
                removed: record.removedNodes.length,
                hidden_panel: false,
                hidden_table_or_tbody: false,
                panel: panel ? panel.id : null,
                table: table ? table.id || table.tagName.toLowerCase() : null
              });
            }
          });
          observer.observe(root, {childList: true, subtree: true, attributes: true, characterData: true});
          window.__v2PerfMutationState = state;
          window.__v2PerfMutationObserver = observer;
        }"""
    )
    for repetition in range(1, 4):
        for tab in FIN_TABS:
            start_index = page.evaluate("() => window.__v2PerfMutationState.mutations.length")
            before_tab_clicks = page.evaluate("() => window.__v2PerfRegression.tabClicks.length")
            started = time.perf_counter()
            page.locator('[data-fin-tab="%s"]' % tab).click(timeout=25000)
            page.wait_for_function(
                "(tab) => { const b = document.querySelector('[data-fin-tab=\"' + tab + '\"]'); const p = b && document.getElementById(b.getAttribute('aria-controls')); return !!b && b.getAttribute('aria-pressed') === 'true' && !!p && !p.hidden; }",
                tab,
                timeout=25000,
            )
            wall_ms = (time.perf_counter() - started) * 1000.0
            frame_settle(page)
            page.wait_for_timeout(40)
            tab_clicks = page.evaluate(PAGE_STATE)["tab_clicks"]
            click = tab_clicks[before_tab_clicks] if len(tab_clicks) > before_tab_clicks else None
            end_index = page.evaluate("() => window.__v2PerfMutationState.mutations.length")
            mutations = page.evaluate("(start, end) => window.__v2PerfMutationState.mutations.slice(start, end)", start_index, end_index)
            active_panel = page.evaluate(
                "(tab) => { const b = document.querySelector('[data-fin-tab=\"' + tab + '\"]'); return b && document.getElementById(b.getAttribute('aria-controls'))?.id || null; }",
                tab,
            )
            for item in mutations:
                item["hidden_panel"] = bool(item.get("panel") and item.get("panel") != active_panel)
                item["hidden_table_or_tbody"] = bool(item.get("table") and item.get("panel") != active_panel)
            overlap = []
            if click:
                click_start = float(click.get("start_ms", 0))
                click_end = float(click.get("outcome_ms", click_start))
                page_long_tasks = page.evaluate(PAGE_STATE)["long_tasks"]
                overlap = [
                    task for task in page_long_tasks
                    if float(task["start_ms"]) <= click_end
                    and float(task["start_ms"]) + float(task["duration_ms"]) >= click_start
                ]
            results.append({
                "repetition": repetition,
                "tab": tab,
                "click_dom_ms": round(float(click.get("duration_ms", 0)), 2) if click else None,
                "playwright_wall_ms": round(wall_ms, 2),
                "synchronous_outcome": bool(click and click.get("synchronous")),
                "dom_mutations": len(mutations),
                "hidden_panel_mutations": sum(1 for item in mutations if item.get("hidden_panel")),
                "hidden_table_tbody_mutations": sum(1 for item in mutations if item.get("hidden_table_or_tbody")),
                "long_tasks": overlap,
                "long_task_count": len(overlap),
                "long_task_total_ms": round(sum(float(task["duration_ms"]) for task in overlap), 2),
                "mutation_samples": mutations[:20],
            })
    observer_enabled = page.evaluate("() => { if (window.__v2PerfMutationObserver) window.__v2PerfMutationObserver.disconnect(); return true; }")
    durations = [item["click_dom_ms"] for item in results if item["click_dom_ms"] is not None]
    long_tasks = [task for item in results for task in item["long_tasks"]]
    return {
        "watcher_supported": page.evaluate("() => !!(window.MutationObserver && window.__v2PerfMutationState)"),
        "observer_enabled": observer_enabled,
        "clicks": results,
        "click_count": len(results),
        "click_samples_complete": len(durations) == len(FIN_TABS) * 3,
        "synchronous_outcomes_complete": all(item["synchronous_outcome"] for item in results),
        "click_dom_median_ms": median_or_zero(durations),
        "click_dom_max_ms": round(max(durations), 2) if durations else 0.0,
        "long_task_numeric": {
            "count": len(long_tasks),
            "total_ms": round(sum(float(task["duration_ms"]) for task in long_tasks), 2),
            "max_ms": round(max((float(task["duration_ms"]) for task in long_tasks), default=0), 2),
        },
    }



def measure_navigation(page, cycles=30, timeout_ms=25000):
    warmup_before = page.evaluate(DOM_METRICS)
    navigate(page, "fin", timeout_ms)
    navigate(page, "board", timeout_ms)
    warmup_after = page.evaluate(DOM_METRICS)
    rows = []
    for index in range(1, cycles + 1):
        before = page.evaluate(DOM_METRICS)
        navigate(page, ROUTE_ORDER[1], timeout_ms)
        navigate(page, ROUTE_ORDER[0], timeout_ms)
        after = page.evaluate(DOM_METRICS)
        rows.append({
            "cycle": index,
            "before": before,
            "after": after,
            "delta": {key: after[key] - before[key] for key in before},
        })
    deltas = [row["delta"]["total_nodes"] for row in rows]
    max_growth = max(max(0, delta) for delta in deltas)
    return {
        "route_cycle": "board -> fin -> board",
        "cycles": cycles,
        "warmup": {"before": warmup_before, "after": warmup_after},
        "rows": rows,
        "total_node_growth": rows[-1]["after"]["total_nodes"] - rows[0]["before"]["total_nodes"],
        "max_cycle_growth": max_growth,
    }


def photo_summary(entries):
    return {
        "total": len(entries),
        "failed": sum(1 for item in entries if item.get("failed")),
        "peak_concurrency": max((item.get("peak_after", 0) for item in entries), default=0),
        "requests": entries,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Read-only performance regression harness для локального стенда CRM v2.",
        epilog=(
            "Назначение: сравнивать осторожные DOM/CDP/network-метрики v2 между прогонами. "
            "CRM_USER/CRM_PASS (или V2_USER/V2_PASS) читаются только из окружения; "
            "credentials не попадают в JSON. --base обязателен и допускает только localhost."
        ),
    )
    parser.add_argument("--base", required=True, help="локальный URL стенда, например http://127.0.0.1:8125/v2/")
    parser.add_argument("--out", default="/tmp/v2_perf_regression.json", help="путь JSON-отчёта")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--label", default="run", help="метка прогона")
    parser.add_argument("--strict", action="store_true", help="ненулевой код при провале осторожных gates")
    parser.add_argument("--photo-concurrency-cap", type=positive_int, default=None,
                        help="необязательный cap пиковой параллельности фото; gate только при заданном cap")
    parser.add_argument("--dom-growth-tolerance", type=nonnegative_int, default=2,
                        help="допустимый рост DOM total nodes за цикл навигации (по умолчанию 2)")
    parser.add_argument("--timeout", type=positive_int, default=25000, help="таймаут browser interaction, мс")
    args = parser.parse_args()

    try:
        base = validate_base(args.base)
    except ValueError as error:
        print("Небезопасный или некорректный --base: %s" % error, file=sys.stderr)
        return 2

    user = first_env("CRM_USER", "V2_USER")
    password = first_env("CRM_PASS", "V2_PASS")
    if not user or not password:
        print("Нужны CRM_USER/CRM_PASS или V2_USER/V2_PASS в окружении", file=sys.stderr)
        return 2
    SECRET_VALUES[:] = [user, password]
    allowed_origin = origin_key(base)
    login_path = "/auth/login"

    out_path = os.path.abspath(args.out)
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    except OSError as error:
        print("Не удалось подготовить каталог для --out: %s" % error, file=sys.stderr)
        return 2

    report = {
        "schema_version": 1,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": base,
        "theme": args.theme,
        "label": safe_text(args.label, 120),
        "read_only": True,
        "config": {
            "tab_switches_per_tab": 3,
            "navigation_cycles": 30,
            "photo_concurrency_cap": args.photo_concurrency_cap,
            "dom_growth_tolerance": args.dom_growth_tolerance,
        },
        "listeners": {"phase_events": "addInitScript", "limitation": None},
        "initial": {},
        "finance_tabs": {},
        "navigation": {},
        "photos": {},
        "events": {},
        "cdp": {},
        "dom": {},
        "pageerrors": [],
        "network_failures": [],
        "network_quiet_failures": [],
        "unsafe_requests": [],
        "harness_errors": [],
    }
    stage = "initial"
    request_stage = {}
    photo_entries = []
    photo_peak = 0
    photo_by_request = {}
    pageerrors = report["pageerrors"]
    network_failures = report["network_failures"]
    unsafe_request_ids = set()
    blocked_request_ids = set()
    phase_events = {"initial": [], "phase2": []}
    all_events = []
    long_task_state = {"supported": False, "error": None, "count": 0, "total_ms": 0.0, "max_ms": 0.0}

    def on_pageerror(error):
        pageerrors.append({"stage": stage, "text": safe_text(error)})

    def on_request(request):
        method = request.method.upper()
        parsed = urlsplit(request.url)
        path = parsed.path
        request_key_value = request_key(request)
        request_stage.setdefault(request_key_value, []).append(stage)
        if method == "GET" and origin_key(request.url) == allowed_origin and PHOTO_PATH.search(path):
            entry = {
                "url": safe_url(request.url),
                "method": method,
                "stage": stage,
                "status": None,
                "failed": False,
            }
            photo_peak = max(
                photo_peak,
                sum(1 for queue in photo_by_request.values() for value in queue if value["active"]) + 1,
            )
            entry["peak_after"] = photo_peak
            photo_by_request.setdefault(request_key_value, []).append({"active": True, "entry": entry, "peak": photo_peak})
            photo_entries.append(entry)
        if method in {"POST", "PATCH", "PUT", "DELETE"}:
            allowed_login = method == "POST" and origin_key(request.url) == allowed_origin and path.rstrip("/") == login_path
            if not allowed_login:
                unsafe_request_ids.add(request_key_value)
                unsafe_request = {"method": method, "url": safe_url(request.url), "stage": stage}
                report["unsafe_requests"].append(unsafe_request)

    def on_response(response):
        try:
            status = response.status
        except Exception:
            return
        request = response.request
        request_key_value = request_key(request)
        stage_queue = request_stage.get(request_key_value, [])
        response_stage = stage_queue.pop(0) if stage_queue else stage
        photo_queue = photo_by_request.get(request_key_value, [])
        photo = photo_queue.pop(0) if photo_queue else None
        if photo and not photo["entry"].get("finished"):
            photo["entry"]["status"] = status
            photo["entry"]["failed"] = status >= 400
            photo["entry"]["finished"] = True
            photo["active"] = False
        if not photo_queue:
            photo_by_request.pop(request_key_value, None)
        if status >= 400:
            network_failures.append({
                "method": request.method,
                "url": safe_url(request.url),
                "status": status,
                "stage": response_stage,
            })

    def on_requestfailed(request):
        request_key_value = request_key(request)
        if request_key_value in blocked_request_ids:
            return
        failure = request.failure or "transport failure"
        stage_queue = request_stage.get(request_key_value, [])
        failure_stage = stage_queue.pop(0) if stage_queue else stage
        network_failures.append({
            "method": request.method,
            "url": safe_url(request.url),
            "status": 0,
            "stage": failure_stage,
            "error": safe_text(failure, 160),
        })
        photo_queue = photo_by_request.get(request_key_value, [])
        photo = photo_queue.pop(0) if photo_queue else None
        if photo and not photo["entry"].get("finished"):
            photo["entry"]["failed"] = True
            photo["entry"]["error"] = safe_text(failure, 160)
            photo["entry"]["finished"] = True
            photo["active"] = False
        if not photo_queue:
            photo_by_request.pop(request_key_value, None)

    def on_console(message):
        if message.type == "error":
            # Диагностика не превращается в pageerror; credentials не печатаются.
            report.setdefault("console_errors", []).append({"stage": stage, "text": safe_text(message.text, 300)})

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1600, "height": 950})
            context.add_init_script(
                script=INIT_SCRIPT
                .replace("__EVENT_NAMES__", json.dumps(list(EVENT_NAMES)))
                .replace("__V2_THEME__", json.dumps(args.theme)),
            )
            page = context.new_page()
            page.set_default_timeout(args.timeout)
            page.on("pageerror", on_pageerror)
            page.on("console", on_console)
            page.on("request", on_request)
            page.on("response", on_response)
            page.on("requestfailed", on_requestfailed)

            def guard_route(route, request):
                method = request.method.upper()
                parsed = urlsplit(request.url)
                same_origin = origin_key(request.url) == allowed_origin
                allowed_login = method == "POST" and same_origin and parsed.path.rstrip("/") == login_path
                if not same_origin or (method in {"POST", "PATCH", "PUT", "DELETE"} and not allowed_login):
                    blocked_request_ids.add(request_key(request))
                    route.abort("blockedbyclient")
                else:
                    route.continue_()

            context.route("**/*", guard_route)
            client = context.new_cdp_session(page)
            client.send("Performance.enable")

            stage = "login"
            if not login(page, base, user, password, args.timeout):
                report["network_quiet_failures"].append({"stage": stage})
            page.wait_for_timeout(500)
            initial_dom = page.evaluate(DOM_METRICS)
            initial_state = page.evaluate(PAGE_STATE)
            report["initial"] = {
                "dom": initial_dom,
                "events": initial_state.get("events", []),
                "long_task_numeric": {
                    "supported": initial_state.get("long_task_supported", False),
                    "error": initial_state.get("long_task_error"),
                    "count": len(initial_state.get("long_tasks", [])),
                    "total_ms": round(sum(float(task["duration_ms"]) for task in initial_state.get("long_tasks", [])), 2),
                    "max_ms": round(max((float(task["duration_ms"]) for task in initial_state.get("long_tasks", [])), default=0), 2),
                },
            }
            initial_cdp = cdp_snapshot(client)

            page.evaluate("() => { if (window.__v2PerfRegression) window.__v2PerfRegression.phase = 'phase2'; }")
            stage = "finance_closed_details"
            closed_start = len(photo_entries)
            navigate(page, "fin", args.timeout)
            if not wait_network_quiet(page, min(args.timeout, 5000)):
                report["network_quiet_failures"].append({"stage": stage})
            fixture_source_available = page.evaluate(
                "() => !!(window.KB_FIN_SOURCE && Array.isArray(window.KB_FIN_SOURCE.incoming))"
            )
            fixture_photo_count = page.evaluate(
                """() => {
                  const source = window.KB_FIN_SOURCE || {};
                  const incoming = Array.isArray(source.incoming) ? source.incoming : [];
                  return incoming.reduce((sum, item) => sum + (Array.isArray(item.photoPaths) ? item.photoPaths.length : 0), 0);
                }"""
            )
            details_before = page.evaluate(
                """() => ({
                  incomingDetails: document.querySelectorAll('#fin-incoming-rows details').length,
                  closedDetails: Array.from(document.querySelectorAll('#fin-incoming-rows details')).filter((d) => !d.open).length,
                  photoButtons: document.querySelectorAll('#fin-incoming-rows .fin-inc-photo').length,
                  photoImages: document.querySelectorAll('#fin-incoming-rows img.fin-inc-photo-image').length
                })"""
            )
            closed_photo_entries = photo_entries[closed_start:]
            closed_requests = sum(1 for item in closed_photo_entries if item.get("stage") == "finance_closed_details")
            stage = "finance_photo_details"
            open_start = len(photo_entries)
            photo_detail = page.locator(
                "#fin-incoming-rows details:has(.fin-inc-photo), #fin-incoming-rows details:has(img.fin-inc-photo-image)"
            ).first
            open_detail_result = {"available": photo_detail.count() > 0, "opened": False, "expected_images": 0}
            if open_detail_result["available"]:
                open_detail_result["expected_images"] = photo_detail.locator(".fin-inc-photo, img.fin-inc-photo-image").count()
                photo_detail.locator("summary").click()
                open_detail_result["opened"] = True
                frame_settle(page)
                if not wait_network_quiet(page, min(args.timeout, 5000)):
                    report["network_quiet_failures"].append({"stage": stage})
            visible_open_images = 0
            if open_detail_result["opened"]:
                visible_open_images = photo_detail.locator("img.fin-inc-photo-image").count()
            image_info = page.evaluate(
                """() => {
                  const images = Array.from(document.querySelectorAll('#fin-incoming-rows img.fin-inc-photo-image'));
                  return {
                    count: images.length,
                    complete: images.filter((img) => img.complete).length,
                    natural_width_positive: images.filter((img) => img.naturalWidth > 0).length,
                    visible: images.filter((img) => !!(img.offsetWidth || img.offsetHeight || img.getClientRects().length)).length
                  };
                }"""
            )
            opened_requests = photo_entries[open_start:]
            report["photos"] = {
                "fixture_source_available": fixture_source_available,
                "fixture_photo_count": fixture_photo_count,
                "closed_details": {
                    "before_open": details_before,
                    "requests": closed_requests,
                    "unexpected": closed_requests,
                },
                "opened_details": {
                    **open_detail_result,
                    "requests_during_open": len(opened_requests),
                    "image_info": image_info,
                    "visible_open_images": visible_open_images,
                    "natural_width_ok": (
                        image_info["count"] > 0
                        and image_info["natural_width_positive"] == image_info["count"]
                        and visible_open_images >= open_detail_result["expected_images"]
                    ),
                },
                **photo_summary(photo_entries),
            }
            if open_detail_result["opened"]:
                try:
                    photo_detail.locator("summary").click()
                except Exception:
                    pass
            stage = "finance_tabs"
            report["finance_tabs"] = measure_tabs(page)
            stage = "navigation"
            report["navigation"] = measure_navigation(page, cycles=30, timeout_ms=args.timeout)
            stage = "final"
            final_dom = page.evaluate(DOM_METRICS)
            final_state = page.evaluate(PAGE_STATE)
            all_events = final_state.get("events", [])
            for event in all_events:
                if event.get("phase") in phase_events:
                    phase_events[event["phase"]].append(event)
            all_long_tasks = final_state.get("long_tasks", [])
            long_task_state = {
                "supported": final_state.get("long_task_supported", False),
                "error": final_state.get("long_task_error"),
                "count": len(all_long_tasks),
                "total_ms": round(sum(float(task["duration_ms"]) for task in all_long_tasks), 2),
                "max_ms": round(max((float(task["duration_ms"]) for task in all_long_tasks), default=0), 2),
            }
            final_cdp = cdp_snapshot(client)
            browser.close()
    except Exception as error:
        report["harness_errors"].append({"stage": stage, "text": safe_text(error, 800)})
        final_cdp = locals().get("final_cdp", initial_cdp if "initial_cdp" in locals() else {})

    report["dom"] = {
        "initial": report.get("initial", {}).get("dom", {}),
        "final": report.get("dom", {}).get("final", {}) or (report.get("initial", {}).get("dom", {}) if not report["harness_errors"] else {}),
        "navigation_warmup": report.get("navigation", {}).get("warmup", {}),
    }
    # final_dom сохраняем явно, даже если отчёт уже частично собран.
    if "final_dom" not in locals():
        final_dom = report.get("initial", {}).get("dom", {})
    report["dom"]["final"] = final_dom
    if report.get("initial", {}).get("dom") and report["navigation"].get("warmup"):
        report["dom"]["initial_after_warmup"] = report["navigation"]["warmup"]["before"]
    cdp_initial = locals().get("initial_cdp", {})
    cdp_final = locals().get("final_cdp", cdp_initial)
    report["cdp"] = {
        "initial": cdp_initial,
        "final": cdp_final,
        "delta": cdp_delta(cdp_initial, cdp_final),
    }
    report["events"] = {
        "listener_installation": "addInitScript",
        "limitation": None,
        "counts": {phase: len(items) for phase, items in phase_events.items()},
        "items": phase_events,
    }
    report["long_tasks"] = long_task_state
    report["checks"] = {
        "pageerror_zero": not report["pageerrors"],
        "network_failures_zero": not report["network_failures"],
        "network_quiet": not report["network_quiet_failures"],
        "no_unsafe_requests": not report["unsafe_requests"],
        "navigation_dom_growth_within_tolerance": (
            report.get("navigation", {}).get("max_cycle_growth", 0) <= args.dom_growth_tolerance
        ),
        "hidden_finance_mutations_zero": sum(
            int(item.get("hidden_panel_mutations", 0))
            for item in report.get("finance_tabs", {}).get("clicks", [])
        ) == 0,
        "finance_click_samples_complete": bool(report.get("finance_tabs", {}).get("click_samples_complete")),
        "finance_synchronous_outcomes_complete": bool(report.get("finance_tabs", {}).get("synchronous_outcomes_complete")),
        "closed_details_no_photo_requests": (
            report.get("photos", {}).get("closed_details", {}).get("unexpected", 0) == 0
        ),
        "expected_photos_loaded": (
            report.get("photos", {}).get("fixture_source_available", False)
            and (
                report.get("photos", {}).get("fixture_photo_count", 0) == 0
                and report.get("photos", {}).get("closed_details", {}).get("before_open", {}).get("photoButtons", 1) == 0
                or bool(report.get("photos", {}).get("opened_details", {}).get("natural_width_ok"))
            )
        ),
    }
    if args.photo_concurrency_cap is not None:
        report["checks"]["photo_peak_within_cap"] = (
            report.get("photos", {}).get("peak_concurrency", 0) <= args.photo_concurrency_cap
        )
    else:
        report["checks"]["photo_peak_within_cap"] = None

    try:
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    except OSError as error:
        print("Не удалось записать JSON: %s" % error, file=sys.stderr)
        return 2

    photo = report.get("photos", {})
    tabs = report.get("finance_tabs", {})
    nav = report.get("navigation", {})
    cdp = report.get("cdp", {}).get("final", {})
    strict_failed = args.strict and not all(
        value for value in report["checks"].values() if value is not None
    )
    summary = (
        "v2 perf: label=%s theme=%s | pageerror=%d network=%d unsafe=%d | "
        "tabs=%d median=%.2fms long=%d | nav growth max=%d | photos total=%d failed=%d peak=%d closed=%d | "
        "CDP nodes=%s layout=%s script=%s | strict=%s | JSON=%s"
        % (
            safe_text(args.label, 80), args.theme, len(report["pageerrors"]), len(report["network_failures"]),
            len(report["unsafe_requests"]), tabs.get("click_count", 0), tabs.get("click_dom_median_ms", 0.0),
            tabs.get("long_task_numeric", {}).get("count", 0), nav.get("max_cycle_growth", 0),
            photo.get("total", 0), photo.get("failed", 0), photo.get("peak_concurrency", 0),
            photo.get("closed_details", {}).get("requests", 0), cdp.get("Nodes", 0),
            cdp.get("LayoutCount", 0), cdp.get("ScriptDuration", 0),
            ("FAIL" if strict_failed else ("PASS" if args.strict else "off")), out_path,
        )
    )
    print(summary)
    if report["harness_errors"]:
        print("harness: FAIL (%s)" % report["harness_errors"][0]["text"][:180], file=sys.stderr)
    failed = bool(
        report["pageerrors"]
        or report["network_failures"]
        or report["network_quiet_failures"]
        or report["unsafe_requests"]
        or report["harness_errors"]
    )
    if strict_failed:
        failed = True
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
