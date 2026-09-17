#!/usr/bin/env python3
"""Сборка site-v2 из прототипа tools/mockups (единый источник правды).

Генерирует развёртываемое приложение site-v2/:
- index.html: базовые стили вынесены в css/shell-v2-base.css, вместо демо-модулей
  подключается загрузчик js/v2/boot.js (api.js + boot.js), добавляется оверлей входа;
- js/ и css/ модули копируются из прототипа как есть;
- js/v2/api.js и js/v2/boot.js — адаптер источника данных (DEMO/API) и авторизация.

Запуск: python3 tools/build_site_v2.py
"""
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
MOCKUPS = ROOT / "tools" / "mockups"
OUT = ROOT / "site-v2"

API_JS = r"""/* site-v2: сессия и fetch к CRM API. Токен — Bearer из localStorage
   (конвенция старого фронта) плюс httpOnly-кука, которую ставит сервер. */
(function () {
    'use strict';
    const TOKEN_KEY = 'crm_token';
    const ROLE_KEY = 'crm_role';
    // База API: на проде фронт и API за одним nginx — пустая строка (same-origin).
    // Для локального стенда задаётся в localStorage: crm_api_base=http://127.0.0.1:8125
    const API_BASE = (function () {
        try { return localStorage.getItem('crm_api_base') || window.V2_API_BASE || ''; }
        catch (e) { return window.V2_API_BASE || ''; }
    })();
    function token() {
        try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
    }
    function save(tokenValue, role) {
        try {
            localStorage.setItem(TOKEN_KEY, tokenValue);
            if (role) localStorage.setItem(ROLE_KEY, role);
            localStorage.setItem('crm_logged_in', '1');
        } catch (e) { /* без хранилища — только httpOnly-кука */ }
    }
    function clear() {
        try {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(ROLE_KEY);
            localStorage.removeItem('crm_logged_in');
        } catch (e) { /* ignore */ }
        document.cookie = 'crm_token=; path=/; max-age=0';
    }
    async function api(path, opts) {
        opts = opts || {};
        const headers = Object.assign({}, opts.headers || {});
        const t = token();
        if (t) headers['Authorization'] = 'Bearer ' + t;
        if (opts.form) headers['Content-Type'] = 'application/x-www-form-urlencoded';
        else if (opts.body) headers['Content-Type'] = 'application/json';
        const response = await fetch(API_BASE + path, {
            method: opts.method || 'GET',
            headers: headers,
            credentials: 'include',
            body: opts.form ? new URLSearchParams(opts.form) : (opts.body ? JSON.stringify(opts.body) : undefined)
        });
        if (response.status === 401) {
            const err = new Error('unauthorized');
            err.unauthorized = true;
            throw err;
        }
        if (!response.ok) {
            const err = new Error('HTTP ' + response.status);
            err.status = response.status;
            try { const data = await response.json(); err.detail = data && data.detail || null; } catch (e) { /* без тела */ }
            throw err;
        }
        return response.json();
    }
    window.V2Api = {
        token: token,
        save: save,
        clear: clear,
        api: api,
        login: function (username, password, remember) {
            return api('/auth/login', { method: 'POST', form: { username: username, password: password, remember: remember ? 'true' : '' } });
        },
        me: function () { return api('/auth/me'); },
        users: function () { return api('/auth/users'); }
    };
})();
"""

BOOT_JS = r"""/* site-v2: загрузчик. Авторизация → чтение коллекций → сборка KBData в
   формах прототипа → подключение интерфейсных модулей. ?demo=1 или
   недоступный API — работа на демо-данных с честной пометкой.
   Этап 0 плана замены: только чтение; мутации живут до перезагрузки. */
(function () {
    'use strict';
    const DEMO = /[?&]demo=1/.test(location.search);
    // Элемент ищется на каждый вызов: boot стартует в <head>, до готовности DOM.
    const setText = function (t) {
        const el = document.getElementById('shell-source');
        if (el) el.textContent = t;
    };
    function inject(src) {
        return new Promise(function (resolve, reject) {
            const s = document.createElement('script');
            s.src = src;
            s.onload = resolve;
            s.onerror = function () { reject(new Error('не загрузился ' + src)); };
            document.head.appendChild(s);
        });
    }
    async function injectAll(list) { for (const src of list) await inject(src); }

    // Канон статусов сделки (schemas.CARD_STATUSES) → идентификаторы/цвета v2.
    const STATUS_MAP = [
        ['Новый запрос', 'new', 'faint', 'board'],
        ['В работе', 'work', 'accent', 'board'],
        ['Ждет оплаты', 'pay', 'warn', 'board'],
        ['Сборка', 'assembly', 'ok', 'board'],
        ['На списание', 'writeoff', 'danger', 'writeoff']
    ];
    const STAGE_OF_STATUS = {
        'Новый запрос': 'new', 'В работе': 'work', 'Ждет оплаты': 'pay',
        'Сборка': 'assembly', 'На списание': 'writeoff', 'Закрыто': 'done'
    };
    function slug(name) {
        return 'st-' + String(name).toLowerCase().replace(/[^a-zа-я0-9]+/gi, '-').replace(/^-|-$/g, '');
    }
    function kopecks(value) { return Math.round((Number(value) || 0) * 100); }
    function isoToRu(value) {
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
        return m ? m[3] + '.' + m[2] + '.' + m[1] : '';
    }
    function initials(name) {
        const clean = String(name || '').replace(/[^A-Za-zА-Яа-яЁё ]/g, ' ').trim();
        const words = clean.split(/\s+/).filter(Boolean);
        return (words.length > 1 ? words[0][0] + words[1][0] : clean.slice(0, 2)).toUpperCase() || '··';
    }

    function buildKbData(payload) {
        const cardsRaw = payload.cards, clientsRaw = payload.clients, usersRaw = payload.users,
              suppliersRaw = payload.suppliers, tasksRaw = payload.tasks;

        const statuses = STATUS_MAP.map(function (row, i) {
            return { id: row[1], name: row[0], color: row[2], position: i, role: row[3] };
        });
        const stageOf = {
            'Новый запрос': 'new', 'В работе': 'work', 'Ждет оплаты': 'pay',
            'Сборка': 'assembly', 'На списание': 'writeoff', 'Закрыто': 'done'
        };

        const users = usersRaw.map(function (u) {
            return { id: 'us-' + u.id, username: u.username, full_name: u.username, initials: initials(u.username), role: u.role };
        });
        const unassigned = { id: 'us-none', username: '—', full_name: 'Не назначен', initials: '··', role: 'manager' };

        const clients = clientsRaw.map(function (c) {
            return { id: 'cl-' + c.id, name: c.name, unp: c.unp || '', contact_person: c.contact_person || '', phone: c.phone || '', address: c.address || '' };
        });
        const suppliers = suppliersRaw.map(function (s) {
            return { id: 'sup-' + s.id, name: s.name, unp: s.unp || '', contact_person: s.contact_person || '', phone: s.phone || '', email: s.email || '', address: s.address || '' };
        });

        // Магазины — из фактических значений сделок (эндпоинта справочника нет).
        const storeNames = [];
        cardsRaw.forEach(function (c) {
            const name = String(c.store_location || '').trim();
            if (name && storeNames.indexOf(name) < 0) storeNames.push(name);
        });
        const stores = storeNames.map(function (name) { return { id: name, name: name }; });

        const userById = {};
        users.forEach(function (u) { userById['us-' + u.id.replace('us-', '')] = u; });
        const clientById = {};
        clients.forEach(function (c) { clientById[c.id] = c; });

        // Неизвестные статусы (если появятся) — дополнительные колонки.
        cardsRaw.forEach(function (c) {
            const status = String(c.status || '');
            if (status && !stageOf[status] && status !== 'Закрыто') {
                const id = slug(status);
                stageOf[status] = id;
                statuses.push({ id: id, name: status, color: 'faint', position: 100 + statuses.length, role: 'board' });
            }
        });

        const cards = cardsRaw.map(function (c) {
            const clientObj = c.client ? clientById['cl-' + c.client.id] : null;
            const manager = (c.owner_id && userById['us-' + c.owner_id]) || unassigned;
            return {
                id: String(c.id),
                title: c.title || 'Сделка',
                clientId: clientObj ? clientObj.id : null,
                client: clientObj ? clientObj.name : (c.title || 'Без клиента'),
                store: String(c.store_location || '').trim() || (stores[0] && stores[0].id) || '',
                manager: manager,
                amount: kopecks(c.total_amount),
                paidAmount: kopecks(c.paid_amount),
                issued: 0, // история выписки подключается на этапе 2.6
                stage: stageOf[String(c.status || '')] || 'new',
                deadline: isoToRu(c.due_date),
                note: c.description || '',
                paymentTerms: '',
                paymentDetails: null,
                groupId: c.writeoff_group_id ? 'gr-' + c.writeoff_group_id : null,
                checklist: (c.checklists || []).map(function (item) {
                    return {
                        id: 'ck-' + item.id,
                        label: (item.supplier && item.supplier.name) || item.company_name || 'Позиция',
                        supplier_id: item.supplier_id ? 'sup-' + item.supplier_id : null,
                        ordered: false,
                        received: false
                    };
                }),
                docs: []
            };
        });

        const tasks = tasksRaw.map(function (t) {
            return {
                id: 'task-' + t.id,
                title: t.title,
                clientId: t.client_id ? 'cl-' + t.client_id : null,
                assignee: t.assignee_id ? 'us-' + t.assignee_id : null,
                due: isoToRu(t.due_date),
                done: t.status === 'done',
                closed: t.completed_at ? 'закрыто ' + isoToRu(t.completed_at) : undefined
            };
        });

        window.KBData = {
            today: new Date(),
            statuses: statuses,
            stores: stores,
            users: users.concat([unassigned]),
            clients: clients,
            suppliers: suppliers,
            tasks: tasks,
            cards: cards,
            groups: [],
            nextGroupId: 1
        };
        window.KBData.statusColor = function (status) {
            const value = String(status.color || '').replace(/^#/, '');
            return /^[0-9a-f]{6}$/i.test(value) ? '#' + value : 'var(--' + (value || 'faint') + ')';
        };
        window.KBData.boardStatuses = function () {
            return window.KBData.statuses.filter(function (s) { return s.role === 'board'; })
                .slice().sort(function (a, b) { return a.position - b.position; });
        };
        window.KBData.writeoffStatus = function () {
            return window.KBData.statuses.filter(function (s) { return s.role === 'writeoff'; })[0] || null;
        };
        window.KBData.storeName = function (id) { return String(id || ''); };
        window.KBData.clientById = function (id) {
            return window.KBData.clients.filter(function (c) { return c.id === id; })[0] || null;
        };
    }

    const APP_SCRIPTS = [
        'js/shell-v2-select.js', 'js/shell-v2-payment.js', 'js/shell-v2-management.js',
        'js/shell-v2-fin.js', 'js/shell-v2-board.js', 'js/shell-v2-insights.js',
        'js/shell-v2-navigation.js'
    ];
    const DEMO_SCRIPTS = ['js/shell-v2-data.js'].concat(APP_SCRIPTS);

    async function bootDemo() {
        setText('Предпросмотр · демо-данные (?demo=1)');
        await injectAll(DEMO_SCRIPTS);
    }
    async function bootApi() {
        setText('Подключение к CRM…');
        if (!window.V2Api.token()) { const e = new Error('unauthorized'); e.unauthorized = true; throw e; }
        const meUser = await window.V2Api.me();
        const results = await Promise.all([
            window.V2Api.api('/kanban/cards'),
            window.V2Api.api('/clients'),
            window.V2Api.users(),
            window.V2Api.api('/suppliers'),
            window.V2Api.api('/tasks'),
            window.V2Api.api('/payments/transactions'),
            window.V2Api.api('/payments/documents')
        ]);
        buildKbData({ cards: results[0], clients: results[1], users: results[2], suppliers: results[3], tasks: results[4] });
        window.KB_FIN_SOURCE = buildFinSource(window.KBData.cards, results[5], results[6]);
        enableMutations();
        renderUser(meUser);
        setText('Источник: CRM API · изменения до перезагрузки, запись — следующий этап плана');
        await injectAll(APP_SCRIPTS);
    }

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function renderUser(me) {
        const box = document.getElementById('v2-user');
        if (!box) return;
        box.innerHTML = '<span class="pill accent">' + esc(me.username) + ' · ' + esc(me.role || '') + '</span>' +
            '<button type="button" class="btn btn-ghost btn-sm" id="v2-logout">Выйти</button>';
        document.getElementById('v2-logout').addEventListener('click', async function () {
            try { await window.V2Api.api('/auth/logout', { method: 'POST' }); } catch (e) { /* сессия уже чиста */ }
            window.V2Api.clear();
            location.reload();
        });
    }
    // Реестр оплат v2: по каждой сделке — сумма, оплачено (итог минус остаток)
    // и последняя выписанная ТН. Остатки приходят записями is_document=false.
    function buildFinSource(cards, transactions, documents) {
        const remainder = {};
        (transactions || []).forEach(function (t) {
            if (t.is_document || t.card_id == null) return;
            remainder[t.card_id] = kopecks(t.amount);
        });
        const docsByCard = {};
        (documents || []).forEach(function (d) {
            if (d.card_id == null) return;
            (docsByCard[d.card_id] = docsByCard[d.card_id] || []).push(d);
        });
        const outgoing = cards.map(function (card) {
            const numeric = Number(card.id);
            const docs = (docsByCard[numeric] || []).slice().sort(function (a, b) {
                return new Date(b.date || 0) - new Date(a.date || 0);
            });
            const latest = docs[0] || null;
            const restKop = remainder[numeric] !== undefined ? remainder[numeric] : card.amount;
            return {
                id: 'c' + numeric,
                card: card.id + ' · ' + card.title,
                date: latest ? isoToRu(latest.invoice_date || latest.date) : card.deadline,
                client: card.client,
                amount: card.amount,
                paid: Math.max(0, card.amount - restKop),
                store: card.store,
                estimate: '',
                tn: latest ? { number: latest.invoice_number || '', date: isoToRu(latest.invoice_date || latest.date), bill: '' } : null,
                calculated: Boolean(latest && latest.is_calculated),
                posted: Boolean(latest && latest.is_written_off),
                tnHere: false,
                billHere: false,
                print: (latest && latest.print_status) || '',
                authority: '',
                note: ''
            };
        });
        return { outgoing: outgoing, incoming: [] };
    }
    // Запись (Этап 2.1/2.2): статус/поля карточки и задачи уходят в CRM API.
    // Неудача показывается тостом; локальное состояние правится отдельно
    // и выправится следующей перезагрузкой с сервера.
    function enableMutations() {
        window.KBData.mutate = async function (kind, payload) {
            try {
                if (kind === 'card') {
                    await window.V2Api.api('/cards/' + payload.id, { method: 'PATCH', body: payload.fields });
                } else if (kind === 'status') {
                    await window.V2Api.api('/kanban/cards/' + payload.id + '/status', { method: 'PATCH', body: { status: payload.status } });
                } else if (kind === 'task-status') {
                    await window.V2Api.api('/tasks/' + payload.id, { method: 'PATCH', body: { status: payload.status } });
                } else if (kind === 'task-create') {
                    await window.V2Api.api('/tasks', { method: 'POST', body: { title: payload.title, status: 'todo', due_date: payload.due || null, assignee_id: payload.assignee || null, client_id: payload.client || null } });
                } else {
                    return false;
                }
                return true;
            } catch (err) {
                console.error('Изменение не сохранено в CRM:', err);
                const toast = document.getElementById('kb-toast');
                if (toast) {
                    toast.hidden = false;
                    toast.textContent = 'Не сохранено в CRM: ' + (err.detail || err.message || 'ошибка');
                    setTimeout(function () { toast.hidden = true; }, 4000);
                }
                return false;
            }
        };
    }
    function showLogin(message) {
        setText('Требуется вход');
        const overlay = document.getElementById('login-overlay');
        if (!overlay) return;
        overlay.hidden = false;
        const errorEl = document.getElementById('login-error');
        if (message) errorEl.textContent = message;
        document.getElementById('login-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            errorEl.textContent = '';
            const button = document.getElementById('login-submit');
            button.disabled = true;
            try {
                const result = await window.V2Api.login(
                    document.getElementById('login-username').value.trim(),
                    document.getElementById('login-password').value,
                    document.getElementById('login-remember').checked
                );
                window.V2Api.save(result.access_token, result.role);
                overlay.hidden = true;
                await bootApi();
            } catch (err) {
                errorEl.textContent = err.unauthorized || err.status === 401 ? 'Неверное имя пользователя или пароль.' :
                    err.status === 429 ? 'Слишком много попыток входа. Подождите минуту.' :
                    'Сервер недоступен. Проверьте подключение и адрес API.';
                button.disabled = false;
            }
        });
        document.getElementById('login-username').focus();
    }

    // boot.js подключён в <head> — стартуем после разбора DOM,
    // иначе оверлей входа и контейнеры интерфейса ещё не существуют.
    async function start() {
        try {
            if (DEMO) { await bootDemo(); return; }
            try {
                await bootApi();
            } catch (err) {
                if (err && err.unauthorized) { showLogin(''); return; }
                console.error('API недоступен, переключаюсь на демо-данные:', err);
                setText('CRM API недоступен — показаны демо-данные');
                await bootDemo();
            }
        } catch (err) {
            console.error(err);
            setText('Ошибка загрузки: ' + (err && err.message || 'неизвестная'));
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
"""

V2_EXTRA_CSS = r"""/* site-v2: вход и статус источника (нет в прототипе) */
.login-overlay {
    position: fixed; inset: 0; background: var(--bg);
    display: flex; align-items: center; justify-content: center; z-index: 200;
}
.login-box {
    width: 360px; max-width: 92vw; background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--r-xl);
    box-shadow: var(--sh-2); padding: var(--s6); display: flex; flex-direction: column; gap: var(--s3);
}
.login-box h1 { font-size: var(--font-h1); letter-spacing: -.3px; }
.login-box .login-sub { color: var(--muted); font-size: var(--font-ui); margin-top: -6px; }
.login-box label { display: flex; flex-direction: column; gap: 4px; font-size: var(--font-ui); font-weight: 600; color: var(--muted); }
.login-box input[type="text"], .login-box input[type="password"] {
    font: inherit; font-size: var(--font-body); color: var(--text);
    background: var(--surface-2); border: 1px solid var(--border-strong);
    border-radius: var(--r-md); padding: 9px 11px; width: 100%;
}
.login-remember { display: flex; align-items: center; gap: 8px; min-height: 24px; font-size: var(--font-ui); color: var(--muted); cursor: pointer; }
.login-remember input { accent-color: var(--accent); }
.login-error { color: var(--danger); font-size: var(--font-ui); font-weight: 600; min-height: 1em; }
[hidden] { display: none !important; }
"""

HTML_TRANSFORMS = [
    # Демо-модуль данных и остальные скрипты подключает boot.js (см. regex ниже);
    # select.js меняем на пару адаптер+загрузчик
    ('<script src="./shell-v2-select.js" defer></script>', '<script src="js/v2/api.js"></script>\n<script src="js/v2/boot.js"></script>'),
    ('<p class="shell-preview">Предпросмотр · данные не подключены</p>', '<p class="shell-preview" id="shell-source">Загрузка…</p>'),
    ('    </header>', '      <div class="topbar-right"><span id="v2-user" class="row" style="gap:8px"></span></div>\n    </header>'),
    # Оверлей входа перед закрытием body
    ('</body>', """<div class="login-overlay" id="login-overlay" hidden>
  <form class="login-box" id="login-form">
    <h1>Свет в доме · CRM</h1>
    <p class="login-sub">Новый интерфейс (v2) · вход в существующий аккаунт</p>
    <label for="login-username">Логин<input id="login-username" type="text" autocomplete="username" required></label>
    <label for="login-password">Пароль<input id="login-password" type="password" autocomplete="current-password" required></label>
    <label class="login-remember"><input id="login-remember" type="checkbox"> Запомнить меня (30 дней)</label>
    <p class="login-error" id="login-error" role="alert"></p>
    <button class="btn btn-primary" id="login-submit" type="submit">Войти</button>
  </form>
</div>
</body>"""),
]

def main():
    html = (MOCKUPS / "shell-v2-prototype.html").read_text(encoding="utf-8")

    # 1. Базовые стили прототипа → отдельный файл
    style_match = re.search(r"<style>\n(.*?)\n</style>", html, re.S)
    assert style_match, "не найден <style> блок прототипа"
    base_css = style_match.group(1)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "css").mkdir(parents=True)
    (OUT / "js" / "v2").mkdir(parents=True)

    (OUT / "css" / "shell-v2-base.css").write_text(base_css + "\n\n" + V2_EXTRA_CSS, encoding="utf-8")

    # 2. Модульные css/js — как есть
    for css in MOCKUPS.glob("shell-v2-*.css"):
        shutil.copyfile(css, OUT / "css" / css.name)
    for js in MOCKUPS.glob("shell-v2-*.js"):
        shutil.copyfile(js, OUT / "js" / js.name)

    body = html

    # 3. Фин-скрипт прототипа (inline) → модуль js/shell-v2-fin.js
    fin_start = body.find("<script>\n(function () {\n    'use strict';\n    const root = document.getElementById('view-fin');")
    assert fin_start >= 0, "не найден inline-скрипт финансов"
    fin_end = body.index("</script>", fin_start) + len("</script>")
    fin_js = body[fin_start + len("<script>"):fin_end - len("</script>")].strip()
    (OUT / "js" / "shell-v2-fin.js").write_text(fin_js + "\n", encoding="utf-8")
    body = body[:fin_start] + body[fin_end:]

    # 3a. index.html: пути, вынос стилей, boot вместо демо-скриптов, оверлей входа
    body = body.replace('<style>\n' + base_css + '\n</style>', '<link rel="stylesheet" href="css/shell-v2-base.css">')
    for src in sorted(MOCKUPS.glob("shell-v2-*.css")):
        body = body.replace(f'<link rel="stylesheet" href="./{src.name}">', f'<link rel="stylesheet" href="css/{src.name}">')
    for old, new in HTML_TRANSFORMS:
        assert old in body, "не найден фрагмент: " + old[:60]
        body = body.replace(old, new)
    body = re.sub(r'<script src="\./shell-v2-[^"]*" defer></script>\n?', '', body)
    body = body.replace('<title>Доска сделок — Свет в доме · Предпросмотр</title>',
                        '<title>Свет в доме · CRM v2</title>')
    body = body.replace('<html lang="ru" data-theme="light">', '<html lang="ru" data-theme="light">\n<!-- Сгенерировано tools/build_site_v2.py из tools/mockups — не править вручную -->')
    (OUT / "index.html").write_text(body, encoding="utf-8")

    # 4. Адаптер и загрузчик
    (OUT / "js" / "v2" / "api.js").write_text(API_JS, encoding="utf-8")
    (OUT / "js" / "v2" / "boot.js").write_text(BOOT_JS, encoding="utf-8")

    # 5. README для каталога
    (OUT / "README.md").write_text(
        "# site-v2 — новый фронтенд (генерируется)\n\n"
        "Собирается из прототипа `tools/mockups` командой:\n\n"
        "    python3 tools/build_site_v2.py\n\n"
        "Вручную не править: правки делаются в прототипе или в "
        "`tools/build_site_v2.py` (встроенные api.js/boot.js), затем пересборка.\n\n"
        "Режимы: по умолчанию — CRM API (вход по учётке CRM); `?demo=1` — демо-данные.\n",
        encoding="utf-8")

    print(f"site-v2 собран: {OUT}")
    for f in sorted(OUT.rglob('*')):
        if f.is_file():
            print(' ', f.relative_to(OUT))

if __name__ == "__main__":
    main()
