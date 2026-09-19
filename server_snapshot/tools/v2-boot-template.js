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
            // Кэш-штамп сборки: после деплоя браузер не держит старый модуль
            s.src = src + (window.V2_ASSET_VER ? '?v=' + window.V2_ASSET_VER : '');
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
    // Единая конвертация рублей в копейки — через KBApi.cents (B8).
    // Локальный алиас для краткости в buildKbData/buildFinSource.
    var kopecks = (window.KBApi && window.KBApi.cents) || function (v) { return Math.round((Number(v) || 0) * 100); };
    // 'cl-22' → 22: клиентские id в v2 хранятся с префиксом, API ждёт число
    function numericId(id) {
        const n = parseInt(String(id == null ? '' : id).replace(/[^0-9]/g, ''), 10);
        return isNaN(n) ? null : n;
    }
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

        const statusesRaw = payload.statuses || [];
        const CANON_ID = {
            'Новый запрос': 'new', 'В работе': 'work', 'Ждет оплаты': 'pay',
            'Сборка': 'assembly', 'На списание': 'writeoff', 'Закрыто': 'done'
        };
        let statuses, stageOf;
        if (statusesRaw.length) {
            statuses = statusesRaw
                .filter(function (row) { return row.is_active !== false; })
                .sort(function (a, b) { return (a.position || 0) - (b.position || 0) || a.id - b.id; })
                .map(function (row) {
                    return {
                        id: CANON_ID[row.name] || slug(row.name),
                        name: row.name,
                        color: row.color || 'faint',
                        position: row.position || 0,
                        role: row.name === 'На списание' ? 'writeoff' : 'board',
                        serverId: row.id,
                        // Бекенд принимает в карточках только канонические статусы
                        // (schemas.CARD_STATUSES); новые статусы колонкой будут,
                        // но назначать их сделкам нельзя до унификации модели.
                        canAssign: Boolean(CANON_ID[row.name])
                    };
                });
            stageOf = {};
            statuses.forEach(function (s) { stageOf[s.name] = s.id; });
            stageOf['Закрыто'] = 'done';
        } else {
            statuses = STATUS_MAP.map(function (row, i) {
                return { id: row[1], name: row[0], color: row[2], position: i, role: row[3] };
            });
            stageOf = {
                'Новый запрос': 'new', 'В работе': 'work', 'Ждет оплаты': 'pay',
                'Сборка': 'assembly', 'На списание': 'writeoff', 'Закрыто': 'done'
            };
        }

        const users = usersRaw.map(function (u) {
            return { id: 'us-' + u.id, username: u.username, full_name: u.full_name || u.username, initials: initials(u.full_name || u.username), role: u.role };
        });
        const unassigned = { id: 'us-none', username: '—', full_name: 'Не назначен', initials: '··', role: 'manager' };

        const clients = clientsRaw.map(function (c) {
            return {
                id: 'cl-' + c.id, name: c.name, unp: c.unp || '',
                contact_person: c.contact_person || '', phone: c.phone || '',
                address: c.address || '',
                // A11: серверные поля баланса (пакет B).
                // Если сервер ещё не отдаёт — undefined.
                cash_balance: c.cash_balance !== undefined ? c.cash_balance : undefined,
                overdue_deals: c.overdue_deals !== undefined ? c.overdue_deals : undefined
            };
        });
        const suppliers = suppliersRaw.map(function (s) {
            return { id: 'sup-' + s.id, name: s.name, unp: s.unp || '', contact_person: s.contact_person || '', phone: s.phone || '', email: s.email || '', address: s.address || '' };
        });

        // Магазины: справочник /dictionaries/stores плюс неявные магазины из
        // карточек. В БД store_locations пока пуст, cards.store_location хранит
        // имя строкой — сводим имя к id справочника, а неизвестные имена
        // становятся магазинами сами (заодно при наполнении справочника
        // связка не сломается).
        const storesRaw = payload.stores || [];
        const stores = storesRaw.filter(function (s) { return s.is_active !== false; })
            .map(function (s) { return { id: 'store-' + s.id, name: s.name, address: s.address || '', phone: s.phone || '' }; });
        const storeIdByName = {};
        stores.forEach(function (s) { storeIdByName[s.name] = s.id; });
        cardsRaw.forEach(function (c) {
            const name = String(c.store_location || '').trim();
            if (name && !storeIdByName[name]) {
                storeIdByName[name] = name;
                stores.push({ id: name, name: name });
            }
        });

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

        const docsByCard = {};
        (payload.documents || []).forEach(function (d) {
            if (d.card_id == null) return;
            (docsByCard[d.card_id] = docsByCard[d.card_id] || []).push(d);
        });

        const cards = cardsRaw.map(function (c) {
            const clientObj = c.client ? clientById['cl-' + c.client.id] : null;
            const manager = (c.owner_id && userById['us-' + c.owner_id]) || unassigned;
            return {
                id: String(c.id),
                title: c.title || 'Сделка',
                clientId: clientObj ? clientObj.id : null,
                // Клиента нет — значит нет. Раньше сюда подставлялось название
                // сделки, и сохранение карточки привязывало/создавало клиента
                // с именем сделки (на проде 994 карточки без клиента).
                client: clientObj ? clientObj.name : '',
                // Магазин — только фактический. Подстановка stores[0] при пустом
                // или неизвестном значении показывала чужой магазин, а сохранение
                // записывала его как настоящий (на проде справочник store_locations
                // пуст, при этом 674 карточки идут без магазина).
                store: storeIdByName[String(c.store_location || '').trim()] || '',
                manager: manager,
                amount: kopecks(c.total_amount),
                paidAmount: kopecks(c.paid_amount),
                issued: 0, // история выписки подключается на этапе 2.6
                stage: stageOf[String(c.status || '')] || 'new',
                deadline: isoToRu(c.due_date),
                note: c.description || '',
                paymentTerms: c.payment_terms || '',
                paymentDueDate: String(c.payment_due_date || '').slice(0, 10),
                payment_status: c.payment_status || '',
                paymentDetails: null,
                groupId: c.writeoff_group_id ? 'gr-' + c.writeoff_group_id : null,
                // A12: серверные поля остатка и статуса списания (пакет B).
                // Если сервер ещё не отдаёт — undefined (доска покажет «—»).
                // Никакого fallback на клиентский расчёт.
                remaining: c.remaining !== undefined ? c.remaining : undefined,
                remaining_kop: c.remaining_kop !== undefined ? c.remaining_kop : undefined,
                writeoff_status: c.writeoff_status !== undefined ? c.writeoff_status : undefined,
                issued_total: c.issued_total !== undefined ? c.issued_total : undefined,
                checklist: (c.checklists || []).map(function (item) {
                    return {
                        id: 'ck-' + item.id,
                        label: (item.supplier && item.supplier.name) || item.company_name || 'Позиция',
                        supplier_id: item.supplier_id ? 'sup-' + item.supplier_id : null,
                        note: item.note || '',
                        invFile: item.invoice_file_name || null,
                        // Флаги закупки и сумма пункта — с сервера (миграция 0009,
                        // ChecklistResponse.ordered/received/amount). Здесь был
                        // хардкод false и отсутствие amount: галочки «Заказано» /
                        // «Получено» и сумма слетали после перезагрузки страницы.
                        amount: item.amount === null || item.amount === undefined ? '' : Number(item.amount),
                        ordered: Boolean(item.ordered),
                        received: Boolean(item.received)
                    };
                }),
                attachments: (c.attachments || []).map(function (a) {
                    return { id: a.id, name: a.file_name, path: a.file_path };
                }),
                docs: (docsByCard[c.id] || []).map(function (d) {
                    // invoice_number в БД хранится целиком («ТН 0002351», «ТН ТТН4881042»,
                    // встречаются ТТН) — показываем как записано, без склейки серии.
                    return { txId: d.id, series: '', number: String(d.invoice_number || ''), date: isoToRu(d.invoice_date || d.date), amount: kopecks(d.amount), originalsReturned: Boolean(d.is_invoice_doc) };
                })
            };
        });
        // «К выписке» — ровно как считает прод (writeoff-status): из
        // не-документных транзакций карточки выписанными частями являются
        // только складские списания и записи с номером накладной. Оплаты
        // и неоформленные остатки в выписанное не входят.
        const issuedByCard = {};
        (payload.transactions || []).forEach(function (t) {
            if (t.is_document || t.card_id == null) return;
            // Фидбек 18.09 («Рацио Домус»): сгруппированная строка реестра
            // агрегирует номер первой накладной и несёт ВСЮ сумму сделки —
            // брать её amount нельзя, выписано считалось полной суммой
            // (57 046,38 вместо 38 824,52). Честная сумма — invoiced_amount
            // из бэкенда; старый разбор частей оставлен как запасной путь.
            if (t.invoiced_amount !== undefined && t.invoiced_amount !== null) {
                issuedByCard[t.card_id] = kopecks(t.invoiced_amount);
                return;
            }
            if (!t.is_warehouse_writeoff && !(t.invoice_number || '').trim()) return;
            issuedByCard[t.card_id] = (issuedByCard[t.card_id] || 0) + kopecks(t.amount);
        });
        cards.forEach(function (c) {
            const issued = issuedByCard[Number(c.id)];
            c.issued = issued !== undefined ? Math.min(issued, c.amount)
                : c.docs.reduce(function (a, d) { return a + d.amount; }, 0);
            // Условия оплаты, сохранённые в CRM (payment_terms), восстанавливаются
            // как снимок: датой оплаты из payment_due_date, предоплата — пустая.
            if (c.paymentTerms) {
                c.paymentDetails = {
                    terms: c.paymentTerms,
                    mode: c.paymentDueDate ? 'date' : '',
                    due: c.paymentDueDate || '',
                    start: '',
                    days: null,
                    prepay: null
                };
            }
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

        // Группы списания — с сервера (GET /writeoffs/groups/). Раньше здесь
        // всегда лежал пустой массив: групповая ТН существовала только до
        // перезагрузки, а вкладка «Накладные» у карточки из группы ничего не
        // находила, и отмена группы была недостижима.
        const docByGroup = {};
        (payload.documents || []).forEach(function (d) {
            if (d.writeoff_group_id == null || d.card_id != null) return;
            if (!docByGroup[d.writeoff_group_id]) docByGroup[d.writeoff_group_id] = d;
        });
        const groups = (payload.groups || []).map(function (g) {
            const groupClient = g.client_id ? clientById['cl-' + g.client_id] : null;
            const groupDoc = docByGroup[g.id] || null;
            return {
                id: 'gr-' + g.id,
                serverId: g.id,
                serverTxId: groupDoc ? groupDoc.id : null,
                name: g.name || ('Группа ' + g.id),
                client: groupClient ? groupClient.name : '',
                store: g.store_location || '',
                // Серия отдельно не хранится: номер накладной записан целиком.
                series: '',
                number: String((groupDoc && groupDoc.invoice_number) || g.invoice_number || ''),
                date: isoToRu(g.invoice_date || (groupDoc && groupDoc.invoice_date) || ''),
                amount: kopecks(g.total_amount),
                writtenOff: Boolean(g.written_off),
                // Покрытие по карточкам сервер не хранит — известна только сумма
                // группы. Отмена группы идёт через сервер, а не локальным
                // пересчётом остатков.
                covers: (g.cards || []).map(function (c) { return { cardId: String(c.id), amount: null }; })
            };
        });

        window.KBData = {
            // Единый источник «сегодня» — UTC-дата (не зависит от пояса клиента).
            today: (function () {
                const iso = window.KBApi && window.KBApi.todayUTC ? window.KBApi.todayUTC() : '';
                const p = String(iso).split('-');
                return p.length === 3 ? new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])) : new Date();
            })(),
            statuses: statuses,
            stores: stores,
            users: users.concat([unassigned]),
            clients: clients,
            suppliers: suppliers,
            tasks: tasks,
            cards: cards,
            groups: groups,
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
        window.KBData.storeName = function (id) {
            const s = stores.filter(function (x) { return x.id === id; })[0];
            return s ? s.name : String(id || '');
        };
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
        setText('Демо-данные');
        await injectAll(DEMO_SCRIPTS);
    }
    async function bootApi() {
        setText('Подключение к CRM…');
        if (!window.V2Api.token()) { const e = new Error('unauthorized'); e.unauthorized = true; throw e; }
        const meUser = await window.V2Api.me();
        // B6: сохраняем verified-роль из /auth/me, а не из ответа логина.
        // Роль всегда подтверждена сервером при каждом старте.
        if (meUser && meUser.role) window.V2Api.save(window.V2Api.token(), meUser.role);
        await _loadAndApplyData();
        enableMutations();
        renderUser(meUser);
        setText('CRM');
        await injectAll(APP_SCRIPTS);
    }

    // Загрузка и применение всех коллекций (вынесено для переиспользования
    // в KBApi.all()). Возвращает { KBData, KB_FIN_SOURCE } или бросает
    // ошибку (B2: ошибка пробрасывается, не кешируется пустотой).
    async function _loadAndApplyData() {
        // Загружаем всё разом, но через allSettled: при сбое нужно назвать,
        // какой именно раздел не ответил. Данные при этом НЕ применяем частично —
        // неполная картина молча выглядит как настоящая (так работала старая
        // ветка с переходом на демо). Ошибка пробрасывается наверх, в showFatal.
        const sources = [
            ['сделки', function () { return window.V2Api.api('/kanban/cards'); }],
            ['клиенты', function () { return window.V2Api.api('/clients'); }],
            ['пользователи', function () { return window.V2Api.users(); }],
            ['поставщики', function () { return window.V2Api.api('/suppliers'); }],
            ['задачи', function () { return window.V2Api.api('/tasks'); }],
            ['реестр оплат', function () { return window.V2Api.api('/payments/transactions'); }],
            ['документы', function () { return window.V2Api.api('/payments/documents'); }],
            ['магазины', function () { return window.V2Api.api('/dictionaries/stores'); }],
            ['статусы', function () { return window.V2Api.api('/dictionaries/statuses'); }],
            ['входящие накладные', function () { return window.V2Api.api('/nakladnye'); }],
            ['группы списания', function () { return window.V2Api.api('/writeoffs/groups/'); }]
        ];
        const settled = await Promise.allSettled(sources.map(function (s) { return s[1](); }));
        const unauthorized = settled.filter(function (s) {
            return s.status === 'rejected' && s.reason && s.reason.unauthorized;
        })[0];
        if (unauthorized) throw unauthorized.reason;
        const failed = [];
        settled.forEach(function (s, i) {
            if (s.status === 'rejected') {
                const code = s.reason && s.reason.status ? ' (код ' + s.reason.status + ')' : '';
                failed.push(sources[i][0] + code);
            }
        });
        if (failed.length) {
            const err = new Error('не загрузили разделы: ' + failed.join(', '));
            err.failed = failed;
            throw err;
        }
        const results = settled.map(function (s) { return s.value; });
        buildKbData({ cards: results[0], clients: results[1], users: results[2], suppliers: results[3], tasks: results[4], stores: results[7], statuses: results[8], documents: results[6], nakladnye: results[9], transactions: results[5], groups: results[10] });
        window.KB_FIN_SOURCE = buildFinSource(window.KBData.cards, results[5], results[6], results[9]);
        // Фидбек 18.09: журнал сделки в v2 — из CRM (GET /activity?card_id),
        // включая «Импорт почты» с текстом письма. В демо-режиме загрузчика
        // нет — вкладка остаётся на локальных событиях.
        window.KBData.loadCardActivity = async function (cardId) {
            return await window.V2Api.api('/activity?card_id=' + encodeURIComponent(cardId) + '&limit=100');
        };
        // B9: регистрируем загрузчик для KBApi.all() (коалесинг перезагрузок).
        if (window.KBApi && window.KBApi._setFetcher) {
            window.KBApi._setFetcher(_loadAndApplyData);
        }
        return window.KBData;
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
    // Реестр и документы v2 — по данным CRM. «К выписке» считается так же,
    // как продовый writeoff-status: из не-документных транзакций карточки
    // выписанными частями являются только складские списания и записи
    // с номером накладной; оплаты и неоформленные остатки — нет.
    // Реестр оплат v2 = сгруппированный реестр из рабочей версии: ОДНА
    // строка на сделку за КАЖДОЙ группой записей. Карточка попадает в
    // реестр сразу при переносе в «Сборку» (запись-остаток создаёт бэкенд)
    // — фильтра «финансового следа» больше нет, из-за него строки
    // пропадали (фидбек 18.09: «в реестре не все карточки»).
    // «Просчет»/«Списание» — те же поля сгруппированной строки, что и в
    // рабочем реестре (is_calculated / is_written_off, агрегат по частям),
    // поэтому галочки в старом и новом реестре всегда совпадают.
    function buildFinSource(cards, transactions, documents, nakladnye) {
        const cardById = {};
        (cards || []).forEach(function (c) { cardById[c.id] = c; });
        const docsByCard = {};
        (documents || []).forEach(function (d) {
            if (d.card_id == null) return;
            (docsByCard[d.card_id] = docsByCard[d.card_id] || []).push(d);
        });
        const outgoing = (transactions || []).filter(function (t) { return !t.is_document; }).map(function (t) {
            const card = t.card_id != null ? cardById[String(t.card_id)] : null;
            const docs = (docsByCard[t.card_id] || []).slice().sort(function (a, b) {
                return new Date(b.invoice_date || b.date || 0) - new Date(a.invoice_date || a.date || 0);
            });
            const latest = docs[0] || null;
            // См. комментарий в buildKbData: у сгруппированной строки amount —
            // вся сумма сделки; честная сумма выписки — invoiced_amount.
            const issued = (t.invoiced_amount !== undefined && t.invoiced_amount !== null)
                ? kopecks(t.invoiced_amount)
                : docs.reduce(function (a, d) { return a + kopecks(d.amount); }, 0);
            return {
                id: 't' + t.id,
                txId: t.id,
                txDate: t.date,
                cardId: card ? String(card.id) : (t.card_id != null ? String(t.card_id) : null),
                card: card ? (card.id + ' · ' + card.title) : (t.company_name || 'Сделка'),
                date: isoToRu(t.date) || (latest ? isoToRu(latest.invoice_date || latest.date) : (card ? card.deadline : '')),
                client: card ? card.client : (t.company_name || ''),
                amount: card ? card.amount : kopecks(t.amount),
                paid: card ? Math.min(card.amount, card.paidAmount) : Math.min(kopecks(t.amount), kopecks(t.paid_amount || 0)),
                store: t.store_location || (card ? window.KBData.storeName(card.store) : ''),
                estimate: '',
                tn: latest ? { number: latest.invoice_number || '', date: isoToRu(latest.invoice_date || latest.date), bill: '' } : null,
                calculated: Boolean(t.is_calculated),
                posted: Boolean(t.is_written_off),
                partIds: (Array.isArray(t.part_ids) && t.part_ids.length) ? t.part_ids : [t.id],
                docTxId: latest ? latest.id : null,
                tnHere: Boolean(latest && latest.is_invoice_doc),
                billHere: Boolean(latest && latest.is_bill_doc),
                print: (latest && latest.print_status) || '',
                authority: '',
                note: t.note || ''
            };
        });
        // Рабочий реестр показывает свежие записи сверху — так же здесь.
        outgoing.sort(function (a, b) {
            const pa = a.date.split('.').reverse().join('-');
            const pb = b.date.split('.').reverse().join('-');
            return pb.localeCompare(pa);
        });
        // Входящие накладные от поставщиков (GET /nakladnye). Аргумент и раньше
        // передавался, но сигнатура была на три параметра — данные молча
        // выбрасывались, и вкладка «Входящие» на реальной базе была пуста.
        const incoming = (nakladnye || []).map(function (n) {
            return {
                id: 'n' + n.id,
                supplier: n.supplier_name || n.supplier || '',
                // Номер — как в БД: без склейки серии и префикса «ТН» (решение 18.09).
                number: String(n.doc_number || ''),
                date: isoToRu(n.doc_date),
                store: n.store || '',
                amount: kopecks(n.amount),
                vat: kopecks(n.vat_amount),
                checked: Boolean(n.is_verified),
                arrived: Boolean(n.is_arrived),
                paid: Boolean(n.is_paid),
                file: n.excel_path || (Array.isArray(n.photo_paths) && n.photo_paths[0]) || '',
                photoPaths: Array.isArray(n.photo_paths) ? n.photo_paths : [],
                excelPath: n.excel_path || null
            };
        });
        incoming.sort(function (a, b) {
            const pa = a.date.split('.').reverse().join('-');
            const pb = b.date.split('.').reverse().join('-');
            return pb.localeCompare(pa);
        });
        return { outgoing: outgoing, incoming: incoming };
    }
    // Запись (Этап 2.1/2.2): статус/поля карточки и задачи уходят в CRM API.
    // Неудача показывается тостом; локальное состояние правится отдельно
    // и выправится следующей перезагрузкой с сервера.
    function enableMutations() {
        window.KBData.mutate = async function (kind, payload) {
            try {
                if (kind === 'card') {
                    // A6: не слать пустой note/description (сервер создаёт пустой комментарий).
                    // A7: не слать client_id: null (затирает клиента карточки).
                    var fields = Object.assign({}, payload.fields);
                    if (fields.client_id === null || fields.client_id === undefined) delete fields.client_id;
                    if (fields.note === '') delete fields.note;
                    if (fields.description === '') delete fields.description;
                    await window.V2Api.api('/cards/' + payload.id, { method: 'PATCH', body: fields });
                } else if (kind === 'status') {
                    // A6: не слать пустую заметку при смене статуса.
                    var sp = { status: payload.status };
                    if (payload.note) sp.note = payload.note;
                    await window.V2Api.api('/kanban/cards/' + payload.id + '/status', { method: 'PATCH', body: sp });
                } else if (kind === 'task-status') {
                    await window.V2Api.api('/tasks/' + payload.id, { method: 'PATCH', body: { status: payload.status } });
                } else if (kind === 'task-create') {
                    // B5: защита от двойного сабмита
                    return await window.V2Api._guardCreate(
                        'task-create:' + payload.title,
                        function () { return window.V2Api.api('/tasks', { method: 'POST', body: { title: payload.title, status: 'todo', due_date: payload.due || null, assignee_id: payload.assignee || null, client_id: payload.client || null } }); }
                    );
                } else if (kind === 'store-save') {
                    return await (payload.id
                        ? window.V2Api.api('/dictionaries/stores/' + payload.id, { method: 'PATCH', body: payload.fields })
                        : window.V2Api._guardCreate('store-save:' + JSON.stringify(payload.fields),
                            function () { return window.V2Api.api('/dictionaries/stores', { method: 'POST', body: payload.fields }); }));
                } else if (kind === 'status-save') {
                    return await (payload.id
                        ? window.V2Api.api('/dictionaries/statuses/' + payload.id, { method: 'PATCH', body: payload.fields })
                        : window.V2Api._guardCreate('status-save:' + JSON.stringify(payload.fields),
                            function () { return window.V2Api.api('/dictionaries/statuses', { method: 'POST', body: payload.fields }); }));
                } else if (kind === 'user-create') {
                    // B5: защита от двойного сабмита
                    return await window.V2Api._guardCreate(
                        'user-create:' + (payload.fields && payload.fields.username || ''),
                        function () { return window.V2Api.api('/auth/users', { method: 'POST', body: payload.fields }); }
                    );
                } else if (kind === 'user-save') {
                    // P0-2 (аудит 19.09): админка шлёт kind 'user-save' и для
                    // создания, и для правки — обработчика не было, запрос
                    // вообще не уходил. Пароль ≥8 обязателен только при
                    // создании (POST /auth/users); при правке (PATCH) шлём
                    // роль и опционально новый пароль. full_name на сервере
                    // нет (ни в модели, ни в схемах) — не отправляем.
                    var uf = payload.fields || {};
                    var ubody = { role: uf.role || 'manager' };
                    if (uf.password) ubody.password = uf.password;
                    if (payload.id != null) {
                        return await window.V2Api.api('/auth/users/' + payload.id, { method: 'PATCH', body: ubody });
                    }
                    if (!uf.password || uf.password.length < 8) {
                        return Promise.reject({ detail: 'Пароль обязателен при создании (минимум 8 символов)' });
                    }
                    return await window.V2Api._guardCreate(
                        'user-save:' + (uf.username || ''),
                        function () { return window.V2Api.api('/auth/users', { method: 'POST', body: { username: uf.username, password: uf.password, role: ubody.role } }); }
                    );
                } else if (kind === 'supplier-save') {
                    return await (payload.id
                        ? window.V2Api.api('/suppliers/' + payload.id, { method: 'PATCH', body: payload.fields })
                        : window.V2Api._guardCreate('supplier-save:' + JSON.stringify(payload.fields),
                            function () { return window.V2Api.api('/suppliers', { method: 'POST', body: payload.fields }); }));
                } else if (kind === 'client-save') {
                    return await (payload.id
                        ? window.V2Api.api('/clients/' + payload.id, { method: 'PATCH', body: payload.fields })
                        : window.V2Api._guardCreate('client-save:' + JSON.stringify(payload.fields),
                            function () { return window.V2Api.api('/clients', { method: 'POST', body: payload.fields }); }));
                } else if (kind === 'payment-terms') {
                    // payment_terms есть в CardUpdate (общий PATCH карточки),
                    // а в CardPaymentUpdate его нет.
                    return await window.V2Api.api('/cards/' + payload.id, { method: 'PATCH', body: { payment_terms: payload.terms || null, payment_due_date: payload.due || null } });
                } else if (kind === 'register-payment') {
                    // Как в рабочей версии: PATCH /cards/{id}/payment полями
                    // статуса (Оплачен/Частично/Отсрочка/Не оплачен) —
                    // выбор статуса определяет деньги, а не наоборот.
                    return await window.V2Api.api('/cards/' + payload.id + '/payment', { method: 'PATCH', body: payload.fields });
                } else if (kind === 'issue-invoice') {
                    // B5: защита от двойного сабмита выписки
                    return await window.V2Api._guardCreate(
                        'issue-invoice:' + payload.id + ':' + payload.number,
                        function () { return window.V2Api.api('/payments/cards/' + payload.id + '/issue-invoice', { method: 'POST', body: { invoice_number: payload.number, invoice_date: payload.date, amount: payload.amount, store_location: payload.store || null } }); }
                    );
                } else if (kind === 'invoice-edit') {
                    // Правка накладной: дата (ISO), номер и сумма — как на проде
                    // (PATCH /payments/transactions/{id}).
                    return await window.V2Api.api('/payments/transactions/' + payload.txId, { method: 'PATCH', body: { invoice_date: payload.isoDate, invoice_number: payload.number, amount: payload.amountByn } });
                } else if (kind === 'annul-tx') {
                    return await window.V2Api.api('/payments/transactions/' + payload.txId, { method: 'DELETE' });
                } else if (kind === 'group-issue') {
                    // B5: защита от двойного сабмита групповой выписки
                    return await window.V2Api._guardCreate(
                        'group-issue:' + (payload.cards || []).join(',') + ':' + payload.number,
                        async function () {
                            // Сервер живёт по префиксу /writeoffs/groups (не /writeoff-groups).
                            const g = await window.V2Api.api('/writeoffs/groups/', { method: 'POST', body: { card_ids: payload.cards, name: payload.name } });
                            await window.V2Api.api('/writeoffs/groups/' + g.id + '/issue-invoice', { method: 'POST', body: { invoice_number: payload.number, invoice_date: payload.date, amount: payload.amount } });
                            let txId = null;
                            try {
                                const docs = await window.V2Api.api('/payments/documents');
                                const found = (docs || []).find(d => d.writeoff_group_id === g.id && (d.invoice_number || '') === payload.number);
                                if (found) txId = found.id;
                            } catch (e) { /* отмена группы будет локальной */ }
                            return { id: g.id, txId: txId };
                        }
                    );
                } else if (kind === 'card-delete') {
                    // Удаление карточки — мягкое: карточка уходит в корзину
                    // (is_deleted), восстановление — PATCH /cards/{id}/restore.
                    return await window.V2Api.api('/kanban/cards/' + payload.id, { method: 'DELETE' });
                } else if (kind === 'fin-flag') {
                    // Фидбек 18.09: «Просчет»/«Списание» реестра v2 — те же
                    // ручные поля, что в основном реестре: is_calculated /
                    // is_written_off по КАЖДОЙ части сделки (part_ids), как
                    // это делает основной реестр при сгруппированной строке.
                    const field = payload.field === 'calculated' ? 'is_calculated' : 'is_written_off';
                    const ids = (Array.isArray(payload.partIds) && payload.partIds.length) ? payload.partIds : [];
                    if (!ids.length) return false;
                    await Promise.all(ids.map(function (id) {
                        return window.V2Api.api('/payments/transactions/' + id, { method: 'PATCH', body: { [field]: payload.checked } });
                    }));
                    return true;
                } else if (kind === 'doc-flag') {
                    // «ТН у нас»/«Счёт у нас» в документах v2 — поля
                    // is_invoice_doc / is_bill_doc записи-документа, как в
                    // основных «Документах».
                    const field = payload.field === 'tnHere' ? 'is_invoice_doc' : 'is_bill_doc';
                    return await window.V2Api.api('/payments/transactions/' + payload.txId, { method: 'PATCH', body: { [field]: payload.checked } });
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
                // Токен сохраняем сразу; роль будет уточнена из /auth/me в bootApi() (B6).
                window.V2Api.save(result.access_token, result.role);
                overlay.hidden = true;
                // bootApi() вызовет /auth/me и перезапишет роль подтверждённой.
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
    // Сбой загрузки показываем как сбой. Раньше здесь был переход на демо-данные:
    // достаточно было одному из десяти запросов ответить 403/500, и менеджер
    // работал с выдуманными сделками «К-101…», принимая их за боевые.
    function showFatal(err) {
        const status = err && err.status ? ' (код ' + err.status + ')' : '';
        const detail = (err && (typeof err.detail === 'string' ? err.detail : err.message)) || 'неизвестная ошибка';
        setText('CRM недоступен');
        const host = document.getElementById('shell-content');
        if (!host) return;
        host.innerHTML = '<section class="card" role="alert">' +
            '<h2>Не удалось загрузить данные CRM' + esc(status) + '</h2>' +
            '<p>' + esc(detail) + '</p>' +
            '<p class="fin-note">Демо-данные вместо боевых не показываются намеренно: ' +
            'работа с ними выглядела бы как настоящие сделки и суммы.</p>' +
            '<button type="button" class="btn btn-primary" id="v2-retry">Повторить</button>' +
            '</section>';
        const retry = document.getElementById('v2-retry');
        if (retry) retry.addEventListener('click', function () { start(); });
    }

    async function start() {
        try {
            if (DEMO) { await bootDemo(); return; }
            try {
                await bootApi();
            } catch (err) {
                if (err && err.unauthorized) { showLogin(''); return; }
                console.error('Загрузка данных CRM не удалась:', err);
                showFatal(err);
            }
        } catch (err) {
            console.error(err);
            setText('Ошибка загрузки: ' + (err && err.message || 'неизвестная'));
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
