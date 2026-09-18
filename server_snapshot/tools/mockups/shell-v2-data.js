/* ============================================================
   Демо-«сервер»: справочники и карточки в одном месте, как на
   проде (deal_statuses, store_locations, clients, users,
   suppliers + сделки). Превью: без сети и хранилища, всё живёт
   до перезагрузки. Идентификаторы статусов фиксированные —
   на них завязаны карточки, доска и проверки.
   ============================================================ */
(function () {
    'use strict';

    var KBData = {
        // Демо-дата «сегодня»: согласована с календарём задач (12 сентября 2026).
        today: new Date(2026, 8, 12),

        // deal_statuses: role='board' — колонка доски, 'writeoff' — очередь выписки.
        statuses: [
            { id: 'new',      name: 'Новый запрос', color: 'faint',  position: 0, role: 'board' },
            { id: 'work',     name: 'В работе',     color: 'accent', position: 1, role: 'board' },
            { id: 'pay',      name: 'Ждёт оплаты',  color: 'warn',   position: 2, role: 'board' },
            { id: 'assembly', name: 'Сборка',       color: 'ok',     position: 3, role: 'board' },
            { id: 'writeoff', name: 'На списание',  color: 'danger', position: 4, role: 'writeoff' }
        ],

        // store_locations
        stores: [
            { id: 'store-1', name: 'Матусевича',  address: 'г. Минск, ул. Матусевича, 10',  phone: '+375 17 200-11-22' },
            { id: 'store-2', name: 'Богдановича', address: 'г. Минск, ул. Богдановича, 118', phone: '+375 17 200-33-44' },
            { id: 'store-3', name: 'БН',          address: 'г. Минск, пр-т БН, 55',         phone: '+375 17 200-55-66' },
            { id: 'store-4', name: 'Домбровская', address: 'г. Минск, ул. Домбровской, 9',  phone: '+375 17 200-77-88' }
        ],

        // users: одна таблица и для админки (username), и для ответственных на доске.
        users: [
            { id: 'us-1', username: 'ivanov',      full_name: 'Иванов Иван',    initials: 'ИИ', role: 'admin' },
            { id: 'us-2', username: 'matusevich',  full_name: 'Матусевич А.',   initials: 'МБ', role: 'manager' },
            { id: 'us-3', username: 'bogdanovich', full_name: 'Богданович Г.',  initials: 'БГ', role: 'manager' },
            { id: 'us-4', username: 'dombrovskaya', full_name: 'Домбровская Н.', initials: 'ДБ', role: 'manager' }
        ],

        // clients: сущности с ID — карточки ссылаются на них, а не на строки.
        clients: [
            { id: 'cl-1', name: 'ЗАО «СветлогорскДом»', unp: '100555666', contact_person: 'Лукашевич Ирина', phone: '+375 29 111-22-33', address: 'г. Минск, пр-т Дзержинского, д. 119, пом. 3' },
            { id: 'cl-2', name: 'ИП Ковалёв С.М.',      unp: '291333444', contact_person: 'Ковалёв Сергей',  phone: '+375 29 222-33-44', address: 'г. Минск, ул. Одинцова, д. 42' },
            { id: 'cl-3', name: 'ООО «Веснаторг»',      unp: '190111222', contact_person: 'Петрова Анна',    phone: '+375 29 333-44-55', address: 'г. Минск, ул. Тимирязева, д. 65' },
            { id: 'cl-4', name: 'ООО «РемонтСити»',     unp: '191777888', contact_person: 'Гончарук Дмитрий', phone: '+375 29 444-55-66', address: 'г. Минск, ул. Кошевого, д. 8' },
            { id: 'cl-5', name: 'ЧТУП «ЛюменБел»',      unp: '192555001', contact_person: 'Соколов Павел',   phone: '+375 29 555-66-77', address: 'г. Минск, ул. Платонова, д. 22' },
            { id: 'cl-6', name: 'ООО «Гродторг»',       unp: '500111888', contact_person: 'Веренич Ольга',   phone: '+375 152 44-55-66', address: 'г. Гродно, ул. Горького, д. 71' },
            { id: 'cl-7', name: 'ИП Савицкая Е.А.',     unp: '193666777', contact_person: 'Савицкая Елена',  phone: '+375 29 666-77-88', address: 'г. Минск, ул. Якубова, д. 3' },
            { id: 'cl-8', name: 'ОАО «Могилёвстрой»',   unp: '700222333', contact_person: 'Козлов Игорь',    phone: '+375 222 22-33-44', address: 'г. Могилёв, ул. Первомайская, д. 31' }
        ],

        // suppliers: тот же справочник, что использует закупка и админка.
        suppliers: [
            { id: 'demo-1', name: 'Люстра Опт',    unp: '191001122', contact_person: 'Хомич Андрей',  phone: '+375 29 700-11-22', email: 'sale@lustra-opt.by', address: 'г. Минск, ул. Селицкого, д. 17' },
            { id: 'demo-2', name: 'СветКомплект',  unp: '192003344', contact_person: 'Романова Ирина', phone: '+375 29 700-33-44', email: 'info@svetkomplekt.by', address: 'г. Минск, ул. Аранская, д. 44' },
            { id: 'demo-3', name: 'ЭлектроСнаб',   unp: '500005566', contact_person: 'Гринь Виктор',  phone: '+375 152 60-11-22', email: 'opt@electrosnab.by', address: 'г. Гродно, ул. Победы, д. 28' }
        ],

        // tasks: демо-набор, согласованный с календарём (сегодня — 12.09.2026).
        tasks: [
            { id: 'task-1', title: 'Позвонить СветлогорскДом',     clientId: 'cl-1', assignee: 'us-1', due: '11.09.2026', done: false },
            { id: 'task-2', title: 'Отгрузка LED-лент РемонтСити', clientId: 'cl-4', assignee: 'us-3', due: '13.09.2026', done: false },
            { id: 'task-3', title: 'Согласовать смету Ковалёв',    clientId: 'cl-2', assignee: 'us-1', due: '14.09.2026', done: false },
            { id: 'task-4', title: 'Замер освещения на Веснаторг', clientId: 'cl-3', assignee: 'us-2', due: '17.09.2026', done: false },
            { id: 'task-5', title: 'Проверить акт Ковалёв',        clientId: 'cl-2', assignee: 'us-1', due: '06.09.2026', done: true, closed: 'закрыто 06.09' }
        ]
    };

    var SUPPLIER_ITEMS = ['Профиль алюминиевый', 'Драйверы Mean Well', 'LED-модули 2835', 'Рассеиватели опал', 'Клеммы и крепёж'];

    // Детерминированный PRNG (mulberry32) — одинаковые ~60 карточек при каждой загрузке.
    function mulberry32(seed) {
        return function () {
            seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
            var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    function buildCards() {
        var rnd = mulberry32(20260915);
        var paidRnd = mulberry32(20260916); // отдельный поток: суммы оплат не сдвигают демо-раскладку
        var boardStatuses = KBData.statuses.filter(function (s) { return s.role === 'board' || s.role === 'writeoff'; });
        boardStatuses = boardStatuses.slice().sort(function (a, b) { return a.position - b.position; });
        var cards = [];
        for (var i = 0; i < 60; i++) {
            var client = KBData.clients[Math.floor(rnd() * KBData.clients.length)];
            var title = [
                'Светильники для офиса', 'LED-ленты на склад', 'БРА-серия для кафе', 'Люстра зал №2',
                'Трек-система шоурум', 'Прожекторы фасада', 'УФ-лампа для салона', 'Панели потолочные',
                'Датчики движения', 'Трансформаторы 12V', 'Споты кухонный гарнитур', 'Линейные светильники цех',
                'Уличные фонари двор', 'Таблички с подсветкой', 'Гирлянды фасадные', 'Армстронг 600х600'
            ][Math.floor(rnd() * 16)];
            var manager = KBData.users[Math.floor(rnd() * KBData.users.length)];
            var store = KBData.stores[Math.floor(rnd() * KBData.stores.length)];
            var stage = boardStatuses[Math.floor(rnd() * boardStatuses.length)];
            var amount = 12000 + Math.floor(rnd() * 880000); // копейки
            var day = 1 + Math.floor(rnd() * 28);
            var checklist = [];
            var nItems = 1 + Math.floor(rnd() * 3);
            for (var j = 0; j < nItems; j++) {
                var item = SUPPLIER_ITEMS[Math.floor(rnd() * SUPPLIER_ITEMS.length)];
                // в очереди списания позиции уже заказаны и получены
                var ordered = stage.id === 'writeoff' || rnd() > 0.45;
                checklist.push({ id: 'ck' + i + j, label: item, ordered: ordered, received: ordered && (stage.id === 'writeoff' || rnd() > 0.4) });
            }
            // Оплачено по модели карточки (paid_amount): 0 / часть / полная сумма.
            var paidRoll = paidRnd();
            var paidAmount = 0;
            if (paidRoll > 0.8) paidAmount = amount;
            else if (paidRoll > 0.55) paidAmount = Math.floor(amount * (0.15 + paidRnd() * 0.5) / 100) * 100;
            cards.push({
                id: 'K-' + (101 + i),
                title: title + ' — ' + client.name,
                clientId: client.id,
                client: client.name,
                store: store.id,
                manager: manager,
                amount: amount,
                paidAmount: paidAmount,    // оплата — отдельно от условий оплаты и выписки
                issued: 0,                 // списано копейками
                stage: stage.id,
                deadline: day + '.09.2026',
                note: 'Демо-заметка по сделке ' + (101 + i),
                paymentTerms: '',
                paymentDetails: null,
                checklist: checklist,
                docs: []                   // выписанные ТН
            });
        }
        return cards;
    }

    KBData.cards = buildCards();

    // Групповые списания (аналог writeoff_groups): одна накладная на
    // несколько карточек. covers — сколько покрыто у каждой карточки;
    // отмена группы возвращает эти суммы в «Осталось выписать».
    KBData.groups = [];
    KBData.nextGroupId = 1;

    // Цвет статуса: имя токена ('accent') или hex из админки (с решёткой или без).
    KBData.statusColor = function (status) {
        var value = String(status.color || '').replace(/^#/, '');
        return /^[0-9a-f]{6}$/i.test(value) ? '#' + value : 'var(--' + (value || 'faint') + ')';
    };
    KBData.boardStatuses = function () {
        return KBData.statuses.filter(function (s) { return s.role === 'board'; })
            .slice().sort(function (a, b) { return a.position - b.position; });
    };
    KBData.writeoffStatus = function () {
        return KBData.statuses.filter(function (s) { return s.role === 'writeoff'; })
            .sort(function (a, b) { return a.position - b.position; })[0] || null;
    };
    KBData.storeName = function (id) {
        var store = KBData.stores.filter(function (s) { return s.id === id; })[0];
        return store ? store.name : String(id || '');
    };
    KBData.clientById = function (id) {
        return KBData.clients.filter(function (c) { return c.id === id; })[0] || null;
    };

    window.KBData = KBData;
})();
