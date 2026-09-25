/* ============================================================
   Канбан: компактная доска. В собранном виде данные читаются из
   CRM (KBData), а правки уходят на сервер (KBData.mutate / V2Api);
   в предпросмотре мокапа (file://, ?demo=1) тех же хуков нет —
   там доска живёт на наборе shell-v2-data.js до перезагрузки.
   Выписанные ТН передаются в финансовые документы.
   Реестр оплат остаётся отдельным набором данных.
   Все суммы — целые копейки (integers), форматирование на выводе.
   ============================================================ */
(function () {
    'use strict';

    // Справочники и карточки — из KBData: на проде её наполняет загрузчик
    // (js/v2/boot.js, ответы CRM API), в предпросмотре мокапа — shell-v2-data.js.
    // Статусы, магазины и пользователи правятся в админке и управляют доской.
    var cards = KBData.cards;

    function boardStatuses() { return KBData.boardStatuses(); }
    function writeoffStatus() { return KBData.writeoffStatus(); }
    // priority: 'all'|'0'..'3' (строкой — как в значении селекта), pay: 'all'|'bn'|'cash',
    // listPage — страница табличных видов, viewId — применённый сохранённый вид.
    var state = { search: '', store: 'all', compact: true, queueMode: 'pending', history: [], hideFilled: false, priority: 'all', pay: 'all', listPage: 1, viewId: '' };

    // Состояние доски в адресе (#board?q=…&store=…&mode=…) — ссылкой на
    // отфильтрованный вид можно поделиться. replaceState: без мусора в истории.
    function readBoardParams() {
        var hash = location.hash.slice(1);
        var q = hash.indexOf('?');
        if (q < 0) return {};
        var out = {};
        hash.slice(q + 1).split('&').forEach(function (pair) {
            if (!pair) return;
            var kv = pair.split('=');
            out[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || '');
        });
        return out;
    }
    function writeBoardParams(patch) {
        var hash = location.hash.slice(1);
        var route = hash.indexOf('?') >= 0 ? hash.slice(0, hash.indexOf('?')) : hash;
        if (route && route !== 'board') return; // активен другой раздел — адрес не трогаем
        var p = readBoardParams();
        Object.keys(patch).forEach(function (k) {
            if (patch[k]) p[k] = patch[k]; else delete p[k];
        });
        var qs = Object.keys(p).map(function (k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(p[k]);
        }).join('&');
        history.replaceState(null, '', '#board' + (qs ? '?' + qs : ''));
    }

    // Запись в CRM (site-v2): в прототипе KBData.mutate нет — хук молчит.
    function apiMutate(kind, payload) {
        if (window.KBData.mutate) return window.KBData.mutate(kind, payload);
        return Promise.resolve(null); // прототип: записи в CRM нет
    }
    // Ролевая видимость (пункт 9 плана): разрушающие действия, закрытые на
    // сервере ролью admin, помечаются data-admin-only, а скрывает их одна
    // функция загрузчика — V2Role.apply (v2-boot-template.js). Вызов
    // идемпотентен, поэтому повторяется после каждой перестройки своих узлов.
    // В предпросмотре мокапа (file://, ?demo=1) загрузчика нет — молчим, там
    // интерфейс показывают целиком намеренно.
    function applyRoleMarks(root) {
        if (window.V2Role && window.V2Role.apply) window.V2Role.apply(root);
    }
    function isoFromRu(value) {
        var m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(String(value || '').trim());
        return m ? m[3] + '-' + m[2] + '-' + m[1] : null;
    }
    function ruFromIso(value) {
        var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
        return m ? m[3] + '.' + m[2] + '.' + m[1] : value;
    }

    // ---------- утилиты ----------
    function money(cents) {
        var sign = cents < 0 ? '−' : '';
        var abs = Math.abs(cents);
        var rub = Math.floor(abs / 100);
        var kop = ('0' + (abs % 100)).slice(-2);
        return sign + String(rub).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ',' + kop;
    }
    function parseMoney(str) {
        // ввод: "1 234,56" / "1234.56" / "−1234,56" / "-1234.56" (копейки не принимаем вслепую — считаем рубли и копейки)
        var s = String(str || '').trim().replace(/\s|\u00a0/g, '').replace(',', '.').replace(/\u2212/, '-');
        if (!/^-?\d+(\.\d{1,2})?$/.test(s)) return null;
        var neg = s.charAt(0) === '-';
        if (neg) s = s.slice(1);
        var parts = s.split('.');
        var rub = parseInt(parts[0], 10);
        var kop = parts.length === 2 ? parseInt((parts[1] + '0').slice(0, 2), 10) : 0;
        var result = rub * 100 + kop;
        return neg ? -result : result;
    }
    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    // Аудит F7, вторая половина (V2-UI-AUDIT-2026-09-22): тот же legacy-импорт
    // приносит имена вторым видом мусора — MIME encoded-word (RFC 2047) из
    // заголовков писем: «=?windows-1251?B?8OXq4ujn6PL7LTIzMDkyNS5kb2N4?=», а
    // длинное имя разбито на несколько слов с переносом строки между ними
    // (разделитель между соседними словами в имя не входит — склеиваем без него).
    // Расшифровка только для показа: в БД и на диске имя остаётся как лежало.
    // Исключений наружу не пускаем ни на каком шаге — битый base64, неизвестная
    // кодировка, браузер без нужного TextDecoder отдадут исходную строку:
    // иначе у сделки рухнет весь список вложений ради одного мусорного имени.
    var MIME_WORD = /=\?([^?]+)\?([BbQq])\?([^?]*)\?=/g;
    var MIME_WORDS = /(?:=\?[^?]+\?[BbQq]\?[^?]*\?=)(?:(?:\r?\n)?[ \t]*(?:=\?[^?]+\?[BbQq]\?[^?]*\?=))*/g;
    // Байты quoted-printable: «=XX» — hex, «_» — пробел. Всё, что не похоже на
    // Q-encoding (управляющие символы, не-ASCII), — «не распаковали».
    function qpToBytes(text) {
        var out = [];
        for (var i = 0; i < text.length; i++) {
            var ch = text.charAt(i);
            if (ch === '_') { out.push(32); continue; }
            if (ch === '=') {
                var hex = text.substr(i + 1, 2);
                if (!/^[0-9A-Fa-f]{2}$/.test(hex)) return null;
                out.push(parseInt(hex, 16));
                i += 2;
                continue;
            }
            var code = text.charCodeAt(i);
            if (code < 32 || code > 126) return null;
            out.push(code);
        }
        return new Uint8Array(out);
    }
    function mimeWordText(charset, encoding, text) {
        try {
            if (typeof TextDecoder !== 'function') return null;
            var enc = String(encoding).toUpperCase();
            var bytes = null;
            if (enc === 'B') {
                var b64 = String(text).replace(/\s+/g, '');
                if (!b64.length || b64.length % 4 === 1) return null;
                var bin = atob(b64);
                bytes = new Uint8Array(bin.length);
                for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            } else if (enc === 'Q') {
                bytes = qpToBytes(String(text));
            }
            if (!bytes || !bytes.length) return null;
            // Неизвестный label (или кодировка, которую браузер не берётся
            // декодировать) — RangeError, его ловим выше. Битые байты
            // TextDecoder меняет на U+FFFD молча, но пустое имя хуже исходного
            // мусора — тоже отказ.
            var decoded = new TextDecoder(String(charset).trim()).decode(bytes);
            return decoded.length ? decoded : null;
        } catch (e) {
            return null;
        }
    }
    // Вся цепочка слов раскрывается целиком или не раскрывается вовсе: имя,
    // собранное из куска расшифрованного текста и куска нетронутого base64,
    // читателю не помогает — оставляем исходное.
    function decodeMimeWords(raw) {
        return raw.replace(MIME_WORDS, function (run) {
            var text = '';
            var ok = true;
            run.replace(MIME_WORD, function (word, charset, encoding, body) {
                var decoded = mimeWordText(charset, encoding, body);
                if (decoded === null) { ok = false; return word; }
                text += decoded;
                return '';
            });
            return ok ? text : run;
        });
    }
    // Аудит F7 (V2-UI-AUDIT-2026-09-22): вложения из legacy-импорта лежат в БД с
    // percent-кодированным именем — в двух видах: честный «%D1%81…» и
    // санитизированный браузером «_D1_81…» (при скачивании «%» поехал в «_»).
    // Показываем человекочитаемое имя. Признак мусора — серия hex-пар НЕ младше
    // двух (одиночные «_00…», «_20…» живут в нормальных именах: «IMGG_0013.pdf»,
    // «3015_260730151520_001.pdf») и декодируется она в не-ASCII: иначе
    // «файл_20_23.pdf» превратился бы в «файл #.pdf». decodeURIComponent бросает
    // URIError на битой последовательности — фолбэк всегда исходная строка.
    // Порядок проходов: encoded-word раньше percent, и второй проход запускается
    // только если первый ничего не нашёл, — иначе распаковали бы уже
    // распакованное (база в Q-кодировке сама состоит из «=XX», а в имени после
    // MIME-раскодировки могли бы остаться hex-пары).
    function displayName(name) {
        var raw = String(name == null ? '' : name);
        var mime = decodeMimeWords(raw);
        if (mime !== raw) return mime;
        var runs = /(?:%[0-9A-Fa-f]{2}){2,}|(?:_[0-9A-Fa-f]{2}){2,}/g;
        return raw.replace(runs, function (run) {
            try {
                var decoded = decodeURIComponent(run.replace(/_/g, '%'));
                return /[^\x00-\x7F]/.test(decoded) ? decoded : run;
            } catch (e) {
                return run;
            }
        });
    }
    function stageName(id) {
        var s = KBData.statuses.find(function (x) { return x.id === id; });
        return s ? s.name : id;
    }
    // 1 сделка / 2 сделки / 5 сделок — тосты и подтверждения читают число.
    function pluralRu(n, forms) {
        var d = n % 10, h = n % 100;
        return forms[d === 1 && h !== 11 ? 0 : (d >= 2 && d <= 4 && (h < 10 || h >= 20) ? 1 : 2)];
    }
    // Название этапа «Сборка» — из справочника (админка переименовывает
    // статусы); по-русски и без словаря, чтобы в тексте подтверждения не
    // лезло служебное «assembly».
    function assemblyStageName() {
        var s = KBData.statuses.filter(function (x) { return x.id === 'assembly'; })[0];
        return (s && s.name) || 'Сборка';
    }
    // Название этапа очереди списания — из справочника: админка переименовывает
    // статусы, а подсказка дровера держала «На списание» хардкодом (аудит F10).
    function writeoffStageName() {
        var st = writeoffStatus();
        return (st && st.name) || 'На списание';
    }
    // ── Отключённый пользователь (is_active=false, пункт 15 плана) ────────
    // Тот же приём, что в «Пульте дня» и задачах (shell-v2-insights.js):
    // исторические сделки на таком ответственном остаются и должны быть
    // видны, поэтому из списков он не исчезает — только приглушён цветом
    // токена и подписан по-русски. Флаг в справочник кладёт загрузчик
    // (v2-boot-template.js); в демо-наборе и у служебного «Не назначен»
    // поля нет — значит «активен», не выдумываем.
    function isOffUser(u) { return !!u && u.is_active === false; }
    function userNameOff(u) {
        var name = u ? String(u.full_name || u.username || '') : '';
        return name + (isOffUser(u) ? ' (отключён)' : '');
    }
    // Отключённый пользователь остаётся в истории, но получает внешний класс
    // приглушения и понятную подсказку без inline-стилей.
    function offUserAttrs(u) {
        return isOffUser(u)
            ? ' class="user-off-faint" title="Пользователь отключён: в CRM не входит, ответить не сможет; сделки на нём остались"'
            : '';
    }
    // 'cl-22' → 22
    function numericClientIdValue(id) {
        var n = parseInt(String(id == null ? '' : id).replace(/[^0-9]/g, ''), 10);
        return isNaN(n) ? null : n;
    }
    // A12: сервер — единственный источник остатка. remaining_kop (int, копейки)
    // отдаётся в карточке; если серверное поле отсутствует (старый кэш
    // загрузчика, предпросмотр мокапа без сервера) — null, и вызывающий код
    // показывает «—», а не считает сам.
    function cardRemaining(card) {
        if (card.remaining_kop !== undefined && card.remaining_kop !== null) return card.remaining_kop;
        return null;
    }
    // Выписано в копейках: серверное issued_total (руб→коп), которое доска
    // синхронно правит при оптимистичной выписке/отмене ТН, или — если сервер
    // его не отдал — счётчик card.issued: загрузчик собирает его по записям
    // реестра, а в предпросмотре мокапа он чисто локальный.
    function cardIssued(card) {
        if (card.issued_total !== undefined && card.issued_total !== null) return Math.round(card.issued_total * 100);
        return card.issued || 0;
    }
    // Для проверок «осталось к выписке > 0» (фильтр очередей): если сервер
    // не отдал remaining_kop, карточка пропускается — не показываем и не
    // считаем её «к списанию», пока серверные данные не появятся.
    function hasRemaining(card) {
        var r = cardRemaining(card);
        return r !== null && r > 0;
    }
    // Форматирование остатка: число или «—» если серверных данных нет.
    function moneyOrDash(cents) {
        return cents === null || cents === undefined ? '—' : money(cents);
    }
    // Дефект 7 реестра V2-WORKPLAN-2026-09-22: после локальной отмены ТН деньги
    // карточки нельзя инвалидировать — без remaining_kop карточка не проходит
    // hasRemaining и выпадает из очереди «На списание» до перезагрузки.
    // Пересчитываем из того, что пользователь видит в «Накладных»: суммы docs и
    // card.amount — целые копейки (шапка файла), issued_total — рубли (cardIssued
    // умножает на 100), отсечка остатка нулём — как max(0, …) в services/card_money.py.
    function recalcCardMoney(card) {
        var issued = Math.round((card.docs || []).reduce(function (a, d) { return a + (d.amount || 0); }, 0));
        card.issued = issued;
        card.issued_total = issued / 100;
        // Сумма сделки неизвестна — остаток выдумывать нечем: оставляем null,
        // hasRemaining(null) сознательно держит такую карточку вне очереди
        // (см. комментарий к cardRemaining), подмены серверным расчётом нет.
        var amountKnown = typeof card.amount === 'number' && isFinite(card.amount);
        card.remaining_kop = amountKnown ? Math.max(0, Math.round(card.amount - issued)) : null;
    }
    // Симметрия к предыдущему пересчёту (дефект 7 реестра, путь ВЫПИСКИ):
    // групповая ТН закрывает карточку, а не «оставляет неизвестной». Сервер
    // поднимает is_warehouse_writeoff на ВСЕХ записях карточки и ведёт её
    // статус в «Закрыто» (routers/writeoff_groups_router.py, issue-invoice),
    // значит выписано = вся сумма сделки, остаток = 0. Инвалидация
    // remaining_kop/issued_total роняла карточку из обеих очередей: без
    // остатка она не проходила фильтр «На списание», а в «Списано» попадала
    // только при сохранённом cardIssued > 0. Σ docs здесь неприменим — у
    // группового документа card_id = NULL и в docs карточки он не лежит.
    function coverCardByGroupIssue(card) {
        var known = typeof card.amount === 'number' && isFinite(card.amount);
        var issued = known ? Math.round(card.amount) : cardIssued(card);
        card.issued = issued;
        card.issued_total = issued / 100;
        card.remaining_kop = known ? Math.max(0, Math.round(card.amount - issued)) : null;
        // Сервер после групповой выписки ведёт карточку в статус «Закрыто» —
        // это id 'closed', а не псевдо-этап полной выписки 'done' (Р2 пункта 12).
        card.stage = 'closed';
    }
    // Причина отказа одним текстом: серверные detail идут по-русски и без
    // точки на конце — конечную пунктуацию срезаем, когда вставляем причину
    // в середину фразы.
    function reasonText(err) {
        return String((err && (err.detail || err.message)) || 'ошибка').replace(/[.!?]+$/, '');
    }
    // 'cl-22' → 22: клиентские id в v2 с префиксом, API ждёт число
    // (фидбек 18.09: Number('cl-22') = NaN ломал баланс и кассу клиента)
    function numericClientId(id) {
        const n = parseInt(String(id == null ? '' : id).replace(/[^0-9]/g, ''), 10);
        return isNaN(n) ? null : n;
    }
    function checklistSummary(card) {
        var total = card.checklist.length;
        var done = card.checklist.filter(function (i) { return i.received; }).length;
        return { total: total, received: done, ordered: card.checklist.filter(function (i) { return i.ordered; }).length };
    }

    // ---------- рендер доски ----------
    var boardEl = document.getElementById('kb-board');
    var searchEl = document.getElementById('kb-search');
    var storeEl = document.getElementById('kb-store-select');
    var rootEl = document.getElementById('kb-root');

    function visibleCards() {
        var q = state.search.trim().toLowerCase();
        return cards.filter(function (c) {
            if (state.store !== 'all' && c.store !== state.store) return false;
            if (q && (c.title + ' ' + c.client + ' ' + c.id).toLowerCase().indexOf(q) === -1) return false;
            // Приоритет строкой — сравнение как в legacy (site/js/kanban.js:225);
            // карточки демо-набора без поля считаются «Без приоритета».
            if (state.priority !== 'all' && String(c.priority || 0) !== state.priority) return false;
            // «Безнал (БН)» — псевдо-магазин legacy: отдельного поля оплаты нет,
            // фильтр по сырому store_location (storeRaw из boot). Карточки без
            // магазина не попадают ни в «БН», ни в «Наличные» — как в legacy.
            if (state.pay !== 'all') {
                var raw = String(c.storeRaw || '');
                if (state.pay === 'bn' && raw !== 'БН') return false;
                if (state.pay === 'cash' && (raw === '' || raw === 'БН')) return false;
            }
            return true;
        });
    }

    // Плашка пустой колонки (пункт 7 V2-WORKPLAN). «Пусто» — когда колонка
    // правда пуста; «Нет совпадений» — когда под активный фильтр не попала
    // ни одна её сделка, а доска в целом что-то показывает; плашки нет —
    // когда фильтры не оставили ничего вообще: там говорит общий блок
    // kb-no-results, а не четыре одинаковых.
    function columnPlateText(nothingAtAll) {
        if (!filtersActive()) return 'Пусто';
        return nothingAtAll ? '' : 'Нет совпадений';
    }
    function columnPlateHTML(nothingAtAll) {
        var text = columnPlateText(nothingAtAll);
        return text ? '<p class="kb-empty">' + text + '</p>' : '';
    }

    // Иконка счётчика — пустой <i>, закрашенный CSS-маской (.kb-counter-ico,
    // shell-v2-board.css), а не инлайн-<svg>: гейт «SVG в доске < 100» считает
    // элементы <svg> внутри #kb-board, и спрайт <symbol>+<use> его не закрывает —
    // <use> всё равно остаётся внутри своего <svg>. Семантика доступности
    // не меняется: role="img" + title + aria-label на обёртке, число — в span.
    function counterHTML(flag, label, count) {
        return '<span class="kb-counter" data-counter="' + flag + '" role="img" title="' + label + ': ' + count + '" aria-label="' + label + ': ' + count + '"><i class="kb-counter-ico" aria-hidden="true"></i><span aria-hidden="true">' + count + '</span></span>';
    }
    function cardHTML(c) {
        var cl = checklistSummary(c);
        var chips = '<span class="pill">' + esc(KBData.storeName(c.store)) + '</span>' +
            '<span class="pill"' + offUserAttrs(c.manager) + '><span class="avatar sm">' + esc(c.manager.initials || '··') + '</span>' + esc(userNameOff(c.manager)) + '</span>';
        chips += counterHTML('ordered', 'Заказано', cl.ordered) + counterHTML('received', 'Получено', cl.received);
        // Статус оплаты — из CRM (payment_status); признак полной оплаты —
        // по серверным полям (paidAmount/amount), не по хардкоду строки.
        var fullyPaid = c.paidAmount >= c.amount && c.amount > 0;
        var payStatus = c.payment_status || (fullyPaid ? 'Оплачен' : (c.paidAmount > 0 ? 'Частично' : 'Не оплачен'));
        if (payStatus === 'Оплачен' || fullyPaid) chips += '<span class="pill ok">оплачено полностью</span>';
        else if (payStatus === 'Отсрочка') chips += '<span class="pill warn">отсрочка' + (c.paidAmount > 0 ? ' · ' + money(c.paidAmount) : '') + '</span>';
        else if (c.paidAmount > 0) chips += '<span class="pill ok">оплачено ' + money(c.paidAmount) + '</span>';
        var remKop = cardRemaining(c);
        var issuedKop = cardIssued(c);
        if (remKop !== null && issuedKop > 0 && remKop > 0) chips += '<span class="pill warn">остаток ' + moneyOrDash(remKop) + '</span>';
        return '<div class="kb-card" draggable="true" data-card="' + esc(c.id) + '">' +
            '<button type="button" class="kb-card-open" data-card-open="' + esc(c.id) + '" aria-label="Открыть сделку ' + esc(c.title) + '" aria-haspopup="dialog">' +
                '<span class="kb-card-title">' + esc(c.title) + '</span>' +
                '<span class="kb-card-meta num">' + esc(c.id) + ' · ' + esc(c.deadline) + '</span>' +
                '<span class="kb-card-chips">' + chips + '</span>' +
                '<span class="kb-card-foot num"><b>' + money(c.amount) + ' BYN</b>' +
                (issuedKop > 0 ? ' · списано ' + money(issuedKop) : '') +
                (c.docs.length ? ' · ТН: ' + c.docs.length : '') +
                (c.groupId ? ' · групповая ТН' : '') + '</span>' +
            '</button>' +
            '<button type="button" class="kb-card-del" data-card-del="' + esc(c.id) + '" aria-label="Удалить карточку ' + esc(c.title) + '" title="Удалить"></button>' +
            '</div>';
    }

    function renderBoard() {
        var list = visibleCards();
        boardEl.innerHTML = '';
        boardStatuses().forEach(function (st) {
            var inCol = list.filter(function (c) { return c.stage === st.id; });
            var sum = inCol.reduce(function (a, c) { return a + c.amount; }, 0);
            var col = document.createElement('section');
            col.className = 'kb-col';
            col.dataset.stage = st.id;
            col.style.setProperty('--stage-color', KBData.statusColor(st));
            col.setAttribute('aria-label', st.name + ' — карточек: ' + inCol.length);
            col.innerHTML =
                '<header class="kb-col-head"><span class="kb-col-title">' + esc(st.name) + '</span>' +
                '<span class="kb-col-count num">' + inCol.length + '</span>' +
                '<span class="kb-col-sum num">' + money(sum) + '</span></header>' +
                '<div class="kb-cards">' +
                (inCol.length ? inCol.map(cardHTML).join('') : columnPlateHTML(!list.length)) +
                '</div>';
            boardEl.appendChild(col);
        });
        syncNoResults();
        // Все перестройки колонок идут отсюда: refresh(), kb:reloaded, смена
        // фильтра и поиска. Плитки — уже другие элементы с другой геометрией,
        // поэтому кэш rect'ов перетаскивания обнуляем здесь, а не в каждом
        // месте вызова.
        invalidateRects();
    }

    // Shared order is authoritative, including when a search hides neighbours.
    function moveCard(card, stage, beforeId) {
        var index = cards.indexOf(card);
        if (index < 0) return;
        cards.splice(index, 1);
        var target = beforeId ? cards.findIndex(function(c) { return c.id === beforeId; }) : -1;
        if (target < 0) {
            target = cards.length;
            for (var i = cards.length - 1; i >= 0; i--) {
                if (cards[i].stage === stage) { target = i + 1; break; }
            }
        }
        card.stage = stage;
        cards.splice(target, 0, card);
    }
    // Точечное обновление шапки колонки после локального переноса: те же
    // числа, что считает renderBoard(), но без перестройки колонок.
    function updateColumnHead(stageId) {
        var st = KBData.statuses.find(function (x) { return x.id === stageId; });
        var col = boardEl.querySelector('.kb-col[data-stage="' + stageId + '"]');
        if (!st || !col) return;
        var visible = visibleCards();
        var inCol = visible.filter(function (c) { return c.stage === stageId; });
        col.querySelector('.kb-col-count').textContent = inCol.length;
        col.querySelector('.kb-col-sum').textContent = money(inCol.reduce(function (a, c) { return a + c.amount; }, 0));
        col.setAttribute('aria-label', st.name + ' — карточек: ' + inCol.length);
        var list = col.querySelector('.kb-cards');
        var plate = list.querySelector('.kb-empty');
        var plateText = inCol.length ? '' : columnPlateText(!visible.length);
        if (plate && plate.textContent !== plateText) plate.remove();
        if (plateText && !list.querySelector('.kb-empty')) {
            list.insertAdjacentHTML('beforeend', '<p class="kb-empty">' + plateText + '</p>');
        }
        // Шапка могла сменить высоту (другое число/сумма), «Пусто» — состав
        // колонки: плитки под ней едут.
        invalidateRects();
    }
    // Точечная синхронизация одной карточки вместо refresh(): плитка
    // перестраивается из cardHTML(), шапка её колонки и очередь
    // пересчитываются. Полный renderBoard() на одну галочку закупки стоил
    // ~2000 мутаций DOM и 60-70 мс блокировки (аудит 22.09, V2-UI-AUDIT F1).
    // opts.documents — дополнительно разослать kb:documents: нужно, когда
    // изменились деньги, клиент, название или документы (их читают финансы
    // и календарь), и не нужно для флагов закупки ordered/received.
    function syncCard(card, opts) {
        var o = opts || {};
        var tile = boardEl.querySelector('.kb-card[data-card="' + card.id + '"]');
        var visible = visibleCards().indexOf(card) >= 0;
        if (tile && !visible) tile.remove();
        else if (tile) {
            var holder = document.createElement('div');
            holder.innerHTML = cardHTML(card);
            if (holder.firstElementChild) tile.parentNode.replaceChild(holder.firstElementChild, tile);
        } else if (visible) {
            // Плитки нет, хотя карточка теперь видна (сменился этап или фильтр) —
            // вставка с соблюдением порядка колонок дороже полной перерисовки.
            refresh();
            return;
        }
        // syncCard приходит из async-колбэков (галочка закупки, ответ сервера),
        // которые вполне успевают выстрелить посреди перетаскивания: плитка
        // заменена новой — соседние сдвинулись, их rect'ы больше не верны.
        invalidateRects();
        updateColumnHead(card.stage);
        renderQueue();
        if (o.documents) dispatchDocuments();
    }
    var drag = null;
    var dragFrame = 0;
    var suppressCardClick = false;
    // ---------- троттлинг и кэш геометрии перетаскивания ----------
    // placeDrop() весь построен на чтениях геометрии: elementFromPoint, rect
    // плейсхолдера и rect каждой плитки колонки в поиске соседа. Каждое чтение
    // после записи в DOM форсирует синхронный пересчёт layout, а на колонке
    // в 168 карточек один поиск соседа — это десятки чтений. Хуже того, вызовов
    // было два на кадр: из обработчика dragover и из rAF-петли автоскролла.
    // Поэтому: (1) rect читаем из кэша, который живёт от инвалидации до
    // инвалидации; (2) requestDrop() схлопывает все события кадра в один
    // пересчёт слота.
    var rectCache = new Map();
    function cachedRect(el) {
        var rect = rectCache.get(el);
        if (!rect) {
            rect = el.getBoundingClientRect();
            rectCache.set(el, rect);
        }
        return rect;
    }
    function invalidateRects() { rectCache.clear(); }
    // Сброс всегда полный, а не по одному элементу: плитки .kb-card закрыты
    // свойством content-visibility: auto, и для внеполосных getBoundingClientRect()
    // отдаёт оценку из contain-intrinsic-size — она «скачком» становится
    // настоящей, когда плитка раскрывается при прокрутке.
    // Плюс rect'ы относительны вьюпорта: браузер сам докручивает страницу у
    // края окна, а resize меняет геометрию колонок. Capture-слушатели —
    // страховка; вне перетаскивания чистить пустой Map ничего не стоит.
    window.addEventListener('scroll', invalidateRects, true);
    window.addEventListener('resize', invalidateRects);
    // Пункт 16: скролл доски/очереди/списка подкидывает дозагрузку карточек
    // (фаза 2 загрузчика) — для медленной сети, когда автозапуск ещё не всё
    // добрал. Троттлинг 300 мс: scroll-событий десятки в секунду, а кик
    // идемпотентен и защищён внутри загрузчика. На предпросмотре мокапа
    // KBData.ensureCardsLoaded отсутствует — молчим.
    var cardsKickAt = 0;
    function kickCardsLoader() {
        var now = Date.now();
        if (now - cardsKickAt < 300) return;
        cardsKickAt = now;
        if (window.KBData && typeof KBData.ensureCardsLoaded === 'function') KBData.ensureCardsLoaded();
    }
    window.addEventListener('scroll', kickCardsLoader, true);
    // Флаг занятого слота: повторные requestDrop() в том же кадре игнорируются.
    var dropFramePending = false;
    function requestDrop() {
        if (dropFramePending) return;
        dropFramePending = true;
        requestAnimationFrame(function() {
            dropFramePending = false;
            if (drag) placeDrop(drag.x, drag.y);
        });
    }
    function cleanupDrag() {
        cancelAnimationFrame(dragFrame);
        dragFrame = 0;
        if (!drag) return;
        drag.source.classList.remove('kb-drag-source');
        drag.placeholder.remove();
        boardEl.querySelectorAll('.kb-drop-column').forEach(function(col) { col.classList.remove('kb-drop-column'); });
        boardEl.classList.remove('kb-dragging');
        drag = null;
        // Плейсхолдер убран, плитки вернулись на места — кэш геометрии устарел.
        invalidateRects();
        setTimeout(function() { suppressCardClick = false; }, 0);
    }
    function placeDrop(x, y) {
        if (!drag) return;
        var hit = document.elementFromPoint(x, y);
        var col = hit && hit.closest('#kb-board .kb-col');
        // Подсветку колонки пишем только когда она реально сменилась: класс
        // .kb-drop-column меняет лишь border-color, но проход querySelectorAll
        // с toggle по всем колонкам на каждое событие dragover — лишний.
        if (drag.markedColumn !== (col || null)) {
            if (drag.markedColumn) drag.markedColumn.classList.remove('kb-drop-column');
            if (col) col.classList.add('kb-drop-column');
            drag.markedColumn = col || null;
        }
        if (!col) {
            drag.column = null;
            // Уход курсора с доски возвращает плитки на места — кэш устарел.
            if (drag.placeholder.parentNode) { drag.placeholder.remove(); invalidateRects(); }
            return;
        }
        drag.column = col;
        var list = col.querySelector('.kb-cards');
        var slotRect = cachedRect(drag.placeholder);
        if (drag.placeholder.parentNode === list && y >= slotRect.top && y <= slotRect.bottom) return;
        var neighbours = Array.from(list.querySelectorAll('.kb-card')).filter(function(el) { return el !== drag.source; });
        var before = neighbours.find(function(el) {
            var rect = cachedRect(el);
            return y < rect.top + rect.height / 2;
        });
        // Avoid replacing the slot on every native dragover event.
        if (drag.placeholder.parentNode !== list || drag.placeholder.nextElementSibling !== (before || null)) {
            list.insertBefore(drag.placeholder, before || null);
            // Плейсхолдер — элемент с border и своей высотой: его перестановка
            // двигает плитки колонки, значит кэш геометрии больше не верен.
            invalidateRects();
        }
        drag.beforeId = before ? before.dataset.card : null;
    }
    function edgeSpeed(point, start, end) {
        var edge = Math.min(48, (end - start) / 3);
        if (point < start + edge) return -Math.ceil(12 * Math.max(0, 1 - (point - start) / edge));
        if (point > end - edge) return Math.ceil(12 * Math.max(0, 1 - (end - point) / edge));
        return 0;
    }
    function scrollDrag() {
        if (!drag) return;
        if (drag.column) {
            var list = drag.column.querySelector('.kb-cards');
            var rect = cachedRect(list);
            var dy = edgeSpeed(drag.y, rect.top, rect.bottom);
            var boardRect = cachedRect(boardEl);
            var dx = edgeSpeed(drag.x, boardRect.left, boardRect.right);
            // Раньше петля звала placeDrop() безусловно — вместе с обработчиком
            // dragover выходило два пересчёта слота на кадр. Пересчитываем,
            // только если автоскролл реально сдвинул содержимое: сдвиг меняет
            // геометрию, поэтому сначала сбрасываем кэш.
            if (dy || dx) {
                if (dy) list.scrollTop += dy;
                if (dx) boardEl.scrollLeft += dx;
                invalidateRects();
                requestDrop();
            }
        }
        dragFrame = requestAnimationFrame(scrollDrag);
    }
    boardEl.addEventListener('dragstart', function(e) {
        var source = e.target.closest('.kb-card');
        if (!source || !e.dataTransfer) return;
        cleanupDrag();
        // Старт с чистого кэша: предыдущее перетаскивание могло оставить rect
        // элементов, которые с тех пор переехали.
        invalidateRects();
        var placeholder = document.createElement('div');
        placeholder.className = 'kb-drop-placeholder';
        placeholder.setAttribute('aria-hidden', 'true');
        placeholder.style.height = source.offsetHeight + 'px';
        drag = { source: source, placeholder: placeholder, card: cards.find(function(c) { return c.id === source.dataset.card; }), column: null, markedColumn: null, x: e.clientX, y: e.clientY };
        suppressCardClick = true;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', source.dataset.card);
        boardEl.classList.add('kb-dragging');
        // Let the browser capture the card as the native drag image first.
        requestAnimationFrame(function() {
            if (!drag) return;
            source.classList.add('kb-drag-source');
            placeDrop(drag.x, drag.y);
            dragFrame = requestAnimationFrame(scrollDrag);
        });
    });
    document.addEventListener('dragover', function(e) {
        if (!drag) return;
        drag.x = e.clientX; drag.y = e.clientY;
        requestDrop();
        // Слот теперь пересчитывается в следующем кадре, то есть drag.column
        // отстаёт на кадр. От него зависит preventDefault() — без него браузер
        // не разрешит drop в этой колонке. Поэтому колонку для разрешения drop
        // определяем и по e.target: closest() — без чтения геометрии.
        var overCol = (e.target && e.target.closest && e.target.closest('#kb-board .kb-col')) || drag.column;
        if (overCol) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
    });
    boardEl.addEventListener('drop', function(e) {
        if (!drag) return;
        placeDrop(e.clientX, e.clientY);
        if (!drag.column) { cleanupDrag(); return; }
        e.preventDefault();
        var fromStage = drag.card.stage;
        var toStage = drag.column.dataset.stage;
        // Р4: колонка статуса, которого нет в справочнике карточек
        // (canAssign=false), не принимает карточек — сервер ответил бы 422.
        // Перестановка внутри такой колонки тоже запрещена: этап не меняется,
        // но карточке в ней жить нельзя.
        var targetStatus = KBData.statuses.filter(function (st) { return st.id === toStage; })[0];
        if (targetStatus && targetStatus.canAssign === false) {
            notify('Статус не из справочника карточек: сервер его не примет', true);
            cleanupDrag();
            return;
        }
        // id и имя статуса — до cleanupDrag(): он обнуляет drag.
        var cardId = Number(drag.card.id);
        var status = stageName(toStage);
        moveCard(drag.card, toStage, drag.beforeId);
        // Оптимистичный drop: карточка остаётся там, куда её бросили, шапки
        // колонок и очередь пересчитываются локально — полная перерисовка
        // доски (сотни карточек) не нужна. Отказ сервера откатит к истине.
        if (drag.placeholder.parentNode) drag.placeholder.parentNode.insertBefore(drag.source, drag.placeholder);
        var changedStage = fromStage !== toStage;
        cleanupDrag();
        if (changedStage) { updateColumnHead(fromStage); updateColumnHead(toStage); }
        renderQueue();
        // Перенос между колонками меняет сделку, но не состав групповых ТН —
        // kb:groups здесь был мёртвым грузом (перестройка строк групп в финансах).
        dispatchDocuments();
        apiMutate('status', { id: cardId, status: status }).then(function (ok) {
            if (ok === false) { refresh(); return; } // сервер отказал: тост уже показан boot-слоем
            // Успех раньше был молчаливым: оптимистичный переезд плитки выглядит
            // готовым фактом и не отличался от незаписанного этапа. Тост только
            // для переноса между колонками — перестановка внутри колонки этап не
            // меняет и объяснений не требует.
            if (changedStage) notify('Сделка ' + cardId + ' перенесена в «' + status + '»');
        });
    });
    document.addEventListener('dragend', cleanupDrag);
    // Файл, брошенный мимо зон прикрепления, не должен открываться браузером
    // вместо страницы. Перетаскивание плиток (drag != null) не трогаем —
    // у него свои обработчики.
    document.addEventListener('dragover', function (e) {
        if (drag) return;
        if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') !== -1) e.preventDefault();
    });
    document.addEventListener('drop', function (e) {
        if (!drag && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') !== -1) e.preventDefault();
        cleanupDrag();
    });
    document.addEventListener('keydown', function(e) { if (e.key === 'Escape') cleanupDrag(); });
    window.addEventListener('blur', cleanupDrag);

    function populateStores() {
        storeEl.innerHTML = optionsHTML([['all', 'Все магазины']].concat(KBData.stores.map(function (s) { return [s.id, s.name]; })), state.store);
        window.KBSelect.enhance(storeEl);
    }
    storeEl.addEventListener('change', function() {
        state.store = storeEl.value;
        writeBoardParams({ store: state.store === 'all' ? '' : state.store });
        markViewDirty();
        refresh({ view: true });
    });

    // ---------- Фильтры приоритета и типа оплаты (пункт 13) ----------
    // Опции строит JS по образцу populateStores; в разметке — placeholder-опция.
    var priorityEl = document.getElementById('kb-priority-select');
    var payEl = document.getElementById('kb-pay-select');
    var PAY_OPTIONS = [['all', 'Вся оплата'], ['bn', 'Безнал (БН)'], ['cash', 'Наличные']];
    function enhanceSelect(el) {
        if (window.KBSelect && window.KBSelect.enhance) window.KBSelect.enhance(el);
    }
    function priorityOptions() {
        return [['all', 'Все приоритеты']].concat(NC_PRIORITY.map(function (p) { return [String(p[0]), p[1]]; }));
    }
    function priorityLabel(value) {
        var row = NC_PRIORITY.filter(function (p) { return String(p[0]) === String(value); })[0];
        return row ? row[1] : String(value);
    }
    function payLabel(pay) { return pay === 'bn' ? 'Безнал (БН)' : 'Наличные'; }
    function populateFilterSelects() {
        if (priorityEl) { priorityEl.innerHTML = optionsHTML(priorityOptions(), state.priority); enhanceSelect(priorityEl); }
        if (payEl) { payEl.innerHTML = optionsHTML(PAY_OPTIONS, state.pay); enhanceSelect(payEl); }
    }
    if (priorityEl) priorityEl.addEventListener('change', function () {
        state.priority = priorityEl.value;
        writeBoardParams({ priority: state.priority === 'all' ? '' : state.priority });
        markViewDirty();
        refresh({ view: true });
    });
    if (payEl) payEl.addEventListener('change', function () {
        state.pay = payEl.value;
        writeBoardParams({ pay: state.pay === 'all' ? '' : state.pay });
        markViewDirty();
        refresh({ view: true });
    });

    // ---------- Сохранённые виды доски (состав — пункт 15, расширение пункта 13) ----------
    // Хранятся в localStorage с ключом на пользователя; на file:// без хранилища
    // — тихая деградация: чтение даёт [], запись false, ничего не падает.
    var VIEW_NONE = '__none';
    var VIEW_MODES = ['board', 'list', 'archive'];
    var viewSelectEl = document.getElementById('kb-view-select');
    var viewSaveBtn = document.getElementById('kb-view-save');
    function svKey() {
        return 'v2.savedViews.board.' + ((window.V2Api && window.V2Api.userId && window.V2Api.userId()) || 'anon');
    }
    function svRead() {
        try {
            var list = JSON.parse(localStorage.getItem(svKey()) || '[]');
            return Array.isArray(list) ? list : [];
        } catch (e) { return []; }
    }
    function svWrite(list) {
        try { localStorage.setItem(svKey(), JSON.stringify(list)); return true; } catch (e) { return false; }
    }
    // Любой ручной фильтр-доступ (поиск, магазин, приоритет, оплата, сброс,
    // смена режима) делает применённый вид неактивным.
    function markViewDirty() {
        state.viewId = '';
        if (viewSelectEl && viewSelectEl.value !== VIEW_NONE) {
            viewSelectEl.value = VIEW_NONE;
            enhanceSelect(viewSelectEl);
        }
    }
    function populateViewSelect() {
        if (!viewSelectEl) return;
        var views = svRead();
        // Выбранный вид мог быть удалён — тихо считаем вид неактивным.
        if (state.viewId && !views.some(function (v) { return v.id === state.viewId; })) state.viewId = '';
        viewSelectEl.innerHTML = optionsHTML([[VIEW_NONE, 'Вид: —']].concat(views.map(function (v) { return [v.id, v.name]; })), state.viewId || VIEW_NONE);
        enhanceSelect(viewSelectEl);
    }
    // Сохранение при существующем имени перезаписывает тот вид (upsert).
    function savedViewFromState(name) {
        var list = svRead();
        var existing = list.filter(function (v) { return v.name === name; })[0] || null;
        var view = {
            id: existing ? existing.id : 'sv-' + Date.now().toString(36),
            name: name,
            q: state.search,
            store: state.store,
            statuses: [],   // задел пункта 15: UI к ним в этом пункте нет
            assignee: null,
            mode: VIEW_MODES.indexOf(state.queueMode) >= 0 ? state.queueMode : 'board',
            priority: state.priority,
            pay: state.pay
        };
        var next = existing ? list.map(function (v) { return v.id === existing.id ? view : v; }) : list.concat([view]);
        return svWrite(next) ? view : null;
    }
    function viewSummary(v) {
        var parts = [];
        if (v.q) parts.push('поиск «' + v.q + '»');
        if (v.store && v.store !== 'all') parts.push('магазин «' + KBData.storeName(v.store) + '»');
        if (v.priority && v.priority !== 'all') parts.push('приоритет «' + priorityLabel(v.priority) + '»');
        if (v.pay && v.pay !== 'all') parts.push('оплата «' + payLabel(v.pay) + '»');
        parts.push({ board: 'Доска', list: 'Список', archive: 'Архив' }[v.mode] || 'Доска');
        return parts.join(', ');
    }
    function applySavedView(view) {
        // Неизвестные значения (магазин исчез из справочника, битые
        // priority/pay/mode) применяются тихо по умолчанию; statuses/assignee
        // из пункта 15 здесь игнорируются — UI к ним в этом пункте нет.
        state.search = String(view.q || '');
        searchEl.value = state.search;
        setSearchPanel(state.search !== '');
        syncSearchClear();
        state.store = (view.store && view.store !== 'all' && KBData.stores.some(function (s) { return s.id === view.store; })) ? view.store : 'all';
        state.priority = /^[0-3]$/.test(String(view.priority)) ? String(view.priority) : 'all';
        state.pay = (view.pay === 'bn' || view.pay === 'cash') ? view.pay : 'all';
        state.listPage = 1;
        state.viewId = view.id;
        populateStores();
        populateFilterSelects();
        // queueMode сам пишет mode в адрес, фильтры — следом отдельным патчем.
        queueMode(VIEW_MODES.indexOf(view.mode) >= 0 ? view.mode : 'board', true);
        writeBoardParams({
            q: state.search,
            store: state.store === 'all' ? '' : state.store,
            priority: state.priority === 'all' ? '' : state.priority,
            pay: state.pay === 'all' ? '' : state.pay
        });
        refresh({ view: true });
        document.dispatchEvent(new CustomEvent('kb:saved-view-applied', { detail: { id: view.id, name: view.name } }));
    }
    var viewsDialog = null;
    var viewsOpener = null;
    function ensureViewsDialog() {
        if (viewsDialog) return viewsDialog;
        viewsDialog = document.createElement('dialog');
        viewsDialog.id = 'kb-views-dlg';
        viewsDialog.className = 'v2-form-dialog';
        viewsDialog.setAttribute('aria-labelledby', 'kb-views-heading');
        viewsDialog.addEventListener('close', function () {
            if (viewsOpener && viewsOpener.isConnected) viewsOpener.focus();
            viewsOpener = null;
        });
        // Делегирование удаления — один раз на диалог: содержимое (и его
        // слушатели) пересобирается при каждом открытии, слушатель диалога — нет.
        viewsDialog.addEventListener('click', function (e) {
            var del = e.target.closest('[data-view-del]');
            if (!del) return;
            svWrite(svRead().filter(function (v) { return v.id !== del.dataset.viewDel; }));
            if (state.viewId === del.dataset.viewDel) markViewDirty();
            populateViewSelect();
            openViewsDialog(viewsOpener);
        });
        document.body.appendChild(viewsDialog);
        return viewsDialog;
    }
    function openViewsDialog(opener) {
        var dlg = ensureViewsDialog();
        viewsOpener = opener || null;
        var views = svRead();
        dlg.innerHTML = '<form id="kb-views-form"><h2 id="kb-views-heading">Сохранённые виды</h2>' +
            '<div class="mgmt-fields">' +
            '<label for="kb-view-name" class="mgmt-span"><span>Название вида</span>' +
            '<input id="kb-view-name" type="text" maxlength="40" autocomplete="off"></label>' +
            '</div>' +
            '<p id="kb-views-error" class="kb-form-error" role="alert" hidden></p>' +
            '<div class="mgmt-actions">' +
            '<button type="button" class="btn btn-ghost" data-views-cancel>Отмена</button>' +
            '<button type="submit" class="btn btn-primary">Сохранить текущий вид</button>' +
            '</div>' +
            '<div class="kb-views-list">' +
            (views.length ? views.map(function (v) {
                return '<div class="kb-views-row">' +
                    '<div class="kb-views-info"><b>' + esc(v.name) + '</b>' +
                    '<span class="kb-views-sum">' + esc(viewSummary(v)) + '</span></div>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-view-del="' + esc(v.id) + '">Удалить</button>' +
                    '</div>';
            }).join('') : '<p class="kb-note">Пока нет сохранённых видов.</p>') +
            '</div></form>';
        dlg.querySelector('[data-views-cancel]').addEventListener('click', function () { dlg.close(); });
        dlg.querySelector('#kb-views-form').addEventListener('submit', function (e) {
            e.preventDefault();
            var input = dlg.querySelector('#kb-view-name');
            var name = input.value.trim().slice(0, 40);
            if (!name) {
                var errorEl = dlg.querySelector('#kb-views-error');
                errorEl.textContent = 'Введите название вида.';
                errorEl.hidden = false;
                input.focus();
                return;
            }
            var view = savedViewFromState(name);
            if (!view) {
                // Хранилище недоступно (file://) — остаёмся в диалоге с причиной.
                var failEl = dlg.querySelector('#kb-views-error');
                failEl.textContent = 'Хранилище браузера недоступно — вид не сохранён.';
                failEl.hidden = false;
                return;
            }
            state.viewId = view.id;
            populateViewSelect();
            dlg.close();
        });
        if (!dlg.open) dlg.showModal();
        dlg.querySelector('#kb-view-name').focus();
    }
    if (viewSelectEl) viewSelectEl.addEventListener('change', function () {
        if (viewSelectEl.value === VIEW_NONE) { markViewDirty(); return; }
        var view = svRead().filter(function (v) { return v.id === viewSelectEl.value; })[0];
        if (view) applySavedView(view); else markViewDirty();
    });
    if (viewSaveBtn) viewSaveBtn.addEventListener('click', function () { openViewsDialog(viewSaveBtn); });
    // Debounce: полная перерисовка доски (сотни карточек) не должна бежать
    // на каждый символ — ждём паузу в вводе 150 мс.
    var searchTimer = 0;
    var searchWrap = document.getElementById('kb-search-wrap');
    var searchToggle = document.getElementById('kb-search-toggle');
    var searchClear = document.getElementById('kb-search-close');
    var noResultsEl = document.getElementById('kb-no-results');
    var noResultsNote = document.getElementById('kb-no-results-note');
    var searchOpener = null;

    // ---------- Поиск: одно поле, один крестик, один способ закрыть ----------
    // Пункт 7 V2-WORKPLAN. Крестик в открытом поле один и он же сброс: пока в
    // поле есть текст — чистит текст (поле остаётся открытым), когда текст уже
    // пуст — закрывает поиск. Esc закрывает всегда и, как описано в
    // mockups/README.md, заодно сбрасывает запрос. Подпись кнопки меняется
    // вместе с содержимым поля, поэтому ни одно нажатие не «вслепую».
    function searchIsOpen() { return searchWrap.classList.contains('is-open'); }
    function syncSearchClear() {
        var hasText = searchEl.value.length > 0;
        var label = hasText ? 'Очистить поиск' : 'Закрыть поиск';
        searchClear.setAttribute('aria-label', label);
        searchClear.title = label;
    }
    function setSearchPanel(open) {
        searchWrap.classList.toggle('is-open', open);
        searchEl.inert = !open;
        searchToggle.setAttribute('aria-expanded', String(open));
        searchToggle.setAttribute('aria-label', open ? 'Закрыть поиск' : 'Открыть поиск');
        syncSearchClear();
    }
    function openSearch(event) {
        var from = document.activeElement;
        searchOpener = from && from !== document.body ? from : searchToggle;
        if (!searchIsOpen()) setSearchPanel(true);
        searchEl.classList.toggle('is-pointer-focus', !!event && event.detail !== 0);
        searchEl.focus();
    }
    function clearSearchQuery() {
        clearTimeout(searchTimer);
        searchEl.value = '';
        state.search = '';
        writeBoardParams({ q: '' });
        syncSearchClear();
        markViewDirty();
        refresh({ view: true });
    }
    function closeSearch() {
        if (!searchIsOpen()) return;
        var hadQuery = state.search !== '';
        setSearchPanel(false);
        // Перерисовка только если запрос действительно что-то сужал: закрыть
        // пустое поле — не повод пересобирать сотни плиток.
        if (hadQuery) clearSearchQuery(); else searchEl.value = '';
        // Фокус — туда, откуда открывали (лупа к этому моменту снова видна),
        // а не в body и не на скрытое поле.
        var back = searchOpener && searchOpener.isConnected ? searchOpener : searchToggle;
        if (back !== document.activeElement) back.focus();
        searchOpener = null;
    }
    searchToggle.onclick = openSearch;
    searchClear.onclick = function (event) {
        if (searchEl.value.length > 0) {
            clearSearchQuery();
            searchEl.classList.toggle('is-pointer-focus', event.detail !== 0);
            searchEl.focus();
            return;
        }
        closeSearch();
    };
    searchEl.addEventListener('focusout', function () {
        searchEl.classList.remove('is-pointer-focus');
    });
    // Esc закрывает РОВНО один слой — верхний. Пока открыт модальный <dialog>
    // (дровер сделки, подтверждение KBConfirm, форма групповой ТН), остальная
    // страница нативно inert: фокус не может быть в поле поиска, а клавиша
    // принадлежит диалогу. Страховка здесь — на случай не-модального show().
    function onSearchEscape(e) {
        if (e.key !== 'Escape' || document.querySelector('dialog[open]')) return;
        e.preventDefault();
        e.stopPropagation();
        closeSearch();
    }
    // Слушатель на обоих контролах открытого поиска: Esc работает и когда
    // фокус уже перешёл на крестик (после клика по нему).
    searchEl.addEventListener('keydown', onSearchEscape);
    searchClear.addEventListener('keydown', onSearchEscape);
    searchEl.addEventListener('input', function () {
        state.search = searchEl.value;
        writeBoardParams({ q: state.search });
        syncSearchClear();
        markViewDirty();
        clearTimeout(searchTimer);
        // Явная обёртка: setTimeout не передаёт аргументы, а ссылку на
        // refresh(opts) вообще нельзя отдавать колбэкам событий — у Event есть
        // собственное свойство view (Window), оно попало бы в opts.view.
        searchTimer = setTimeout(function () { refresh({ view: true }); }, 150);
    });

    // ---------- Что сужает выдачу и как это снять ----------
    // Пункт 13 V2-WORKPLAN («Список»/«Архив», фильтры по приоритету и типу
    // оплаты, «только мои», кнопка «Сбросить» в тулбаре) дорастает сюда: и
    // подсказка о пустом результате, и сброс берут список причин из одного
    // места, а не читают state напрямую.
    function filterReasons() {
        var out = [];
        var q = state.search.trim();
        if (q) out.push('поиск «' + q + '»');
        if (state.store !== 'all') out.push('магазин «' + KBData.storeName(state.store) + '»');
        if (state.priority !== 'all') out.push('приоритет «' + priorityLabel(state.priority) + '»');
        if (state.pay !== 'all') out.push('оплата «' + payLabel(state.pay) + '»');
        return out;
    }
    function filtersActive() { return filterReasons().length > 0; }
    function resetBoardFilters() {
        clearTimeout(searchTimer);
        searchEl.value = '';
        state.search = '';
        storeEl.value = 'all';
        state.store = 'all';
        state.priority = 'all';
        state.pay = 'all';
        state.listPage = 1;
        if (priorityEl) priorityEl.value = 'all';
        if (payEl) payEl.value = 'all';
        writeBoardParams({ q: '', store: '', priority: '', pay: '' });
        // Магазин — не голый select: KBSelect показывает свою подпись и её
        // надо пересинхронизировать после программного значения.
        if (window.KBSelect && window.KBSelect.enhance) {
            window.KBSelect.enhance(storeEl);
            if (priorityEl) window.KBSelect.enhance(priorityEl);
            if (payEl) window.KBSelect.enhance(payEl);
        }
        markViewDirty();
        syncSearchClear();
        refresh({ view: true });
        // Кнопка сброса жила внутри подсказки и удалена перерисовкой:
        // возвращаем фокус в ту же панель инструментов, а не в body.
        (searchIsOpen() ? searchEl : searchToggle).focus();
    }
    // Одна общая подсказка вместо колонок по «Пусто», когда пуст результат
    // фильтра. Считается по тому виду, который сейчас на экране: доска,
    // очередь выписки или «Списано» (корзина под поиск не фильтруется).
    function syncNoResults() {
        if (!noResultsEl) return;
        var reasons = filterReasons();
        var empty;
        if (state.queueMode === 'board') empty = !visibleCards().length;
        else if (state.queueMode === 'trash') empty = false;
        else if (state.queueMode === 'list' || state.queueMode === 'archive') empty = !listCards().length;
        else empty = !queueCards().length;
        var show = reasons.length > 0 && empty;
        noResultsEl.hidden = !show;
        if (show) {
            noResultsNote.textContent = 'Активные фильтры: ' + reasons.join(', ') +
                '. Измените их или сбросьте — вернутся все сделки.';
        }
    }
    var resetFiltersBtn = document.getElementById('kb-reset-filters');
    if (resetFiltersBtn) resetFiltersBtn.onclick = resetBoardFilters;
    var resetToolbarBtn = document.getElementById('kb-reset');
    if (resetToolbarBtn) resetToolbarBtn.onclick = resetBoardFilters;
    document.getElementById('kb-density').onclick = function() { setDensity(!state.compact); };
    function setDensity(compact) {
        state.compact = compact;
        rootEl.classList.toggle('kb-compact', compact);
        rootEl.classList.toggle('kb-normal', !compact);
        document.getElementById('kb-density').setAttribute('aria-pressed', String(compact));
    }

    var filtersPanel = document.getElementById('kb-filters-panel');
    var filtersToggle = document.getElementById('kb-filters-toggle');
    var filtersCount = document.getElementById('kb-filters-count');
    function syncFilterToggle() {
        if (!filtersToggle || !filtersCount) return;
        var count = [state.store, state.priority, state.pay].filter(function (value) { return value && value !== 'all'; }).length;
        filtersCount.textContent = String(count);
        filtersCount.hidden = count === 0;
        filtersToggle.setAttribute('aria-label', count
            ? 'Фильтры: ' + count + ' ' + pluralRu(count, ['активный', 'активных', 'активных'])
            : 'Фильтры');
    }
    function setFiltersPanel(open) {
        if (!filtersPanel || !filtersToggle) return;
        if (!open && window.KBSelect) window.KBSelect.close(false);
        filtersPanel.hidden = !open;
        filtersToggle.setAttribute('aria-expanded', String(open));
    }
    if (filtersToggle) filtersToggle.onclick = function () {
        setFiltersPanel(filtersPanel.hidden);
    };

    var dialog = document.getElementById('kb-dialog');
    var activeCard = null;
    // Закрытие дровера с коротким выходом: класс .closing запускает CSS-анимацию
    // kb-detail-exit (120ms), по её концу — dialog.close(). Страховка по таймеру:
    // если animationend не придёт (prefers-reduced-motion, прерванный кадр) —
    // закрываем всё равно.
    function closeDialog() {
        if (!dialog.open || dialog.classList.contains('closing')) return;
        var finished = false;
        var finish = function () {
            if (finished) return;
            finished = true;
            dialog.removeEventListener('animationend', onAnimEnd);
            dialog.classList.remove('closing');
            if (dialog.open) dialog.close();
        };
        var onAnimEnd = function (e) { if (e.animationName === 'kb-detail-exit') finish(); };
        dialog.addEventListener('animationend', onAnimEnd);
        dialog.classList.add('closing');
        setTimeout(finish, 200);
    }
    // Esc закрывает через тот же выход: гасим нативный cancel и идём сами.
    dialog.addEventListener('cancel', function (e) { e.preventDefault(); closeDialog(); });
    // Рассылка данных соседним разделам. События дорогие: kb:documents тянет
    // renderDay() + renderCalendar() (insights.js) и перестройку строк ТН
    // (финансовый блок прототипа), kb:groups — перестройку строк групп.
    // Шлём только когда соответствующие данные действительно изменились.
    function dispatchDocuments() {
        document.dispatchEvent(new CustomEvent('kb:documents', { detail: cards }));
    }
    function dispatchGroups() {
        document.dispatchEvent(new CustomEvent('kb:groups', { detail: KBData.groups }));
    }
    // opts.view — перерисовка только вида (фильтр, поиск, плотность): данные не
    // менялись, финансам и календарю события не нужны.
    function refresh(opts) {
        var viewOnly = !!(opts && opts.view === true);
        syncFilterToggle();
        var scroll = Array.from(boardEl.querySelectorAll('.kb-cards')).map(function(el) { return el.scrollTop; });
        renderBoard();
        // Одна точка диспетчеризации: в табличных видах renderQueue со своими
        // groupPick-чистками не выполняется (скролл колонок доски не трогаем).
        if (state.queueMode === 'list' || state.queueMode === 'archive') renderList();
        else renderQueue();
        boardEl.querySelectorAll('.kb-cards').forEach(function(el, i) { el.scrollTop = scroll[i] || 0; });
        if (viewOnly) return;
        dispatchDocuments();
        dispatchGroups();
    }
    // Перечитать данные с сервера без F5. Если загрузчика нет (предпросмотр
    // мокапа на shell-v2-data.js) или он не зарегистрирован — хотя бы
    // перерисовываем доску. Ошибку загрузки не глотает: её показывает
    // вызывающий код.
    function reloadData() {
        if (!window.KBData || !window.KBData.reload) { refresh(); return Promise.resolve(null); }
        return window.KBData.reload().then(function (data) {
            if (!data) refresh();
            return data;
        });
    }
    // Добавлена следующая порция карточек: один общий refresh для доски и
    // табличных видов. Открытый дровер не трогаем — обычная догрузка не должна
    // перерисовывать карточку, на которой пользователь работает.
    document.addEventListener('kb:cards-appended', function () { refresh(); });
    // Данные перечитаны с сервера без F5 (boot: KBData.reload → KBApi.all()
    // обновляет коллекции на месте, поэтому массив cards остаётся тем же).
    // Открытый дровер переводим на свежий объект карточки: прежний указывает
    // на данные до перезагрузки. Карточки больше нет (удалена) — закрываем.
    document.addEventListener('kb:reloaded', function () {
        if (dialog.open && activeCard) {
            var fresh = cards.find(function (c) { return c.id === activeCard.id; });
            if (fresh) openCard(fresh); else closeDialog();
        }
        populateStores();
        refresh();
    });
    document.addEventListener('fin:originals', function(e) {
        var c = cards.find(function(card) { return card.id === e.detail.cardId; });
        if (!c || !c.docs[e.detail.index]) return;
        c.docs[e.detail.index][e.detail.field] = e.detail.checked;
    });
    // Групповая выписка: выбранные в очереди карточки закрываются ОДНОЙ накладной
    // (аналог «групп списаний» на проде). Отмена возвращает остаток каждой.
    var groupPick = [];
    var editingDoc = null; // индекс накладной в открытой карточке, которую правят
    // Комбобокс с поиском (фидбек 18.09 «квадратное и круглое, белый
    // шрифт»): нативный datalist выглядел чужеродно. Список стилизован
    // под v2, фильтруется по вводу.
    var comboSequence = 0;
    function initCombo(input, options) {
        if (!input) return;
        var wrap = document.createElement('div');
        wrap.className = 'kb-combo';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        var list = document.createElement('div');
        var listId = 'kb-combo-list-' + (++comboSequence);
        list.id = listId;
        list.className = 'kb-combo-list';
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        wrap.appendChild(list);
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', listId);
        var items = [];
        var optionEls = [];
        var activeIndex = -1;
        function setActive(index) {
            activeIndex = index;
            optionEls.forEach(function (el, i) {
                var active = i === activeIndex;
                el.classList.toggle('is-active', active);
                el.setAttribute('aria-selected', String(active));
            });
            if (activeIndex < 0 || !optionEls[activeIndex]) {
                input.removeAttribute('aria-activedescendant');
            } else {
                input.setAttribute('aria-activedescendant', optionEls[activeIndex].id);
            }
        }
        function setListOpen(open) {
            var isOpen = Boolean(open && items.length);
            list.hidden = !isOpen;
            input.setAttribute('aria-expanded', String(isOpen));
            if (!isOpen) setActive(-1);
        }
        function selectOption(index) {
            if (index < 0 || index >= items.length) return;
            input.value = items[index];
            setListOpen(false);
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        function renderList() {
            var q = input.value.trim().toLowerCase();
            items = options.filter(function (o) { return !q || o.toLowerCase().indexOf(q) !== -1; }).slice(0, 12);
            list.innerHTML = items.map(function (o, i) {
                return '<div class="kb-combo-item" role="option" id="' + listId + '-option-' + i + '" aria-selected="false">' + esc(o) + '</div>';
            }).join('');
            optionEls = Array.prototype.slice.call(list.querySelectorAll('.kb-combo-item'));
            setActive(-1);
            setListOpen(items.length > 0);
            optionEls.forEach(function (el, i) {
                el.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    selectOption(i);
                });
            });
        }
        input.addEventListener('focus', renderList);
        input.addEventListener('input', renderList);
        input.addEventListener('blur', function () { setTimeout(function () { setListOpen(false); }, 150); });
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                if (!optionEls.length) return;
                e.preventDefault();
                var next = activeIndex < 0
                    ? (e.key === 'ArrowDown' ? 0 : optionEls.length - 1)
                    : (activeIndex + (e.key === 'ArrowDown' ? 1 : -1) + optionEls.length) % optionEls.length;
                setActive(next);
                setListOpen(true);
            } else if (e.key === 'Home' || e.key === 'End') {
                if (!optionEls.length) return;
                e.preventDefault();
                setActive(e.key === 'Home' ? 0 : optionEls.length - 1);
                setListOpen(true);
            } else if (e.key === 'Enter') {
                if (activeIndex >= 0) {
                    e.preventDefault();
                    selectOption(activeIndex);
                }
            } else if (e.key === 'Escape') {
                e.preventDefault();
                setListOpen(false);
            }
        });
    }
    function initCardCombos() {
        if (!dialog) return;
        // Item #5: клиент — только из справочника; пустое = не указан.
        initCombo(dialog.querySelector('#kb-deal-client'),
            KBData.clients.map(function (cl) { return cl.name; }).filter(Boolean));
        initCombo(document.getElementById('kb-add-supplier'),
            (KBData.suppliers || []).map(function (s) { return s.name; }));
        dialog.querySelectorAll('.kb-sup-combo').forEach(function (input) {
            initCombo(input, (KBData.suppliers || []).map(function (s) { return s.name; }));
        });
    }
    // Корзина (фидбек 18.09): удалённые карточки — восстановление и
    // удаление навсегда (последнее — только администратор на бэкенде).
    var trashCache = [];
    function renderTrash() {
        var body = document.getElementById('kb-queue-body');
        if (!body) return;
        if (!window.V2Api || !window.V2Api.token()) {
            body.innerHTML = '<p class="kb-note">Корзина доступна при подключении к CRM.</p>';
            return;
        }
        body.innerHTML = '<p class="kb-note">Загрузка корзины…</p>';
        window.V2Api.api('/kanban/trash').then(function (list) {
            trashCache = list || [];
            body.innerHTML = '<p class="kb-note">В корзине: ' + trashCache.length + '</p>' + (trashCache.length ? trashCache.map(function (c) {
                return '<div class="kb-q-row"><div><b>' +
                    esc(c.id + ' · ' + (c.title || '')) +
                    '</b><div>' + esc(c.store_location || '') + '</div></div>' +
                    '<div class="kb-q-actions">' +
                    // «Навсегда» — DELETE /kanban/cards/{id}/permanent, закрыт
                    // require_role admin/superadmin (kanban_router.py:242).
                    // «Восстановить» не помечено: PATCH /cards/{id}/restore
                    // (:221) доступен любой аутентифицированной роли.
                    '<button type="button" class="btn btn-ghost btn-sm" data-trash-restore="' + c.id + '">Восстановить</button>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-trash-purge="' + c.id + '" data-admin-only>Удалить навсегда</button></div></div>';
            }).join('') : '<p class="kb-note">Корзина пуста.</p>');
            applyRoleMarks(body);
        }).catch(function () { body.innerHTML = '<p class="kb-note">Не удалось загрузить корзину.</p>'; });
    }
    // Состав очереди — одна выборка и для строк, и для подсказки о пустом
    // результате: «Найдено: N» и «Ничего не найдено» не должны расходиться.
    // Очередь — по остатку к выписке (как на проде): полностью выписанная
    // карточка не висит в «На списание», даже если статус ещё там же,
    // а в «Списано» попадает и без смены статуса.
    function queueCards() {
        return visibleCards().filter(function (c) {
            if (state.queueMode === 'pending') return c.stage === 'writeoff' && hasRemaining(c);
            // 'closed' (статус «Закрыто») остаётся в «Списано»: раньше закрытые
            // и полностью выписанные делили один id 'done' (Р2 пункта 12).
            return c.stage === 'done' || c.stage === 'closed' ||
                (c.stage === 'writeoff' && cardIssued(c) > 0 && !hasRemaining(c));
        });
    }
    function renderQueue() {
        if (state.queueMode === 'trash') { renderTrash(); syncNoResults(); return; }
        var list = queueCards();
        document.getElementById('kb-queue-count').textContent = cards.filter(function(c) { return c.stage === 'writeoff' && hasRemaining(c); }).length;
        groupPick = groupPick.filter(function (id) {
            var c = cards.find(function (x) { return x.id === id; });
            return c && c.stage === 'writeoff' && hasRemaining(c);
        });
        var pending = state.queueMode === 'pending';
        var allPicked = pending && list.length > 0 && list.every(function (c) { return groupPick.indexOf(c.id) >= 0; });
        var toolbar = pending ?
            '<div class="kb-q-toolbar"><label class="kb-q-check"><input type="checkbox" id="kb-group-all"' + (allPicked ? ' checked' : '') + (list.length ? '' : ' disabled') + '> Выбрать все</label>' +
            '<div class="row">' +
            '<button type="button" class="btn btn-primary btn-sm" id="kb-group-issue"' + (groupPick.length > 1 ? '' : ' disabled') + '>Накладная на группу' + (groupPick.length > 1 ? ' (' + groupPick.length + ')' : '') + '</button>' +
            // Выбрать можно и одну сделку — «убрать из списания» работает и
            // поштучно, и списком. Кнопка с меткой data-admin-only: роут
            // DELETE /payments/cards/{id}/writeoff закрыт ролью admin
            // (payments_router.py:647-648), под другой ролью она отвечала бы
            // 403 на каждый клик.
            '<button type="button" class="btn btn-ghost btn-sm" id="kb-q-unwriteoff"' + (groupPick.length ? '' : ' disabled') + ' data-admin-only>Убрать из списания' + (groupPick.length ? ' (' + groupPick.length + ')' : '') + '</button>' +
            '</div></div>' : '';
        var queueBody = document.getElementById('kb-queue-body');
        // Предпросмотр мокапа (file://, ?demo=1) сервера не имеет — действия
        // очереди там не строим вовсе, а не показываем мёртвые кнопки.
        var crm = Boolean(window.V2Api && window.V2Api.token());
        queueBody.innerHTML = '<p class="kb-note">Поиск и магазин общие с доской. Найдено: ' + list.length + '</p>' + toolbar + list.map(function(c) {
            return '<div class="kb-q-row" role="button" tabindex="0" data-card="' + esc(c.id) + '" aria-haspopup="dialog" aria-label="Открыть ' + esc(c.id + ' · ' + c.title) + '">' +
                (pending ? '<input type="checkbox" class="kb-q-pick" data-group-pick="' + esc(c.id) + '" aria-label="Выбрать ' + esc(c.id) + ' для действий очереди"' + (groupPick.indexOf(c.id) >= 0 ? ' checked' : '') + '>' : '') +
                '<div><b>' + esc(c.id + ' · ' + c.title) + '</b><div>' + esc(KBData.storeName(c.store)) + ' · К выписке ' + moneyOrDash(cardRemaining(c)) + ' BYN</div></div>' +
                // Поштучный выход из списания — в обоих списках очереди: в
                // «На списание» попадают сделки с остатком, а полностью
                // выписанная сделка живёт в «Списано» и по выборке из
                // «На списание» была бы недостижима (в legacy крестик стоял
                // на плитке обеих колонок).
                (crm ? '<div class="kb-q-actions"><button type="button" class="btn btn-ghost btn-sm" data-row-unwriteoff="' + esc(c.id) + '" data-admin-only>Убрать из списания</button></div>' : '') +
                '</div>';
        }).join('');
        // Тулбар пересобирается на каждую отрисовку очереди — метки
        // data-admin-only ставятся здесь же, иначе под не-админом остаётся
        // живая кнопка, отвечающая 403.
        applyRoleMarks(queueBody);
        syncNoResults();
    }
    // Режим рабочей области. Имя историческое (queueMode): теперь включает не
    // только очереди, но и табличные виды «Список»/«Архив» (пункт 13).
    function queueMode(mode, skipRender) {
        state.queueMode = mode;
        if (mode === 'list' || mode === 'archive') state.listPage = 1;
        writeBoardParams({ mode: mode === 'board' ? '' : mode });
        boardEl.hidden = mode !== 'board';
        document.getElementById('kb-queue').hidden = ['pending', 'history', 'trash'].indexOf(mode) < 0;
        document.getElementById('kb-list').hidden = ['list', 'archive'].indexOf(mode) < 0;
        document.getElementById('kb-q-board').setAttribute('aria-pressed', String(mode === 'board'));
        document.getElementById('kb-q-pending').setAttribute('aria-pressed', String(mode === 'pending'));
        document.getElementById('kb-q-history').setAttribute('aria-pressed', String(mode === 'history'));
        document.getElementById('kb-q-list').setAttribute('aria-pressed', String(mode === 'list'));
        document.getElementById('kb-q-archive').setAttribute('aria-pressed', String(mode === 'archive'));
        if (skipRender === true) return;
        if (mode === 'list' || mode === 'archive') renderList();
        else renderQueue();
    }
    // ---------- Табличные виды «Список» и «Архив» (пункт 13) ----------
    // Паритет renderListView() legacy (site/js/kanban.js:1058-1227): все
    // не-удалённые сделки; «Архив» — только stage === 'closed' (закрытые по
    // статусу сделки) как ЖИВОЙ фильтр над KBData.cards (отмена ТН возвращает
    // карточку из архива после reloadData), без снимка и кэша состава.
    var LIST_PAGE_SIZE = 25;
    var listBodyEl = document.getElementById('kb-list-body');
    // 'done' — псевдо-этап полной выписки (статус ещё «На списание», остаток
    // нулевой): в колонке «Статус» ему честно печатается «Списано».
    // 'closed' — статус сделки «Закрыто».
    var LIST_STAGE_FALLBACK = { 'new': 'Новый запрос', 'work': 'В работе', 'pay': 'Ждет оплаты', 'assembly': 'Сборка', 'writeoff': 'На списание', 'done': 'Списано', 'closed': 'Закрыто' };
    var LIST_HEAD = '<thead><tr><th scope="col">Сделка</th><th scope="col">Статус</th><th scope="col">Сумма</th>' +
        '<th scope="col" class="col-pay">Оплата</th><th scope="col" class="col-store">Магазин</th>' +
        '<th scope="col" class="col-manager">Менеджер</th><th scope="col">Дедлайн</th>' +
        '<th scope="col" class="col-priority">Приоритет</th></tr></thead>';
    // Единая выборка вида: и строки таблицы, и подсказка «Ничего не найдено»
    // считаются по ней (приём из queueCards — «Найдено: N» не расходится).
    function listCards() {
        var list = visibleCards();
        if (state.queueMode === 'archive') list = list.filter(function (c) { return c.stage === 'closed'; });
        return list;
    }
    // 'ДД.ММ.ГГГГ' → число вида ГГГГММДД для сравнения; пустая/битая — null.
    function ruDateKey(value) {
        var m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(String(value || '').trim());
        return m ? Number(m[3] + m[2] + m[1]) : null;
    }
    function todayKey() {
        // Как в boot: UTC-дата KBApi.todayUTC; в предпросмотре мокапа —
        // демо-дата KBData.today, без неё — локальные «сегодня».
        var iso = window.KBApi && window.KBApi.todayUTC ? window.KBApi.todayUTC() : '';
        var p = String(iso).split('-');
        if (p.length === 3) return Number(p[0] + p[1] + p[2]);
        var t = KBData.today || new Date();
        return t.getFullYear() * 10000 + (t.getMonth() + 1) * 100 + t.getDate();
    }
    function sortListCards(list) {
        var todayNum = todayKey();
        return list.map(function (c) {
            return { card: c, deadline: ruDateKey(c.deadline), created: ruDateKey(c.createdAt) };
        }).sort(function (a, b) {
            var aOver = a.deadline !== null && a.deadline < todayNum;
            var bOver = b.deadline !== null && b.deadline < todayNum;
            if (aOver !== bOver) return aOver ? -1 : 1; // просроченные наверх
            // Далее по дате создания — новые сверху; без даты — в конец.
            if (a.created !== b.created) return (b.created || 0) - (a.created || 0);
            return 0;
        }).map(function (row) { return row.card; });
    }
    function listStageName(c) {
        var st = KBData.statuses.filter(function (x) { return x.id === c.stage; })[0];
        return (st && st.name) || LIST_STAGE_FALLBACK[c.stage] || String(c.stage || '');
    }
    // Тот же вывод, что на плитке (payment_status или производный), текстом.
    function listPayText(c) {
        var fullyPaid = c.paidAmount >= c.amount && c.amount > 0;
        return c.payment_status || (fullyPaid ? 'Оплачен' : (c.paidAmount > 0 ? 'Частично' : 'Не оплачен'));
    }
    function listRowHTML(c, todayNum) {
        var deadlineKey = ruDateKey(c.deadline);
        var overdue = deadlineKey !== null && deadlineKey < todayNum;
        return '<tr>' +
            '<td><button type="button" class="kb-list-open" data-card="' + esc(c.id) + '">' + esc(c.id + ' · ' + c.title) + '</button></td>' +
            '<td>' + esc(listStageName(c)) + '</td>' +
            '<td class="num">' + esc(money(c.amount)) + ' BYN</td>' +
            '<td class="col-pay">' + esc(listPayText(c)) + '</td>' +
            '<td class="col-store">' + esc(KBData.storeName(c.store) || '—') + '</td>' +
            '<td class="col-manager"' + offUserAttrs(c.manager) + '>' + esc(userNameOff(c.manager)) + '</td>' +
            '<td class="num' + (overdue ? ' is-overdue' : '') + '">' + esc(c.deadline || '—') + '</td>' +
            '<td class="col-priority">' + esc(Number(c.priority || 0) ? priorityLabel(c.priority) : '—') + '</td>' +
            '</tr>';
    }
    function renderList() {
        if (!listBodyEl) return;
        // Сортировка и пагинация пересчитываются на каждый вызов: данные
        // (этапы, остатки, состав архива) могли измениться.
        var list = sortListCards(listCards());
        var total = list.length;
        var pages = Math.max(1, Math.ceil(total / LIST_PAGE_SIZE));
        if (state.listPage > pages) state.listPage = pages;
        if (state.listPage < 1) state.listPage = 1;
        var from = (state.listPage - 1) * LIST_PAGE_SIZE;
        var rows = list.slice(from, from + LIST_PAGE_SIZE);
        var todayNum = todayKey();
        var head = '<p class="kb-note">' +
            (state.queueMode === 'archive' ? 'Только закрытые сделки.' : 'Все сделки, кроме удалённых.') +
            ' Найдено: ' + total + '</p>';
        var table = total
            ? '<table class="kb-list-table">' + LIST_HEAD + '<tbody>' +
              rows.map(function (c) { return listRowHTML(c, todayNum); }).join('') +
              '</tbody></table>'
            : '<p class="kb-note">Сделок нет.</p>';
        var pager = '';
        if (total > LIST_PAGE_SIZE) {
            pager = '<div class="kb-list-pager">' +
                '<button type="button" class="btn btn-ghost btn-sm" data-list-page="prev"' + (state.listPage <= 1 ? ' disabled' : '') + '>‹ Назад</button>' +
                '<span class="kb-list-pageinfo num">Показано ' + (from + 1) + '–' + Math.min(from + LIST_PAGE_SIZE, total) + ' из ' + total + '</span>' +
                '<button type="button" class="btn btn-ghost btn-sm" data-list-page="next"' + (state.listPage >= pages ? ' disabled' : '') + '>Вперёд ›</button>' +
                '</div>';
        }
        listBodyEl.innerHTML = head + table + pager;
        syncNoResults();
    }
    if (listBodyEl) listBodyEl.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-list-page]');
        if (!btn || btn.disabled) return;
        state.listPage += btn.dataset.listPage === 'next' ? 1 : -1;
        renderList();
    });
    // Фидбек 20.09: тост больше не висит вечно — авто-закрытие через 6 с
    // (достаточно прочитать и успеть нажать «Списано»; вручную — крестик).
    // isError — стиль ошибки (класс toast-error, как в management.js/insights.js)
    // и без кнопки «Списано»: к отказу записи очередь выписки не относится.
    var toastTimer = 0;
    function notify(text, isError) {
        var toast = document.getElementById('kb-toast');
        toast.hidden = false;
        toast.classList.toggle('toast-error', Boolean(isError));
        toast.innerHTML = '<span>' + esc(text) + '</span>' +
            (isError ? '' : '<button class="btn-quiet" id="kb-see-history">Списано</button>') +
            '<button class="btn-quiet" id="kb-dismiss" aria-label="Закрыть уведомление">×</button>';
        clearTimeout(toastTimer);
        var hideToast = function () { clearTimeout(toastTimer); toast.hidden = true; toast.classList.remove('toast-error'); };
        toastTimer = setTimeout(hideToast, 6000);
        document.getElementById('kb-dismiss').onclick = hideToast;
        var seeHistory = document.getElementById('kb-see-history');
        if (seeHistory) seeHistory.onclick = function() {
            queueMode('history');
            document.getElementById('kb-q-history').focus(); hideToast();
        };
    }
    // ---------- Подтверждение вместо window.confirm (фидбек 20.09) ----------
    // Нативный confirm не стилизован. Один <dialog> на страницу, вопросы
    // идут по очереди; ask() возвращает Promise<boolean> (true — подтверждено).
    var kbConfirmEl = null;
    var kbConfirmResolve = null;
    var kbConfirmOpener = null; // куда вернуть фокус после закрытия
    var kbConfirmOpenerAddr = null; // и его адрес: узел могли перестроить вместе с дровером
    function kbConfirmSettle(value) {
        if (!kbConfirmResolve) return;
        var resolve = kbConfirmResolve;
        kbConfirmResolve = null;
        resolve(value);
    }
    var KBConfirm = {
        ask: function (opts) {
            opts = opts || {};
            if (!kbConfirmEl) {
                kbConfirmEl = document.createElement('dialog');
                kbConfirmEl.id = 'kb-confirm';
                kbConfirmEl.setAttribute('aria-labelledby', 'kb-confirm-title');
                document.body.appendChild(kbConfirmEl);
                // Закрытие без выбора (Esc) — всегда «отмена»; click-ветки
                // успевают вызвать kbConfirmSettle раньше, здесь это no-op.
                kbConfirmEl.addEventListener('cancel', function () { kbConfirmSettle(false); });
                kbConfirmEl.addEventListener('close', function () {
                    kbConfirmSettle(false);
                    // Контент не чистим: форм и required-полей здесь нет, а
                    // очистка в момент закрытия при переоткрытии гоняется с
                    // ask() и оставляла диалог открытым, но пустым.
                    if (kbConfirmOpener && kbConfirmOpener.isConnected) kbConfirmOpener.focus();
                    // Ответ подтверждён и дровер перестроен (удаление вложения):
                    // узла-источника больше нет — возвращаем фокус по адресу, а
                    // не в body (тот же дефект 8, только через KBConfirm).
                    else if (kbConfirmOpenerAddr) restoreDrawerFocus(kbConfirmOpenerAddr);
                    kbConfirmOpener = null;
                    kbConfirmOpenerAddr = null;
                });
            }
            if (kbConfirmEl.open) {
                // Новый вопрос вытесняет прежний; его промис resolves(false) явно,
                // не полагаясь на порядок событийной очистки close.
                kbConfirmSettle(false);
                kbConfirmEl.close();
            }
            kbConfirmOpener = document.activeElement;
            // Тот же дефект 8 через KBConfirm: подтверждение удаления вложения
            // перестраивает дровер (openCard), и узел-источник к моменту close()
            // уже вырезан. Address переживает перестройку.
            kbConfirmOpenerAddr = drawerFocusAddress();
            kbConfirmEl.innerHTML = '<h2 id="kb-confirm-title">' + esc(opts.title || 'Подтвердите действие') + '</h2>' +
                (opts.message ? '<p class="kb-confirm-msg">' + esc(opts.message) + '</p>' : '') +
                '<div class="kb-confirm-foot">' +
                '<button type="button" class="btn btn-ghost" data-confirm-cancel>' + esc(opts.cancelText || 'Отмена') + '</button>' +
                // danger:false — неопасное действие (прикрепить к группе):
                // красная кнопка на нём читается как «удалишь».
                '<button type="button" class="btn ' + (opts.danger === false ? 'btn-primary' : 'btn-danger') + '" data-confirm-ok>' + esc(opts.confirmText || 'Удалить') + '</button></div>';
            kbConfirmEl.querySelector('[data-confirm-cancel]').onclick = function () { kbConfirmSettle(false); kbConfirmEl.close(); };
            kbConfirmEl.querySelector('[data-confirm-ok]').onclick = function () { kbConfirmSettle(true); kbConfirmEl.close(); };
            kbConfirmEl.showModal();
            // Действие опасное — стартовый фокус на безопасной «Отмене».
            kbConfirmEl.querySelector('[data-confirm-cancel]').focus();
            return new Promise(function (resolve) { kbConfirmResolve = resolve; });
        }
    };
    window.KBConfirm = KBConfirm;
    // Помощники разметки полей сделки (optionsHTML/dealField/dealInput/dealSelect).
    // Правка поля не сессионная: она уходит на сервер через apiMutate('card'),
    // а выписанные суммы меняют только выписка ТН и её отмена.
    function optionsHTML(values, selected) {
        return values.map(function(value) {
            var pair = Array.isArray(value) ? value : [value, value];
            return '<option value="' + esc(pair[0]) + '"' + (String(pair[0]) === String(selected) ? ' selected' : '') + '>' + esc(pair[1]) + '</option>';
        }).join('');
    }
    function dealField(key, label, control, extraClass) {
        return '<label class="kb-edit-field' + (extraClass ? ' ' + extraClass : '') + '" for="kb-deal-' + key + '"><span>' + label + '</span>' + control + '</label>';
    }
    function dealInput(c, key, label, type) {
        // Item #12: maxlength 200 = серверный лимит заголовка (schemas.py:249).
        return dealField(key, label, '<input id="kb-deal-' + key + '" data-deal-field="' + key + '" type="' + (type || 'text') + '" value="' + esc(key === 'amount' ? money(c.amount) : c[key]) + '"' + (key === 'amount' ? ' inputmode="decimal"' : ' maxlength="200"') + (key === 'amount' && c.stage === 'done' ? ' disabled' : '') + '>');
    }
    function dealSelect(key, label, values, selected, disabled, extraClass) {
        return dealField(key, label, '<select id="kb-deal-' + key + '" data-deal-field="' + key + '"' + (disabled ? ' disabled' : '') + '>' + optionsHTML(values, selected) + '</select>', extraClass);
    }
    window.KBPayment.escape = esc;
    // Save only validated, normalized values. Raw input remains in KBPayment's
    // per-card WeakMap, including inactive date/day/prepay fields.
    //
    // Отрисовка состояния оплаты БЕЗ записи на сервер. Раньше валидация,
    // покраска и PATCH жили в одной функции, а вызывались из отрисовки
    // карточки: каждое открытие отправляло payment_terms/payment_due_date
    // и создавало версию записи — чтение превращалось в запись, история
    // пухла, а при одновременной работе затирались чужие правки.
    function paintPayment(c, result, saved) {
        dialog.querySelectorAll('[data-payment-field]').forEach(function(input) {
            var error = result.errors[input.dataset.paymentField] || '';
            input.setCustomValidity(error);
            input.setAttribute('aria-invalid', String(!!error));
            var message = document.getElementById(input.id + '-error');
            if (message) message.textContent = error;
        });
        var status = document.getElementById('kb-payment-status');
        if (status) {
            status.dataset.valid = String(result.valid);
            // Аудит F6 (V2-UI-AUDIT-2026-09-22): строка состояния печатала
            // «Условия оплаты» и без выбранного срока, и без сохранённых
            // деталей — под подсказкой payment.js «Условия оплаты не выбраны.»
            // вставала вторая такая же подпись, висячая и пустая по смыслу.
            // Название поля уже есть у контрола в шапке; здесь сообщаем только
            // изменение и только когда есть что сообщить.
            var details = [];
            if (result.due) details.push('Оплатить до ' + result.due);
            if (result.prepay !== null) details.push('Предоплата ' + money(result.prepay) + ' BYN');
            var statusText = result.valid
                ? (saved ? 'Сохранено' + (details.length ? ' · ' + details.join(' · ') : '')
                         : details.join(' · '))
                : 'Не сохранено · ' + Object.values(result.errors).join(' ');
            // Сначала возвращаем строку в дерево (в дровере действует
            // #kb-dialog [hidden] { display:none !important }), потом меняем
            // текст — иначе role="status" нечего объявлять.
            status.hidden = !statusText;
            status.textContent = statusText;
        }
        // Sync validation on the visible combobox, not only its hidden native.
        var mode = dialog.querySelector('#kb-payment-mode');
        if (mode) window.KBSelect.enhance(mode);
    }
    // Сохранение — только из пользовательских событий (input/change).
    function updatePayment(c) {
        var d = window.KBPayment.draft(c);
        var result = window.KBPayment.validate(c, parseMoney);
        if (c.paymentDetails && c.paymentDetails.prepay !== null && c.paymentDetails.prepay >= c.amount) c.paymentDetails = null;
        if (result.valid) {
            c.paymentDetails = { terms: d.terms, mode: result.due ? d.mode : '',
                due: result.due, start: result.due && d.mode === 'days' ? d.start : '',
                days: result.due && d.mode === 'days' ? Number(d.days) : null, prepay: result.prepay };
            apiMutate('payment-terms', { id: Number(c.id), terms: d.terms || null, due: result.due || null });
            // Дата оплаты появилась/изменилась — календарю пульта нужно перерисоваться.
            document.dispatchEvent(new CustomEvent('kb:payment-saved', { detail: { cardId: c.id } }));
        }
        paintPayment(c, result, true);
    }
    function renderPayment(c, reveal) {
        window.KBSelect.close();
        var payDetails = document.getElementById('kb-payment-details');
        if (payDetails) payDetails.outerHTML = window.KBPayment.render(c);
        updatePayment(c);
        var newDetails = document.getElementById('kb-payment-details');
        if (newDetails) window.KBSelect.enhance(newDetails);
        if (reveal) {
            var overviewTab = document.getElementById('kb-tab-overview');
            if (overviewTab) overviewTab.click();
            var detailContent = dialog.querySelector('.kb-detail-content');
            if (detailContent) detailContent.scrollTop = 0;
        }
    }
    // Поля пункта закупки. Файл из этого комплекта живёт только в памяти
    // вкладки (blob-URL) и на сервер не уходит; загружаемый счёт-фактура —
    // другой путь, data-inv-up → POST /checklists/{id}/invoice.
    // Item #8: поставщики — из KBData.suppliers (реальный справочник).
    var procurementFiles = new Map();
    function supplierOptions() {
        // Реальный справочник; если пуст — показываем подсказку.
        var real = (KBData.suppliers || []).map(function (s) { return [s.id, s.name]; });
        return real;
    }
    function procurementFields(c, item) {
        var id = esc(item.id), disabled = c.stage === 'done' ? ' disabled' : '';
        var attachment = procurementFiles.get(item);
        var opts = supplierOptions();
        var emptyLabel = opts.length ? 'Выберите поставщика' : 'Поставщики не загружены';
        return '<div class="kb-procurement-fields"><label class="kb-edit-field" for="kb-supplier-' + id + '"><span>Поставщик</span><select id="kb-supplier-' + id + '" data-item-supplier="' + id + '"' + disabled + '>' +
            [['', emptyLabel]].concat(opts).map(function(s) {
                return '<option value="' + s[0] + '"' + (item.supplier_id === s[0] ? ' selected' : '') + '>' + esc(s[1]) + '</option>';
            }).join('') + '</select></label><div class="kb-item-file">' +
            (attachment ? '<a class="kb-item-download" href="' + esc(attachment.url) + '" download="' + esc(attachment.file.name) + '">' + esc(attachment.file.name) + '</a>' : '<span class="kb-detail-hint">Файл не прикреплён</span>') +
            '<div class="kb-item-file-actions"><button type="button" data-item-attach="' + id + '"' + disabled + '>' + (attachment ? 'Заменить файл' : 'Прикрепить файл') + '</button>' +
            (attachment ? '<button type="button" data-item-detach="' + id + '"' + disabled + '>Открепить</button>' : '') + '</div>' +
            '<input type="file" id="kb-file-' + id + '" data-item-file="' + id + '" accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.doc,.docx,.xls,.xlsx,.csv,.txt" aria-label="Файл для ' + esc(item.label) + '" hidden' + disabled + '>' +
            '<small id="kb-file-error-' + id + '" class="kb-field-error" role="alert"></small></div></div>';
    }
    function procurementItem(id) {
        return activeCard && activeCard.checklist.find(function(item) { return item.id === id; });
    }
    function updateItemFile(item) {
        var row = dialog.querySelector('[data-procurement-item="' + item.id + '"]');
        var fields = row.querySelector('.kb-procurement-fields');
        window.KBSelect.close();
        fields.outerHTML = procurementFields(activeCard, item);
        window.KBSelect.enhance(row);
        row.querySelector('[data-item-attach]').focus({ preventScroll: true });
    }
    dialog.addEventListener('click', function(e) {
        var button = e.target.closest('[data-item-attach], [data-item-detach]');
        if (!button || !activeCard || activeCard.stage === 'done') return;
        var item = procurementItem(button.dataset.itemAttach || button.dataset.itemDetach);
        if (!item) return;
        if (button.hasAttribute('data-item-attach')) dialog.querySelector('#kb-file-' + item.id).click();
        else {
            var old = procurementFiles.get(item);
            if (old) URL.revokeObjectURL(old.url);
            procurementFiles.delete(item);
            updateItemFile(item);
        }
    });
    dialog.addEventListener('change', function(e) {
        var input = e.target;
        if (!input.matches('[data-item-supplier], [data-item-file]') || !activeCard || activeCard.stage === 'done') return;
        var item = procurementItem(input.dataset.itemSupplier || input.dataset.itemFile);
        if (!item) return;
        if (input.hasAttribute('data-item-supplier')) {
            item.supplier_id = input.value || null;
            return;
        }
        var file = input.files[0];
        if (!file) return;
        var error = file.size > 25 * 1024 * 1024 ? 'Максимальный размер файла — 25 МБ.' :
            !/\.(pdf|jpe?g|png|gif|webp|docx?|xlsx?|csv|txt)$/i.test(file.name) ? 'Выберите PDF, изображение, документ Office, CSV или TXT.' : '';
        input.value = '';
        dialog.querySelector('#kb-file-error-' + item.id).textContent = error;
        if (error) return;
        var url = URL.createObjectURL(file), old = procurementFiles.get(item);
        procurementFiles.set(item, { file: file, url: url });
        if (old) URL.revokeObjectURL(old.url);
        updateItemFile(item);
    });

    // Прикрепление файлов (жалоба 21.09: кнопка «Прикрепить файл» не
    // открывала выбор, перетаскивание не принималось). Клик по label
    // дублируем явным input.click(): нативная активация скрытого input
    // ненадёжна в части WebView. Перетаскивание принимают пункт закупки
    // (.kb-check-item → файл счёта) и зона вложений (.kb-att-upload →
    // вложения сделки). Делегаты на dialog, потому что модалка
    // перерисовывается при каждом openCard.
    dialog.addEventListener('click', function (e) {
        // Клик по самому input — это всплывший синтетический клик от нашего
        // же input.click() ниже: без этого.guardа пикер зовётся дважды.
        if (e.target.closest('input[type="file"]')) return;
        var lab = e.target.closest('.kb-inv-up, .kb-att-upload-btn');
        if (!lab) return;
        var input = lab.querySelector('input[type="file"]');
        if (!input) return;
        e.preventDefault(); // гасим нативную пересылку клика label → input
        input.click();
    });
    dialog.addEventListener('dragover', function (e) {
        var zone = e.target.closest('.kb-check-item, .kb-att-upload');
        if (!zone || !e.dataTransfer) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        zone.classList.add('kb-drop-hot');
    });
    dialog.addEventListener('dragleave', function (e) {
        var zone = e.target.closest('.kb-check-item, .kb-att-upload');
        if (zone && (!e.relatedTarget || !zone.contains(e.relatedTarget))) zone.classList.remove('kb-drop-hot');
    });
    dialog.addEventListener('drop', function (e) {
        var zone = e.target.closest('.kb-check-item, .kb-att-upload');
        if (!zone || !e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault();
        zone.classList.remove('kb-drop-hot');
        if (zone.classList.contains('kb-check-item')) {
            var rawId = String(zone.dataset.procurementItem || '').replace(/[^0-9]/g, '');
            uploadChecklistInvoice(rawId, e.dataTransfer.files[0]);
        } else {
            uploadCardAttachments(e.dataTransfer.files);
        }
    });

    // ---------- Группа списания в дровере (дефекты 5 и 9 реестра) ----------
    // Роливые гейты состава группы разные, поэтому и метки разные:
    // attach (:104) и detach (:129) в writeoff_groups_router.py идут с общим
    // dependencies=[Depends(get_current_user)] — доступны любой роли, кнопки
    // без data-admin-only. Роспуск DELETE /writeoffs/groups/{sid} (:280-281)
    // закрыт require_role("admin","superadmin") — решение владельца 23.09, тот
    // же уровень, что у /annul. Без метки менеджер видел бы живую кнопку,
    // отвечающую 403 на каждый клик.
    function cardGroup(c) {
        if (!c || !c.groupId) return null;
        return KBData.groups.filter(function (x) { return x.id === c.groupId; })[0] || null;
    }
    // «Выписана» = серверный written_off. Отмена (POST …/annul) снимает его и
    // затирает invoice_number/invoice_date, но НЕ снимает writeoff_group_id с
    // карточек — поэтому строка «Групповая ТН», нарисованная по одному
    // c.groupId, была ложной и у разматанной группы, и у группы в «Сборке».
    function groupIssued(g) {
        return Boolean(g && g.writtenOff);
    }
    function groupInvoiceLabel(g) {
        var num = ((g.series ? g.series + ' ' : '') + (g.number || '')).trim();
        return num || 'без номера';
    }
    // Разбивки группы по карточкам сервер не отдаёт (covers[].amount === null
    // в boot), поэтому сумма — либо честная строка из снимка, либо «—»:
    // money(null) дало бы «0,00», то есть несуществующее покрытие.
    function groupCoverMoney(c, g) {
        var own = g.covers.filter(function (x) { return x.cardId === c.id; })[0];
        if (!own || own.amount === null || own.amount === undefined) return '—';
        return money(own.amount) + ' <small>BYN</small>';
    }
    function groupInvoiceRow(c, g) {
        // Дозадача к пункту 9: сервер разматывает группу только
        // администратору (POST /writeoffs/groups/{id}/annul —
        // require_role admin/superadmin, writeoff_groups_router.py:240),
        // поэтому под другой ролью кнопка не «мёртвая», а скрытая.
        return '<li class="kb-detail-doc"><div><b>Групповая ТН ' + esc(groupInvoiceLabel(g)) + '</b><span>' + esc(g.date || '') + '</span>' +
            '<span class="kb-detail-hint">Группа «' + esc(g.name) + '» · карточек в группе: ' + g.covers.length + '</span></div>' +
            '<div class="kb-doc-side"><strong>' + groupCoverMoney(c, g) + '</strong>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-doc-cancel="group-' + esc(g.id) + '" data-admin-only>Отменить</button></div></li>';
    }
    // Группа без выписанной ТН: вместо строки-пустышки с мёртвой «Отменить» —
    // честный статус и живые действия (serverId есть только у групп, собранных
    // на сервере; локальная группа предпросмотра его не получает).
    // Осиротевшая запись-документ (serverTxId при written_off = false — такое
    // возможно после ручной чистки или старой отмены) остаётся доступной: её
    // по-прежнему снимает тот же обработчик annul-tx, просто под другим именем
    // кнопки — без «Отменить» несуществующую ТН.
    function groupOpenRow(g) {
        var managed = !(g.serverId === undefined || g.serverId === null) && window.V2Api && window.V2Api.token();
        var actions = managed ?
            '<button type="button" class="btn btn-ghost btn-sm" data-group-leave="' + esc(g.id) + '">Выйти из группы</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-group-disband="' + esc(g.id) + '" data-admin-only>Распустить группу</button>' : '';
        if (managed && g.serverTxId) {
            actions += '<button type="button" class="btn btn-ghost btn-sm" data-doc-cancel="group-' + esc(g.id) + '" data-admin-only>Удалить документ группы</button>';
        }
        return '<li class="kb-detail-doc"><div><b>Группа «' + esc(g.name) + '»</b>' +
            '<span class="kb-detail-hint">Карточек в группе: ' + g.covers.length + ' · групповая ТН ещё не выписана.' +
            ' Состав группы можно менять до выписки — после неё и выход, и роспуск сервер отвергает.</span></div>' +
            (actions ? '<div class="kb-doc-side">' + actions + '</div>' : '') + '</li>';
    }
    // Кандидаты на присоединение — те же проверки, что делает сервер
    // (writeoff_groups_router.py:108-125): группа не закрыта накладной,
    // карточка не в другой группе, совпали клиент и магазин, а статус
    // карточки — «Сборка» или «На списание» (ALLOWED_GROUP_STATUSES).
    // Клиента сверяет по id — как сервер: имена в базе не уникальны
    // (на копии прод-БД есть однофамильцы), и по имени список предлагал
    // чужую группу. Магазин сервер сравнивает строкой (store_location),
    // поэтому он и здесь остаётся именем.
    function sameGroupClient(g, c) {
        // Поля нет вовсе — снимок группы старше этого id (предпросмотр
        // мокапа или старый кэш загрузчика): остаётся прежнее сравнение имён.
        if (g.clientId === undefined || c.clientId === undefined) {
            return (g.client || '') === (c.client || '');
        }
        return (g.clientId || null) === (c.clientId || null);
    }
    function attachableGroups(c) {
        if (!c || c.groupId) return [];
        if (!window.V2Api || !window.V2Api.token()) return [];
        var w = writeoffStatus();
        var allowed = ['assembly'];
        if (w) allowed.push(w.id);
        if (allowed.indexOf(c.stage) < 0) return [];
        var store = KBData.storeName(c.store);
        return KBData.groups.filter(function (g) {
            if (!g.serverId || g.writtenOff) return false;
            return sameGroupClient(g, c) && (g.store || '') === store;
        });
    }
    function groupAttachBlock(candidates) {
        return '<div class="kb-detail-note"><h3>Групповая накладная</h3>' +
            '<p>Сделку можно присоединить к открытой группе того же клиента и магазина' +
            ' — тогда по ней выпишут одну ТН на всю группу.</p><div class="kb-group-join">' +
            dealField('groupjoin', 'Группа', '<select id="kb-deal-groupjoin">' + optionsHTML(candidates.map(function (g) {
                return [g.id, g.name + ' · карточек: ' + g.covers.length];
            }), candidates[0].id) + '</select>') +
            '<button type="button" class="btn btn-primary btn-sm" data-group-join>Прикрепить к группе</button></div></div>';
    }

    function cardTabContent(c, currentStage) {
        var cl = checklistSummary(c);
        // Фидбек 18.09: клиент выбирается из справочника с поиском
        // (datalist), а не вводится текстом; новая фамилия — предлагается создать.
        var clientOptions = KBData.clients
            .filter(function (cl) { return cl.id !== 'us-none' && cl.name; })
            .map(function (cl) { return '<option value="' + esc(cl.name) + '"></option>'; }).join('');
        // Item #5: клиент — только фактическое значение; если не привязан —
        // пустое поле (placeholder). Загрузчик мог подставить c.title как
        // fallback — не выводим его, чтобы не создавать ложного впечатления.
        var clientDisplay = c.clientId ? (c.client || '') : '';
        var info = dealInput(c, 'title', 'Название') +
            dealField('client', 'Клиент (начните вводить — поиск по справочнику)',
                '<input id="kb-deal-client" data-deal-field="client" value="' + esc(clientDisplay) + '" placeholder="Не указан" autocomplete="off" maxlength="200">' +
                '<datalist id="kb-clients-datalist">' + clientOptions + '</datalist>') +
            dealSelect('store', 'Магазин', [['', 'Не указан']].concat(KBData.stores.map(function (s) { return [s.id, s.name]; })), c.store || '') +
            // Item #4: менеджер по id, не по индексу массива. Отключённый
            // (пункт 15) остаётся в списке — исторические сделки на нём
            // должны читаться, — но подписан в каждой строке выбора, а поле
            // приглушено, когда такой ответственный выбран.
            dealSelect('manager', 'Менеджер', [['', 'Не назначен']].concat(KBData.users.map(function (u) {
                return [u.id, userNameOff(u)];
            })), c.manager ? c.manager.id : '', false, isOffUser(c.manager) ? 'kb-mgr-off' : '') +
            dealInput(c, 'deadline', 'Срок · ДД.ММ.ГГГГ') + dealInput(c, 'amount', 'Сумма, BYN') +
            // Пункт 5 плана: почта отправителя. По ней блок «Письма отправителя»
            // во «Истории» подбирает переписку; id и data-deal-field совпадают с
            // селектором подсказки senderEmailControl(), иначе кнопка «Перейти к
            // полю почты» осталась бы без цели.
            dealField('sender_email', 'Почта отправителя',
                '<input id="kb-deal-sender_email" data-deal-field="sender_email" type="email" value="' + esc(c.sender_email || '') +
                '" placeholder="Не указана" autocomplete="off" maxlength="200">') +
            dealField('note', 'Заметка', '<textarea id="kb-deal-note" data-deal-field="note" rows="2" maxlength="4000">' + esc(c.note) + '</textarea>');
        var checks = c.checklist.map(function(item, i) {
            var rawId = String(item.id).replace(/[^0-9]/g, '');
            var done = c.stage === 'done' ? ' disabled' : '';
            var invName = displayName(item.invFile || '');
            var invFileHtml = item.invFile
                ? '<a class="kb-inv-dl" href="#" data-inv-dl="' + rawId + '" data-inv-name="' + esc(invName) + '" title="' + esc(invName) + '">📎 ' + esc(invName) + '</a>' +
                  '<button type="button" class="kb-inv-clear" data-inv-clear="' + rawId + '" title="Открепить файл: ' + esc(invName) + '" aria-label="Открепить файл ' + esc(invName) + '">✕</button>'
                : '<label class="kb-inv-up">Прикрепить файл<input type="file" class="kb-inv-input" data-inv-up="' + rawId + '" hidden></label>';
            // Компактный пункт: поставщик (с поиском) + сумма + файл в одну
            // строку; примечание ниже. Без дублей имени и нативных селектов.
            return '<div class="kb-check-item" data-procurement-item="' + esc(item.id) + '">' +
                '<div class="kb-check-line">' +
                '<input class="kb-sup-combo" data-cl-id="' + rawId + '" value="' + esc(item.supplierName || item.label || '') + '" placeholder="Поставщик (выберите или впишите)" autocomplete="off">' +
                '<input class="kb-sup-amount" data-cl-id="' + rawId + '" value="' + esc(item.amount || '') + '" placeholder="Сумма, BYN" inputmode="decimal">' +
                invFileHtml +
                '</div>' +
                '<input class="kb-cl-note" data-cl-id="' + rawId + '" value="' + esc(item.note || '') + '" placeholder="Примечание к закупке…" maxlength="500">' +
                '<div class="kb-check-row"><label><input type="checkbox" data-check="' + i + '" data-flag="ordered" ' + (item.ordered ? 'checked' : '') + (item.received ? ' disabled' : '') + '> Заказано</label><label><input type="checkbox" data-check="' + i + '" data-flag="received" ' + (item.received ? 'checked' : '') + (!item.ordered || c.stage === 'done' ? ' disabled' : '') + '> Получено</label></div></div>';
        }).join('');
        var docs = c.docs.map(function(d, i) {
            // Дата накладной может храниться в ДД.ММ.ГГГГ (прототип) или ISO
            // (записи, выписанные до нормализации) — приводим к ISO для input.
            var docIso = /^\d{4}-\d{2}-\d{2}$/.test(String(d.date)) ? d.date : (isoFromRu(d.date) || '');
            if (editingDoc === i) {
                return '<li class="kb-detail-doc kb-doc-edit"><div>' +
                    '<label>Дата выписки <input type="date" data-doc-field="date" value="' + esc(docIso) + '"></label>' +
                    '<label>Номер <input data-doc-field="number" value="' + esc(d.number) + '"></label>' +
                    '<label>Сумма, BYN <input inputmode="decimal" data-doc-field="amount" value="' + money(d.amount) + '"></label>' +
                    '<p class="kb-form-error" id="kb-doc-edit-error" role="alert"></p></div>' +
                    '<div class="kb-doc-side"><button type="button" class="btn btn-primary btn-sm" data-doc-edit-save="' + i + '">Сохранить</button>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-doc-edit-cancel>Отмена</button></div></li>';
            }
            // Фидбек 18.09: складское списание — отметка, что товар физически
            // отгружен (is_warehouse_writeoff у записи реестра). Состояние —
            // из лениво загруженного /payments/cards/{id}/invoices.
            var whId = c._invIds ? c._invIds[d.number] : null;
            var whOn = c._invFlags ? Boolean(c._invFlags[d.number]) : null;
            var whHtml = !whId ? '' :
                '<label class="kb-originals"><input type="checkbox" data-wh="' + esc(whId) + '" data-wh-number="' + esc(d.number) + '"' + (whOn ? ' checked' : '') + '> Списано со склада</label>';
            // Отказ второго шага (копия ТН) остаётся видимым: галочка стоит,
            // документа нет — без кнопки повтора пользователь с этим уже
            // ничего не делает до перезагрузки страницы.
            var copyFail = whId && c._docCopyFailed ? c._docCopyFailed[whId] : '';
            var copyRetry = !copyFail ? '' :
                '<button type="button" class="btn btn-ghost btn-sm" data-doc-copy="' + esc(whId) + '" title="Причина отказа: ' + esc(copyFail) + '">Создать копию ТН</button>';
            return '<li class="kb-detail-doc" data-doc-number="' + esc(d.number) + '"><div><b>' + esc((d.series ? d.series + ' ' : '') + d.number) + '</b><span>' + esc(d.date) + '</span><label class="kb-originals"><input type="checkbox" data-originals="' + i + '"' + (d.originalsReturned ? ' checked' : '') + '> Оригинал ТН возвращён</label>' + whHtml + '</div><div class="kb-doc-side"><strong>' + money(d.amount) + ' <small>BYN</small></strong>' + copyRetry +
                '<button type="button" class="btn btn-ghost btn-sm" data-doc-edit="' + i + '">Изменить</button>' +
                // Дозадача к пункту 9: одиночная отмена ТН идёт в
                // DELETE /payments/transactions/{id}, а он закрыт ролью admin
                // (payments_router.py:356 — V11, удаление необратимо и тянет
                // пересчёт остатка). Под другой ролью кнопка была видна и
                // отвечала 403 — прячем её тем же механизмом, что и отмену
                // групповой ТН ниже.
                '<button type="button" class="btn btn-ghost btn-sm" data-doc-cancel="' + i + '" data-admin-only>Отменить</button></div></li>';
        }).join('');
        var grp = cardGroup(c);
        var issuedGroup = groupIssued(grp);
        var groupEntry = issuedGroup ? groupInvoiceRow(c, grp) : '';
        var groupOpen = grp && !issuedGroup ? groupOpenRow(grp) : '';
        var joinable = attachableGroups(c);
        var groupJoin = !grp && joinable.length ? groupAttachBlock(joinable) : '';
        var history = c.docs.map(function(d) {
            return '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Выписана накладная ' + esc((d.series ? d.series + ' ' : '') + d.number) + '</b><p>' + esc(d.date) + ' · ' + money(d.amount) + ' BYN</p></div></li>';
        }).join('');
        // Та же правка, что и в «Накладных»: «выписана групповая ТН» в
        // журнале по одному c.groupId приписывало сделку и к группе в
        // «Сборке», и к уже разматанной.
        if (grp) {
            if (issuedGroup) {
                var hcov = grp.covers.filter(function (x) { return x.cardId === c.id; })[0];
                history += '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Выписана групповая ТН ' + esc(groupInvoiceLabel(grp)) + '</b><p>' + esc(grp.date || '') + ' · ' + (hcov && hcov.amount !== null && hcov.amount !== undefined ? money(hcov.amount) + ' BYN · ' : '') + 'одна накладная на группу из ' + grp.covers.length + ' карточек</p></div></li>';
            } else {
                history += '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Сделка в группе «' + esc(grp.name) + '»</b><p>Карточек в группе: ' + grp.covers.length + ' · групповая ТН ещё не выписана.</p></div></li>';
            }
        }
        return '<section id="kb-panel-overview" role="tabpanel" aria-labelledby="kb-tab-overview" tabindex="0">' + window.KBPayment.render(c) +
            '<div class="kb-overview-head"><h3 class="kb-detail-section-title">О сделке</h3><button type="button" id="kb-hide-filled" aria-pressed="false" aria-controls="kb-overview-fields">Скрыть заполненные поля</button></div><div class="kb-deal-fields" id="kb-overview-fields">' + info + '</div><p class="kb-detail-empty" id="kb-overview-empty" role="status" hidden>Все поля заполнены. Нажмите «Показать все поля», чтобы изменить их.</p></section>' +
            '<section id="kb-panel-procurement" role="tabpanel" aria-labelledby="kb-tab-procurement" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">Закупка у поставщиков <span>Заказано ' + cl.ordered + ' · Получено ' + cl.received + ' (из ' + cl.total + ')</span></h3>' +
            '<p class="kb-detail-hint">Файл до 25 МБ</p>' +
            '<div class="kb-add-check">' +
            '<input id="kb-add-supplier" class="kb-add-supplier" placeholder="Поставщик (выберите или впишите)">' +
            '<input id="kb-add-amount" class="kb-add-amount" inputmode="decimal" placeholder="Сумма, BYN">' +
            '<button type="button" class="btn btn-primary btn-sm" id="kb-add-check">Добавить</button></div>' +
            (checks || '<p class="kb-detail-empty">Закупка не требуется.</p>') +
            '<h3 class="kb-detail-section-title">Вложения сделки <span>' + (c.attachments || []).length + '</span></h3>' +
            '<div id="kb-attachments-list">' +
            ((c.attachments || []).length ? c.attachments.map(function (a) {
                var attName = displayName(a.name || '') || 'Вложение без имени';
                return '<div class="kb-check-row kb-att-row" data-att-row="' + esc(a.id) + '">' +
                    '<button type="button" class="kb-att-dl kb-att-dl-compact" data-att-id="' + (a.id || '') + '" data-att-name="' + esc(attName) + '" title="' + esc(attName) + '">' +
                    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                    '<span class="grow trunc" title="' + esc(attName) + '">' + esc(attName) + '</span></button>' +
                    '<button type="button" class="btn btn-ghost btn-sm kb-att-del" data-att-del="' + esc(a.id) + '" data-att-del-name="' + esc(attName) + '" title="Удалить вложение: ' + esc(attName) + '" aria-label="Удалить вложение ' + esc(attName) + '">✕</button>' +
                    '</div>';
            }).join('') : '<p class="kb-detail-empty">Вложений нет.</p>') +
            '</div>' +
            '<div class="kb-att-upload">' +
            '<label class="kb-att-upload-btn"><input type="file" id="kb-att-upload-input" multiple accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.doc,.docx,.xls,.xlsx,.csv,.txt" hidden>Загрузить файлы</label>' +
            '<p class="kb-detail-hint">До 25 МБ каждый, несколько файлов</p>' +
            '</div>' +
            '</section>' +
            '<section id="kb-panel-invoices" role="tabpanel" aria-labelledby="kb-tab-invoices" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">Выписанные накладные <span>' + (c.docs.length + (groupEntry ? 1 : 0)) + '</span></h3>' +
            (groupEntry + groupOpen + docs ? '<ul class="kb-detail-docs">' + groupEntry + groupOpen + docs + '</ul>' : '<div class="kb-detail-empty"><b>Накладных пока нет</b><p>ТН выписываются на этапе «' + esc(writeoffStageName()) + '» — кнопка «Выписать накладную» появляется в карточке на этом этапе.</p></div>') +
            groupJoin +
            '<p class="kb-detail-hint">Выписанные ТН доступны в «Финансы → Документы». Оплата учитывается отдельно.</p></section>' +
            '<section id="kb-panel-history" role="tabpanel" aria-labelledby="kb-tab-history" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">История сделки</h3><div id="kb-history-real"><p class="kb-detail-empty">Журнал загружается…</p></div>' +
            '<h3 class="kb-detail-section-title">Письма отправителя</h3><div id="kb-email-related">' + emailsBoxHTML(c) + '</div>' +
            '<h3 class="kb-detail-section-title">Выписки по сделке</h3><ol class="kb-detail-history">' + history +
            '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Текущий этап: ' + esc(currentStage) + '</b><p>Состояние карточки сейчас; время перехода не записывается.</p></div></li></ol></section>';
    }
    // Фидбек 18.09: скачивание вложения сделки (в т.ч. файла из письма) —
    // GET /attachments/{id}/download с авторизацией, сохранение под своим
    // именем. Старые записи без id — подсказка, где скачать.
    async function downloadAttachment(id, name) {
        if (!id) { notify('У этого вложения нет id — скачайте его из рабочей версии.'); return; }
        try {
            const resp = await window.V2Api.download('/attachments/' + id + '/download');
            if (!resp.ok) {
                let detail = '';
                try { const j = await resp.json(); if (j && j.detail) detail = String(j.detail); } catch (e) {}
                throw new Error(detail || ('HTTP ' + resp.status));
            }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = name || 'attachment';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            notify('Не удалось скачать файл: ' + (err.message || 'ошибка'));
        }
    }
    function bindAttachmentDownloads() {
        dialog.querySelectorAll('.kb-att-dl').forEach(function (btn) {
            btn.onclick = function () { downloadAttachment(btn.dataset.attId, btn.dataset.attName); };
        });
    }
    // ---------- Вложения: удаление и загрузка ----------
    // Удаление вложения: DELETE /attachments/{id} с подтверждением.
    // При ошибке — тост с текстом сервера, список не меняется.
    function deleteAttachment(id, name) {
        if (!id || !window.V2Api || !window.V2Api.token()) return;
        KBConfirm.ask({
            title: 'Удалить вложение?',
            message: '«' + (name || id) + '»'
        }).then(function (ok) {
            if (!ok) return;
            var snapshot = activeCard ? (activeCard.attachments || []).slice() : null;
            // Оптимистично убираем из списка сразу (UX): при ошибке вернём.
            if (activeCard && activeCard.attachments) {
                activeCard.attachments = activeCard.attachments.filter(function (a) { return String(a.id) !== String(id); });
                openCard(activeCard);
            }
            window.V2Api.api('/attachments/' + id, { method: 'DELETE' }).then(function () {
                notify('Вложение удалено.');
            }).catch(function (err) {
                // Откат списка при ошибке
                if (activeCard && snapshot) activeCard.attachments = snapshot;
                if (activeCard) openCard(activeCard);
                notify('Не удалено: ' + (err.detail || err.message || 'ошибка'));
            });
        });
    }
    // Загрузка нескольких файлов: каждый файл — отдельный запрос,
    // падение одного не рвёт остальные. Лимит 25 МБ проверяется ДО запроса.
    function uploadCardAttachments(files) {
        if (!activeCard || !window.V2Api || !window.V2Api.token()) return;
        var cardId = Number(activeCard.id);
        var uploaded = 0;
        var errors = [];
        var pending = files.length;
        if (!pending) return;
        Array.from(files).forEach(function (file) {
            if (file.size > 25 * 1024 * 1024) {
                errors.push(file.name + ': больше 25 МБ');
                pending--;
                if (!pending) finishUpload(uploaded, errors);
                return;
            }
            window.V2Api.upload('/files/attach/' + cardId, file).then(function (resp) {
                if (!resp.ok) {
                    return resp.json().then(function (j) {
                        throw new Error(j && j.detail ? String(j.detail) : ('HTTP ' + resp.status));
                    });
                }
                return resp.json();
            }).then(function (saved) {
                // Добавляем вложение в карточку из ответа сервера
                if (saved && saved.id && activeCard) {
                    activeCard.attachments = activeCard.attachments || [];
                    activeCard.attachments.push({ id: saved.id, name: saved.file_name, path: saved.file_path });
                }
                uploaded++;
                pending--;
                if (!pending) finishUpload(uploaded, errors);
            }).catch(function (err) {
                errors.push(file.name + ': ' + (err.detail || err.message || 'ошибка'));
                pending--;
                if (!pending) finishUpload(uploaded, errors);
            });
        });
        function finishUpload(count, errs) {
            if (count > 0 && activeCard) openCard(activeCard);
            if (count > 0 && !errs.length) notify('Загружено файлов: ' + count);
            else if (count > 0 && errs.length) notify('Загружено: ' + count + ', ошибки: ' + errs.join('; '));
            else if (errs.length) notify('Загрузка не удалась: ' + errs.join('; '));
        }
    }
    // Файл счёта пункта закупки: загрузка и обновление пункта на экране.
    // Раньше имя файла оседало в c._invFiles, которое разметка не читала, —
    // после успешной загрузки пункт продолжал показывать «Прикрепить файл»,
    // и прикрепление выглядело сломанным. Теперь пишем в item.invFile,
    // который рендерит cardTabContent (как это уже делает открепление).
    function uploadChecklistInvoice(id, file) {
        if (!id || !file || !window.V2Api || !window.V2Api.token()) return;
        if (file.size > 25 * 1024 * 1024) { notify('Файл больше 25 МБ.'); return; }
        window.V2Api.upload('/checklists/' + id + '/invoice', file).then(function (resp) {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.json();
        }).then(function (saved) {
            if (saved.invoice_file_name) {
                var item = procurementItem('ck-' + id);
                if (item) item.invFile = saved.invoice_file_name;
                openCard(activeCard);
            }
            notify('Файл счёта прикреплён.');
        }).catch(function (e2) { notify('Файл не прикреплён: ' + (e2.detail || e2.message || 'ошибка')); });
    }
    // Привязка обработчиков удаления и загрузки вложений
    function bindAttachmentActions() {
        dialog.querySelectorAll('.kb-att-del').forEach(function (btn) {
            btn.addEventListener('click', function () {
                deleteAttachment(btn.dataset.attDel, btn.dataset.attDelName);
            });
        });
        var uploadInput = dialog.querySelector('#kb-att-upload-input');
        if (uploadInput) {
            uploadInput.addEventListener('change', function () {
                if (uploadInput.files && uploadInput.files.length) {
                    uploadCardAttachments(uploadInput.files);
                    uploadInput.value = '';
                }
            });
        }
    }
    // ---------- Письма отправителя (пункт 8 плана V2-WORKPLAN-2026-09-22) ----------
    // GET /email-parser/related/{card_id} — прошлые сделки того же отправителя
    // (email_parser_router.py:597). Блок живёт во вкладке «История», но запрос
    // раньше запускался только из «Связать со сделкой» — то есть при просмотре
    // никогда, и плейсхолдер «Загрузка…» висел вечно (аудит F3, запросов 0).
    // Теперь загрузка стартует при открытии вкладки (см. selectTab в openCard) и
    // кэшируется на объекте карточки: переключение вкладок туда-сюда и перестройки
    // дровера сервер не дёргают, перечитывает только явное действие — «Обновить»
    // / «Повторить» или смена почты в «Обзоре» (пункт 5). «Связать со сделкой»
    // снимок не пересилает: там сервер удаляет открытую карточку, поэтому
    // перечитываются все данные (reloadData). KBData.reload создаёт
    // карточки заново, поэтому после перечитывания данных кэш сам пустеет.
    // Состояния различимы: идёт запрос (плейсхолдер), писем нет / не указан
    // адрес отправителя (подсказка), ошибка (сообщение + «Повторить»).
    var EMAILS_LOADING_HTML = '<p class="kb-detail-empty">Загрузка…</p>';
    // Чем заполнить блок при сборке вкладки: кэшем или честным «Загрузка…».
    function emailsBoxHTML(c) {
        return c.emailsHtml || EMAILS_LOADING_HTML;
    }
    // Блок принадлежит дроверу карточки: в режимах оплаты и выписки dialog
    // пересобирается целиком и блока там нет.
    function emailsBox() {
        var box = document.getElementById('kb-email-related');
        return (box && dialog.contains(box)) ? box : null;
    }
    // Поле почты — «Почта отправителя» в «Обзоре» (ключ sender_email, пункт 5).
    // Переход к нему отдаём только когда поле уже есть в разметке: в мокапе без
    // загрузчика и в режимах оплаты/выписки дровер его не строит — висячей
    // мёртвой кнопки не показываем, а текст подсказки верен и без поля.
    function senderEmailControl() {
        return dialog.querySelector('[data-deal-field="sender_email"], #kb-deal-sender_email');
    }
    function emailsNoSenderHTML() {
        var hasField = Boolean(senderEmailControl());
        var note = hasField ? ' Почтовый адрес задаётся в поле «Почта отправителя» во вкладке «Обзор».' : '';
        return '<p class="kb-detail-empty">У сделки не указан почтовый адрес отправителя — сравнивать письма не с чем.' + note + '</p>' +
            (hasField ? '<button type="button" class="btn btn-ghost btn-sm" data-emails-goto-sender>Перейти к полю почты</button>' : '');
    }
    function emailsListHTML(related) {
        // Разметка строки — та же, что у документов и группы в «Накладных»
        // (.kb-detail-docs/.kb-detail-doc/.kb-doc-side): тот же список «текст +
        // действие у правого края», свои классы под это плодить нечем.
        return '<ul class="kb-detail-docs">' + related.map(function (item) {
            var date = item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : '';
            return '<li class="kb-detail-doc"><div><b>' + esc(item.title || 'Без темы') + '</b>' +
                '<span>' + esc(date) + '</span></div>' +
                '<div class="kb-doc-side"><button type="button" class="btn btn-ghost btn-sm kb-email-link" data-link-target="' + esc(item.id) + '" data-link-target-title="' + esc(item.title || '') + '">Связать со сделкой</button></div></li>';
        }).join('') + '</ul>' + emailsReloadHTML();
    }
    function emailsEmptyHTML(sender) {
        return '<p class="kb-detail-empty">Других писем от ' + esc(String(sender)) + ' нет.</p>' + emailsReloadHTML();
    }
    // Кэш живёт до перечитывания данных, поэтому у пользователя должен остаться
    // способ перечитать список по своей воле, а не только после отказа.
    function emailsReloadHTML() {
        return '<button type="button" class="btn btn-ghost btn-sm" data-emails-retry>Обновить</button>';
    }
    // Сброс снимка по адресу: поколение поднимаем здесь, а не только на старте
    // нового запроса — иначе ответ, ушедший по прежнему адресу, доживёт до
    // settle() и закэшируется уже под новым (гонка из предыдущего пакета).
    function emailsSnapshotReset(c, html) {
        c.emailsGeneration = (c.emailsGeneration || 0) + 1;
        c.emailsLoading = false;
        c.emailsHtml = html || '';
    }
    function emailsErrorHTML(reason) {
        // Отказ — тоже состояние, и оно переживает перестройки дровера: молча
        // повторять запрос при каждом переключении вкладок значит долбить сервер.
        return '<p class="kb-detail-empty">Не удалось загрузить письма: ' + esc(reason) + '</p>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-emails-retry>Повторить</button>';
    }
    function loadRelatedEmails(c, force) {
        if (!c) return;
        var box = emailsBox();
        if (!box) return;
        if (!force) {
            if (c.emailsLoading) return;                                  // запрос уже в полёте
            if (c.emailsHtml) { box.innerHTML = c.emailsHtml; return; }    // кэш на карточку
        }
        if (!window.V2Api || !window.V2Api.token()) {
            box.innerHTML = '<p class="kb-detail-empty">Доступно при подключении к CRM.</p>';
            return;
        }
        // Единственная роль, которой почтовый роутер закрыт: у /email-parser
        // сквозной гейт require_role("manager","warehouse","superadmin","admin")
        // (email_parser_router.py:100) — под documents запрос вернул бы 403.
        var role = window.V2Api.currentRole ? window.V2Api.currentRole() : null;
        if (role === 'documents') {
            box.innerHTML = '<p class="kb-detail-empty">Недоступно для вашей роли.</p>';
            return;
        }
        // Адрес отдаёт загрузчик (sender_email в маппинге карточки), поэтому
        // ветка undefined — запасной путь для снимка из старого кэша: тогда
        // решает сервер, он отвечает sender_email:null. Известен и пуст —
        // заведомо пустой запрос не отправляем вовсе.
        var address = (c.sender_email === undefined || c.sender_email === null)
            ? null : String(c.sender_email).trim();
        if (address === '') {
            emailsSnapshotReset(c, emailsNoSenderHTML());
            box.innerHTML = c.emailsHtml;
            return;
        }
        // Поколение загрузки: ответ /related приходит по тому адресу, который
        // был на старте запроса. Если почту сменили в полёте (быстрое
        // переключение вкладок), settle() иначе закэшировал бы чужой снимок
        // под новым адресом — счётчик делает опоздавший ответ безвредным.
        var generation = (c.emailsGeneration = (c.emailsGeneration || 0) + 1);
        c.emailsLoading = true;
        box.innerHTML = EMAILS_LOADING_HTML;
        var settle = function (html) {
            if (c.emailsGeneration !== generation) return;            // устаревший ответ
            c.emailsLoading = false;
            c.emailsHtml = html;
            // Блок переспрашиваем: за время запроса дровер мог перестроиться на
            // другую карточку — в чужой блок писать нельзя.
            var target = emailsBox();
            if (activeCard === c && target) target.innerHTML = html;
        };
        window.V2Api.api('/email-parser/related/' + encodeURIComponent(c.id)).then(function (data) {
            var related = (data && data.related) || [];
            if (!data || !data.sender_email) settle(emailsNoSenderHTML());
            else if (!related.length) settle(emailsEmptyHTML(data.sender_email));
            else settle(emailsListHTML(related));
        }).catch(function (err) {
            settle(emailsErrorHTML(err.detail || err.message || 'ошибка'));
        });
    }
    // Кнопки блока (повтор, переход к почте, «Связать со сделкой») вешаются
    // делегированием на dialog: разметка блока возвращается и из кэша, а
    // навешивание по querySelector после каждой перестройки копило дубли.
    dialog.addEventListener('click', function (e) {
        if (e.target.closest('[data-emails-retry]')) { loadRelatedEmails(activeCard, true); return; }
        if (e.target.closest('[data-emails-goto-sender]')) {
            var field = senderEmailControl();
            if (!field) return;
            var overviewTab = document.getElementById('kb-tab-overview');
            if (overviewTab) overviewTab.click();
            if (!(window.KBSelect && window.KBSelect.focus(field))) field.focus({ preventScroll: true });
            return;
        }
        var link = e.target.closest('.kb-email-link');
        if (link && activeCard) linkEmailToCard(activeCard, link.dataset.linkTarget, link.dataset.linkTargetTitle);
    });
    // POST /email-parser/link/{card_id} — связать письмо с текущей сделкой.
    // Тело: { target_card_id: <id целевой сделки из related> }.
    function linkEmailToCard(currentCard, targetCardId, targetTitle) {
        if (!targetCardId || !window.V2Api) return;
        // Двойной клик унёс бы текст письма в целевую дважды: сервер не
        // проверяет, что карточка-источник уже помечена удалённой, а перечитывание
        // данных отвечает не мгновенно.
        if (currentCard._emailLinkPending) return;
        currentCard._emailLinkPending = true;
        window.V2Api.api('/email-parser/link/' + encodeURIComponent(currentCard.id), {
            method: 'POST',
            body: { target_card_id: Number(targetCardId) }
        }).then(function (result) {
            currentCard._emailLinkPending = false;
            var message = result && result.message ? result.message : 'Письмо связано со сделкой.';
            // Сервер помечает удалённой ту карточку, что открыта в дровере
            // (src.is_deleted = True в link_card_to_existing), и дописывает
            // текст в целевую. Держать открытой «письмо» нельзя: карточки на
            // доске и её деньги без перечитывания остались бы старым снимком.
            // Дровер закрывает подписчик kb:reloaded — тем же путём, что уже
            // удалённую карточку. Рапортуем после успешного перечитывания.
            reloadData().then(function () {
                notify(message);
            }).catch(function (err) {
                refresh();
                notify(message + ' Данные не обновились: ' + ((err && (err.detail || err.message)) || 'ошибка'), true);
            });
        }).catch(function (err) {
            currentCard._emailLinkPending = false;
            notify('Не удалось связать: ' + (err.detail || err.message || 'ошибка'));
        });
    }
    // Дефект 3 реестра V2-WORKPLAN-2026-09-22: складское списание должно
    // заводить копию накладной в «Документах», как это делает legacy
    // (site/js/writeoffs.js:736-770). Роут идемпотентен: если копия по паре
    // (карточка + номер) уже есть, сервер отдаёт её же — новой строки не
    // появляется, поэтому resolve(true) означает «копию завели сейчас».
    // Гейт роли у роута только авторизационный (payments_router.py:317-318),
    // поэтому data-admin-only на это действие не вешаем.
    function duplicateAsDocument(card, txId) {
        return window.V2Api.api('/payments/transactions/' + txId + '/duplicate_as_document', { method: 'POST' })
            .then(function (doc) {
                if (card._docCopyFailed) delete card._docCopyFailed[txId];
                // Ответ — TransactionResponse (рубли, ISO-дата); в card.docs
                // доска держит копейки и ДД.ММ.ГГГГ — тот же формат, что
                // подставляет загрузчик, иначе money() покажет мусор.
                var known = !doc || !doc.id || card.docs.some(function (d) { return String(d.txId) === String(doc.id); });
                if (known) return false;
                card.docs.push({
                    txId: doc.id, series: '', number: String(doc.invoice_number || ''),
                    date: ruFromIso(String(doc.invoice_date || doc.date || '').slice(0, 10)),
                    amount: Math.round((Number(doc.amount) || 0) * 100),
                    originalsReturned: Boolean(doc.is_invoice_doc)
                });
                // Строки документов в финансах собираются из card.docs по
                // событию kb:documents — без него копия появилась бы только
                // после перезагрузки страницы.
                syncCard(card, { documents: true });
                return true;
            });
    }
    function bindWarehouseFlagChange(input, c) {
        input.addEventListener('change', function () {
            if (!input.checked) { input.checked = true; return; } // списание необратимо с плитки
            if (!window.V2Api || !window.V2Api.token()) return;
            var txId = input.dataset.wh;
            // Два запроса идут подряд; на время цепочки галочка гасится,
            // иначе второй клик отправляет повторную выписку копии.
            input.disabled = true;
            window.V2Api.api('/payments/transactions/' + txId, {
                method: 'PATCH',
                body: { is_warehouse_writeoff: true }
            }).then(function () {
                // Флаг на сервере стоит — поднимаем и локальный снимок,
                // иначе следующая перестройка дровера покажет галочку
                // снятой: loadWarehouseFlags кэш не обновляет.
                if (c._invFlags) c._invFlags[input.dataset.whNumber] = true;
                return duplicateAsDocument(c, txId).then(function (created) {
                    notify(created ? 'Товар списан со склада, копия ТН заведена в «Документах».' : 'Товар списан со склада.');
                }, function (e3) {
                    // Отказ второго шага не откатывает первый: списание
                    // состоялось, не хватает только копии. Говорим, чего
                    // именно нет, и оставляем кнопку повтора в строке.
                    c._docCopyFailed = c._docCopyFailed || {};
                    c._docCopyFailed[txId] = reasonText(e3);
                    // Перерисовываем только свой дровер: за время запроса
                    // в нём могла открыться другая карточка.
                    if (activeCard === c) openCard(c);
                    notify('Со склада списано, но копия ТН не создана: ' + reasonText(e3) + '. Повтор — кнопкой в строке накладной.', true);
                });
            }).catch(function (e2) {
                input.disabled = false;
                input.checked = false;
                notify('Не списано: ' + reasonText(e2), true);
            });
        });
    }
    // Ленивая загрузка не должна перестраивать весь дровер: добавляем в
    // существующие строки только чекбоксы, которым нужны id и флаг реестра.
    function updateWarehouseFlagControls(c) {
        var panel = document.getElementById('kb-panel-invoices');
        if (!panel) return;
        panel.querySelectorAll('.kb-detail-doc[data-doc-number]').forEach(function (row) {
            var number = row.dataset.docNumber;
            var whId = c._invIds ? c._invIds[number] : null;
            if (!whId || row.querySelector('input[data-wh]')) return;
            var content = row.children[0];
            if (!content) return;
            var label = document.createElement('label');
            label.className = 'kb-originals';
            var input = document.createElement('input');
            input.type = 'checkbox';
            input.setAttribute('data-wh', String(whId));
            input.setAttribute('data-wh-number', number);
            input.checked = Boolean(c._invFlags[number]);
            label.appendChild(input);
            label.appendChild(document.createTextNode(' Списано со склада'));
            content.appendChild(label);
            bindWarehouseFlagChange(input, c);
        });
    }
    // Фидбек 18.09: флаги складского списания по накладным карточки —
    // из эндпоинта чек-листа накладных (id записи реестра + written_off).
    function loadWarehouseFlags(c) {
        if (!window.V2Api || !window.V2Api.token() || !c.docs.length || c._invFlags || c._invFlagsLoading) return;
        c._invFlagsLoading = true;
        window.V2Api.api('/payments/cards/' + Number(c.id) + '/invoices').then(function (info) {
            c._invFlags = {};
            c._invIds = {};
            // эндпоинт отдаёт written_off (складское списание) и id записи
            (info.issued || []).forEach(function (i) {
                c._invFlags[i.invoice_number] = Boolean(i.written_off);
                c._invIds[i.invoice_number] = i.id;
            });
            c._invFlagsLoading = false;
            if (dialog.open && activeCard === c) updateWarehouseFlagControls(c);
        }).catch(function () {
            c._invFlagsLoading = false;
            /* тише некуда: флажки появятся после перезагрузки */
        });
    }
    // Фидбек 18.09: вкладка «История» показывает журнал из CRM (та же лента,
    // что в основной версии) — включая «Импорт почты» с текстом письма.
    // Текст письма содержит переносы — выводится как есть (pre-wrap).
    function loadCardHistory(c) {
        var box = document.getElementById('kb-history-real');
        if (!box) return;
        if (!window.KBData.loadCardActivity) {
            box.innerHTML = '<p class="kb-detail-empty">Журнал доступен при подключении к CRM.</p>';
            return;
        }
        if (c.historyLoaded) { box.innerHTML = c.historyHtml; return; }
        if (c._historyLoading) return;
        c._historyLoading = true;
        window.KBData.loadCardActivity(Number(c.id)).then(function (items) {
            var historyHtml;
            if (!items || !items.length) {
                historyHtml = '<p class="kb-detail-empty">Журнал пуст.</p>';
            } else {
                var rows = items.map(function (e) {
                    var d = new Date(e.created_at);
                    var when = isNaN(d) ? String(e.created_at || '') : d.toLocaleString('ru-RU');
                    var who = e.user_name ? ' · ' + esc(e.user_name) : '';
                    var details = e.details ? '<p class="kb-history-details">' + esc(e.details) + '</p>' : '';
                    return '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>' + esc(e.action) + '</b>' + who +
                        '<p class="kb-history-date">' + esc(when) + '</p>' + details + '</div></li>';
                }).join('');
                historyHtml = '<ol class="kb-detail-history kb-history-full">' + rows + '</ol>';
            }
            c.historyHtml = historyHtml;
            c.historyLoaded = true;
            c._historyLoading = false;
            var target = document.getElementById('kb-history-real');
            if (dialog.open && activeCard === c && target) target.innerHTML = historyHtml;
        }).catch(function () {
            c._historyLoading = false;
            var target = document.getElementById('kb-history-real');
            if (dialog.open && activeCard === c && target) {
                target.innerHTML = '<p class="kb-detail-empty">Не удалось загрузить журнал. Попробуйте открыть карточку снова.</p>';
            }
        });
    }
    // Recalculate only on an explicit toggle or a fresh open, never during edits.
    function updateOverviewVisibility(preserved) {
        var visible = 0;
        dialog.querySelectorAll('#kb-overview-fields [data-deal-field]').forEach(function(input) {
            var previous = preserved && preserved[input.dataset.dealField];
            if (previous) {
                input.value = previous.value;
                input.setCustomValidity(previous.error);
                if (previous.invalid !== null) input.setAttribute('aria-invalid', previous.invalid);
            }
            var filled = input.value.trim() !== '' && input.validity.valid && input.getAttribute('aria-invalid') !== 'true';
            var hidden = previous ? previous.hidden : state.hideFilled && filled;
            var wrapper = input.closest('.kb-edit-field');
            if (wrapper) wrapper.hidden = hidden;
            if (!hidden) visible++;
        });
        var toggle = document.getElementById('kb-hide-filled');
        toggle.setAttribute('aria-pressed', String(state.hideFilled));
        toggle.textContent = state.hideFilled ? 'Показать все поля' : 'Скрыть заполненные поля';
        document.getElementById('kb-overview-empty').hidden = visible !== 0;
    }
    var renderingCard = false;
    // Общий путь удаления (фидбек 20.09): и из диалога, и с плитки.
    // resetBtn дисейблится на время запроса; с плитки он не нужен.
    function deleteCard(c, resetBtn) {
        var setDisabled = function (disabled) { if (resetBtn) resetBtn.disabled = disabled; };
        setDisabled(true);
        apiMutate('card-delete', { id: Number(c.id) }).then(function (ok) {
            if (ok === false) { setDisabled(false); notify('Удаление не прошло на сервере.'); return; }
            var ci = KBData.cards.indexOf(c);
            if (ci >= 0) KBData.cards.splice(ci, 1);
            // Реестр финансов чистим на месте: фин-модуль держит ссылку на
            // массив finSource.outgoing (prototype: `const outgoing = ...`),
            // подмена массива оставила бы удалённую сделку в реестре до F5.
            if (window.KB_FIN_SOURCE && Array.isArray(window.KB_FIN_SOURCE.outgoing)) {
                var finRows = window.KB_FIN_SOURCE.outgoing;
                for (var ri = finRows.length - 1; ri >= 0; ri--) {
                    if (finRows[ri].cardId === c.id) finRows.splice(ri, 1);
                }
            }
            document.querySelectorAll('#fin-payment-rows tr[data-card="' + c.id + '"], #fin-document-rows tr[data-card="' + c.id + '"]').forEach(function (row) { row.remove(); });
            // Диалог закрываем только если в нём открыта именно эта карточка
            // (удаление с плитки не должно гасить чужой открытый диалог).
            if (dialog.open && activeCard === c) closeDialog();
            refresh();
            notify(c.id + ' — карточка удалена в корзину.');
        }).catch(function (err) {
            setDisabled(false);
            notify('Не удалено: ' + (err.detail || err.message || 'ошибка'));
        });
    }
    // Дефект 8 реестра V2-WORKPLAN-2026-09-22: дровер собирается целиком
    // (dialog.innerHTML = …), и сфокусированный элемент гибнет вместе со старым
    // деревом — активным остаётся <body>, а Tab у клавиатурщика начинается с
    // начала страницы. Перестроек у дровера много: ответ loadWarehouseFlags
    // (~244 мс после открытия), kb:reloaded, отмена ТН, вложения, пункт
    // закупки — поэтому адрес фокуса снимается и возвращается в единственной
    // точке сборки карточки, а не заплаткой в каждом вызове. Всё вне modally
    // открытого <dialog> нативно недоступно для фокуса, поэтому ловим и
    // возвращаем фокус только внутри дровера: наружу (и в inert-область) он
    // уехать не может по построению.
    function drawerFocusAddress() {
        var el = document.activeElement;
        if (!el || el === document.body || !dialog.contains(el)) return null;
        var addr = { tag: el.tagName, id: el.id || '', data: {}, path: [] };
        for (var key in el.dataset) addr.data[key] = el.dataset[key];
        for (var node = el; node && node !== dialog; node = node.parentNode) {
            if (!node.parentNode) return null; // узел уже вырезан из дровера — адрес не построить
            addr.path.unshift(Array.prototype.indexOf.call(node.parentNode.children, node));
        }
        return addr;
    }
    function sameFocusAddress(el, addr) {
        if (!el || el.tagName !== addr.tag) return false;
        if (addr.id && el.id !== addr.id) return false;
        return Object.keys(addr.data).every(function (k) { return el.dataset[k] === addr.data[k]; });
    }
    function followFocusPath(path) {
        var node = dialog;
        for (var i = 0; i < path.length; i++) {
            node = node.children[path[i]];
            if (!node) return null;
        }
        return node === dialog ? null : node;
    }
    function resolveDrawerFocus(addr) {
        // Порядок: id (переживает перестройку, элементы размечены), затем путь
        // в дереве (тот же макет из тех же данных), затем поиск совпадения по
        // data-* — путь съезжает, когда состав строк изменился (отменённая ТН,
        // удалённое вложение).
        if (addr.id) {
            var byId = document.getElementById(addr.id);
            if (byId && dialog.contains(byId) && byId.tagName === addr.tag) return byId;
        }
        var byPath = followFocusPath(addr.path);
        if (byPath && sameFocusAddress(byPath, addr)) return byPath;
        if (Object.keys(addr.data).length) {
            var same = dialog.getElementsByTagName(addr.tag.toLowerCase());
            for (var i = 0; i < same.length; i++) if (sameFocusAddress(same[i], addr)) return same[i];
        }
        return null;
    }
    function tryDrawerFocus(el) {
        if (!el || !dialog.contains(el) || el.disabled) return false;
        if (el.closest('[inert]')) return false; // внутрь inert фокус не уходит
        try { el.focus({ preventScroll: true }); } catch (e) { return false; }
        // Скрытый (hidden на панели вкладки) элемент фокус не примет —
        // проверяем фактом, а не догадкой.
        return document.activeElement === el;
    }
    function restoreDrawerFocus(addr) {
        if (!addr) return;
        if (tryDrawerFocus(resolveDrawerFocus(addr))) return;
        // Устойчивый якорь дровера — его крестик (он же [autofocus] при
        // открытии): вернуться в начало дровера честнее, чем оставить фокус в
        // body, откуда Tab стартует за пределами модального окна.
        tryDrawerFocus(dialog.querySelector('.kb-detail-close') ||
            dialog.querySelector('[autofocus]') || dialog.querySelector('[data-close]'));
    }
    function openCard(c) {
        window.KBSelect.close();
        var focusAddr = drawerFocusAddress();
        window.KBPayment.draft(c);
        if (!activeCard || activeCard.id !== c.id) editingDoc = null;
        var preserved = null;
        if (dialog.open && activeCard === c && dialog.querySelector('#kb-overview-fields')) {
            preserved = {};
            dialog.querySelectorAll('#kb-overview-fields [data-deal-field]').forEach(function(input) {
                var wrap = input.closest('.kb-edit-field');
                preserved[input.dataset.dealField] = { value: input.value, hidden: wrap ? wrap.hidden : false,
                    error: input.validity.customError ? input.validationMessage : '', invalid: input.getAttribute('aria-invalid') };
            });
        }
        var selectedTab = activeCard && activeCard.id === c.id ? dialog.dataset.cardTab || 'overview' : 'overview';
        var content = dialog.querySelector('.kb-detail-content');
        var scrollTop = activeCard && activeCard.id === c.id && dialog.open && content ? content.scrollTop : 0;
        activeCard = c;
        // 'closed' печатается по-русски и в фолбэке (пустой словарь), где
        // stageName не найдёт статус в справочнике и вернул бы сырой id.
        var currentStage = c.stage === 'done' ? 'Списано' : c.stage === 'closed' ? 'Закрыто' : stageName(c.stage);
        // Псевдо-опции селекта: 'done' — полная выписка, 'closed' — статус
        // «Закрыто». В справочнике «Закрыто» есть, но колонкой и селектом не
        // назначается (role archive) — без псевдо-опции у закрытой карточки в
        // списке этапов не было бы её текущего значения.
        var stages = boardStatuses().concat([writeoffStatus(), { id: 'done', name: 'Списано' }, { id: 'closed', name: 'Закрыто' }]).filter(Boolean);
        var tabs = [['overview', 'Обзор'], ['procurement', 'Закупка'], ['invoices', 'Накладные'], ['history', 'История']];
        renderingCard = true;
        dialog.innerHTML = '<div class="kb-detail"><header class="kb-detail-head"><div><div class="kb-detail-meta"><span>Сделка ' + esc(c.id) + '</span><span class="kb-detail-badge">' + esc(currentStage) + '</span></div>' +
            '<h2 id="kb-dialog-title">' + esc(c.title) + '</h2></div><button type="button" class="kb-detail-close" data-close aria-label="Закрыть карточку" autofocus><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>' +
            '<div class="kb-detail-summary"><dl class="kb-detail-money"><div class="kb-detail-total"><dt>Сумма сделки</dt><dd>' + money(c.amount) + ' <small>BYN</small></dd></div><div><dt>Оплачено</dt><dd>' + money(c.paidAmount) + ' <small>BYN</small></dd></div><div><dt>Выписано</dt><dd>' + money(cardIssued(c)) + ' <small>BYN</small></dd></div><div><dt>Осталось выписать</dt><dd>' + moneyOrDash(cardRemaining(c)) + ' <small>BYN</small></dd></div></dl>' +
            '<div class="kb-detail-controls">' +
            dealSelect('paymentTerms', 'Условия оплаты', [['', 'Не выбраны'], ['deferred', 'Отсрочка'], ['full', 'Оплата 100%'], ['partial_deferred', 'Частичная оплата + отсрочка платежа']], c.paymentTerms) +
            dealSelect('stage', 'Этап', stages.filter(function(st) { return (st.id !== 'done' || c.stage === 'done') && (st.id !== 'closed' || c.stage === 'closed') && st.canAssign !== false; }).map(function(st) { return [st.id, st.name]; }), c.stage, c.stage === 'done' || c.stage === 'closed') +
            '</div></div>' +
            '<div class="kb-detail-tabs" role="tablist" aria-label="Разделы сделки">' + tabs.map(function(tab) {
                return '<button type="button" role="tab" id="kb-tab-' + tab[0] + '" data-card-tab="' + tab[0] + '" aria-controls="kb-panel-' + tab[0] + '" aria-selected="false" tabindex="-1">' + tab[1] + '</button>';
            }).join('') + '</div><div class="kb-detail-content">' + cardTabContent(c, currentStage) + '</div>' +
            '<footer class="kb-dialog-foot"><div>' +
            (c.stage !== 'done' && c.stage !== 'closed' ? '<button class="btn btn-ghost" id="kb-pay">Внести оплату</button>' : '') +
            (c.stage === 'assembly' ? '<button class="btn btn-primary" id="kb-send">В списание</button>' : '') +
            (c.stage === 'writeoff' ? '<button class="btn btn-primary" id="kb-issue">Выписать накладную</button>' : '') +
            '<button class="btn btn-ghost btn-sm kb-card-delete" id="kb-delete">Удалить</button>' +
            '<button class="btn btn-ghost" data-close>Закрыть</button></div></footer></div>';
        renderingCard = false;
        function selectTab(name, focus) {
            dialog.dataset.cardTab = name;
            dialog.querySelectorAll('[data-card-tab]').forEach(function(button) {
                var selected = button.dataset.cardTab === name;
                button.setAttribute('aria-selected', String(selected));
                button.tabIndex = selected ? 0 : -1;
                var panel = document.getElementById(button.getAttribute('aria-controls'));
                if (panel) panel.hidden = !selected;
                if (selected && focus) button.focus();
            });
            // Пункт 8 (аудит F3): письма отправителя грузились только из
            // «Связать со сделкой», поэтому при открытии вкладки «История»
            // плейсхолдер «Загрузка…» висел вечно. Запуск — по открытию
            // вкладки; внутри loadRelatedEmails есть кэш на карточку и
            // защита от повторного запроса, так что переключение туда-сюда
            // сервер не дёргает.
            if (name === 'history') {
                loadCardHistory(c);
                loadRelatedEmails(c, false);
            } else if (name === 'invoices') {
                loadWarehouseFlags(c);
            }
        }
        var tablist = dialog.querySelector('[role="tablist"]');
        tablist.onclick = function(e) {
            var button = e.target.closest('[data-card-tab]');
            if (button) selectTab(button.dataset.cardTab, false);
        };
        tablist.onkeydown = function(e) {
            var button = e.target.closest('[data-card-tab]');
            if (!button || ['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(e.key) === -1) return;
            e.preventDefault();
            var index = tabs.findIndex(function(tab) { return tab[0] === button.dataset.cardTab; });
            index = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (index + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            selectTab(tabs[index][0], true);
        };
        updateOverviewVisibility(preserved);
        // Отрисовка карточки ничего не сохраняет: только показываем состояние
        // условий оплаты (PATCH уходит лишь из input/change — см. updatePayment).
        paintPayment(c, window.KBPayment.validate(c, parseMoney), false);
        window.KBSelect.enhance(dialog);
        document.getElementById('kb-hide-filled').onclick = function() {
            state.hideFilled = !state.hideFilled;
            updateOverviewVisibility(null);
        };
        selectTab(selectedTab, false);
        bindAttachmentDownloads();
        // Раньше не вызывалась вовсе: «Загрузить файлы» у вложений сделки
        // и кнопки «✕» удаления были мёртвыми.
        bindAttachmentActions();
        initCardCombos();
        dialog.querySelectorAll('input[data-wh]').forEach(function (input) {
            bindWarehouseFlagChange(input, c);
        });
        dialog.querySelectorAll('[data-doc-copy]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (!window.V2Api || !window.V2Api.token()) return;
                btn.disabled = true;
                duplicateAsDocument(c, btn.dataset.docCopy).then(function () {
                    notify('Копия ТН заведена в «Документах».');
                    if (activeCard === c) openCard(c);   // снимаем кнопку повтора
                }, function (e2) {
                    btn.disabled = false;
                    notify('Копия не создана: ' + reasonText(e2), true);
                });
            });
        });
        dialog.querySelectorAll('.kb-cl-note').forEach(function (input) {
            input.addEventListener('change', function () {
                var id = input.dataset.clId;
                if (!id || !window.V2Api || !window.V2Api.token()) return;
                window.V2Api.api('/checklists/' + id, { method: 'PATCH', body: { note: input.value } })
                    .catch(function (e2) { notify('Примечание закупки не сохранено: ' + (e2.detail || e2.message || 'ошибка')); });
            });
        });
        // Поставщик и сумма пункта закупки — сохраняются в CRM
        dialog.querySelectorAll('.kb-sup-combo').forEach(function (input) {
            input.addEventListener('change', function () {
                var id = input.dataset.clId;
                if (!id || !window.V2Api || !window.V2Api.token()) return;
                var name = input.value.trim();
                if (!name) return;
                var sup = (KBData.suppliers || []).filter(function (s) { return s.name.toLowerCase() === name.toLowerCase(); })[0];
                var body = sup ? { supplier_id: numericClientIdValue(sup.id) } : { company_name: name };
                window.V2Api.api('/checklists/' + id, { method: 'PATCH', body: body })
                    .catch(function (e2) { notify('Поставщик не сохранён: ' + (e2.detail || e2.message || 'ошибка')); });
            });
        });
        dialog.querySelectorAll('.kb-sup-amount').forEach(function (input) {
            input.addEventListener('change', function () {
                var id = input.dataset.clId;
                if (!id || !window.V2Api || !window.V2Api.token()) return;
                var amount = parseMoney(input.value);
                if (!Number.isSafeInteger(amount) || amount < 0) return;
                window.V2Api.api('/checklists/' + id, { method: 'PATCH', body: { amount: amount / 100 } })
                    .catch(function (e2) { notify('Сумма закупки не сохранена: ' + (e2.detail || e2.message || 'ошибка')); });
            });
        });
        // Открепить файл счёта
        dialog.querySelectorAll('.kb-inv-clear').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var id = btn.dataset.invClear;
                if (!id || !window.V2Api || !window.V2Api.token()) return;
                window.V2Api.api('/checklists/' + id + '/invoice', { method: 'DELETE' })
                    .then(function () {
                        var item = procurementItem('ck-' + id);
                        if (item) { item.invFile = null; openCard(activeCard || c); }
                        notify('Файл откреплён.');
                    })
                    .catch(function (e2) { notify('Не откреплено: ' + (e2.detail || e2.message || 'ошибка')); });
            });
        });
        // Файл счёта поставщика: скачивание и прикрепление
        dialog.querySelectorAll('.kb-inv-dl').forEach(function (a) {
            a.addEventListener('click', function (ev) {
                ev.preventDefault();
                window.V2Api.download('/checklists/' + a.dataset.invDl + '/invoice/download').then(function (resp) {
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    return resp.blob();
                }).then(function (blob) {
                    var url = URL.createObjectURL(blob);
                    var tmp = document.createElement('a');
                    tmp.href = url; tmp.download = a.dataset.invName || 'invoice';
                    document.body.appendChild(tmp); tmp.click(); tmp.remove();
                    URL.revokeObjectURL(url);
                }).catch(function (e2) { notify('Файл не скачался: ' + (e2.message || 'ошибка')); });
            });
        });
        dialog.querySelectorAll('.kb-inv-input').forEach(function (inp) {
            inp.addEventListener('change', function () {
                uploadChecklistInvoice(inp.dataset.invUp, inp.files && inp.files[0]);
            });
        });
        // Добавление пункта закупки: поставщик из справочника (можно вписать
        // нового — уйдёт текстом) + сумма.
        var addBtn = document.getElementById('kb-add-check');
        if (addBtn) addBtn.addEventListener('click', function () {
            var supplier = (document.getElementById('kb-add-supplier') || {}).value || '';
            supplier = supplier.trim();
            var amount = parseMoney((document.getElementById('kb-add-amount') || {}).value);
            if (!supplier) { notify('Укажите поставщика.'); return; }
            if (!Number.isSafeInteger(amount) || amount <= 0) { notify('Укажите сумму больше нуля.'); return; }
            var sup = (KBData.suppliers || []).filter(function (s) { return s.name.toLowerCase() === supplier.toLowerCase(); })[0];
            var body = { amount: amount / 100 };
            if (sup) body.supplier_id = numericClientIdValue(sup.id);
            else body.company_name = supplier;
            window.V2Api.api('/cards/' + Number(c.id) + '/checklists', { method: 'POST', body: body })
                .then(function (saved) {
                    // Пункт появляется сразу, без перезагрузки страницы
                    c.checklist.push({
                        id: 'ck-' + saved.id,
                        label: supplier,
                        supplier_id: sup ? sup.id : null,
                        note: '', invFile: null,
                        ordered: false, received: false
                    });
                    openCard(c);
                    notify('Пункт закупки добавлен.');
                })
                .catch(function (e2) { notify('Не добавлено: ' + (e2.detail || e2.message || 'ошибка')); });
        });
        if (!dialog.open) dialog.showModal();
        dialog.querySelector('.kb-detail-content').scrollTop = scrollTop;
        var send = document.getElementById('kb-send');
        if (send) send.onclick = function() { c.stage = 'writeoff'; apiMutate('status', { id: Number(c.id), status: stageName('writeoff') }); closeDialog(); refresh(); notify(c.id + ' — передана в очередь выписки.'); };
        var issue = document.getElementById('kb-issue');
        if (issue) issue.onclick = function() { openIssue(c); };
        var pay = document.getElementById('kb-pay');
        if (pay) pay.onclick = function() { openPay(c); };
        var del = document.getElementById('kb-delete');
        if (del) del.onclick = function() {
            KBConfirm.ask({
                title: 'Удалить карточку?',
                message: '«' + (c.title || c.id) + '» уйдёт в корзину — восстановление через администратора.'
            }).then(function (ok) {
                if (ok) deleteCard(c, del); // Item #9: запрос только после подтверждения
            });
        };
        // Дозадача к пункту 9: метки data-admin-only ставятся разметкой, а
        // скрывает их загрузчик — проход после каждой сборки дровера.
        applyRoleMarks(dialog);
        // Дефект 8: возврат фокуса — в самом конце, когда вкладки разложены и
        // обработчики навешаны. Скролл дровера восстановлен выше, поэтому
        // preventScroll.
        restoreDrawerFocus(focusAddr);
    }

    // Оплата — логика рабочей версии (выбирается СТАТУС, деньги следуют
    // за ним) плюс касса клиента (фидбек 18.09): баланс = приходы − счета.
    // «Оплачен (100%)» одним кликом; «Частично» — сумма; «Отсрочка» —
    // сумма и дата; «Не оплачен» — обнуление с подтверждением; «Аванс» —
    // деньги в кассу клиента без сделки. Источник «Из баланса» закрывает
    // счёт уже внесёнными деньгами: касса не растёт, кредит списывается.
    function openPay(c) {
        window.KBSelect.close();
        var total = c.amount, paid = c.paidAmount;
        var hasClient = Boolean(c.clientId);
        var clientBalance = null; // придёт с сервера
        dialog.innerHTML = '<h2 id="kb-dialog-title">Внести оплату · ' + esc(c.id) + '</h2>' +
            '<p class="kb-detail-hint">Сумма сделки: ' + money(total) + ' BYN · Оплачено: ' + money(paid) + ' BYN · Долг: ' + money(total - paid) + ' BYN</p>' +
            '<p class="kb-detail-hint" id="kb-pay-client" hidden></p>' +
            '<div class="kb-pay-modes">' +
            [['Оплачен', 'Оплачен (100%)'], ['Частично', 'Частично'], ['Отсрочка', 'Отсрочка'], ['Аванс', 'Аванс в кассу клиента'], ['Не оплачен', 'Не оплачен']].map(function (pair) {
                return '<button type="button" class="btn btn-ghost kb-pay-mode" data-mode="' + pair[0] + '">' + pair[1] + '</button>';
            }).join('') + '</div>' +
            '<form id="kb-pay-form" class="kb-form" hidden>' +
            '<div class="kb-field" id="kb-pay-amount-wrap"><label for="kb-pay-amount">Сумма, BYN</label>' +
            '<input id="kb-pay-amount" inputmode="decimal" placeholder="0,00"></div>' +
            '<div class="kb-field" id="kb-pay-due-wrap" hidden><label for="kb-pay-due">Оплатить до</label>' +
            '<input id="kb-pay-due" type="date"></div>' +
            '<div class="kb-field" id="kb-pay-note-wrap" hidden><label for="kb-pay-note">Комментарий</label>' +
            '<input id="kb-pay-note" maxlength="200" placeholder="например: предоплата по счёту"></div>' +
            '<label class="login-remember" id="kb-pay-from-balance-wrap" hidden><input type="checkbox" id="kb-pay-from-balance"> <span>Из баланса клиента (без новых денег)</span></label>' +
            '<p class="kb-form-error" id="kb-pay-error" role="alert"></p>' +
            '<p class="kb-form-hint" id="kb-pay-hint"></p>' +
            '<button class="btn btn-primary" type="submit">Провести</button>' +
            '<button class="btn btn-ghost" type="button" data-close>Отмена</button></form>';
        var form = document.getElementById('kb-pay-form');
        var amountWrap = document.getElementById('kb-pay-amount-wrap');
        var dueWrap = document.getElementById('kb-pay-due-wrap');
        var noteWrap = document.getElementById('kb-pay-note-wrap');
        var fromBalanceWrap = document.getElementById('kb-pay-from-balance-wrap');
        var amountInput = document.getElementById('kb-pay-amount');
        var dueInput = document.getElementById('kb-pay-due');
        var noteInput = document.getElementById('kb-pay-note');
        var mode = '';

        if (hasClient) {
            window.V2Api.api('/clients/' + numericClientId(c.clientId) + '/balance').then(function (bal) {
                clientBalance = Math.round(bal.balance * 100);
                var line = document.getElementById('kb-pay-client');
                if (line) {
                    line.hidden = false;
                    line.innerHTML = 'Клиент «' + esc(c.client) + '» · баланс: <b>' + money(clientBalance) + ' BYN</b>' +
                        (clientBalance > 0 ? ' — аванс, можно закрыть из баланса' : clientBalance < 0 ? ' — долг клиента' : '');
                }
            }).catch(function () { /* баланс недоступен — работаем без него */ });
        }

        function useFromBalance() {
            return hasClient && document.getElementById('kb-pay-from-balance').checked;
        }
        function updateFromBalanceVisibility() {
            var wrap = document.getElementById('kb-pay-from-balance-wrap');
            if (!wrap) return;
            var applicable = hasClient && (mode === 'Оплачен' || mode === 'Частично') && clientBalance !== null && clientBalance > 0;
            wrap.hidden = !applicable;
            if (applicable) {
                wrap.querySelector('span').textContent = 'Из баланса клиента (доступно ' + money(clientBalance) + ' BYN)';
            }
        }

        dialog.querySelectorAll('.kb-pay-mode').forEach(function (b) {
            b.onclick = function () {
                mode = b.dataset.mode;
                dialog.querySelectorAll('.kb-pay-mode').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
                form.hidden = false;
                amountWrap.hidden = mode === 'Оплачен';
                dueWrap.hidden = mode !== 'Отсрочка';
                noteWrap.hidden = mode !== 'Аванс';
                fromBalanceWrap.hidden = !(hasClient && (mode === 'Оплачен' || mode === 'Частично'));
                document.getElementById('kb-pay-hint').textContent =
                    mode === 'Оплачен'
                        ? (hasClient && clientBalance > 0
                            ? 'Счёт закрывается полностью. Можно закрыть деньгами или из баланса клиента (доступно ' + money(Math.min(clientBalance, total - paid)) + ' BYN) — отметьте ниже.'
                            : 'Счёт закрывается полностью — новая оплата ' + money(total - paid) + ' BYN.')
                    : mode === 'Частично' ? 'Абсолютная оплаченная сумма; закрывающая счёт — сама отмечается «Оплачен».' :
                    mode === 'Отсрочка' ? 'Оплаченная сейчас часть (можно 0) и дата, до которой ждём остаток.' :
                    mode === 'Аванс' ? 'Деньги в кассу клиента без привязки к сделке — зачтутся при доборе.' :
                    'Будет обнулено покрытие счёта; касса клиента не меняется.';
                if (mode !== 'Оплачен') {
                    amountInput.value = (mode === 'Отсрочка' && paid > 0) || (paid > 0 && paid < total) ? money(paid) : '';
                    amountInput.focus();
                }
                updateFromBalanceVisibility();
            };
        });

        // Item #9: finish теперь async — локальное состояние меняется ТОЛЬКО
        // после успешного ответа сервера. Второй шаг (касса) — отдельно;
        // при его провале явно сообщаем, что именно не удалось.
        function finish(fields, message, cashAmountKop) {
            var submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            apiMutate('register-payment', { id: Number(c.id), fields: fields }).then(function (ok) {
                if (ok === false) {
                    if (submitBtn) submitBtn.disabled = false;
                    notify('Оплата не сохранена на сервере.');
                    return;
                }
                if (fields.paid_amount !== undefined) c.paidAmount = Math.round(fields.paid_amount * 100);
                if (fields.payment_due_date !== undefined) c.paymentDueDate = fields.payment_due_date || '';
                if (fields.payment_status !== undefined) c.payment_status = fields.payment_status;
                closeDialog(); refresh();
                notify(c.id + ' — ' + message);
                // Двухшаговая: оплата + касса. Касса — отдельный запрос.
                if (cashAmountKop > 0 && hasClient) {
                    window.V2Api.api('/clients/' + numericClientId(c.clientId) + '/payments', {
                        method: 'POST',
                        body: { amount: cashAmountKop / 100, card_id: Number(c.id), note: 'Оплата по сделке' }
                    }).catch(function (e2) {
                        notify('Оплата проведена, но касса клиента не обновилась: ' + (e2.detail || e2.message || 'ошибка'));
                    });
                }
            }).catch(function (err) {
                if (submitBtn) submitBtn.disabled = false;
                notify('Оплата не проведена: ' + (err.detail || err.message || 'ошибка'));
            });
        }
        form.onsubmit = function (e) {
            e.preventDefault();
            var err = document.getElementById('kb-pay-error');
            err.textContent = '';
            if (!mode) return;
            if (mode === 'Аванс') {
                var adv = parseMoney(amountInput.value);
                if (!Number.isSafeInteger(adv) || adv <= 0) { err.textContent = 'Укажите сумму аванса.'; return; }
                window.V2Api.api('/clients/' + numericClientId(c.clientId) + '/payments', {
                    method: 'POST',
                    body: { amount: adv / 100, card_id: Number(c.id), note: noteInput.value || 'Аванс клиента' }
                }).then(function () {
                    closeDialog(); refresh();
                    notify(c.id + ' — аванс ' + money(adv) + ' BYN в кассу клиента.');
                }).catch(function (e2) {
                    err.textContent = 'Не сохранено: ' + (e2.detail || e2.message || 'ошибка');
                });
                return;
            }
            if (mode === 'Оплачен') {
                if (total <= 0) { err.textContent = 'Укажите сумму сделки.'; return; }
                var fromB = useFromBalance();
                if (fromB && clientBalance < total - paid) { err.textContent = 'Баланса клиента не хватает (нужно ' + money(total - paid) + ' BYN).'; return; }
                var cashNow = fromB ? 0 : (total - paid);
                finish({ paid_amount: total / 100, payment_status: 'Оплачен', from_balance: fromB || undefined },
                    fromB ? 'закрыто из баланса, оплачено 100%.' : 'оплачено 100% — ' + money(total) + ' BYN.',
                    cashNow);
                return;
            }
            var amount = parseMoney(amountInput.value);
            if (mode === 'Частично') {
                if (!Number.isSafeInteger(amount) || amount <= 0) { err.textContent = 'Укажите сумму оплаты.'; return; }
                var newStatus = amount >= total - 1 ? 'Оплачен' : 'Частично';
                var fromB2 = useFromBalance();
                if (fromB2 && clientBalance < amount - paid) { err.textContent = 'Баланса клиента не хватает.'; return; }
                var cashNow2 = fromB2 ? 0 : Math.max(0, amount - paid);
                finish({ paid_amount: amount / 100, payment_status: newStatus, from_balance: fromB2 || undefined },
                    'оплачено ' + money(amount) + ' BYN' + (fromB2 ? ' из баланса' : '') + ', статус «' + newStatus + '».',
                    cashNow2);
                return;
            }
            if (mode === 'Отсрочка') {
                if (!Number.isSafeInteger(amount) || amount < 0) { err.textContent = 'Укажите оплаченную сумму (0 и более).'; return; }
                if (!dueInput.value) { err.textContent = 'Укажите дату отсрочки.'; return; }
                finish({ paid_amount: amount / 100, payment_status: 'Отсрочка', payment_due_date: dueInput.value }, 'отсрочка до ' + dueInput.value + ', оплачено ' + money(amount) + ' BYN.', 0);
                return;
            }
            if (mode === 'Не оплачен') {
                if (paid > 0) {
                    KBConfirm.ask({
                        title: 'Обнулить покрытие счёта?',
                        message: 'Оплачено ' + money(paid) + ' BYN. Касса клиента не меняется.',
                        confirmText: 'Обнулить'
                    }).then(function (ok) {
                        if (ok) finish({ paid_amount: 0, payment_status: 'Не оплачен', payment_due_date: null }, 'покрытие счёта обнулено (касса без изменений).', 0);
                    });
                } else {
                    finish({ paid_amount: 0, payment_status: 'Не оплачен', payment_due_date: null }, 'покрытие счёта обнулено (касса без изменений).', 0);
                }
            }
        };
    }

    function openIssue(c) {
        window.KBSelect.close();
        var rem = cardRemaining(c);
        var remDisplay = moneyOrDash(rem);
        dialog.innerHTML = '<h2 id="kb-dialog-title">Выписать накладную · ' + esc(c.id) + '</h2><p>Остаток: ' + remDisplay + ' BYN</p><form id="kb-issue-form" class="kb-form"><div class="kb-field"><label for="kb-date">Дата выписки</label><input id="kb-date" type="date" required></div><div class="kb-field"><label for="kb-series">Серия</label><input id="kb-series" maxlength="30" required></div><div class="kb-field"><label for="kb-number">Номер</label><input id="kb-number" maxlength="50" required></div><div class="kb-field"><label for="kb-amount">Сумма, BYN</label><input id="kb-amount" inputmode="decimal" value="' + remDisplay + '" required></div><p class="kb-form-error" id="kb-error" role="alert"></p><p class="kb-form-hint">Частичная выписка оставляет остаток в очереди. Полная — переносит в «Списано». ТН появится в финансовых документах.</p><button class="btn btn-primary" type="submit">Подтвердить выписку</button><button class="btn btn-ghost" type="button" data-close>Отмена</button></form>';
        document.getElementById('kb-date').focus();
        var submitted = false;
        document.getElementById('kb-issue-form').onsubmit = function(e) {
            e.preventDefault();
            if (submitted) return;
            var amount = parseMoney(document.getElementById('kb-amount').value);
            var series = document.getElementById('kb-series').value.trim();
            var number = document.getElementById('kb-number').value.trim();
            var date = document.getElementById('kb-date').value;
            var remVal = cardRemaining(c);
            var error = !series || !number || !date ? 'Заполните дату, серию и номер.' :
                !Number.isSafeInteger(amount) || amount <= 0 || (remVal !== null && amount > remVal) ? 'Сумма должна быть больше нуля и не превышать остаток.' :
                c.docs.some(function(d) { return d.series.toLowerCase() === series.toLowerCase() && d.number.toLowerCase() === number.toLowerCase(); }) ? 'Эта накладная уже выписана по карточке.' : '';
            if (error) { document.getElementById('kb-error').textContent = error; return; }
            document.getElementById('kb-error').textContent = '';
            submitted = true;
            // Await server response before mutating local state (item #2/#9).
            apiMutate('issue-invoice', { id: Number(c.id), number: number, date: date, amount: amount / 100 }).then(function (saved) {
                // Ответ сервера: {success, invoice_id, amount, rest, card_closed, message}
                var doc = { series: series, number: number, date: ruFromIso(date), amount: amount, originalsReturned: false };
                if (saved && saved.invoice_id) doc.txId = saved.invoice_id;
                c.docs.push(doc);
                c.issued += amount;
                // Обновляем серверные поля, если сервер их отдал
                if (saved && saved.rest !== undefined) c.remaining_kop = Math.round(saved.rest * 100);
                if (saved && saved.card_closed) c.stage = 'done';
                else if (c.remaining_kop !== null && c.remaining_kop <= 0) c.stage = 'done';
                closeDialog(); refresh();
                var nextCard = document.querySelector('#kb-queue-body [data-card="' + c.id + '"]');
                (nextCard || document.getElementById('kb-q-pending')).focus({ preventScroll: true });
                notify(c.id + (c.stage === 'done' ? ' — выписана полностью. Перенесена в «Списано».' : ' — частичная выписка. Осталось ' + moneyOrDash(cardRemaining(c)) + ' BYN.'));
            }).catch(function (err) {
                submitted = false;
                var errEl = document.getElementById('kb-error');
                if (errEl) errEl.textContent = (err && (err.detail || err.message)) || 'Не удалось выписать накладную.';
            });
        };
    }
    // Карточка открывается из любого места: клик по нативной кнопке с
    // data-card-open (содержимое плитки или финансовая строка) либо по
    // свободной области контейнера с data-card. Остальной интерактивный
    // элемент внутри строки карточку не открывает.
    document.addEventListener('click', function(e) {
        if (suppressCardClick) return;
        var openBtn = e.target.closest('[data-card-open]');
        if (openBtn) {
            var openedCard = cards.find(function(c) { return c.id === openBtn.dataset.cardOpen; });
            if (openedCard) openCard(openedCard);
            return;
        }
        var cardEl = e.target.closest('[data-card]');
        if (!cardEl) return;
        var interactive = e.target.closest('input, select, textarea, button, label, a, summary');
        if (interactive && interactive !== cardEl && cardEl.contains(interactive)) return;
        var card = cards.find(function(c) { return c.id === cardEl.dataset.card; });
        if (card) openCard(card);
    });
    // Фидбек 20.09: удаление прямо с плитки. Открытие карточки не мешает:
    // кнопка отсекается guard'ом interactive в обработчике выше.
    document.addEventListener('click', function(e) {
        var delBtn = e.target.closest('[data-card-del]');
        if (!delBtn) return;
        var card = cards.find(function(c) { return c.id === delBtn.dataset.cardDel; });
        if (!card) return;
        KBConfirm.ask({
            title: 'Удалить карточку?',
            message: '«' + (card.title || card.id) + '» уйдёт в корзину — восстановление через администратора.'
        }).then(function (ok) {
            if (ok) deleteCard(card, null);
        });
    });
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var cardEl = e.target.closest ? e.target.closest('[data-card][role="button"]') : null;
        if (!cardEl) return;
        // Клавиша на контроле ВНУТРИ строки — её родное действие, а не
        // открытие: preventDefault ниже глушил и клик по кнопке удаления с
        // плитки, и пробел галочки выборки в очереди, и активацию «Убрать из
        // списания». Набор по плитке/строке при этом остаётся — он идёт с
        // самого контейнера, у которого этих тегов нет.
        var inner = e.target.closest ? e.target.closest('input, select, textarea, button, a, label, summary') : null;
        if (inner && inner !== cardEl) return;
        e.preventDefault();
        var card = cards.find(function(c) { return c.id === cardEl.dataset.card; });
        if (card) openCard(card);
    });
    dialog.addEventListener('click', function(e) {
        var editStart = e.target.closest('[data-doc-edit]');
        if (editStart && activeCard) {
            editingDoc = Number(editStart.dataset.docEdit);
            openCard(activeCard);
            return;
        }
        if (e.target.closest('[data-doc-edit-cancel]') && activeCard) {
            editingDoc = null;
            openCard(activeCard);
            return;
        }
        var editSave = e.target.closest('[data-doc-edit-save]');
        if (editSave && activeCard) {
            var docIndex = Number(editSave.dataset.docEditSave);
            var doc = activeCard.docs[docIndex];
            if (!doc) return;
            var iso = dialog.querySelector('[data-doc-field="date"]').value;
            var number = dialog.querySelector('[data-doc-field="number"]').value.trim();
            var amountKop = parseMoney(dialog.querySelector('[data-doc-field="amount"]').value);
            var errorEl = document.getElementById('kb-doc-edit-error');
            if (!iso || !number || !amountKop || amountKop <= 0) {
                if (errorEl) errorEl.textContent = 'Заполните дату, номер и положительную сумму.';
                return;
            }
            var applyEdit = function () {
                doc.date = ruFromIso(iso);
                doc.number = number;
                doc.amount = amountKop;
                editingDoc = null;
                refresh();
                openCard(activeCard);
            };
            if (doc.txId) {
                editSave.disabled = true;
                apiMutate('invoice-edit', { txId: doc.txId, isoDate: iso, number: number, amountByn: amountKop / 100 }).then(function (ok) {
                    if (ok === false) {
                        editSave.disabled = false;
                        if (errorEl) errorEl.textContent = 'CRM не приняла правку.';
                        return;
                    }
                    applyEdit();
                });
            } else {
                applyEdit();
            }
            return;
        }
        var cancel = e.target.closest('[data-doc-cancel]');
        if (cancel && activeCard) {
            // Как на проде: отмена документа возвращает остаток; «Списано»
            // держится только на покрытии суммы выписанными ТН.
            if (!cancel.dataset.armed) {
                dialog.querySelectorAll('[data-doc-cancel][data-armed]').forEach(function (other) {
                    delete other.dataset.armed;
                    other.textContent = other.dataset.label || 'Отменить';
                });
                // Надпись разная у отмены ТН и у удаления осиротевшего
                // документа группы — возвращать надо именно её.
                cancel.dataset.label = cancel.textContent;
                cancel.dataset.armed = '1';
                cancel.textContent = 'Точно отменить?';
                return;
            }
            var target = cancel.dataset.docCancel;
            if (target.indexOf('group-') === 0) {
                var gid = target.slice(6);
                var group = KBData.groups.filter(function (x) { return x.id === gid; })[0];
                if (!group) return;
                var doCancelGroupLocal = function () {
                    group.covers.forEach(function (cov) {
                        var card = cards.find(function (x) { return x.id === cov.cardId; });
                        if (!card) return;
                        card.groupId = null;
                        // Разбивку группы по карточкам сервер не отдаёт
                        // (covers[].amount === null в boot), поэтому вычитать
                        // cov.amount не из чего — деньги берём из оставшихся
                        // docs, иначе карточка терялась в очереди (дефект 7).
                        recalcCardMoney(card);
                        var w = writeoffStatus();
                        if (hasRemaining(card) && card.stage === 'done' && w) card.stage = w.id;
                    });
                    KBData.groups.splice(KBData.groups.indexOf(group), 1);
                    refresh();
                    notify('Групповая ТН ' + group.series + ' ' + group.number + ' отменена. Остатки возвращены ' + group.covers.length + ' карточкам.');
                    openCard(activeCard);
                    var groupInvoicesTab = document.getElementById('kb-tab-invoices');
                    if (groupInvoicesTab) groupInvoicesTab.click();
                };
                var groupLabel = ((group.series ? group.series + ' ' : '') + (group.number || '')).trim() || 'без номера';
                // Кнопка уже «взведена» (data-armed): на время запроса гасим её,
                // а при отказе возвращаем в исходное — повторить одним кликом.
                var disarmCancel = function () {
                    cancel.disabled = false;
                    delete cancel.dataset.armed;
                    cancel.textContent = cancel.dataset.label || 'Отменить';
                };
                // P0-хотфикс 23.09 (часть C): выписанную группу разматывает
                // только серверный POST /writeoffs/groups/{id}/annul. Прежний
                // annul-tx (DELETE /payments/transactions/{id}) по документу
                // группы с card_id=NULL не снимал written_off и не возвращал
                // карточки из «Закрыто», а UI рапортовал «остатки возвращены»
                // (дефект 2 реестра V2-WORKPLAN-2026-09-22).
                if (group.writtenOff && group.serverId) {
                    if (!window.V2Api || !window.V2Api.api) {
                        notify('Отмена групповой ТН недоступна: CRM API не подключён.', true);
                        return;
                    }
                    cancel.disabled = true;
                    window.V2Api.api('/writeoffs/groups/' + group.serverId + '/annul', { method: 'POST' })
                        .then(function (unwound) {
                            // Число карточек — из ответа сервера (он разматывал).
                            var count = (unwound && unwound.cards && unwound.cards.length) || group.covers.length;
                            // Источник правды — сервер: локальный пересчёт
                            // остатков не дублируем, перечитываем данные и
                            // рапортуем только после успешного перечитывания.
                            reloadData()
                                .then(function () {
                                    notify('Групповая ТН ' + groupLabel + ' отменена, остатки возвращены ' + count + ' карточкам.');
                                    var groupInvoicesTab = document.getElementById('kb-tab-invoices');
                                    if (groupInvoicesTab) groupInvoicesTab.click();
                                })
                                .catch(function (e2) {
                                    refresh();
                                    notify('Групповая ТН ' + groupLabel + ' отменена на сервере, но данные не обновились: ' + ((e2 && (e2.detail || e2.message)) || 'ошибка'), true);
                                });
                        })
                        .catch(function (err) {
                            disarmCancel();
                            var status = err && err.status;
                            // Причина сервера идёт в середину русской фразы —
                            // конечную пунктуацию срезаем, чтобы не было «..».
                            var reason = String((err && (err.detail || err.message)) || 'ошибка').replace(/[.!?]+$/, '');
                            // 403 — следствие матрицы ролей (пункт 17): отмена
                            // групповой ТН админская, как и одиночная. Не обходим
                            // и не маскируем: без прав состояние не меняется.
                            if (status === 403) {
                                notify('Отмена групповой ТН доступна только администратору.', true);
                                return;
                            }
                            // 400/404 — клиент разошёлся с сервером (группу уже
                            // отменили, или она не закрыта накладной, или её
                            // нет): перечитываем данные, локально не гадаем.
                            if (status === 400 || status === 404) {
                                notify('Отмена групповой ТН не выполнена: ' + reason + '. Данные перечитаны с сервера.', true);
                                reloadData().catch(function () { refresh(); });
                                return;
                            }
                            // Сеть/401: отмена не подтверждена — данные не трогаем.
                            notify('Отмена групповой ТН не прошла: ' + reason + '. Данные не изменились.', true);
                        });
                    return;
                }
                if (group.writtenOff) {
                    // Выписанная группа без serverId (штатно не случается: boot
                    // и issueGroup его дают). Локально разматывать нельзя —
                    // сервер останется закрытым, а UI соврёт про остатки.
                    notify('Отмена групповой ТН доступна после обновления страницы.', true);
                    return;
                }
                // Фолбэк: одиночный документ группы БЕЗ written_off — annul-tx
                // бьёт точно по нему (serverTxId после части A — id документа).
                if (group.serverTxId) {
                    apiMutate('annul-tx', { txId: group.serverTxId }).then(function (ok) {
                        if (ok === false) { notify('Отмена группы не прошла на сервере — обновите страницу.'); return; }
                        // Группа остаётся на сервере (written_off не менялся,
                        // карточки в ней) — перечитываем, а не вычёркиваем
                        // группу из локального кэша.
                        reloadData()
                            .then(function () { notify('Документ группы ' + groupLabel + ' удалён. Данные перечитаны с сервера.'); })
                            .catch(function () {
                                refresh();
                                notify('Документ группы ' + groupLabel + ' удалён, но данные не обновились.', true);
                            });
                    }).catch(function (err) {
                        notify('Отмена группы не прошла: ' + (err.detail || err.message || 'ошибка'));
                    });
                } else if (group.serverId) {
                    // Группа есть на сервере, но не закрыта накладной и своего
                    // документа не имеет — разматывать на сервере нечего.
                    // Локально вычёркивать группу нельзя: сервер её хранит,
                    // после перечитывания она вернётся (ложная кнопка,
                    // дефект 2 реестра). Перечитывание лечит расхождение.
                    reloadData()
                        .then(function () { notify('Групповая ТН не выписана — отменять нечего. Данные перечитаны с сервера.', true); })
                        .catch(function () {
                            refresh();
                            notify('Групповая ТН не выписана — отменять нечего.', true);
                        });
                } else {
                    // Ни serverId, ни записи-документа у группы нет — сервер о ней
                    // не знает: такая группа собрана локально и живёт только в
                    // предпросмотре мокапа (на бою id группы даёт boot из
                    // /writeoffs/groups/, а issueGroup — id документа). Локальная
                    // отмена безопасна: затирать на сервере нечего.
                    doCancelGroupLocal();
                }
                return;
            }
            var index = Number(target);
            var doc = activeCard.docs[index];
            if (!doc) return;
            // Await server before mutating (item #9 — откат при ошибке).
            var doCancelLocal = function () {
                activeCard.docs.splice(index, 1);
                // Пересчёт — уже после splice: отменённой записи в docs нет.
                recalcCardMoney(activeCard);
                var writeoff = writeoffStatus();
                if (activeCard.stage === 'done' && hasRemaining(activeCard) && writeoff) activeCard.stage = writeoff.id;
                refresh();
                notify(activeCard.id + ' — ТН ' + doc.series + ' ' + doc.number + ' отменена. Остаток к выписке ' + moneyOrDash(cardRemaining(activeCard)) + ' BYN.');
                openCard(activeCard);
                var invoicesTab = document.getElementById('kb-tab-invoices');
                if (invoicesTab) invoicesTab.click();
            };
            if (doc.txId) {
                apiMutate('annul-tx', { txId: doc.txId }).then(function (ok) {
                    if (ok === false) { notify('Отмена не прошла на сервере — обновите страницу.'); return; }
                    doCancelLocal();
                }).catch(function (err) {
                    notify('Отмена не прошла: ' + (err.detail || err.message || 'ошибка'));
                });
            } else {
                doCancelLocal();
            }
            return;
        }
        // Дефект 5 реестра V2-WORKPLAN-2026-09-22: состав группы —
        // прикрепиться, выйти, распустить. Разметка пересобирается на каждом
        // openCard, поэтому обработчик один и делегирующий.
        var join = e.target.closest('[data-group-join]');
        if (join && activeCard) { joinCardToGroup(activeCard); return; }
        var leave = e.target.closest('[data-group-leave]');
        if (leave && activeCard) { leaveGroup(activeCard, leave); return; }
        var dissolve = e.target.closest('[data-group-disband]');
        if (dissolve && activeCard) { disbandGroup(activeCard, dissolve); return; }
        if (e.target.closest('[data-close]')) closeDialog();
    });
    dialog.addEventListener('input', function(e) {
        if (e.target.matches('[data-deal-field]')) e.target.setCustomValidity('');
        if (activeCard && !renderingCard && e.target.matches('input[data-payment-field]')) {
            window.KBPayment.draft(activeCard)[e.target.dataset.paymentField] = e.target.value;
            updatePayment(activeCard);
        }
    });
    dialog.addEventListener('change', function(e) {
        var input = e.target;
        // Replacing a focused input can emit change while the drawer DOM is removed.
        // Its draft has already been captured by openCard; do not re-enter rendering.
        if (!activeCard || renderingCard) return;
        if (input.matches('[data-payment-field]')) {
            window.KBPayment.draft(activeCard)[input.dataset.paymentField] = input.value;
            if (input.dataset.paymentField === 'mode') {
                renderPayment(activeCard, false);
                window.KBSelect.focus(dialog.querySelector('#kb-payment-mode'));
            } else updatePayment(activeCard);
            return;
        }
        if (input.matches('[data-deal-field="paymentTerms"]')) {
            activeCard.paymentTerms = input.value;
            window.KBPayment.draft(activeCard).terms = input.value;
            renderPayment(activeCard, true);
            return;
        }
        if (input.matches('[data-originals]')) {
            // Item #7: пишем флаг на сервер (doc-flag → is_invoice_doc).
            var docIdx = Number(input.dataset.originals);
            var doc = activeCard.docs[docIdx];
            if (!doc) return;
            var prev = doc.originalsReturned;
            doc.originalsReturned = input.checked;
            if (doc.txId && window.KBData.mutate) {
                window.KBData.mutate('doc-flag', { field: 'tnHere', txId: doc.txId, checked: input.checked }).then(function (ok) {
                    if (ok === false) {
                        doc.originalsReturned = prev;
                        input.checked = prev;
                        notify('Флаг не сохранён на сервере.');
                    }
                }).catch(function () {
                    doc.originalsReturned = prev;
                    input.checked = prev;
                    notify('Флаг не сохранён: ошибка сети.');
                });
            }
            // Флаг документа изменился — строка ТН в финансах должна обновиться.
            syncCard(activeCard, { documents: true });
            return;
        }
        if (input.matches('[data-deal-field]')) {
            var key = input.dataset.dealField;
            var value = input.type === 'checkbox' ? input.checked : input.value.trim();
            var error = '';
            if (key === 'amount') {
                value = parseMoney(value);
                if (activeCard.stage === 'done') return;
                if (!Number.isSafeInteger(value) || value <= 0 || value < cardIssued(activeCard) || (cardIssued(activeCard) > 0 && value === cardIssued(activeCard))) error = 'Сумма должна быть больше нуля и уже выписанной суммы.';
            } else if (key === 'title' && !value) error = 'Заполните название.';
            // Item #5: клиент может быть пустым (= не указан), валидация не нужна.
            else if (key === 'deadline') {
                var parts = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(value);
                var date = parts && new Date(Number(parts[3]), Number(parts[2]) - 1, Number(parts[1]));
                if (!parts || date.getFullYear() !== Number(parts[3]) || date.getMonth() !== Number(parts[2]) - 1 || date.getDate() !== Number(parts[1])) error = 'Введите дату в формате ДД.ММ.ГГГГ.';
            } else if (key === 'sender_email') {
                // Пусто — «адрес не указан», это не ошибка. Проверяем сами, а не
                // только нативной валидацией type="email": сообщение должно быть
                // по-русски, как у соседних полей.
                if (value && !/^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/.test(value)) error = 'Укажите адрес почты, например name@example.by.';
            } else if (key === 'manager') {
                // Item #4: сопоставление по id, не по индексу массива.
                // Пустое значение или 'us-none' = менеджер не назначен.
                if (!value || value === 'us-none') value = null;
                else value = KBData.users.find(function (u) { return u.id === value; }) || null;
            }
            else if (key === 'stage') {
                // Закрытая и полностью выписанная карточка — readonly. Статуса
                // вне справочника и неканонического (canAssign=false, Р4) сервер
                // не примет: селект уже отфильтрован, это защита прямого пути.
                var targetStatus = KBData.statuses.filter(function(st) { return st.id === value; })[0];
                if (!targetStatus || activeCard.stage === 'done' || activeCard.stage === 'closed') return;
                if (targetStatus.canAssign === false) { notify('Статус не из справочника карточек: сервер его не примет', true); return; }
                moveCard(activeCard, value, null);
            }
            input.setCustomValidity(error);
            input.setAttribute('aria-invalid', String(!!error));
            if (error) { input.reportValidity(); return; }
            if (key === 'client') {
                // Item #5: пустое значение = клиент не указан; client_id
                // не отправляется. Создание нового — только явным действием.
                if (!value.trim()) {
                    activeCard.clientId = null;
                    activeCard.client = '';
                    apiMutate('card', { id: Number(activeCard.id), fields: { client_id: null } });
                    syncCard(activeCard, { documents: true });
                    return;
                }
                var match = KBData.clients.filter(function (cl) {
                    return cl.name && cl.name.toLowerCase() === value.trim().toLowerCase();
                })[0];
                if (match) {
                    activeCard.clientId = match.id;
                    activeCard.client = match.name;
                    apiMutate('card', { id: Number(activeCard.id), fields: { client_id: numericClientIdValue(match.id) } });
                    syncCard(activeCard, { documents: true });
                } else {
                    // Клиента нет в справочнике — НЕ создаём побочно.
                    // Возвращаем поле к предыдущему значению.
                    notify('Клиента «' + value.trim() + '» нет в справочнике. Создайте его в разделе «Клиенты» и выберите здесь.');
                    input.value = activeCard.clientId ? (activeCard.client || '') : '';
                }
                return;
            }
            activeCard[key] = value;
            if (key === 'title') apiMutate('card', { id: Number(activeCard.id), fields: { title: value } });
            else if (key === 'note') apiMutate('card', { id: Number(activeCard.id), fields: { description: value } });
            else if (key === 'amount') apiMutate('card', { id: Number(activeCard.id), fields: { total_amount: value / 100 } });
            else if (key === 'deadline') apiMutate('card', { id: Number(activeCard.id), fields: { due_date: isoFromRu(value) } });
            else if (key === 'sender_email') {
                // Пустое значение = «очистить адрес»: колонка nullable
                // (models.py:129), поле есть и в CardUpdate, и в CardResponse.
                apiMutate('card', { id: Number(activeCard.id), fields: { sender_email: value || null } });
            }
            else if (key === 'store') {
                // Item #6: отправляем store_location только если пользователь
                // действительно выбрал значение; пустое = 'не указан'.
                var storeName = value ? KBData.storeName(value) : '';
                apiMutate('card', { id: Number(activeCard.id), fields: { store_location: storeName || null } });
            }
            else if (key === 'manager') {
                // Item #4: owner_id по user.id, а не по индексу массива.
                var ownerId = value ? (Number(String(value.id).replace('us-', '')) || null) : null;
                apiMutate('card', { id: Number(activeCard.id), fields: { owner_id: ownerId } });
            }
            else if (key === 'stage') apiMutate('status', { id: Number(activeCard.id), status: stageName(value) });
            // Переезд в другую колонку точечно не обновить: плитка должна
            // сменить колонку и место в порядке — тут полная перерисовка.
            if (key === 'stage') refresh();
            else syncCard(activeCard, { documents: true });
            if (key === 'stage' || key === 'amount') {
                openCard(activeCard);
                var control = dialog.querySelector('[data-deal-field="' + key + '"]');
                if (!window.KBSelect.focus(control)) control.focus({ preventScroll: true });
            } else if (key === 'title') document.getElementById('kb-dialog-title').textContent = value;
            if (key === 'sender_email') {
                // Письма в «Истории» подобраны по старому адресу — снимок
                // больше не действителен. При закрытой вкладке запрос не
                // дёргаем: loadRelatedEmails стартует при открытии «Истории»,
                // пустого кэша достаточно, чтобы список собрался по новому
                // адресу самому (и пустой адрес уйдёт в подсказку без GET).
                // Сброс поднимает поколение: ответ по прежнему адресу,
                // заставший смену почты в полёте, больше не попадёт в кэш.
                emailsSnapshotReset(activeCard);
                if (dialog.dataset.cardTab === 'history') loadRelatedEmails(activeCard, true);
            }
            return;
        }
        if (!input.matches('[data-check]')) return;
        var item = activeCard.checklist[Number(input.dataset.check)];
        item[input.dataset.flag] = input.checked;
        var index = input.dataset.check, flag = input.dataset.flag;
        // Фидбек 18.09: статус закупки сохраняется в CRM
        // (ordered/received в card_checklists), а не только на экране.
        var rawId = String(item.id).replace(/[^0-9]/g, '');
        if (rawId && window.V2Api && window.V2Api.token()) {
            var body = {};
            body[input.dataset.flag] = input.checked;
            window.V2Api.api('/checklists/' + rawId, { method: 'PATCH', body: body })
                .catch(function (e2) { notify('Статус закупки не сохранён: ' + (e2.detail || e2.message || 'ошибка')); });
        }
        // Флаги ordered/received финансы и календарь не читают — kb:documents
        // на галочку закупки не шлём, плитка и очередь обновляются точечно.
        openCard(activeCard); syncCard(activeCard);
        // Item #12: focus safe — после перерендера элемент мог исчезнуть.
        var focusTarget = dialog.querySelector('[data-check="' + index + '"][data-flag="' + flag + '"]');
        if (focusTarget) focusTarget.focus();
    });
    document.getElementById('kb-q-board').onclick = function() { markViewDirty(); queueMode('board'); };
    document.getElementById('kb-q-pending').onclick = function() { markViewDirty(); queueMode('pending'); };
    document.getElementById('kb-q-history').onclick = function() { markViewDirty(); queueMode('history'); };
    var listModeBtn = document.getElementById('kb-q-list');
    if (listModeBtn) listModeBtn.onclick = function() { markViewDirty(); queueMode('list'); };
    var archiveModeBtn = document.getElementById('kb-q-archive');
    if (archiveModeBtn) archiveModeBtn.onclick = function() { markViewDirty(); queueMode('archive'); };
    // ---------- Создание сделки с доски ----------
    // Набор полей — как в форме рабочей версии (site/js/kanban.js): название,
    // сумма, клиент, магазин, срок, приоритет. Статус всегда «Новый запрос»,
    // owner_id не шлём (сервер подставляет текущего пользователя), а созданная
    // сделка не открывается — доска просто перечитывает данные (решение 22.09).
    var NC_PRIORITY = [[0, 'Без приоритета'], [1, 'Низкий'], [2, 'Средний'], [3, 'Высокий']];
    var newCardDialog = null;
    var newCardOpener = null;
    function ensureNewCardDialog() {
        if (newCardDialog) return newCardDialog;
        newCardDialog = document.createElement('dialog');
        newCardDialog.id = 'kb-new-card-dialog';
        newCardDialog.className = 'v2-form-dialog';
        newCardDialog.setAttribute('aria-labelledby', 'kb-nc-heading');
        newCardDialog.addEventListener('close', function () {
            if (window.KBSelect) window.KBSelect.close();
            if (newCardOpener && newCardOpener.isConnected) newCardOpener.focus();
            newCardOpener = null;
            // Форма чистится на закрытии: скрытые required-поля не должны
            // блокировать следующий сабмит (приём из формы клиента).
            newCardDialog.innerHTML = '';
        });
        document.body.append(newCardDialog);
        return newCardDialog;
    }
    function openNewCardForm(opener) {
        if (!window.V2Api || !window.V2Api.token() || !window.KBData.mutate) {
            notify('Создание сделок доступно только при подключении к CRM.', true);
            return;
        }
        var dlg = ensureNewCardDialog();
        newCardOpener = opener || null;
        // В payload магазина уходит ИМЯ: cards.store_location — строка, а не id.
        dlg.innerHTML = '<form id="kb-nc-form"><h2 id="kb-nc-heading">Новая сделка</h2>' +
            '<div class="mgmt-fields">' +
            '<label for="kb-nc-title" class="mgmt-span"><span>Название*</span>' +
            '<input id="kb-nc-title" name="title" type="text" maxlength="200" required autocomplete="off"></label>' +
            '<label for="kb-nc-amount"><span>Сумма, BYN</span>' +
            '<input id="kb-nc-amount" name="total_amount" type="text" inputmode="decimal" placeholder="0,00" autocomplete="off"></label>' +
            '<label for="kb-nc-due"><span>Срок</span><input id="kb-nc-due" name="due_date" type="date"></label>' +
            '<label for="kb-nc-client"><span>Клиент</span><select id="kb-nc-client" name="client_id">' +
            '<option value="">— не привязан —</option>' +
            KBData.clients.map(function (cl) { return '<option value="' + esc(cl.id) + '">' + esc(cl.name) + '</option>'; }).join('') +
            '</select></label>' +
            '<label for="kb-nc-store"><span>Магазин</span><select id="kb-nc-store" name="store_location">' +
            '<option value="">— не выбран —</option>' +
            KBData.stores.map(function (s) { return '<option value="' + esc(s.name) + '">' + esc(s.name) + '</option>'; }).join('') +
            '</select></label>' +
            '<label for="kb-nc-priority"><span>Приоритет</span><select id="kb-nc-priority" name="priority">' +
            NC_PRIORITY.map(function (p) { return '<option value="' + p[0] + '">' + p[1] + '</option>'; }).join('') +
            '</select></label></div>' +
            '<p id="kb-nc-error" class="kb-form-error" role="alert"></p>' +
            '<div class="mgmt-actions"><button class="btn btn-ghost" type="button" data-nc-cancel>Отмена</button>' +
            '<button class="btn btn-primary" type="submit" id="kb-nc-submit">Создать</button></div></form>';
        var errorEl = dlg.querySelector('#kb-nc-error');
        var submitBtn = dlg.querySelector('#kb-nc-submit');
        // Ошибка показывается в форме, а не alert'ом: фокус остаётся на поле.
        function fieldError(input, message) {
            errorEl.textContent = message;
            dlg.querySelectorAll('[aria-invalid="true"]').forEach(function (el) { el.removeAttribute('aria-invalid'); });
            if (input) { input.setAttribute('aria-invalid', 'true'); input.focus(); }
        }
        dlg.querySelector('[data-nc-cancel]').addEventListener('click', function () { dlg.close(); });
        dlg.querySelector('#kb-nc-form').addEventListener('submit', function (e) {
            e.preventDefault();
            errorEl.textContent = '';
            var titleInput = dlg.querySelector('#kb-nc-title');
            var amountInput = dlg.querySelector('#kb-nc-amount');
            var title = titleInput.value.trim();
            if (!title) { fieldError(titleInput, 'Название обязательно.'); return; }
            var amountKop = 0;
            if (amountInput.value.trim()) {
                amountKop = parseMoney(amountInput.value);
                // Отрицательную сумму сервер отвергает (schemas.CardBase) — 422.
                if (amountKop === null || amountKop < 0) {
                    fieldError(amountInput, 'Сумма — число в BYN, не меньше нуля. Например: 1 250,00');
                    return;
                }
            }
            submitBtn.disabled = true;
            apiMutate('card-create', {
                title: title,
                status: 'Новый запрос',
                total_amount: amountKop / 100,
                priority: Number(dlg.querySelector('#kb-nc-priority').value) || 0,
                client_id: numericClientIdValue(dlg.querySelector('#kb-nc-client').value),
                store_location: dlg.querySelector('#kb-nc-store').value || null,
                due_date: dlg.querySelector('#kb-nc-due').value || null
            }).then(function (created) {
                submitBtn.disabled = false;
                // null — in-flight guard: такой сабмит уже уходит, ждём его.
                if (!created) return;
                if (created.error) { fieldError(null, 'Не создано: ' + created.error); return; }
                dlg.close();
                reloadData().then(function () {
                    notify('Сделка создана');
                }).catch(function (err) {
                    refresh();
                    notify('Сделка создана, но доска не обновилась: ' + (err.detail || err.message || 'ошибка'), true);
                });
            }).catch(function (err) {
                submitBtn.disabled = false;
                fieldError(null, 'Не создано: ' + (err.detail || err.message || 'ошибка'));
            });
        });
        dlg.showModal();
        if (window.KBSelect) window.KBSelect.enhance(dlg);
        dlg.querySelector('#kb-nc-title').focus();
    }
    // Кнопки тулбара может не оказаться (устаревшая разметка) — не падаем.
    var newCardBtn = document.getElementById('kb-new-card');
    if (newCardBtn) newCardBtn.addEventListener('click', function () { openNewCardForm(newCardBtn); });
    var trashBtn = document.getElementById('kb-q-trash');
    if (trashBtn) trashBtn.onclick = function() { markViewDirty(); queueMode('trash'); };
    // Делегирование для панели группы: чекбоксы пересобираются при каждой отрисовке.
    document.getElementById('kb-queue-body').addEventListener('click', function (e) {
        var restore = e.target.closest('[data-trash-restore]');
        var purge = e.target.closest('[data-trash-purge]');
        if (restore) {
            window.V2Api.api('/kanban/cards/' + restore.dataset.trashRestore + '/restore', { method: 'PATCH' })
                .then(function () {
                    trashCache = trashCache.filter(function (c) { return String(c.id) !== restore.dataset.trashRestore; });
                    renderTrash();
                    // Item #10: карточка возвращается на доску перечитыванием
                    // данных (KBData.reload → kb:reloaded → refresh) — отдельный
                    // GET карточки и ручная вставка в массив больше не нужны.
                    reloadData()
                        .then(function () { notify('Карточка восстановлена из корзины.'); })
                        .catch(function (e2) {
                            refresh();
                            notify('Карточка восстановлена, но данные не обновились: ' + (e2.detail || e2.message || 'ошибка'), true);
                        });
                })
                .catch(function (e2) { notify('Не восстановлено: ' + (e2.detail || e2.message || 'ошибка')); });
        } else if (purge) {
            KBConfirm.ask({
                title: 'Удалить карточку НАВСЕГДА?',
                message: 'Восстановить будет невозможно.'
            }).then(function (ok) {
                if (!ok) return;
                window.V2Api.api('/kanban/cards/' + purge.dataset.trashPurge + '/permanent', { method: 'DELETE' })
                    .then(function () { trashCache = trashCache.filter(function (c) { return String(c.id) !== purge.dataset.trashPurge; }); renderTrash(); notify('Карточка удалена навсегда.'); })
                    .catch(function (e2) { notify('Не удалено: ' + (e2.detail || e2.message || 'ошибка')); });
            });
        }
    });
    document.getElementById('kb-queue-body').addEventListener('change', function (e) {
        var pick = e.target.closest('[data-group-pick]');
        if (pick) {
            if (pick.checked) groupPick.push(pick.dataset.groupPick);
            else groupPick = groupPick.filter(function (id) { return id !== pick.dataset.groupPick; });
            renderQueue();
            return;
        }
        if (e.target.id === 'kb-group-all') {
            groupPick = e.target.checked ? visibleCards().filter(function (c) { return c.stage === 'writeoff' && hasRemaining(c); }).map(function (c) { return c.id; }) : [];
            renderQueue();
        }
    });
    document.getElementById('kb-queue-body').addEventListener('click', function (e) {
        if (e.target.closest('#kb-group-issue')) openGroupIssue();
        var rowDrop = e.target.closest('[data-row-unwriteoff]');
        if (rowDrop) {
            var one = cards.find(function (c) { return c.id === rowDrop.dataset.rowUnwriteoff; });
            if (one) dropFromWriteoff([one]);
            return;
        }
        if (e.target.closest('#kb-q-unwriteoff')) dropFromWriteoff(pickedCards());
    });
    // Дефект 4 реестра V2-WORKPLAN-2026-09-22: массовое «убрать сделку из
    // списания» (legacy — кнопка на каждой плитке доски списания,
    // site/js/writeoffs.js:389-420). В v2 выбор уже есть в очереди
    // («kb-q-pick»), поэтому действие вешается на него, а не на дровер:
    // в дровере и так поштучная отмена ТН, а выбор в очереди покрывает ровно
    // те карточки, которые лежат «На списание» с остатком.
    // Роут DELETE /payments/cards/{card_id}/writeoff сносит ВСЁ по сделке:
    // запись-остаток, выписанные накладные и их копии в «Документах», и
    // возвращает карточку в «Сборку». Закрыт ролью admin — кнопка под
    // data-admin-only. Отказы (карточка в группе — 400, карточки нет — 404)
    // считаются поштучно: рапортуем честно, сколько ушло и сколько нет.
    var dropPending = false;
    function pickedCards() {
        return groupPick.map(function (id) { return cards.find(function (c) { return c.id === id; }); }).filter(Boolean);
    }
    function dropFromWriteoff(picks) {
        if (dropPending) { notify('Предыдущее удаление из списания ещё не закончено.', true); return; }
        picks = (picks || []).filter(Boolean);
        if (!picks.length) { notify('Сначала отметьте сделки в очереди.', true); return; }
        if (!window.V2Api || !window.V2Api.token()) { notify('Действие доступно при подключении к CRM.', true); return; }
        var one = picks.length === 1;
        var named = picks.map(function (c) { return c.id + ' · ' + c.title; });
        var listed = named.slice(0, 6).join('; ') + (named.length > 6 ? '; ещё ' + (named.length - 6) : '');
        var grouped = picks.filter(function (c) { return c.groupId; }).map(function (c) { return c.id; });
        // Текст подтверждения — по образцу legacy (site/js/writeoffs.js:395-397),
        // но с перечислением сделок: стираются документы, а не гасится галочка,
        // и масштаб отказа («сделки в группе») виден до нажатия.
        var message = (one
            ? 'Сделка ' + picks[0].id + ' будет убрана из списания: все её накладные и их копии в' +
              ' «Документах» будут удалены, сама сделка вернётся в «' + assemblyStageName() + '».'
            : 'Будут убраны из списания ' + picks.length + ' ' + pluralRu(picks.length, ['сделка', 'сделки', 'сделок']) +
              ' (' + listed + '): все их накладные и их копии в «Документах» будут удалены,' +
              ' сделки вернутся в «' + assemblyStageName() + '».');
        if (grouped.length) message += ' В группе состоят: ' + grouped.join(', ') + ' — сервер их не примет,' +
            ' пока карточку не вывести из группы («Накладные» → «Выйти из группы»).';
        message += ' Назад это возвращается только повторной выпиской ТН.';
        dropPending = true;                      // второй клик, пока открыт диалог, не нужен
        KBConfirm.ask({
            title: one ? 'Убрать сделку из списания?' : 'Убрать сделки из списания?',
            message: message,
            confirmText: 'Убрать'
        }).then(function (ok) {
            if (!ok) { dropPending = false; return; }
            var button = document.getElementById('kb-q-unwriteoff');
            if (button) button.disabled = true;
            var dropped = 0, docsRemoved = 0, failed = [];
            var next = function (i) {
                if (i >= picks.length) { finish(); return; }
                var card = picks[i];
                window.V2Api.api('/payments/cards/' + Number(card.id) + '/writeoff', { method: 'DELETE' })
                    .then(function (res) {
                        dropped++;
                        docsRemoved += (res && res.documents_deleted) || 0;
                    })
                    .catch(function (err) { failed.push(card.id + ' — ' + reasonText(err)); })
                    .then(function () { next(i + 1); });
            };
            var finish = function () {
                dropPending = false;
                // Из выборки убираем только обработанные карточки: выборка
                // общая с «Накладной на группу», сбрасывать её целиком — значит
                // молча потерять план другой операции.
                var doneIds = {};
                picks.forEach(function (c) { doneIds[c.id] = true; });
                groupPick = groupPick.filter(function (id) { return !doneIds[id]; });
                var head = one
                    ? (dropped ? 'Сделка ' + picks[0].id + ' убрана из списания' : 'Сделка ' + picks[0].id + ' не убрана из списания')
                    : 'Убрано из списания ' + dropped + ' ' + pluralRu(dropped, ['сделки', 'сделок', 'сделок']) +
                      ' из ' + picks.length;
                if (dropped) head += ' (копий ТН удалено: ' + docsRemoved + ')';
                var tail = failed.length ? '. Отказ: ' + failed.slice(0, 3).join('; ') + (failed.length > 3 ? '; ещё ' + (failed.length - 3) : '') : '';
                // Источник правды — сервер: статусы, остатки и состав
                // «Документов» меняются сразу у нескольких карточек.
                reloadData()
                    .then(function () { notify(head + tail + '.', failed.length > 0); })
                    .catch(function (err) {
                        refresh();
                        notify(head + tail + '. Данные не обновились: ' + reasonText(err) + '.', true);
                    });
            };
            next(0);
        });
    }
    // ---------- Состав группы: прикрепиться / выйти / распустить ----------
    // Дефект 5 реестра V2-WORKPLAN-2026-09-22. Контракты и гейты прочитаны в
    // server_snapshot/routers/writeoff_groups_router.py:
    //   attach  POST   /writeoffs/groups/{group}/cards/{card}  (:104) → группа
    //   detach  DELETE /writeoffs/groups/{group}/cards/{card}  (:129) → группа
    //           или {"detail":"Группа распущена"}, когда ушла последняя карточка
    //   disband DELETE /writeoffs/groups/{group}               (:280) → {"detail":…}
    // Прикрепление и выход — рабочая операция менеджера, они идут с общим
    // dependencies=[Depends(get_current_user)] и остаются без метки. Роспуск
    // закрыт require_role("admin","superadmin") (решение владельца 23.09),
    // поэтому его кнопка помечена data-admin-only — см. groupOpenRow.
    function groupServerId(g) {
        return g && g.serverId !== undefined && g.serverId !== null ? g.serverId : null;
    }
    // Источник правды по группе — сервер: он пересобирает total_amount,
    // состав и признак выписки. Локально ничего не вычитаем, перечитываем.
    function afterGroupChange(message) {
        reloadData()
            .then(function () { notify(message); })
            .catch(function (err) {
                refresh();
                notify(message + ' Данные не обновились: ' + reasonText(err), true);
            });
    }
    function groupActionFailed(prefix, err, button) {
        if (button) button.disabled = false;
        var status = err && err.status;
        if (status === 403) { notify(prefix + ': действие доступно только администратору.', true); return; }
        // 400/404 — снимок разошёлся с сервером (группу уже закрыли накладной,
        // распустили, карточка не в ней): не гадаем локально, перечитываем.
        if (status === 400 || status === 404) {
            reloadData()
                .then(function () { notify(prefix + ': ' + reasonText(err) + '. Данные перечитаны с сервера.', true); })
                .catch(function () { refresh(); notify(prefix + ': ' + reasonText(err), true); });
            return;
        }
        notify(prefix + ': ' + reasonText(err) + '. Данные не изменились.', true);
    }
    function joinCardToGroup(card) {
        if (!window.V2Api || !window.V2Api.token()) { notify('Действие доступно при подключении к CRM.', true); return; }
        var select = dialog.querySelector('#kb-deal-groupjoin');
        var g = select ? KBData.groups.filter(function (x) { return x.id === select.value; })[0] : null;
        var gid = groupServerId(g);
        if (!gid) { notify('Выберите группу, к которой крепить сделку.', true); return; }
        KBConfirm.ask({
            title: 'Прикрепить сделку к группе?',
            message: 'Сделка ' + card.id + ' войдёт в группу «' + g.name + '» — карточек в ней станет ' + (g.covers.length + 1) +
                '. Пока групповая ТН не выписана, сделку можно вывести обратно.',
            confirmText: 'Прикрепить',
            danger: false
        }).then(function (ok) {
            if (!ok) return;
            window.V2Api.api('/writeoffs/groups/' + gid + '/cards/' + Number(card.id), { method: 'POST' })
                .then(function () { afterGroupChange('Сделка ' + card.id + ' прикреплена к группе «' + g.name + '».'); })
                .catch(function (err) { groupActionFailed('Не прикреплено', err); });
        });
    }
    function leaveGroup(card, button) {
        if (!window.V2Api || !window.V2Api.token()) { notify('Действие доступно при подключении к CRM.', true); return; }
        var g = cardGroup(card);
        var gid = groupServerId(g);
        if (!gid) { notify('Группы сделки на сервере не видно — перечитайте данные.', true); return; }
        KBConfirm.ask({
            title: 'Выйти из группы?',
            message: 'Сделка ' + card.id + ' выйдет из группы «' + g.name + '». Если карточка там последняя, сервер распустит группу.',
            confirmText: 'Выйти',
            danger: false
        }).then(function (ok) {
            if (!ok) return;
            button.disabled = true;
            window.V2Api.api('/writeoffs/groups/' + gid + '/cards/' + Number(card.id), { method: 'DELETE' })
                .then(function (res) {
                    // Ответ различает два исхода: состав группы изменился или
                    // группы больше нет (сервер сам её удалил на последнем выходе).
                    afterGroupChange(res && res.detail
                        ? 'Сделка ' + card.id + ' вышла из группы: ' + String(res.detail).toLowerCase() + '.'
                        : 'Сделка ' + card.id + ' вышла из группы «' + g.name + '».');
                })
                .catch(function (err) { groupActionFailed('Выйти не удалось', err, button); });
        });
    }
    function disbandGroup(card, button) {
        if (!window.V2Api || !window.V2Api.token()) { notify('Действие доступно при подключении к CRM.', true); return; }
        var g = cardGroup(card);
        var gid = groupServerId(g);
        if (!gid) { notify('Группы сделки на сервере не видно — перечитайте данные.', true); return; }
        KBConfirm.ask({
            title: 'Распустить группу?',
            message: 'Группа «' + g.name + '» будет удалена, а все её карточки (сейчас ' + g.covers.length +
                ') вернутся в обычные очереди. Выписанные ТН и оплаты карточек не трогаются.' +
                ' Действие видно всем, кто работает с этой базой.',
            confirmText: 'Распустить'
        }).then(function (ok) {
            if (!ok) return;
            button.disabled = true;
            window.V2Api.api('/writeoffs/groups/' + gid, { method: 'DELETE' })
                .then(function () { afterGroupChange('Группа «' + g.name + '» распущена, карточек вышло: ' + g.covers.length + '.'); })
                .catch(function (err) { groupActionFailed('Не распущена', err, button); });
        });
    }
    // Выписка групповой ТН — двухшаговая (создать группу → выписать), и отказ
    // возможен на любом из шагов: флаг занятости общий, его ставят здесь, а
    // снимают в issueGroup.
    var groupIssueBusy = false;
    function openGroupIssue() {
        var picks = groupPick.map(function (id) { return cards.find(function (c) { return c.id === id; }); }).filter(Boolean);
        if (picks.length < 2) return;
        // Item #3: сервер требует Σ total_amount карточек, не сумму остатков.
        var totalAmount = picks.reduce(function (a, c) { return a + c.amount; }, 0);
        var clients = picks.map(function (c) { return c.client; }).filter(function (v, i, arr) { return arr.indexOf(v) === i; });
        var stores = picks.map(function (c) { return KBData.storeName(c.store); }).filter(function (v, i, arr) { return arr.indexOf(v) === i; });
        window.KBSelect.close();
        dialog.innerHTML = '<h2 id="kb-dialog-title">Групповая накладная · ' + picks.length + ' карточек</h2>' +
            '<dl class="kb-kv kb-group-kv"><dt>Карточки</dt><dd>' + picks.map(function (c) { return esc(c.id + ' · ' + moneyOrDash(cardRemaining(c)) + ' BYN'); }).join('<br>') + '</dd>' +
            '<dt>Клиент</dt><dd>' + esc(clients.length === 1 ? (clients[0] || 'Не указан') : 'Разные клиенты') + '</dd>' +
            '<dt>Магазин</dt><dd>' + esc(stores.length === 1 ? (stores[0] || 'Не указан') : 'Разные магазины') + '</dd></dl>' +
            '<p class="kb-detail-hint">Одна накладная на всю группу — как «группы списаний» на проде. Сумма: ' + money(totalAmount) + ' BYN (Σ сумм карточек, как требует сервер).</p>' +
            '<form id="kb-group-form" class="kb-form"><div class="kb-field"><label for="kb-g-date">Дата выписки</label><input id="kb-g-date" type="date" required></div>' +
            '<div class="kb-field"><label for="kb-g-number">Номер накладной</label><input id="kb-g-number" maxlength="50" required></div>' +
            '<div class="kb-field"><label for="kb-g-amount">Сумма, BYN</label><input id="kb-g-amount" value="' + money(totalAmount) + '" disabled></div>' +
            '<p class="kb-form-error" id="kb-g-error" role="alert"></p>' +
            '<button class="btn btn-primary" type="submit">Выписать на группу</button><button class="btn btn-ghost" type="button" data-close>Отмена</button></form>';
        document.getElementById('kb-g-date').focus();
        document.getElementById('kb-group-form').onsubmit = function (e) {
            e.preventDefault();
            if (groupIssueBusy) return;
            var number = document.getElementById('kb-g-number').value.trim();
            var date = document.getElementById('kb-g-date').value;
            // Item #3: проверка дубля — с учётом пустой серии в серверных группах.
            var error = !number || !date ? 'Заполните дату и номер.' :
                KBData.groups.some(function (g) {
                    var gNum = (g.number || '').toLowerCase();
                    return gNum && gNum === number.toLowerCase();
                }) ? 'Накладная с таким номером уже выписана.' : '';
            if (error) { document.getElementById('kb-g-error').textContent = error; return; }
            // Флаг живёт на уровне модуля, а не формы: сбрасывать его должен и
            // отказ в issueGroup, иначе кнопка «Выписать на группу» после
            // первой же ошибки осталась бы навсегда нажатой.
            groupIssueBusy = true;
            issueGroup(picks, number, date, totalAmount);
        };
        if (!dialog.open) dialog.showModal();
    }
    function issueGroup(picks, number, date, totalAmount) {
        // Item #3: прямые API-вызовы с правильными путями /writeoffs/groups
        // (boot template мапит 'group-issue' на старые /writeoff-groups).
        var cardIds = picks.map(function (c) { return Number(c.id); });
        var submitBtn = dialog.querySelector('#kb-group-form button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;
        window.V2Api.api('/writeoffs/groups', { method: 'POST', body: { card_ids: cardIds } })
            .then(function (g) {
                if (!g || !g.id) throw new Error('Группа не создана');
                return window.V2Api.api('/writeoffs/groups/' + g.id + '/issue-invoice', {
                    method: 'POST',
                    body: { invoice_number: number, invoice_date: date, amount: totalAmount / 100 }
                }).then(function (result) {
                    // Обновляем локальный кэш групп.
                    // P0-хотфикс 23.09 (часть A): serverTxId — НАСТОЯЩИЙ id
                    // записи-документа группы из ответа сервера, а не id группы.
                    // Раньше здесь лежал g.id, и отмена групповой ТН в той же
                    // сессии слала DELETE /payments/transactions/{id группы},
                    // снося постороннюю запись реестра с совпавшим номером
                    // (дефект 1 реестра V2-WORKPLAN-2026-09-22). Тот же id —
                    // docTxId для флагов «ТН у нас»/«Счёт у нас» в финансах
                    // (часть D): без него галочка группы не сохранялась до F5.
                    var groupDocTxId = (result && result.invoice_transaction_id) || null;
                    var groupEntry = {
                        id: 'gr-' + g.id, serverId: g.id, serverTxId: groupDocTxId,
                        docTxId: groupDocTxId, tnHere: false, billHere: false,
                        name: g.name || 'Группа', client: picks[0].client || '',
                        // Формат полей — как у boot (GET /writeoffs/groups/):
                        // store = ИМЯ магазина, date = ДД.ММ.ГГГГ. Иначе до F5
                        // строка группы в финансах печатала service-id и ISO,
                        // а «Накладные» карточки — дату в другом виде.
                        store: KBData.storeName(picks[0].store) || '',
                        series: '', number: number, date: ruFromIso(String(date).slice(0, 10)), amount: totalAmount,
                        writtenOff: true,
                        covers: picks.map(function (c) { return { cardId: c.id, amount: null }; })
                    };
                    KBData.groups.push(groupEntry);
                    // Дефект 7 реестра (симметричная часть, закрытая здесь):
                    // раньше деньги только инвалидировались, и карточка
                    // оставалась без остатка в обеих очередях до перезагрузки.
                    picks.forEach(function (c) {
                        c.groupId = 'gr-' + g.id;
                        coverCardByGroupIssue(c);
                    });
                    groupPick = [];
                    closeDialog(); refresh();
                    notify('Групповая ТН ' + number + ' выписана на ' + picks.length + ' карточек · ' + money(totalAmount) + ' BYN.');
                });
            })
            .catch(function (err) {
                if (submitBtn) submitBtn.disabled = false;
                groupIssueBusy = false;
                var errEl = document.getElementById('kb-g-error');
                if (errEl) errEl.textContent = (err && (err.detail || err.message)) || 'Не удалось выписать групповую накладную.';
            });
    }
    // Админка правит справочники → доска и фильтры перерисовываются.
    document.addEventListener('kb:dictionaries-changed', function () {
        // Активный магазин мог исчезнуть из справочника — тихо возвращаем
        // «Все магазины»; хранилище видов при этом не чистим: при применении
        // вид с несуществующим магазином применится без него.
        if (state.store !== 'all' && !KBData.stores.some(function (s) { return s.id === state.store; })) {
            state.store = 'all';
            writeBoardParams({ store: '' });
        }
        populateStores();
        refresh();
    });
    // Пульт дня и «Клиент 360» открывают карточки через этот мост.
    // Раздел переключаем штатным кликом по рейке — приём из gotoTarget()
    // (management.js): свой обработчик navigation.js отрабатывает синхронно
    // (pushState + render), поэтому hashchange в очереди задач не остаётся.
    // Прямая запись location.hash ставила hashchange в очередь, и поздний
    // render() оболочки закрывал только что открытый дровер: «Открыть
    // сделку» с Пульта дня и из «Клиент 360» карточку открывала и тут же
    // захлопывала. after() вызывается уже на доске — карточку открываем
    // ПОСЛЕ переключения раздела.
    function switchToBoard(after) {
        if (/^#board(\?|$)/.test(location.hash)) { after(false); return; }   // уже на доске — лишнего pushState не делаем
        var link = document.querySelector('.rail [data-view="board"]');
        if (link) { link.click(); after(true); return; }
        // Фолбэк: пункт рейки может отсутствовать (например, скрыт по роли).
        // Тогда hashchange придёт следующим тиком — открываем карточку после
        // него, иначе render() оболочки её закроет.
        var once = function () { window.removeEventListener('hashchange', once); after(true); };
        window.addEventListener('hashchange', once);
        location.hash = '#board';
    }
    window.KBBoard = {
        open: function (id) {
            var c = cards.find(function (card) { return card.id === id; });
            if (!c) return;
            switchToBoard(function (switched) {
                // queueMode пишет режим в адрес через writeBoardParams, а тот
                // не трогает URL, пока активен другой раздел — поэтому вызываем
                // ПОСЛЕ клика по рейке: итоговый адрес #board (не #day), режим
                // очереди «Доска», поиск/магазин из state сохраняются.
                queueMode('board');
                openCard(c);
                // render() оболочки следующим кадром утаскивает фокус на
                // #shell-content, а тот под модальным дровером inert — фокус
                // проваливается в body. Возвращаем его в дровер тем же
                // порядком rAF (приём из gotoTarget в management.js).
                if (!switched) return;
                requestAnimationFrame(function () {
                    if (!dialog.open) return;
                    // Возврат при перестройке дровера (restoreDrawerFocus,
                    // дефект 8 реестра) уже поставил фокус внутрь — не тянем его
                    // на крестик и не делаем второй ход за один кадр.
                    if (dialog.contains(document.activeElement)) return;
                    var f = dialog.querySelector('[autofocus]') || dialog.querySelector('.kb-detail-close');
                    if (f && document.activeElement !== f) f.focus({ preventScroll: true });
                });
            });
        }
    };
    // Восстановление состояния из адреса — после наполнения справочников.
    var bp = readBoardParams();
    if (bp.q) { state.search = bp.q; searchEl.value = bp.q; }
    if (bp.store && (bp.store === 'all' || KBData.stores.some(function (s) { return s.id === bp.store; }))) state.store = bp.store;
    if (/^[0-3]$/.test(bp.priority || '')) state.priority = bp.priority;
    if (/^(bn|cash)$/.test(bp.pay || '')) state.pay = bp.pay;
    // Селекты проставляются после восстановления state — populate* читают его.
    populateStores(); populateFilterSelects(); populateViewSelect();
    setDensity(true); refresh();
    // trash из адреса не восстанавливается: корзина — служебный режим сессии.
    queueMode(['pending', 'history', 'list', 'archive'].indexOf(bp.mode) >= 0 ? bp.mode : 'board');
    // Запрос из ссылки (#board?q=…) раскрываем сразу: свёрнутая лупа за
    // невидимым фильтром выглядела как сломанная доска. Фокус при этом не
    // тянем — страница только что загрузилась, а скролл к тулбару не нужен.
    // Без запроса поле остаётся как в разметке: inert + скрытый крестик.
    if (bp.q) setSearchPanel(true);
})();
