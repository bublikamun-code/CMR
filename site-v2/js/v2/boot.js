/* site-v2: загрузчик. Авторизация → чтение коллекций → сборка KBData в
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
