/* ============================================================
   Канбан: компактная доска — демо-логика. Превью, не прод:
   без сети и хранилища, изменения живут до перезагрузки.
   Выписанные ТН передаются в финансовые документы этой демосессии.
   Реестр оплат остаётся отдельным набором данных.
   Все суммы — целые копейки (integers), форматирование на выводе.
   ============================================================ */
(function () {
    'use strict';

    // Справочники и карточки — из демо-«сервера» (shell-v2-data.js).
    // Статусы, магазины и пользователи правятся в админке и управляют доской.
    var cards = KBData.cards;

    function boardStatuses() { return KBData.boardStatuses(); }
    function writeoffStatus() { return KBData.writeoffStatus(); }
    var state = { search: '', store: 'all', compact: true, queueMode: 'pending', history: [], hideFilled: false };

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
    function stageName(id) {
        var s = KBData.statuses.find(function (x) { return x.id === id; });
        return s ? s.name : id;
    }
    // 'cl-22' → 22
    function numericClientIdValue(id) {
        var n = parseInt(String(id == null ? '' : id).replace(/[^0-9]/g, ''), 10);
        return isNaN(n) ? null : n;
    }
    // A12: сервер — единственный источник остатка. remaining_kop (int, копейки)
    // отдаётся в карточке; если серверное поле отсутствует (old cache, demo
    // без сервера) — null, и вызывающий код показывает «—», а не считает сам.
    function cardRemaining(card) {
        if (card.remaining_kop !== undefined && card.remaining_kop !== null) return card.remaining_kop;
        return null;
    }
    // Выписано в копейках: серверное issued_total (руб→коп) или локальный
    // сессионный счётчик card.issued (для демо и оптимистичных обновлений).
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
            return true;
        });
    }

    function counterHTML(flag, label, count) {
        var path = flag === 'ordered' ? '<path d="M3 3h2l2 12h11l3-9H6M9 20h.01M17 20h.01"/>' : '<path d="m3 7 9-4 9 4v10l-9 4-9-4V7Zm0 0 9 4 9-4M12 11v10m-5-6 3 3 6-6"/>';
        return '<span class="kb-counter" data-counter="' + flag + '" role="img" title="' + label + ': ' + count + '" aria-label="' + label + ': ' + count + '"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + '</svg><span aria-hidden="true">' + count + '</span></span>';
    }
    function cardHTML(c) {
        var cl = checklistSummary(c);
        var chips = '<span class="pill">' + esc(KBData.storeName(c.store)) + '</span>' +
            '<span class="pill"><span class="avatar sm">' + esc(c.manager.initials || '··') + '</span>' + esc(c.manager.full_name || c.manager.username || '') + '</span>';
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
        // Фидбек 20.09: удаление с плитки, без захода в карточку. Плитка —
        // div, не button: кнопку удаления нельзя вложить в <button>.
        // role="button" + tabindex сохраняют открытие с клавиатуры.
        return '<div class="kb-card" role="button" tabindex="0" draggable="true" data-card="' + esc(c.id) + '" aria-haspopup="dialog">' +
            '<span class="kb-card-title">' + esc(c.title) + '</span>' +
            '<span class="kb-card-meta num">' + esc(c.id) + ' · ' + esc(c.deadline) + '</span>' +
            '<span class="kb-card-chips">' + chips + '</span>' +
            '<span class="kb-card-foot num"><b>' + money(c.amount) + ' BYN</b>' +
            (issuedKop > 0 ? ' · списано ' + money(issuedKop) : '') +
            (c.docs.length ? ' · ТН: ' + c.docs.length : '') +
            (c.groupId ? ' · групповая ТН' : '') + '</span>' +
            '<button type="button" class="kb-card-del" data-card-del="' + esc(c.id) + '" aria-label="Удалить карточку ' + esc(c.title) + '" title="Удалить"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
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
                (inCol.length ? inCol.map(cardHTML).join('') : '<p class="kb-empty">Пусто</p>') +
                '</div>';
            boardEl.appendChild(col);
        });
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
        var inCol = visibleCards().filter(function (c) { return c.stage === stageId; });
        col.querySelector('.kb-col-count').textContent = inCol.length;
        col.querySelector('.kb-col-sum').textContent = money(inCol.reduce(function (a, c) { return a + c.amount; }, 0));
        col.setAttribute('aria-label', st.name + ' — карточек: ' + inCol.length);
        var list = col.querySelector('.kb-cards');
        var empty = list.querySelector('.kb-empty');
        if (!inCol.length && !empty) list.innerHTML = '<p class="kb-empty">Пусто</p>';
        else if (inCol.length && empty) empty.remove();
    }
    var drag = null;
    var dragFrame = 0;
    var suppressCardClick = false;
    function cleanupDrag() {
        cancelAnimationFrame(dragFrame);
        dragFrame = 0;
        if (!drag) return;
        drag.source.classList.remove('kb-drag-source');
        drag.placeholder.remove();
        boardEl.querySelectorAll('.kb-drop-column').forEach(function(col) { col.classList.remove('kb-drop-column'); });
        boardEl.classList.remove('kb-dragging');
        drag = null;
        setTimeout(function() { suppressCardClick = false; }, 0);
    }
    function placeDrop(x, y) {
        if (!drag) return;
        var hit = document.elementFromPoint(x, y);
        var col = hit && hit.closest('#kb-board .kb-col');
        boardEl.querySelectorAll('.kb-drop-column').forEach(function(el) { el.classList.toggle('kb-drop-column', el === col); });
        if (!col) { drag.column = null; drag.placeholder.remove(); return; }
        col.classList.add('kb-drop-column');
        drag.column = col;
        var list = col.querySelector('.kb-cards');
        var slotRect = drag.placeholder.getBoundingClientRect();
        if (drag.placeholder.parentNode === list && y >= slotRect.top && y <= slotRect.bottom) return;
        var neighbours = Array.from(list.querySelectorAll('.kb-card')).filter(function(el) { return el !== drag.source; });
        var before = neighbours.find(function(el) {
            var rect = el.getBoundingClientRect();
            return y < rect.top + rect.height / 2;
        });
        // Avoid replacing the slot on every native dragover event.
        if (drag.placeholder.parentNode !== list || drag.placeholder.nextElementSibling !== (before || null)) {
            list.insertBefore(drag.placeholder, before || null);
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
            var rect = list.getBoundingClientRect();
            list.scrollTop += edgeSpeed(drag.y, rect.top, rect.bottom);
            var boardRect = boardEl.getBoundingClientRect();
            boardEl.scrollLeft += edgeSpeed(drag.x, boardRect.left, boardRect.right);
            placeDrop(drag.x, drag.y);
        }
        dragFrame = requestAnimationFrame(scrollDrag);
    }
    boardEl.addEventListener('dragstart', function(e) {
        var source = e.target.closest('.kb-card');
        if (!source || !e.dataTransfer) return;
        cleanupDrag();
        var placeholder = document.createElement('div');
        placeholder.className = 'kb-drop-placeholder';
        placeholder.setAttribute('aria-hidden', 'true');
        placeholder.style.height = source.offsetHeight + 'px';
        drag = { source: source, placeholder: placeholder, card: cards.find(function(c) { return c.id === source.dataset.card; }), column: null, x: e.clientX, y: e.clientY };
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
        placeDrop(drag.x, drag.y);
        if (drag.column) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
    });
    boardEl.addEventListener('drop', function(e) {
        if (!drag) return;
        placeDrop(e.clientX, e.clientY);
        if (!drag.column) { cleanupDrag(); return; }
        e.preventDefault();
        var fromStage = drag.card.stage;
        var toStage = drag.column.dataset.stage;
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
        document.dispatchEvent(new CustomEvent('kb:documents', { detail: cards }));
        document.dispatchEvent(new CustomEvent('kb:groups', { detail: KBData.groups }));
        apiMutate('status', { id: cardId, status: status }).then(function (ok) {
            if (ok === false) refresh(); // сервер отказал: тост уже показан boot-слоем
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
        refresh();
    });
    // Debounce: полная перерисовка доски (сотни карточек) не должна бежать
    // на каждый символ — ждём паузу в вводе 150 мс.
    var searchTimer = 0;
    var searchToggle = document.getElementById('kb-search-toggle');
    function toggleSearch(open) {
        document.getElementById('kb-search-wrap').classList.toggle('is-open', open);
        document.getElementById('kb-search-field').inert = !open;
        searchToggle.setAttribute('aria-expanded', String(open));
        searchToggle.setAttribute('aria-label', open ? 'Закрыть поиск' : 'Открыть поиск');
        if (open) searchEl.focus();
        else { clearTimeout(searchTimer); searchEl.value = ''; state.search = ''; writeBoardParams({ q: '' }); refresh(); searchToggle.focus(); }
    }
    searchToggle.onclick = function() { toggleSearch(searchToggle.getAttribute('aria-expanded') !== 'true'); };
    document.getElementById('kb-search-close').onclick = function() { toggleSearch(false); };
    searchEl.addEventListener('keydown', function(e) { if (e.key === 'Escape') { e.preventDefault(); toggleSearch(false); } });
    searchEl.addEventListener('input', function () {
        state.search = searchEl.value;
        writeBoardParams({ q: state.search });
        clearTimeout(searchTimer);
        searchTimer = setTimeout(refresh, 150);
    });
    document.getElementById('kb-density').onclick = function() { setDensity(!state.compact); };
    function setDensity(compact) {
        state.compact = compact;
        rootEl.classList.toggle('kb-compact', compact);
        rootEl.classList.toggle('kb-normal', !compact);
        document.getElementById('kb-density').setAttribute('aria-pressed', String(compact));
    }

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
    function refresh() {
        var scroll = Array.from(boardEl.querySelectorAll('.kb-cards')).map(function(el) { return el.scrollTop; });
        renderBoard(); renderQueue();
        boardEl.querySelectorAll('.kb-cards').forEach(function(el, i) { el.scrollTop = scroll[i] || 0; });
        document.dispatchEvent(new CustomEvent('kb:documents', { detail: cards }));
        document.dispatchEvent(new CustomEvent('kb:groups', { detail: KBData.groups }));
    }
    // Перечитать данные с сервера без F5. Если загрузчик недоступен (прототип
    // на демо-данных) или не зарегистрирован — хотя бы перерисовываем доску.
    // Ошибку загрузки не глотает: её показывает вызывающий код.
    function reloadData() {
        if (!window.KBData || !window.KBData.reload) { refresh(); return Promise.resolve(null); }
        return window.KBData.reload().then(function (data) {
            if (!data) refresh();
            return data;
        });
    }
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
    function initCombo(input, options) {
        if (!input) return;
        var wrap = document.createElement('div');
        wrap.className = 'kb-combo';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        var list = document.createElement('div');
        list.className = 'kb-combo-list';
        list.hidden = true;
        wrap.appendChild(list);
        function renderList() {
            var q = input.value.trim().toLowerCase();
            var items = options.filter(function (o) { return !q || o.toLowerCase().indexOf(q) !== -1; }).slice(0, 12);
            list.innerHTML = items.map(function (o) {
                return '<div class="kb-combo-item">' + esc(o) + '</div>';
            }).join('');
            list.hidden = items.length === 0;
            list.querySelectorAll('.kb-combo-item').forEach(function (el, i) {
                el.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    input.value = items[i];
                    list.hidden = true;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
            });
        }
        input.addEventListener('focus', renderList);
        input.addEventListener('input', renderList);
        input.addEventListener('blur', function () { setTimeout(function () { list.hidden = true; }, 150); });
        input.addEventListener('keydown', function (e) { if (e.key === 'Escape') { list.hidden = true; } });
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
                return '<div class="kb-q-row" style="display:flex;align-items:center;gap:8px;cursor:default"><div style="min-width:0"><b>' + esc(c.id + ' · ' + (c.title || '')) + '</b><div>' + esc(c.store_location || '') + '</div></div>' +
                    '<div class="row" style="gap:6px;margin-left:auto">' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-trash-restore="' + c.id + '">Восстановить</button>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-trash-purge="' + c.id + '">Удалить навсегда</button></div></div>';
            }).join('') : '<p class="kb-note">Корзина пуста.</p>');
        }).catch(function () { body.innerHTML = '<p class="kb-note">Не удалось загрузить корзину.</p>'; });
    }
    function renderQueue() {
        if (state.queueMode === 'trash') { renderTrash(); return; }
        // Очередь — по остатку к выписке (как на проде): полностью выписанная
        // карточка не висит в «На списание», даже если статус ещё там же,
        // а в «Списано» попадает и без смены статуса.
        var list = visibleCards().filter(function (c) {
            if (state.queueMode === 'pending') return c.stage === 'writeoff' && hasRemaining(c);
            return c.stage === 'done' || (c.stage === 'writeoff' && cardIssued(c) > 0 && !hasRemaining(c));
        });
        document.getElementById('kb-queue-count').textContent = cards.filter(function(c) { return c.stage === 'writeoff' && hasRemaining(c); }).length;
        groupPick = groupPick.filter(function (id) {
            var c = cards.find(function (x) { return x.id === id; });
            return c && c.stage === 'writeoff' && hasRemaining(c);
        });
        var pending = state.queueMode === 'pending';
        var allPicked = pending && list.length > 0 && list.every(function (c) { return groupPick.indexOf(c.id) >= 0; });
        var toolbar = pending ?
            '<div class="kb-q-toolbar"><label class="kb-q-check"><input type="checkbox" id="kb-group-all"' + (allPicked ? ' checked' : '') + (list.length ? '' : ' disabled') + '> Выбрать все</label>' +
            '<button type="button" class="btn btn-primary btn-sm" id="kb-group-issue"' + (groupPick.length > 1 ? '' : ' disabled') + '>Накладная на группу' + (groupPick.length > 1 ? ' (' + groupPick.length + ')' : '') + '</button></div>' : '';
        document.getElementById('kb-queue-body').innerHTML = '<p class="kb-note">Поиск и магазин общие с доской. Найдено: ' + list.length + '</p>' + toolbar + list.map(function(c) {
            return '<div class="kb-q-row" role="button" tabindex="0" data-card="' + esc(c.id) + '" aria-haspopup="dialog" aria-label="Открыть ' + esc(c.id + ' · ' + c.title) + '">' +
                (pending ? '<input type="checkbox" class="kb-q-pick" data-group-pick="' + esc(c.id) + '" aria-label="Включить ' + esc(c.id) + ' в групповую накладную"' + (groupPick.indexOf(c.id) >= 0 ? ' checked' : '') + '>' : '') +
                '<div><b>' + esc(c.id + ' · ' + c.title) + '</b><div>' + esc(KBData.storeName(c.store)) + ' · К выписке ' + moneyOrDash(cardRemaining(c)) + ' BYN</div></div></div>';
        }).join('');
    }
    function queueMode(mode) {
        state.queueMode = mode;
        writeBoardParams({ mode: mode === 'board' ? '' : mode });
        boardEl.hidden = mode !== 'board';
        document.getElementById('kb-queue').hidden = mode === 'board';
        document.getElementById('kb-q-board').setAttribute('aria-pressed', String(mode === 'board'));
        document.getElementById('kb-q-pending').setAttribute('aria-pressed', String(mode === 'pending'));
        document.getElementById('kb-q-history').setAttribute('aria-pressed', String(mode === 'history'));
        renderQueue();
    }
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
                    kbConfirmOpener = null;
                });
            }
            if (kbConfirmEl.open) {
                // Новый вопрос вытесняет прежний; его промис resolves(false) явно,
                // не полагаясь на порядок событийной очистки close.
                kbConfirmSettle(false);
                kbConfirmEl.close();
            }
            kbConfirmOpener = document.activeElement;
            kbConfirmEl.innerHTML = '<h2 id="kb-confirm-title">' + esc(opts.title || 'Подтвердите действие') + '</h2>' +
                (opts.message ? '<p class="kb-confirm-msg">' + esc(opts.message) + '</p>' : '') +
                '<div class="kb-confirm-foot">' +
                '<button type="button" class="btn btn-ghost" data-confirm-cancel>' + esc(opts.cancelText || 'Отмена') + '</button>' +
                '<button type="button" class="btn btn-danger" data-confirm-ok>' + esc(opts.confirmText || 'Удалить') + '</button></div>';
            kbConfirmEl.querySelector('[data-confirm-cancel]').onclick = function () { kbConfirmSettle(false); kbConfirmEl.close(); };
            kbConfirmEl.querySelector('[data-confirm-ok]').onclick = function () { kbConfirmSettle(true); kbConfirmEl.close(); };
            kbConfirmEl.showModal();
            // Действие опасное — стартовый фокус на безопасной «Отмене».
            kbConfirmEl.querySelector('[data-confirm-cancel]').focus();
            return new Promise(function (resolve) { kbConfirmResolve = resolve; });
        }
    };
    window.KBConfirm = KBConfirm;
    // Edits are session-local; issued sums remain controlled by openIssue.
    function optionsHTML(values, selected) {
        return values.map(function(value) {
            var pair = Array.isArray(value) ? value : [value, value];
            return '<option value="' + esc(pair[0]) + '"' + (String(pair[0]) === String(selected) ? ' selected' : '') + '>' + esc(pair[1]) + '</option>';
        }).join('');
    }
    function dealField(key, label, control) {
        return '<label class="kb-edit-field" for="kb-deal-' + key + '"><span>' + label + '</span>' + control + '</label>';
    }
    function dealInput(c, key, label, type) {
        // Item #12: maxlength 200 = серверный лимит заголовка (schemas.py:249).
        return dealField(key, label, '<input id="kb-deal-' + key + '" data-deal-field="' + key + '" type="' + (type || 'text') + '" value="' + esc(key === 'amount' ? money(c.amount) : c[key]) + '"' + (key === 'amount' ? ' inputmode="decimal"' : ' maxlength="200"') + (key === 'amount' && c.stage === 'done' ? ' disabled' : '') + '>');
    }
    function dealSelect(key, label, values, selected, disabled) {
        return dealField(key, label, '<select id="kb-deal-' + key + '" data-deal-field="' + key + '"' + (disabled ? ' disabled' : '') + '>' + optionsHTML(values, selected) + '</select>');
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
            var suffix = (result.due ? ' · Оплатить до ' + result.due : '') +
                (result.prepay !== null ? ' · Предоплата ' + money(result.prepay) + ' BYN' : '');
            status.textContent = result.valid
                ? (saved ? 'Сохранено' + suffix : 'Условия оплаты' + suffix)
                : 'Не сохранено · ' + Object.values(result.errors).join(' ');
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
    // Card-only rendering; documents describe demo issuance, never payment events.
    // Preview-only supplier IDs and per-item files; no production uploads.
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
            // Item #4: менеджер по id, не по индексу массива
            dealSelect('manager', 'Менеджер', [['', 'Не назначен']].concat(KBData.users.map(function (u) { return [u.id, u.full_name]; })), c.manager ? c.manager.id : '') +
            dealInput(c, 'deadline', 'Срок · ДД.ММ.ГГГГ') + dealInput(c, 'amount', 'Сумма, BYN') +
            dealField('note', 'Заметка', '<textarea id="kb-deal-note" data-deal-field="note" rows="2" maxlength="4000">' + esc(c.note) + '</textarea>');
        var checks = c.checklist.map(function(item, i) {
            var rawId = String(item.id).replace(/[^0-9]/g, '');
            var done = c.stage === 'done' ? ' disabled' : '';
            var invFileHtml = item.invFile
                ? '<a class="kb-inv-dl" href="#" data-inv-dl="' + rawId + '" data-inv-name="' + esc(item.invFile) + '">📎 ' + esc(item.invFile) + '</a>' +
                  '<button type="button" class="kb-inv-clear" data-inv-clear="' + rawId + '" title="Открепить файл">✕</button>'
                : '<label class="kb-inv-up">Прикрепить файл<input type="file" class="kb-inv-input" data-inv-up="' + rawId + '" hidden></label>';
            // Компактный пункт: поставщик (с поиском) + сумма + файл в одну
            // строку; примечание ниже. Без дублей имени и нативных селектов.
            return '<div class="kb-check-item" data-procurement-item="' + esc(item.id) + '">' +
                '<div class="kb-check-line">' +
                '<input class="kb-sup-combo" data-cl-id="' + rawId + '" value="' + esc(item.supplierName || item.label || '') + '" placeholder="Поставщик (выберите или впишите)" list="kb-suppliers-datalist" autocomplete="off">' +
                '<input class="kb-sup-amount" data-cl-id="' + rawId + '" value="' + esc(item.amount || '') + '" placeholder="Сумма, BYN" inputmode="decimal" style="width:110px">' +
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
                '<label class="kb-originals"><input type="checkbox" data-wh="' + esc(whId) + '"' + (whOn ? ' checked' : '') + '> Списано со склада</label>';
            return '<li class="kb-detail-doc"><div><b>' + esc((d.series ? d.series + ' ' : '') + d.number) + '</b><span>' + esc(d.date) + '</span><label class="kb-originals"><input type="checkbox" data-originals="' + i + '"' + (d.originalsReturned ? ' checked' : '') + '> Оригинал ТН возвращён</label>' + whHtml + '</div><div class="kb-doc-side"><strong>' + money(d.amount) + ' <small>BYN</small></strong>' +
                '<button type="button" class="btn btn-ghost btn-sm" data-doc-edit="' + i + '">Изменить</button>' +
                '<button type="button" class="btn btn-ghost btn-sm" data-doc-cancel="' + i + '">Отменить</button></div></li>';
        }).join('');
        var groupEntry = '';
        if (c.groupId) {
            var g = KBData.groups.filter(function (x) { return x.id === c.groupId; })[0];
            if (g) {
                var own = g.covers.filter(function (x) { return x.cardId === c.id; })[0];
                groupEntry = '<li class="kb-detail-doc"><div><b>Групповая ТН ' + esc(g.series + ' ' + g.number) + '</b><span>' + esc(g.date) + '</span><span class="kb-detail-hint">Группа «' + esc(g.name) + '» · карточек в группе: ' + g.covers.length + '</span></div><div class="kb-doc-side"><strong>' + money(own ? own.amount : 0) + ' <small>BYN</small></strong>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-doc-cancel="group-' + esc(g.id) + '">Отменить</button></div></li>';
            }
        }
        var history = c.docs.map(function(d) {
            return '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Выписана накладная ' + esc((d.series ? d.series + ' ' : '') + d.number) + '</b><p>' + esc(d.date) + ' · ' + money(d.amount) + ' BYN</p></div></li>';
        }).join('');
        if (c.groupId) {
            var hg = KBData.groups.filter(function (x) { return x.id === c.groupId; })[0];
            if (hg) {
                var hcov = hg.covers.filter(function (x) { return x.cardId === c.id; })[0];
                history += '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Выписана групповая ТН ' + esc(hg.series + ' ' + hg.number) + '</b><p>' + esc(hg.date) + ' · ' + money(hcov ? hcov.amount : 0) + ' BYN · одна накладная на группу из ' + hg.covers.length + ' карточек</p></div></li>';
            }
        }
        return '<section id="kb-panel-overview" role="tabpanel" aria-labelledby="kb-tab-overview" tabindex="0">' + window.KBPayment.render(c) +
            '<div class="kb-overview-head"><h3 class="kb-detail-section-title">О сделке</h3><button type="button" id="kb-hide-filled" aria-pressed="false" aria-controls="kb-overview-fields">Скрыть заполненные поля</button></div><div class="kb-deal-fields" id="kb-overview-fields">' + info + '</div><p class="kb-detail-empty" id="kb-overview-empty" role="status" hidden>Все поля заполнены. Нажмите «Показать все поля», чтобы изменить их.</p></section>' +
            '<section id="kb-panel-procurement" role="tabpanel" aria-labelledby="kb-tab-procurement" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">Закупка у поставщиков <span>Заказано ' + cl.ordered + ' · Получено ' + cl.received + ' (из ' + cl.total + ')</span></h3>' +
            '<p class="kb-detail-hint">Файл до 25 МБ</p>' +
            '<div class="kb-add-check">' +
            '<input id="kb-add-supplier" placeholder="Поставщик (выберите или впишите)" style="flex:1;min-width:170px">' +
            '<datalist id="kb-suppliers-datalist">' + (KBData.suppliers || []).map(function (s) { return '<option value="' + esc(s.name) + '"></option>'; }).join('') + '</datalist>' +
            '<input id="kb-add-amount" inputmode="decimal" placeholder="Сумма, BYN" style="width:120px">' +
            '<button type="button" class="btn btn-primary btn-sm" id="kb-add-check">Добавить</button></div>' +
            (checks || '<p class="kb-detail-empty">Закупка не требуется.</p>') +
            '<h3 class="kb-detail-section-title">Вложения сделки <span>' + (c.attachments || []).length + '</span></h3>' +
            '<div id="kb-attachments-list">' +
            ((c.attachments || []).length ? c.attachments.map(function (a) {
                return '<div class="kb-check-row kb-att-row" data-att-row="' + esc(a.id) + '">' +
                    '<button type="button" class="kb-att-dl" data-att-id="' + (a.id || '') + '" data-att-name="' + esc(a.name || 'attachment') + '" title="Скачать файл" style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;background:none;border:none;cursor:pointer;padding:4px 0">' +
                    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                    '<span class="grow trunc">' + esc(a.name || '') + '</span></button>' +
                    '<button type="button" class="btn btn-ghost btn-sm kb-att-del" data-att-del="' + esc(a.id) + '" data-att-del-name="' + esc(a.name || '') + '" title="Удалить вложение">✕</button>' +
                    '</div>';
            }).join('') : '<p class="kb-detail-empty">Вложений нет.</p>') +
            '</div>' +
            '<div class="kb-att-upload">' +
            '<label class="kb-att-upload-btn"><input type="file" id="kb-att-upload-input" multiple accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.doc,.docx,.xls,.xlsx,.csv,.txt" hidden>Загрузить файлы</label>' +
            '<p class="kb-detail-hint">До 25 МБ каждый, несколько файлов</p>' +
            '</div>' +
            '</section>' +
            '<section id="kb-panel-invoices" role="tabpanel" aria-labelledby="kb-tab-invoices" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">Выписанные накладные <span>' + (c.docs.length + (c.groupId ? 1 : 0)) + '</span></h3>' +
            (groupEntry + docs ? '<ul class="kb-detail-docs">' + groupEntry + docs + '</ul>' : '<div class="kb-detail-empty"><b>Накладных пока нет</b><p>Демо-выписка доступна на этапе «На списание».</p></div>') +
            '<p class="kb-detail-hint">Выписанные ТН доступны в «Финансы → Документы». Оплата учитывается отдельно.</p></section>' +
            '<section id="kb-panel-history" role="tabpanel" aria-labelledby="kb-tab-history" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">История сделки</h3><div id="kb-history-real"><p class="kb-detail-empty">Журнал загружается…</p></div>' +
            '<h3 class="kb-detail-section-title">Письма отправителя</h3><div id="kb-email-related"><p class="kb-detail-empty">Загрузка…</p></div>' +
            '<h3 class="kb-detail-section-title">Выписки этой сессии</h3><ol class="kb-detail-history">' + history +
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
    // ---------- Письма отправителя ----------
    // GET /email-parser/related/{card_id} — список связанных писем.
    // Роль documents не имеет доступа к почтовому роутеру — показываем
    // «недоступно для вашей роли» без запроса.
    function loadRelatedEmails(c) {
        var box = document.getElementById('kb-email-related');
        if (!box) return;
        if (!window.V2Api || !window.V2Api.token()) {
            box.innerHTML = '<p class="kb-detail-empty">Доступно при подключении к CRM.</p>';
            return;
        }
        var role = null;
        try { role = localStorage.getItem('crm_role'); } catch (e) { /* без хранилища */ }
        if (role === 'documents') {
            box.innerHTML = '<p class="kb-detail-empty">Недоступно для вашей роли.</p>';
            return;
        }
        window.V2Api.api('/email-parser/related/' + encodeURIComponent(c.id)).then(function (data) {
            var related = (data && data.related) || [];
            if (!related.length) {
                box.innerHTML = '<p class="kb-detail-empty">Писем не найдено.</p>';
                return;
            }
            box.innerHTML = '<ul class="kb-email-list">' + related.map(function (item) {
                var date = item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : '';
                return '<li class="kb-email-row">' +
                    '<div class="kb-email-info"><b>' + esc(item.title || 'Без темы') + '</b>' +
                    '<span class="kb-detail-hint">' + esc(date) + '</span></div>' +
                    '<button type="button" class="btn btn-ghost btn-sm kb-email-link" data-link-target="' + esc(item.id) + '" data-link-target-title="' + esc(item.title || '') + '">Связать со сделкой</button>' +
                    '</li>';
            }).join('') + '</ul>';
            // Обработчики «Связать со сделкой»
            box.querySelectorAll('.kb-email-link').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    linkEmailToCard(c, btn.dataset.linkTarget, btn.dataset.linkTargetTitle);
                });
            });
        }).catch(function (err) {
            box.innerHTML = '<p class="kb-detail-empty">Не удалось загрузить письма: ' + esc(err.detail || err.message || 'ошибка') + '</p>';
        });
    }
    // POST /email-parser/link/{card_id} — связать письмо с текущей сделкой.
    // Тело: { target_card_id: <id целевой сделки из related> }.
    function linkEmailToCard(currentCard, targetCardId, targetTitle) {
        if (!targetCardId || !window.V2Api) return;
        window.V2Api.api('/email-parser/link/' + encodeURIComponent(currentCard.id), {
            method: 'POST',
            body: { target_card_id: Number(targetCardId) }
        }).then(function (result) {
            notify(result && result.message ? result.message : 'Письмо связано со сделкой.');
            // Обновляем блок писем — письмо удаляется на сервере
            if (currentCard) loadRelatedEmails(currentCard);
        }).catch(function (err) {
            notify('Не удалось связать: ' + (err.detail || err.message || 'ошибка'));
        });
    }
    // Фидбек 18.09: флаги складского списания по накладным карточки —
    // из эндпоинта чек-листа накладных (id записи реестра + written_off).
    function loadWarehouseFlags(c) {
        if (!window.V2Api || !window.V2Api.token() || !c.docs.length || c._invFlags) return;
        window.V2Api.api('/payments/cards/' + Number(c.id) + '/invoices').then(function (info) {
            c._invFlags = {};
            c._invIds = {};
            // эндпоинт отдаёт written_off (складское списание) и id записи
            (info.issued || []).forEach(function (i) {
                c._invFlags[i.invoice_number] = Boolean(i.written_off);
                c._invIds[i.invoice_number] = i.id;
            });
            if (dialog.open && activeCard === c) {
                var tab = dialog.dataset.cardTab;
                if (tab === 'invoices' || tab === 'overview') openCard(c);
            }
        }).catch(function () { /* тише некуда: флажки появятся после перезагрузки */ });
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
        window.KBData.loadCardActivity(Number(c.id)).then(function (items) {
            if (!items || !items.length) {
                box.innerHTML = '<p class="kb-detail-empty">Журнал пуст.</p>';
                return;
            }
            var rows = items.map(function (e) {
                var d = new Date(e.created_at);
                var when = isNaN(d) ? String(e.created_at || '') : d.toLocaleString('ru-RU');
                var who = e.user_name ? ' · ' + esc(e.user_name) : '';
                var details = e.details ? '<p class="kb-history-details">' + esc(e.details) + '</p>' : '';
                return '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>' + esc(e.action) + '</b>' + who +
                    '<p class="kb-history-date">' + esc(when) + '</p>' + details + '</div></li>';
            }).join('');
            c.historyHtml = '<ol class="kb-detail-history kb-history-full">' + rows + '</ol>';
            c.historyLoaded = true;
            box.innerHTML = c.historyHtml;
        }).catch(function () {
            box.innerHTML = '<p class="kb-detail-empty">Не удалось загрузить журнал. Попробуйте открыть карточку снова.</p>';
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
    function openCard(c) {
        window.KBSelect.close();
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
        var currentStage = c.stage === 'done' ? 'Списано' : stageName(c.stage);
        var stages = boardStatuses().concat([writeoffStatus(), { id: 'done', name: 'Списано' }]).filter(Boolean);
        var tabs = [['overview', 'Обзор'], ['procurement', 'Закупка'], ['invoices', 'Накладные'], ['history', 'История']];
        renderingCard = true;
        dialog.innerHTML = '<div class="kb-detail"><header class="kb-detail-head"><div><div class="kb-detail-meta"><span>Сделка ' + esc(c.id) + '</span><span class="kb-detail-badge">' + esc(currentStage) + '</span></div>' +
            '<h2 id="kb-dialog-title">' + esc(c.title) + '</h2></div><button type="button" class="kb-detail-close" data-close aria-label="Закрыть карточку" autofocus><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>' +
            '<div class="kb-detail-summary"><dl class="kb-detail-money"><div class="kb-detail-total"><dt>Сумма сделки</dt><dd>' + money(c.amount) + ' <small>BYN</small></dd></div><div><dt>Оплачено</dt><dd>' + money(c.paidAmount) + ' <small>BYN</small></dd></div><div><dt>Выписано</dt><dd>' + money(cardIssued(c)) + ' <small>BYN</small></dd></div><div><dt>Осталось выписать</dt><dd>' + moneyOrDash(cardRemaining(c)) + ' <small>BYN</small></dd></div></dl>' +
            '<div class="kb-detail-controls">' +
            dealSelect('paymentTerms', 'Условия оплаты', [['', 'Не выбраны'], ['deferred', 'Отсрочка'], ['full', 'Оплата 100%'], ['partial_deferred', 'Частичная оплата + отсрочка платежа']], c.paymentTerms) +
            dealSelect('stage', 'Этап', stages.filter(function(st) { return (st.id !== 'done' || c.stage === 'done') && st.canAssign !== false; }).map(function(st) { return [st.id, st.name]; }), c.stage, c.stage === 'done') +
            '</div></div>' +
            '<div class="kb-detail-tabs" role="tablist" aria-label="Разделы сделки">' + tabs.map(function(tab) {
                return '<button type="button" role="tab" id="kb-tab-' + tab[0] + '" data-card-tab="' + tab[0] + '" aria-controls="kb-panel-' + tab[0] + '" aria-selected="false" tabindex="-1">' + tab[1] + '</button>';
            }).join('') + '</div><div class="kb-detail-content">' + cardTabContent(c, currentStage) + '</div>' +
            '<footer class="kb-dialog-foot"><div>' +
            (c.stage !== 'done' ? '<button class="btn btn-ghost" id="kb-pay">Внести оплату</button>' : '') +
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
        loadCardHistory(c);
        initCardCombos();
        loadWarehouseFlags(c);
        dialog.querySelectorAll('input[data-wh]').forEach(function (input) {
            input.addEventListener('change', function () {
                if (!input.checked) { input.checked = true; return; } // списание необратимо с плитки
                if (!window.V2Api || !window.V2Api.token()) return;
                window.V2Api.api('/payments/transactions/' + input.dataset.wh, {
                    method: 'PATCH',
                    body: { is_warehouse_writeoff: true }
                }).then(function () {
                    notify('Товар списан со склада.');
                }).catch(function (e2) {
                    input.checked = false;
                    notify('Не списано: ' + (e2.detail || e2.message || 'ошибка'));
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
    // Карточка открывается из любого места: клик по элементу с data-card
    // (плита доски, строка очереди, строка реестра или документов в
    // финансах). Клик по интерактивному элементу внутри строки (галочка,
    // кнопка, ссылка) карточку не открывает.
    document.addEventListener('click', function(e) {
        if (suppressCardClick) return;
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
        // Enter/Space на кнопке удаления — её родное действие, не открытие.
        var delEl = e.target.closest ? e.target.closest('.kb-card-del') : null;
        if (delEl) return;
        var cardEl = e.target.closest ? e.target.closest('[data-card][role="button"]') : null;
        if (!cardEl) return;
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
                    other.textContent = 'Отменить';
                });
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
                        card.issued = Math.max(0, card.issued - cov.amount);
                        card.groupId = null;
                        // Invalidate server money cache
                        card.remaining_kop = undefined;
                        card.issued_total = undefined;
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
                // Item #3: отмена группы не должна быть молча локальной.
                if (group.serverTxId) {
                    apiMutate('annul-tx', { txId: group.serverTxId }).then(function (ok) {
                        if (ok === false) { notify('Отмена группы не прошла на сервере — обновите страницу.'); return; }
                        doCancelGroupLocal();
                    }).catch(function (err) {
                        notify('Отмена группы не прошла: ' + (err.detail || err.message || 'ошибка'));
                    });
                } else {
                    // Группа без serverTxId — не была сохранена на сервере,
                    // локальная отмена безопасна (создана в этой сессии).
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
                activeCard.issued = Math.max(0, activeCard.issued - doc.amount);
                // Invalidate server money cache so next read recomputes
                activeCard.remaining_kop = undefined;
                activeCard.issued_total = undefined;
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
            refresh();
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
            } else if (key === 'manager') {
                // Item #4: сопоставление по id, не по индексу массива.
                // Пустое значение или 'us-none' = менеджер не назначен.
                if (!value || value === 'us-none') value = null;
                else value = KBData.users.find(function (u) { return u.id === value; }) || null;
            }
            else if (key === 'stage') {
                if (activeCard.stage === 'done' || !KBData.statuses.some(function(st) { return st.id === value; })) return;
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
                    refresh();
                    return;
                }
                var match = KBData.clients.filter(function (cl) {
                    return cl.name && cl.name.toLowerCase() === value.trim().toLowerCase();
                })[0];
                if (match) {
                    activeCard.clientId = match.id;
                    activeCard.client = match.name;
                    apiMutate('card', { id: Number(activeCard.id), fields: { client_id: numericClientIdValue(match.id) } });
                    refresh();
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
            refresh();
            if (key === 'stage' || key === 'amount') {
                openCard(activeCard);
                var control = dialog.querySelector('[data-deal-field="' + key + '"]');
                if (!window.KBSelect.focus(control)) control.focus({ preventScroll: true });
            } else if (key === 'title') document.getElementById('kb-dialog-title').textContent = value;
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
        openCard(activeCard); refresh();
        // Item #12: focus safe — после перерендера элемент мог исчезнуть.
        var focusTarget = dialog.querySelector('[data-check="' + index + '"][data-flag="' + flag + '"]');
        if (focusTarget) focusTarget.focus();
    });
    document.getElementById('kb-q-board').onclick = function() { queueMode('board'); };
    document.getElementById('kb-q-pending').onclick = function() { queueMode('pending'); };
    document.getElementById('kb-q-history').onclick = function() { queueMode('history'); };
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
            '<label for="kb-nc-title" style="grid-column:1/-1"><span>Название*</span>' +
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
    if (trashBtn) trashBtn.onclick = function() { queueMode('trash'); };
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
    });
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
        var submitted = false;
        document.getElementById('kb-group-form').onsubmit = function (e) {
            e.preventDefault();
            if (submitted) return;
            var number = document.getElementById('kb-g-number').value.trim();
            var date = document.getElementById('kb-g-date').value;
            // Item #3: проверка дубля — с учётом пустой серии в серверных группах.
            var error = !number || !date ? 'Заполните дату и номер.' :
                KBData.groups.some(function (g) {
                    var gNum = (g.number || '').toLowerCase();
                    return gNum && gNum === number.toLowerCase();
                }) ? 'Накладная с таким номером уже выписана.' : '';
            if (error) { document.getElementById('kb-g-error').textContent = error; return; }
            submitted = true;
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
                    // Обновляем локальный кэш групп
                    var groupEntry = {
                        id: 'gr-' + g.id, serverId: g.id, serverTxId: result && result.invoice_number ? g.id : null,
                        name: g.name || 'Группа', client: picks[0].client || '', store: picks[0].store || '',
                        series: '', number: number, date: date, amount: totalAmount,
                        writtenOff: true,
                        covers: picks.map(function (c) { return { cardId: c.id, amount: null }; })
                    };
                    KBData.groups.push(groupEntry);
                    // Invalidate server money cache for affected cards
                    picks.forEach(function (c) {
                        c.remaining_kop = undefined;
                        c.issued_total = undefined;
                        c.groupId = 'gr-' + g.id;
                    });
                    groupPick = [];
                    closeDialog(); refresh();
                    notify('Групповая ТН ' + number + ' выписана на ' + picks.length + ' карточек · ' + money(totalAmount) + ' BYN.');
                });
            })
            .catch(function (err) {
                if (submitBtn) submitBtn.disabled = false;
                submitted = false;
                var errEl = document.getElementById('kb-g-error');
                if (errEl) errEl.textContent = (err && (err.detail || err.message)) || 'Не удалось выписать групповую накладную.';
            });
    }
    // Админка правит справочники → доска и фильтры перерисовываются.
    document.addEventListener('kb:dictionaries-changed', function () {
        populateStores();
        refresh();
    });
    // Пульт дня и «Клиент 360» открывают карточки через этот мост.
    window.KBBoard = {
        open: function (id) {
            var c = cards.find(function (card) { return card.id === id; });
            if (!c) return;
            location.hash = '#board';
            queueMode('board');
            openCard(c);
        }
    };
    // Восстановление состояния из адреса — после наполнения справочников.
    var bp = readBoardParams();
    if (bp.q) { state.search = bp.q; searchEl.value = bp.q; }
    if (bp.store && (bp.store === 'all' || KBData.stores.some(function (s) { return s.id === bp.store; }))) state.store = bp.store;
    populateStores(); setDensity(true); refresh(); queueMode(['pending', 'history'].indexOf(bp.mode) >= 0 ? bp.mode : 'board');
})();
