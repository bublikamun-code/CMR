/* ============================================================
   Пульт дня и «Клиент 360»: считаются из тех же демо-данных,
   что и доска (shell-v2-data.js), и обновляются вместе с ней.
   Кнопки «Открыть» переключаются на доску и открывают карточку.
   Превью: задачи, печати и создание сделок остаются образцами.
   ============================================================ */
(function () {
    'use strict';
    var D = window.KBData;

    // «Только мои» (id пользователя или null), активная вкладка «Клиента 360»,
    // демонстрационный месяц календаря (сентябрь 2026).
    var myUser = null;
    var clTab = 'deals';
    var cal = { y: 2026, m: 8 };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function money(cents) {
        var sign = cents < 0 ? '−' : '';
        var abs = Math.abs(cents);
        var rub = Math.floor(abs / 100);
        var kop = ('0' + (abs % 100)).slice(-2);
        return sign + String(rub).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ',' + kop;
    }
    function parseDeadline(value) {
        var m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(String(value || ''));
        return m ? new Date(+m[3], +m[2] - 1, +m[1]) : null;
    }
    // B3 fix: серверные даты хранятся в UTC (миграция 0003), поэтому «сегодня»
    // для сравнений с дедлайнами/задачами берём по UTC, а не по локальному
    // часовому поясу. Иначе вечером UTC+3 фильтр «сегодня» уже показывает
    // завтрашний день. Источник истины — общий KBApi.todayUTC() из адаптера;
    // здесь только разбор строки в Date для арифметики dayDiff.
    function todayUTC() {
        var parts = window.KBApi.todayUTC().split('-');
        return new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2]));
    }
    function dayDiff(a, b) {
        return Math.round((b - a) / 86400000);
    }
    function overdueDays(card) {
        var deadline = parseDeadline(card.deadline);
        // Сравниваем с UTC-датой, чтобы серверные дедлайны (UTC) не разъезжались
        // относительно локального пояса браузера.
        return deadline ? dayDiff(deadline, todayUTC()) : 0;
    }
    function statusName(id) {
        var s = D.statuses.filter(function (x) { return x.id === id; })[0];
        return s ? s.name : id;
    }
    function activeCards() {
        return D.cards.filter(function (c) { return c.stage !== 'done'; });
    }
    function debtCards(cards) {
        return cards.filter(function (c) { return c.paidAmount < c.amount; });
    }

    var ICON_ALERT = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>';
    var ICON_CLOCK = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    var ICON_CHECK = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>';

    function qrow(kind, icon, title, meta, cardId, extraAction) {
        var buttons = '<button class="btn btn-primary btn-sm" data-open-card="' + esc(cardId) + '">Открыть</button>';
        if (extraAction) buttons = '<button class="btn btn-ghost btn-sm" data-open-card="' + esc(cardId) + '">' + esc(extraAction) + '</button>' + buttons;
        return '<div class="qrow ' + kind + '"><span class="lead">' + icon + '</span>' +
            '<div class="q-main"><div class="q-title">' + esc(title) + '</div><div class="q-meta">' + meta + '</div></div>' +
            '<div class="q-actions">' + buttons + '</div></div>';
    }

    // ---------- Пульт дня ----------
    function renderDay() {
        var active = activeCards();
        var funnel = active.reduce(function (a, c) { return a + c.amount; }, 0);
        var paid = active.reduce(function (a, c) { return a + c.paidAmount; }, 0);
        var debts = debtCards(active);
        var overdue = debts.filter(function (c) { return overdueDays(c) > 0; });
        function put(id, value) { var el = document.getElementById(id); if (el) el.innerHTML = value; }
        put('day-kpi-deals', String(active.length));
        put('day-kpi-funnel', money(funnel) + ' <small>BYN</small>');
        put('day-kpi-paid', money(paid) + ' <small>BYN</small>');
        put('day-kpi-overdue', overdue.length + ' <small>из ' + debts.length + '</small>');

        // Очередь: с фильтром «Только мои» — сделки выбранного ответственного
        function inScope(list) {
            return myUser ? list.filter(function (c) { return c.manager && c.manager.id === myUser; }) : list;
        }
        var rows = [];
        inScope(overdue).slice().sort(function (a, b) { return overdueDays(b) - overdueDays(a); }).slice(0, 3).forEach(function (c) {
            rows.push(qrow('hot', ICON_ALERT,
                c.client + ' — оплата просрочена на ' + overdueDays(c) + ' дн.',
                '<span>Оплата ' + money(c.amount - c.paidAmount) + ' BYN</span><span>' + esc(D.storeName(c.store)) + '</span>',
                c.id, 'Оплата'));
        });
        inScope(active.filter(function (c) {
            var diff = -overdueDays(c);
            return diff >= 0 && diff <= 3 && c.stage !== 'assembly';
        })).sort(function (a, b) { return overdueDays(b) - overdueDays(a); }).slice(0, 4).forEach(function (c) {
            rows.push(qrow('wait', ICON_CLOCK,
                c.title,
                '<span>Срок ' + esc(c.deadline) + '</span><span>' + esc(D.storeName(c.store)) + '</span>',
                c.id));
        });
        inScope(active.filter(function (c) { return c.stage === 'assembly'; })).slice(0, 3).forEach(function (c) {
            rows.push(qrow('ok', ICON_CHECK,
                c.title + ' — комплект собран',
                '<span>Сборка · ' + esc(D.storeName(c.store)) + '</span><span>' + esc(c.deadline) + '</span>',
                c.id, 'Отгрузить'));
        });
        rows = rows.slice(0, 6);
        var queue = document.getElementById('day-queue');
        if (queue) queue.innerHTML = rows.length ? rows.join('') :
            '<div class="qrow ok"><span class="lead">' + ICON_CHECK + '</span><div class="q-main"><div class="q-title">Всё под контролем</div><div class="q-meta">Просроченных оплат и близких сроков нет.</div></div></div>';
        var qOverdueCount = inScope(overdue).length;
        var count = document.getElementById('day-queue-count');
        if (count) count.textContent = String(qOverdueCount ? qOverdueCount : rows.length);
        var mineBtn = document.getElementById('day-only-mine');
        if (mineBtn) {
            var me = D.users.filter(function (u) { return u.id === myUser; })[0];
            mineBtn.textContent = me ? 'Мои · ' + (me.full_name || me.username) : 'Только мои';
            mineBtn.setAttribute('aria-pressed', String(!!me));
        }

        // Топ клиентов по сумме активных сделок
        var byClient = new Map();
        active.forEach(function (c) {
            byClient.set(c.clientId, (byClient.get(c.clientId) || 0) + c.amount);
        });
        var top = Array.from(byClient.entries()).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 4);
        var topEl = document.getElementById('day-top-clients');
        if (topEl) topEl.innerHTML = top.map(function (pair) {
            var client = D.clientById(pair[0]);
            return '<div class="stat-line"><span class="trunc">' + esc(client ? client.name : pair[0]) + '</span><b class="num">' + money(pair[1]) + '</b></div>';
        }).join('') || '<p class="fin-note">Активных сделок нет.</p>';

        // Дедлайны недели по ответственным
        var perUser = new Map();
        active.forEach(function (c) {
            var diff = -overdueDays(c);
            if (diff >= 0 && diff <= 7) perUser.set(c.manager, (perUser.get(c.manager) || 0) + 1);
        });
        var deadlinesEl = document.getElementById('day-deadlines');
        if (deadlinesEl) deadlinesEl.innerHTML = D.users.filter(function (u) { return perUser.get(u); }).map(function (u) {
            return '<div class="stat-line"><span class="row"><span class="avatar sm neutral">' + esc(u.initials || '··') + '</span> ' + esc(u.full_name || u.username) + '</span><b class="num">' + perUser.get(u) + '</b></div>';
        }).join('') || '<p class="fin-note">Сделок с дедлайном на этой неделе нет.</p>';

        // Ближайшие отгрузки: готовые сборки по сроку
        var shipmentsEl = document.getElementById('day-shipments');
        if (shipmentsEl) {
            var shipments = active.filter(function (c) { return c.stage === 'assembly'; })
                .sort(function (a, b) { return overdueDays(a) - overdueDays(b); })
                .slice(0, 5);
            shipmentsEl.innerHTML = shipments.map(function (c) {
                return '<div class="trow"><span class="grow"><span class="t">' + esc(c.title) + '</span>' +
                    '<div class="sub">' + esc(D.storeName(c.store)) + ' · срок ' + esc(c.deadline) + '</div></span>' +
                    '<span class="pill">Сборка</span>' +
                    '<span class="num" style="font-weight:800">' + money(c.amount) + '</span></div>';
            }).join('') || '<p class="fin-note">Карточек в сборке сейчас нет.</p>';
        }

        // Воронка по магазинам (тёмная карточка): активные сделки и их суммы
        var funnelSub = document.getElementById('day-funnel-sub');
        if (funnelSub) funnelSub.textContent = active.length + ' из ' + D.cards.length;
        var funnelStats = document.getElementById('day-funnel-stats');
        if (funnelStats) funnelStats.innerHTML = D.stores.map(function (s) {
            var inStore = active.filter(function (c) { return c.store === s.id; });
            return '<div class="stat-line"><span>' + esc(s.name) + '</span><b class="num">' +
                inStore.length + ' · ' + money(inStore.reduce(function (a, c) { return a + c.amount; }, 0)) + '</b></div>';
        }).join('');

        // Списано и очередь: живое состояние выписки (включая группы)
        var writeoffId = (D.writeoffStatus() || {}).id;
        var inQueue = D.cards.filter(function (c) { return c.stage === writeoffId; });
        var doneCards = D.cards.filter(function (c) { return c.stage === 'done'; });
        var wStats = document.getElementById('day-writeoff-stats');
        if (wStats) wStats.innerHTML =
            statLine('К выписке', inQueue.length + ' · ' + money(inQueue.reduce(function (a, c) { return a + c.amount - c.issued; }, 0))) +
            statLine('Списано (за сессию)', doneCards.length + ' · ' + money(doneCards.reduce(function (a, c) { return a + c.issued; }, 0))) +
            statLine('Групповых накладных', D.groups.length) +
            statLine('Обычных ТН (за сессию)', doneCards.reduce(function (a, c) { return a + c.docs.length; }, 0));
    }

    // ---------- Задачи и сводка ----------
    function dueDiff(task) {
        var due = parseDeadline(task.due);
        // B3: сравниваем с UTC-датой (серверные сроки — в UTC).
        return due ? dayDiff(due, todayUTC()) : 0; // >0 — просрочена (today − due)
    }
    function taskRow(t, pillClass) {
        var client = D.clientById(t.clientId);
        var user = D.users.filter(function (u) { return u.id === t.assignee; })[0];
        var sub = (client ? client.name : '') + ' · ' + (user ? (user.full_name || user.username) : '');
        if (t.done) sub = (t.closed ? t.closed + ' · ' : '') + (user ? (user.full_name || user.username) : '');
        return '<div class="trow' + (t.done ? ' done' : '') + '">' +
            '<button type="button" class="box" data-task-toggle="' + esc(t.id) + '" aria-pressed="' + !!t.done + '" aria-label="' + (t.done ? 'Вернуть задачу' : 'Отметить выполненной') + ': ' + esc(t.title) + '">' + (t.done ? '✓' : '') + '</button>' +
            '<span class="grow"><span class="t">' + esc(t.title) + '</span>' +
            '<div class="sub">' + esc(sub) + '</div></span>' +
            (t.done ? '' : '<span class="pill ' + pillClass + '">' + esc(String(t.due).slice(0, 5)) + '</span>') +
            '</div>';
    }
    function taskGroup(label, pillClass, items) {
        if (!items.length) return '';
        return '<div class="group-hd">' + label + ' <span class="cnt">' + items.length + '</span></div>' +
            items.map(function (t) { return taskRow(t, pillClass); }).join('');
    }
    function statLine(label, value, tone) {
        var color = tone === 'danger' && value > 0 ? ' style="color:var(--danger)"' : '';
        return '<div class="stat-line"><span>' + esc(label) + '</span><b class="num"' + color + '>' + value + '</b></div>';
    }
    function renderTasks() {
        var list = document.getElementById('tasks-list');
        if (!list) return;
        var tasks = D.tasks;
        var open = tasks.filter(function (t) { return !t.done; });
        var done = tasks.filter(function (t) { return t.done; });
        var overdue = open.filter(function (t) { return dueDiff(t) > 0; });
        var soon = open.filter(function (t) { var d = dueDiff(t); return d <= 0 && d >= -1; });
        var week = open.filter(function (t) { var d = dueDiff(t); return d < -1 && d >= -7; });
        var later = open.filter(function (t) { var d = dueDiff(t); return d < -7; });
        list.innerHTML =
            taskGroup('Просрочено', 'danger', overdue) +
            taskGroup('Сегодня и завтра', 'warn', soon) +
            taskGroup('На неделе', '', week) +
            taskGroup('Позже', '', later) +
            taskGroup('Выполнено', '', done) ||
            '<p class="fin-note">Задач нет.</p>';
        var progress = document.getElementById('tasks-progress');
        if (progress) progress.textContent = 'выполнено ' + done.length + ' из ' + tasks.length;

        var perUser = new Map();
        open.forEach(function (t) { perUser.set(t.assignee, (perUser.get(t.assignee) || 0) + 1); });
        var byAssignee = D.users.filter(function (u) { return perUser.get(u.id); }).map(function (u) {
            return '<div class="stat-line"><span class="row"><span class="avatar sm neutral">' + esc(u.initials || '··') + '</span> ' +
                esc(u.full_name || u.username) + '</span><b class="num">' + perUser.get(u.id) + '</b></div>';
        }).join('') || '<p class="fin-note">Открытых задач нет.</p>';
        var summary = document.getElementById('tasks-summary');
        if (summary) summary.innerHTML =
            '<div class="group-hd" style="padding-top:0">По исполнителям</div>' + byAssignee +
            '<div class="group-hd">Сроки</div>' +
            statLine('Просрочено', overdue.length, 'danger') +
            statLine('Сегодня и завтра', soon.length) +
            statLine('На неделе', week.length) +
            statLine('Выполнено', done.length);
    }

    // ---------- Клиенты и «Клиент 360» ----------
    var selectedClient = null;
    function clientDeals(clientId) {
        return D.cards.filter(function (c) { return c.clientId === clientId; });
    }
    function initialsOf(name) {
        var words = String(name || '').replace(/«|»/g, '').split(/\s+/).filter(Boolean);
        return (words.length > 1 ? words[0][0] + words[1][0] : String(name || '').slice(0, 2)).toUpperCase();
    }
    // Фидбек 18.09: contact_person у клиентов из импорта писем хранит JSON
    // [{name, role, phone, email}] — показываем человекочитаемо, а не сыром.
    function formatContacts(raw) {
        var s = String(raw || '').trim();
        if (!s) return '';
        try {
            var arr = JSON.parse(s);
            if (Array.isArray(arr)) {
                return arr.map(function (c) {
                    var bits = [];
                    if (c.name) bits.push(c.name);
                    if (c.role) bits.push(c.role);
                    if (c.phone) bits.push(c.phone);
                    if (c.email) bits.push(c.email);
                    return bits.join(' · ');
                }).join(';  ');
            }
        } catch (e) { /* не JSON — покажем как есть */ }
        return s;
    }
    function contactPhone(raw) {
        var m = String(raw || '').match(/"phone"\s*:\s*"([^"]+)"/);
        return m ? m[1] : '';
    }
    function contactPhone(raw) {
        var m = String(raw || '').match(/"phone"\s*:\s*"([^"]+)"/);
        return m ? m[1] : '';
    }
    function renderClients() {
        var query = (document.getElementById('cl-search').value || '').trim().toLocaleLowerCase('ru');
        var rows = D.clients.filter(function (c) {
            return [c.name, c.unp, c.contact_person, c.phone].join(' ').toLocaleLowerCase('ru').includes(query);
        });
        var tbody = document.getElementById('cl-rows');
        tbody.innerHTML = rows.map(function (c) {
            return '<tr tabindex="0" data-cl-id="' + esc(c.id) + '" aria-label="Открыть карточку клиента ' + esc(c.name) + '">' +
                '<td><div class="who"><span class="avatar sm neutral">' + esc(initialsOf(c.name)) + '</span><b>' + esc(c.name) + '</b></div></td>' +
                '<td class="unp">' + esc(c.unp || '—') + '</td>' +
                '<td class="trunc">' + esc(formatContacts(c.contact_person) || '—') + '</td>' +
                '<td class="trunc" title="' + esc(c.address || '') + '">' + esc(c.address || '—') + '</td>' +
                '<td><div class="row" style="gap:2px">' +
                '<button class="icon-action" data-cl-edit="' + esc(c.id) + '" title="Редактировать клиента" tabindex="-1"><svg class="i-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20h4l10-10-4-4L4 16z"/></svg></button>' +
                '<button class="icon-action danger" data-cl-delete="' + esc(c.id) + '" title="Удалить клиента" tabindex="-1"><svg class="i-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M9 6V4h6v2M6 6l1 14h10l1-14"/></svg></button>' +
                '</div></td></tr>';
        }).join('');
        var count = document.getElementById('cl-count');
        if (count) count.textContent = String(rows.length);
        if (!rows.some(function (c) { return c.id === selectedClient; })) {
            selectedClient = rows.length ? rows[0].id : null;
        }
        renderDrawer();
    }
    function renderDrawer() {
        var drawer = document.getElementById('cl-drawer');
        if (!drawer) return;
        drawer.dataset.clientId = selectedClient || '';
        var client = selectedClient ? D.clientById(selectedClient) : null;
        // Баланс кассы клиента (фидбек 18.09)
        if (client && window.V2Api && window.V2Api.token()) {
            var clNum = parseInt(String(client.id).replace(/[^0-9]/g, ''), 10);
            window.V2Api.api('/clients/' + clNum + '/balance').then(function (bal) {
                var line = document.getElementById('cl-cash-line');
                if (line) {
                    var b = Math.round(bal.balance * 100) / 100;
                    line.textContent = b > 0 ? b.toLocaleString('ru-RU', {minimumFractionDigits: 2}) + ' BYN — переплата клиента'
                        : b < 0 ? b.toLocaleString('ru-RU', {minimumFractionDigits: 2}) + ' BYN — долг клиента'
                        : '0,00 BYN';
                }
            }).catch(function (err) {
                // B1 fix: не глотаем ошибку — показываем текст ошибки, а не «недоступно».
                var line = document.getElementById('cl-cash-line');
                if (line) line.textContent = 'Не удалось загрузить баланс' + (err && err.message ? ': ' + err.message : '');
            });
        }
        if (!client) {
            drawer.innerHTML = '<div class="drawer-body"><p class="fin-note">Клиент не выбран.</p></div>';
            return;
        }
        var deals = clientDeals(client.id);
        var active = deals.filter(function (c) { return c.stage !== 'done'; });
        var paid = deals.reduce(function (a, c) { return a + c.paidAmount; }, 0);
        var overdue = debtCards(active).filter(function (c) { return overdueDays(c) > 0; });
        var dealsHtml = deals.slice(0, 5).map(function (c) {
            var cl = c.checklist.filter(function (i) { return i.ordered; }).length;
            return '<div class="trow"><span class="grow"><span class="t">' + esc(c.title) + '</span>' +
                '<div class="sub">заказано ' + cl + '/' + c.checklist.length + ' · срок ' + esc(c.deadline) + '</div></span>' +
                '<span class="pill' + (c.stage === 'writeoff' ? ' warn' : '') + '">' + esc(statusName(c.stage)) + '</span>' +
                '<span class="num" style="font-weight:800">' + money(c.amount) + '</span>' +
                '<button class="btn btn-ghost btn-sm" data-open-card="' + esc(c.id) + '">Открыть</button></div>';
        }).join('') || '<p class="fin-note">Сделок по клиенту пока нет.</p>';
        var overdueHtml = overdue.map(function (c) {
            return '<div class="trow"><span class="grow"><span class="t">Оплата · просрочена с ' + esc(c.deadline) + '</span>' +
                '<div class="sub">' + esc(D.storeName(c.store)) + ' · ' + esc(c.id) + '</div></span>' +
                '<span class="pill danger">Просрочено</span>' +
                '<span class="num" style="font-weight:800">' + money(c.amount - c.paidAmount) + '</span></div>';
        }).join('');
        var cashHtml = '';
        if (window.V2Api && window.V2Api.token()) {
            var clNum = parseInt(String(client.id).replace(/[^0-9]/g, ''), 10);
            cashHtml = '<div class="trow" data-cl-cash><span class="grow"><span class="t">Баланс кассы клиента</span><div class="sub" id="cl-cash-line">загружается…</div></span>' +
                '<span class="row" style="gap:6px;align-items:center"><input id="cl-cash-amount" inputmode="decimal" placeholder="Оплата, BYN" style="width:130px;font:inherit;font-size:12px;padding:5px 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text)">' +
                '<button type="button" class="btn btn-primary btn-sm" id="cl-cash-add">Принять</button></span></div>';
        }
        var paymentsHtml = (cashHtml || '') + debtCards(deals).concat(deals.filter(function (c) { return c.paidAmount >= c.amount; })).slice(0, 6).map(function (c) {
            var rest = c.amount - c.paidAmount;
            var pill = rest <= 0 ? '<span class="pill ok">Оплачено</span>' :
                c.paidAmount > 0 ? '<span class="pill accent">Частично</span>' : '<span class="pill danger">Долг</span>';
            return '<div class="trow"><span class="grow"><span class="t">' + esc(c.id) + ' · ' + esc(c.title) + '</span>' +
                '<div class="sub">оплачено ' + money(c.paidAmount) + ' · долг ' + money(rest) + ' BYN</div></span>' + pill +
                '<span class="num" style="font-weight:800">' + money(c.amount) + '</span></div>';
        }).join('') || '<p class="fin-note">Оплат по сделкам клиента пока нет.</p>';
        var invoicesHtml = '';
        deals.forEach(function (c) {
            c.docs.forEach(function (d) {
                invoicesHtml += '<div class="trow"><span class="grow"><span class="t">ТН ' + esc(d.series + ' ' + d.number) + '</span>' +
                    '<div class="sub">' + esc(c.id) + ' · ' + esc(d.date) + '</div></span>' +
                    '<span class="num" style="font-weight:800">' + money(d.amount) + '</span></div>';
            });
            if (c.groupId) {
                var g = D.groups.filter(function (x) { return x.id === c.groupId; })[0];
                if (g) invoicesHtml += '<div class="trow"><span class="grow"><span class="t">Групповая ТН ' + esc(g.series + ' ' + g.number) + '</span>' +
                    '<div class="sub">' + esc(c.id) + ' · ' + esc(g.date) + ' · группа «' + esc(g.name) + '»</div></span>' +
                    '<span class="num" style="font-weight:800">' + money(g.amount) + '</span></div>';
            }
        });
        if (!invoicesHtml) invoicesHtml = '<p class="fin-note">Выписанных накладных нет.</p>';
        var tasksHtml = D.tasks.filter(function (t) { return t.clientId === client.id; }).map(function (t) {
            return '<div class="trow' + (t.done ? ' done' : '') + '"><span class="box">' + (t.done ? '✓' : '') + '</span>' +
                '<span class="grow"><span class="t">' + esc(t.title) + '</span><div class="sub">срок ' + esc(t.due) + '</div></span>' +
                (t.done ? '' : '<span class="pill' + (dueDiff(t) > 0 ? ' danger' : '') + '">' + esc(String(t.due).slice(0, 5)) + '</span>') + '</div>';
        }).join('') || '<p class="fin-note">Задач по клиенту нет.</p>';
        var clTabs = [['deals', 'Сделки'], ['payments', 'Оплаты'], ['invoices', 'Накладные'], ['tasks', 'Задачи']];
        var panels = { deals: overdueHtml + dealsHtml, payments: paymentsHtml, invoices: invoicesHtml, tasks: tasksHtml };
        drawer.innerHTML =
            '<div class="drawer-head"><div class="crumbs" style="font-size:11px">Клиент · карточка клиента</div>' +
            '<div class="row" style="margin-top:6px"><b style="font-size:17px;letter-spacing:-.3px">' + esc(client.name) + '</b>' +
            '<button class="btn-quiet" style="margin-left:auto" title="Вернуть первого клиента списка" data-cl-reset>✕</button></div>' +
            '<div class="row wrap mt2" style="gap:10px;font-size:11px;color:var(--muted)">' +
            '<span class="unp">УНП ' + esc(client.unp || '—') + '</span><span>' + esc(formatContacts(client.contact_person) || '—') + '</span>' +
            (client.phone ? '<span class="unp">' + esc(client.phone) + '</span>' : '') + '</div></div>' +
            '<div class="drawer-body"><div class="kpi">' +
            '<div class="kpi-item" style="padding:0 12px"><div><div class="kpi-label">Сделок</div><div class="kpi-value num" style="font-size:20px">' + deals.length + '</div></div></div>' +
            '<div class="kpi-item" style="padding:0 12px"><div><div class="kpi-label">Оплачено</div><div class="kpi-value num" style="font-size:20px">' + money(paid) + '</div></div></div>' +
            '<div class="kpi-item" style="padding:0 12px"><div><div class="kpi-label">Просрочено</div><div class="kpi-value num" style="font-size:20px;color:' + (overdue.length ? 'var(--danger)' : 'inherit') + '">' + overdue.length + '</div></div></div>' +
            '</div>' +
            '<div class="tabs-line">' + clTabs.map(function (t) {
                return '<button type="button" class="tab' + (clTab === t[0] ? ' active' : '') + '" data-cl-tab="' + t[0] + '">' + t[1] + '</button>';
            }).join('') + '</div>' +
            (panels[clTab] || '') +
            '</div>' +
            '<div class="drawer-foot">' +
            '<button type="button" class="btn btn-primary btn-sm" data-cl-new-deal="' + esc(selectedClient) + '">Новая сделка</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-cl-call="' + esc(selectedClient) + '"' + (client && client.phone ? '' : ' disabled title="Телефон не указан"') + '>Позвонить</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" style="margin-left:auto" data-cl-print>Печать</button></div>';
    }

    document.getElementById('view-day').addEventListener('click', function (e) {
        var button = e.target.closest('[data-open-card]');
        if (button) window.KBBoard.open(button.dataset.openCard);
    });
    // Debounce: rebuild таблицы клиентов и дровера не должен бежать на
    // каждый символ — ждём паузу в вводе 150 мс.
    var clSearchTimer = 0;
    document.getElementById('cl-search').addEventListener('input', function () {
        clearTimeout(clSearchTimer);
        clSearchTimer = setTimeout(renderClients, 150);
    });
    var rows = document.getElementById('cl-rows');
    rows.addEventListener('click', function (e) {
        if (e.target.closest('.icon-action')) return;
        var tr = e.target.closest('[data-cl-id]');
        if (tr) { selectedClient = tr.dataset.clId; renderDrawer(); }
    });
    rows.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var tr = e.target.closest('[data-cl-id]');
        if (tr) { e.preventDefault(); selectedClient = tr.dataset.clId; renderDrawer(); }
    });
    document.getElementById('cl-drawer').addEventListener('click', function (e) {
        var open = e.target.closest('[data-open-card]');
        if (open) { window.KBBoard.open(open.dataset.openCard); return; }
        var tab = e.target.closest('[data-cl-tab]');
        if (tab) { clTab = tab.dataset.clTab; renderDrawer(); return; }
        if (e.target.closest('[data-cl-reset]')) {
            selectedClient = D.clients.length ? D.clients[0].id : null;
            renderClients();
        }
    });

    // ---------- «Только мои»: выбор ответственного ----------
    var meDialog = null;
    function ensureMeDialog() {
        if (meDialog) return meDialog;
        meDialog = document.createElement('dialog');
        meDialog.setAttribute('aria-label', 'Чья очередь в списке внимания');
        meDialog.addEventListener('click', function (e) {
            var b = e.target.closest('[data-me]');
            if (!b) return;
            myUser = b.dataset.me || null;
            meDialog.close();
            renderDay();
        });
        document.body.append(meDialog);
        return meDialog;
    }
    function openMeDialog() {
        var dlg = ensureMeDialog();
        dlg.innerHTML = '<h2 style="font-size:20px;letter-spacing:-.3px">Чья очередь в списке внимания</h2>' +
            '<div class="kit" style="margin-top:12px">' +
            '<button type="button" class="btn ' + (myUser === null ? 'btn-primary' : 'btn-ghost') + '" data-me="">Все</button>' +
            D.users.map(function (u) {
                return '<button type="button" class="btn ' + (myUser === u.id ? 'btn-primary' : 'btn-ghost') + '" data-me="' + esc(u.id) + '">' + esc(u.full_name || u.username) + '</button>';
            }).join('') + '</div>';
        dlg.showModal();
    }

    // ---------- Создание задачи ----------
    var taskDialog = null;
    function openTaskDialog() {
        if (taskDialog) { taskDialog.showModal(); return; }
        taskDialog = document.createElement('dialog');
        taskDialog.setAttribute('aria-labelledby', 'task-dialog-title');
        taskDialog.addEventListener('close', function () { if (window.KBSelect) window.KBSelect.close(); });
        taskDialog.innerHTML = '<form id="task-form" class="kb-form" style="margin:0">' +
            '<h2 id="task-dialog-title" style="grid-column:1/-1">Новая задача</h2>' +
            '<div class="kb-field wide"><label for="task-title">Что сделать</label><input id="task-title" maxlength="200" required></div>' +
            '<div class="kb-field"><label for="task-due">Срок</label><input id="task-due" type="date" required></div>' +
            '<div class="kb-field"><label for="task-assignee">Исполнитель</label><select id="task-assignee">' +
            D.users.map(function (u, i) { return '<option value="' + i + '">' + esc(u.full_name || u.username) + '</option>'; }).join('') + '</select></div>' +
            '<div class="kb-field wide"><label for="task-client">Клиент</label><select id="task-client"><option value="">— без клиента —</option>' +
            D.clients.map(function (c) { return '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>'; }).join('') + '</select></div>' +
            '<p class="kb-form-error" id="task-error" role="alert"></p>' +
            '<button class="btn btn-primary" type="submit">Создать</button>' +
            '<button class="btn btn-ghost" type="button" data-task-cancel>Отмена</button></form>';
        taskDialog.querySelector('[data-task-cancel]').addEventListener('click', function () { taskDialog.close(); });
        taskDialog.querySelector('#task-form').addEventListener('submit', function (e) {
            e.preventDefault();
            var title = taskDialog.querySelector('#task-title').value.trim();
            var due = taskDialog.querySelector('#task-due').value;
            if (!title || !due) { taskDialog.querySelector('#task-error').textContent = 'Заполните название и срок.'; return; }
            var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(due);
            var assignee = D.users[Number(taskDialog.querySelector('#task-assignee').value)] || D.users[0];
            var clientId = taskDialog.querySelector('#task-client').value || null;
            var seq = D.tasks.reduce(function (max, t) { var n = Number(String(t.id).replace('task-', '')); return n > max ? n : max; }, 0) + 1;
            D.tasks.push({ id: 'task-' + seq, title: title, clientId: clientId, assignee: assignee.id, due: m ? m[3] + '.' + m[2] + '.' + m[1] : due, done: false });
            apiMutate('task-create', {
                title: title,
                due: due,
                assignee: Number(String(assignee.id).replace('us-', '')) || null,
                client: clientId ? Number(String(clientId).replace('cl-', '')) || null : null
            });
            taskDialog.close();
            renderTasks();
            renderCalendar();
        });
        document.body.append(taskDialog);
        taskDialog.showModal();
        if (window.KBSelect) window.KBSelect.enhance(taskDialog);
        taskDialog.querySelector('#task-title').focus();
    }

    function apiMutate(kind, payload) { if (D.mutate) D.mutate(kind, payload); }

    // Отметка «выполнено / вернуть» делегированием по списку задач
    document.getElementById('tasks-list').addEventListener('click', function (e) {
        var box = e.target.closest('[data-task-toggle]');
        if (!box) return;
        var task = D.tasks.filter(function (t) { return t.id === box.dataset.taskToggle; })[0];
        if (!task) return;
        task.done = !task.done;
        apiMutate('task-status', { id: Number(String(task.id).replace('task-', '')), status: task.done ? 'done' : 'todo' });
        if (task.done) {
            // B3: дата закрытия задачи — по UTC, чтобы совпадать с серверной датой.
            var todayUtc = todayUTC();
            var dd = String(todayUtc.getUTCDate()).padStart(2, '0');
            var mm = String(todayUtc.getUTCMonth() + 1).padStart(2, '0');
            task.closed = 'закрыто ' + dd + '.' + mm;
        } else delete task.closed;
        renderTasks();
        renderCalendar();
    });

    // ---------- Календарь из данных ----------
    var MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
    function renderCalendar() {
        var grid = document.getElementById('shell-cal-grid');
        if (!grid) return;
        var offset = (new Date(cal.y, cal.m, 1).getDay() + 6) % 7; // Пн = 0
        var days = new Date(cal.y, cal.m + 1, 0).getDate();
        var cells = Math.ceil((offset + days) / 7) * 7;
        var events = {};
        if (cal.y === 2026 && cal.m === 8) {
            D.cards.forEach(function (c) {
                if (c.stage === 'done') return;
                var d = parseDeadline(c.deadline);
                if (!d || d.getMonth() !== cal.m || d.getFullYear() !== cal.y) return;
                (events[d.getDate()] = events[d.getDate()] || []).push({ cls: 'a', text: c.client });
                // Дата оплаты из заданных условий отсрочки (paymentDetails.due, ISO)
                if (c.paymentDetails && c.paymentDetails.due) {
                    var pm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(c.paymentDetails.due);
                    if (pm) {
                        var pd = new Date(+pm[1], +pm[2] - 1, +pm[3]);
                        if (pd.getMonth() === cal.m && pd.getFullYear() === cal.y) {
                            (events[pd.getDate()] = events[pd.getDate()] || []).push({ cls: 'b', text: 'Оплата · ' + c.client });
                        }
                    }
                }
            });
            D.tasks.forEach(function (t) {
                if (t.done) return;
                var d = parseDeadline(t.due);
                if (!d || d.getMonth() !== cal.m || d.getFullYear() !== cal.y) return;
                (events[d.getDate()] = events[d.getDate()] || []).push({ cls: 'c', text: t.title });
            });
        }
        var html = '<div class="wname">Пн</div><div class="wname">Вт</div><div class="wname">Ср</div><div class="wname">Чт</div><div class="wname">Пт</div><div class="wname we">Сб</div><div class="wname we">Вс</div>';
        for (var i = 0; i < cells; i++) {
            var day = i - offset + 1;
            if (day < 1 || day > days) { html += '<div class="day" style="background:transparent"></div>'; continue; }
            // B3: подсветка «сегодня» — по UTC, в соответствии с серверными датами.
            var todayUtc = todayUTC();
            var isToday = todayUtc.getUTCFullYear() === cal.y && todayUtc.getUTCMonth() === cal.m && todayUtc.getUTCDate() === day;
            var dayEvents = events[day] || [];
            var shown = dayEvents.slice(0, 2);
            html += '<div class="day' + (isToday ? ' today' : '') + '"><span class="n">' + day + '</span>' +
                shown.map(function (ev) { return '<span class="ev ' + ev.cls + '" title="' + esc(ev.text) + '">' + esc(ev.text) + '</span>'; }).join('') +
                (dayEvents.length > shown.length ? '<span class="more">+' + (dayEvents.length - shown.length) + ' ещё</span>' : '') + '</div>';
        }
        grid.innerHTML = html;
        var label = document.getElementById('cal-month');
        if (label) label.textContent = MONTHS[cal.m] + ' ' + cal.y;
        var note = document.getElementById('cal-note');
        if (note) note.hidden = (cal.y === 2026 && cal.m === 8);
    }
    document.getElementById('day-only-mine').addEventListener('click', openMeDialog);
    document.getElementById('task-new').addEventListener('click', openTaskDialog);
    document.getElementById('cal-prev').addEventListener('click', function () { cal.m--; if (cal.m < 0) { cal.m = 11; cal.y--; } renderCalendar(); });
    document.getElementById('cal-next').addEventListener('click', function () { cal.m++; if (cal.m > 11) { cal.m = 0; cal.y++; } renderCalendar(); });
    document.getElementById('cal-today').addEventListener('click', function () { var t = todayUTC(); cal = { y: t.getUTCFullYear(), m: t.getUTCMonth() }; renderCalendar(); });

    document.addEventListener('kb:documents', function () { renderDay(); renderCalendar(); });
    document.addEventListener('kb:payment-saved', function () { renderCalendar(); });
    document.addEventListener('kb:dictionaries-changed', function () { renderDay(); renderClients(); renderTasks(); renderCalendar(); });
    renderDay();
    renderClients();
    renderTasks();
    renderCalendar();
})();

/* ============================================================
   Клиенты — живой CRM-функционал v2 (фидбек 18.09):
   создание, редактирование, удаление, баланс кассы, приём оплаты.
   ============================================================ */
(function () {
    'use strict';
    function numeric(id) {
        var n = parseInt(String(id == null ? '' : id).replace(/[^0-9]/g, ''), 10);
        return isNaN(n) ? null : n;
    }
    function fmtKop(v) {
        return (Math.round(v) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    // Свои esc: esc из первого IIFE сюда не виден, а значения клиента
    // (например contact_person из импорта писем) подставляются в value-атрибут.
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function refreshAll() { location.reload(); }

    // Локальный тост по образцу management.js: alert блокирует страницу,
    // тост — нет (kb-toast живёт в прототипе глобально).
    function toast(text, isError) {
        var t = document.getElementById('kb-toast');
        if (!t) return;
        t.hidden = false;
        t.textContent = text;
        t.classList.toggle('toast-error', Boolean(isError));
        setTimeout(function () { t.hidden = true; t.classList.remove('toast-error'); }, 4000);
    }

    // Фидбек 20.09: создание/редактирование клиента было цепочкой нативных
    // window.prompt («каждое поле отдельно по очереди») — заменено одной
    // формой-диалогом в стиле mgmt-dialog админки (класс .v2-form-dialog
    // из management.css). Поля те же пять, что были в prompt-цепочке.
    var CL_FIELDS = [
        ['name', 'Имя клиента*', true, 'text'],
        ['phone', 'Телефон', false, 'tel'],
        ['unp', 'УНП', false, 'text'],
        ['contact_person', 'Контактное лицо', false, 'text'],
        ['address', 'Адрес', false, 'text']
    ];
    var clDialog = null;
    var clResolve = null;
    var clOpener = null;
    function ensureClientDialog() {
        if (clDialog) return clDialog;
        clDialog = document.createElement('dialog');
        clDialog.id = 'cl-dialog';
        clDialog.className = 'v2-form-dialog';
        clDialog.setAttribute('aria-labelledby', 'cl-dialog-title');
        // Любое закрытие без сабмита («Отмена», Escape/cancel, клик мимо
        // невозможен у showModal) — отмена: отдаём null и возвращаем фокус.
        clDialog.addEventListener('close', function () {
            var resolve = clResolve;
            clResolve = null;
            if (resolve) resolve(null);
            if (clOpener && clOpener.isConnected) clOpener.focus();
            clOpener = null;
            // B4-паттерн mgmt-dialog: чистим форму, чтобы скрытые required-поля
            // не блокировали следующий сабмит.
            clDialog.innerHTML = '';
        });
        document.body.append(clDialog);
        return clDialog;
    }
    function openClientForm(client, opener) {
        if (!window.V2Api || !window.V2Api.token()) {
            toast('Доступно только при подключении к CRM.', true);
            return Promise.resolve(null);
        }
        var dlg = ensureClientDialog();
        clOpener = opener || null;
        dlg.innerHTML = '<form id="cl-form"><h2 id="cl-dialog-title">' + (client ? 'Редактирование клиента' : 'Новый клиент') + '</h2>' +
            '<div class="mgmt-fields">' + CL_FIELDS.map(function (f) {
                var value = client ? (client[f[0]] || '') : '';
                return '<label for="cl-f-' + f[0] + '"><span>' + f[1] + '</span>' +
                    '<input id="cl-f-' + f[0] + '" name="' + f[0] + '" type="' + f[3] + '"' +
                    (f[2] ? ' required' : '') + ' value="' + esc(value) + '"></label>';
            }).join('') + '</div>' +
            '<p id="cl-form-error" role="alert"></p>' +
            '<div class="mgmt-actions"><button class="btn btn-ghost" type="button" data-cl-cancel>Отмена</button>' +
            '<button class="btn btn-ghost" type="submit">Сохранить</button></div></form>';
        dlg.querySelector('[data-cl-cancel]').addEventListener('click', function () { dlg.close(); });
        dlg.querySelector('#cl-form').addEventListener('submit', function (event) {
            event.preventDefault();
            var raw = Object.fromEntries(new FormData(event.target));
            var values = {};
            Object.keys(raw).forEach(function (k) { values[k] = String(raw[k]).trim(); });
            // Валидация имени — как в прежней prompt-цепочке.
            if (!values.name) { dlg.querySelector('#cl-form-error').textContent = 'Имя обязательно.'; return; }
            var resolve = clResolve;
            clResolve = null;
            dlg.close();
            if (resolve) resolve(values);
        });
        var promise = new Promise(function (resolve) { clResolve = resolve; });
        dlg.showModal();
        dlg.querySelector('#cl-f-name').focus();
        return promise;
    }

    function openNewDealFor(clientId) {
        var cl = (window.KBData.clients || []).filter(function (c) { return numeric(c.id) === numeric(clientId); })[0];
        var title = window.prompt('Название новой сделки', cl ? (cl.name || '') : '');
        if (title === null) return;
        title = title.trim();
        if (!title) { window.alert('Название обязательно'); return; }
        window.V2Api.api('/kanban/cards', { method: 'POST', body: { title: title, status: 'Новый запрос', total_amount: 0, client_id: numeric(clientId) } })
            .then(function () { window.alert('Сделка создана в «Новый запрос».'); refreshAll(); })
            .catch(function (e) { window.alert('Не создано: ' + (e.detail || e.message || 'ошибка')); });
    }
    document.addEventListener('click', function (event) {
        var deal = event.target.closest('[data-cl-new-deal]');
        if (deal && window.V2Api && window.V2Api.token()) { openNewDealFor(deal.dataset.clNewDeal); return; }
        // Приём оплаты в кассу клиента прямо из карточки клиента (фидбек 18.09)
        var cashBtn = event.target.closest('#cl-cash-add');
        if (cashBtn) {
            var drawerEl = document.getElementById('cl-drawer');
            var input = document.getElementById('cl-cash-amount');
            var amount = parseFloat(String(input && input.value || '').replace(/\s|\u00a0/g, '').replace(',', '.'));
            if (!isFinite(amount) || amount <= 0) { window.alert('Укажите сумму оплаты.'); return; }
            var num = parseInt(String(drawerEl && drawerEl.dataset.clientId || '').replace(/[^0-9]/g, ''), 10);
            if (!num) return;
            window.V2Api.api('/clients/' + num + '/payments', { method: 'POST', body: { amount: amount, note: 'Оплата в кассу (v2)' } })
                .then(function () { window.alert('Оплата ' + amount + ' BYN принята в кассу клиента.'); refreshAll(); })
                .catch(function (e2) { window.alert('Не принято: ' + (e2.detail || e2.message || 'ошибка')); });
            return;
        }
        var call = event.target.closest('[data-cl-call]');
        if (call) {
            var cl = (window.KBData.clients || []).filter(function (c) { return numeric(c.id) === numeric(call.dataset.clCall); })[0];
            var phone = cl && (cl.phone || contactPhone(cl.contact_person));
            if (cl && phone) window.open('tel:' + String(phone).replace(/[^+0-9]/g, ''), '_self');
            return;
        }
        if (event.target.closest('[data-cl-print]')) window.print();
    });

    var newBtn = document.getElementById('cl-new');
    if (newBtn) newBtn.addEventListener('click', function (event) {
        openClientForm(null, event.currentTarget).then(function (fields) {
            if (!fields) return;
            window.V2Api.api('/clients', { method: 'POST', body: fields })
                .then(function () {
                    // Тост должен успеть показаться до перезагрузки — раньше
                    // эту паузу держал модальный alert.
                    toast('Клиент создан.');
                    setTimeout(refreshAll, 600);
                })
                .catch(function (e) { toast('Не создано: ' + (e.detail || e.message || 'ошибка'), true); });
        });
    });

    document.addEventListener('click', function (event) {
        var editBtn = event.target.closest('[data-cl-edit]');
        if (editBtn) {
            var num = numeric(editBtn.dataset.clEdit);
            var client = (window.KBData.clients || []).filter(function (c) { return numeric(c.id) === num; })[0];
            if (!client) return;
            openClientForm(client, editBtn).then(function (fields) {
                if (!fields) return;
                window.V2Api.api('/clients/' + num, { method: 'PATCH', body: fields })
                    .then(function () { toast('Клиент сохранён.'); setTimeout(refreshAll, 600); })
                    .catch(function (e) { toast('Не сохранено: ' + (e.detail || e.message || 'ошибка'), true); });
            });
            return;
        }
        var delBtn = event.target.closest('[data-cl-delete]');
        if (delBtn) {
            var num2 = numeric(delBtn.dataset.clDelete);
            var cl = (window.KBData.clients || []).filter(function (c) { return numeric(c.id) === num2; })[0];
            if (!cl) return;
            if (!window.confirm('Удалить клиента «' + (cl.name || '') + '»? Сделки останутся, но отвяжутся.')) return;
            window.V2Api.api('/clients/' + num2, { method: 'DELETE' })
                .then(function () { window.alert('Клиент удалён.'); refreshAll(); })
                .catch(function (e) { window.alert('Не удалено: ' + (e.detail || e.message || 'недостаточно прав')); });
        }
    });

    // Drawer: баланс кассы + приём оплаты. Перерисовывается при смене клиента.
    function onDrawerChange() {
        var drawer = document.getElementById('cl-drawer');
        if (!drawer) return;
        var clientId = numeric(drawer.dataset.clientId);
        var body = drawer.querySelector('.drawer-body');
        if (!body) return;
        // Этот колбэк висит на MutationObserver того же поддерева, которое сам
        // и меняет (old.remove() + вставка блока). Без метки «в работе» каждый
        // виток заново запускает наблюдателя, очередь микротасков не иссякает —
        // страница перестаёт отвечать и сыплет запросами баланса до перезагрузки.
        // Метка снимается при сбое, чтобы ошибка не кешировалась пустотой (B2).
        if (body.dataset.balanceDone === clientId + '' || body.dataset.balanceLoading === clientId + '') return;
        var old = body.querySelector('[data-cl-balance-block]');
        if (old) old.remove();
        if (!clientId) return;
        body.dataset.balanceLoading = clientId + '';
        var block = document.createElement('div');
        block.setAttribute('data-cl-balance-block', '1');
        block.innerHTML = '<p class="fin-note" data-cl-balance>Баланс кассы: загружается…</p>' +
            '<div class="row" style="gap:6px;margin:0 0 10px">' +
            '<input data-cl-pay-amount inputmode="decimal" placeholder="Оплата, BYN" style="width:150px">' +
            '<button type="button" class="btn btn-primary btn-sm" data-cl-pay="' + clientId + '">Принять оплату</button></div>';
        body.insertAdjacentElement('afterbegin', block);
        if (!window.V2Api || !window.V2Api.token()) { block.querySelector('[data-cl-balance]').textContent = ''; return; }
        // B2 fix: не помечаем загрузку как завершённую до успешного ответа —
        // иначе при сетевом сбое «баланс: » кешируется как пустота и не ретраится.
        window.V2Api.api('/clients/' + clientId + '/balance').then(function (bal) {
            body.dataset.balanceDone = clientId + '';
            delete body.dataset.balanceLoading;
            var el = block.querySelector('[data-cl-balance]');
            el.textContent = 'Баланс кассы: ' + fmtKop(Math.round(bal.balance * 100)) + ' BYN' +
                (bal.balance > 0 ? ' — переплата клиента' : bal.balance < 0 ? ' — долг клиента' : '');
        }).catch(function (err) {
            // B1 fix: не глотаем ошибку — показываем состояние и даём повторить.
            delete body.dataset.balanceLoading;
            var el = block.querySelector('[data-cl-balance]');
            if (el) el.textContent = 'Не удалось загрузить баланс' + (err && err.message ? ': ' + err.message : '');
        });
        block.querySelector('[data-cl-pay]').addEventListener('click', function () {
            var input = block.querySelector('[data-cl-pay-amount]');
            var amount = parseFloat(String(input && input.value || '').replace(/\s|\u00a0/g, '').replace(',', '.'));
            if (!isFinite(amount) || amount <= 0) { window.alert('Укажите сумму оплаты.'); return; }
            window.V2Api.api('/clients/' + clientId + '/payments', { method: 'POST', body: { amount: amount, note: 'Оплата в кассу (v2)' } })
                .then(function () { window.alert('Оплата ' + amount + ' BYN принята в кассу клиента.'); refreshAll(); })
                .catch(function (e) { window.alert('Не принято: ' + (e.detail || e.message || 'ошибка')); });
        });
    }
    var drawerEl = document.getElementById('cl-drawer');
    if (drawerEl) {
        new MutationObserver(onDrawerChange).observe(drawerEl, { childList: true, subtree: true });
        document.addEventListener('click', function (e) {
            if (e.target.closest('[data-cl-id]')) setTimeout(onDrawerChange, 50);
        });
    }
})();
