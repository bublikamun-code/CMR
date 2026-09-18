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
        // ввод: "1 234,56" / "1234.56" / "123456" (копейки не принимаем вслепую — считаем рубли и копейки)
        var s = String(str || '').trim().replace(/\s|\u00a0/g, '').replace(',', '.');
        if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
        var parts = s.split('.');
        var rub = parseInt(parts[0], 10);
        var kop = parts.length === 2 ? parseInt((parts[1] + '0').slice(0, 2), 10) : 0;
        return rub * 100 + kop;
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
    function remaining(card) { return card.amount - card.issued; }
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
        if (c.paidAmount >= c.amount && c.amount > 0) chips += '<span class="pill ok">оплачено полностью</span>';
        else if (c.paidAmount > 0) chips += '<span class="pill ok">оплачено ' + money(c.paidAmount) + '</span>';
        if (c.issued > 0 && c.issued < c.amount) chips += '<span class="pill warn">остаток ' + money(remaining(c)) + '</span>';
        return '<button type="button" class="kb-card" draggable="true" data-card="' + esc(c.id) + '" aria-haspopup="dialog">' +
            '<span class="kb-card-title">' + esc(c.title) + '</span>' +
            '<span class="kb-card-meta num">' + esc(c.id) + ' · ' + esc(c.deadline) + '</span>' +
            '<span class="kb-card-chips">' + chips + '</span>' +
            '<span class="kb-card-foot num"><b>' + money(c.amount) + ' BYN</b>' +
            (c.issued > 0 ? ' · списано ' + money(c.issued) : '') +
            (c.docs.length ? ' · ТН: ' + c.docs.length : '') +
            (c.groupId ? ' · групповая ТН' : '') + '</span>' +
            '</button>';
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
        moveCard(drag.card, drag.column.dataset.stage, drag.beforeId);
        apiMutate('status', { id: Number(drag.card.id), status: stageName(drag.column.dataset.stage) });
        cleanupDrag(); refresh();
    });
    document.addEventListener('dragend', cleanupDrag);
    document.addEventListener('drop', cleanupDrag);
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
    var searchToggle = document.getElementById('kb-search-toggle');
    function toggleSearch(open) {
        document.getElementById('kb-search-wrap').classList.toggle('is-open', open);
        document.getElementById('kb-search-field').inert = !open;
        searchToggle.setAttribute('aria-expanded', String(open));
        searchToggle.setAttribute('aria-label', open ? 'Закрыть поиск' : 'Открыть поиск');
        if (open) searchEl.focus();
        else { searchEl.value = ''; state.search = ''; writeBoardParams({ q: '' }); refresh(); searchToggle.focus(); }
    }
    searchToggle.onclick = function() { toggleSearch(searchToggle.getAttribute('aria-expanded') !== 'true'); };
    document.getElementById('kb-search-close').onclick = function() { toggleSearch(false); };
    searchEl.addEventListener('keydown', function(e) { if (e.key === 'Escape') { e.preventDefault(); toggleSearch(false); } });
    searchEl.addEventListener('input', function () { state.search = searchEl.value; writeBoardParams({ q: state.search }); refresh(); });
    document.getElementById('kb-density').onclick = function() { setDensity(!state.compact); };
    function setDensity(compact) {
        state.compact = compact;
        rootEl.classList.toggle('kb-compact', compact);
        rootEl.classList.toggle('kb-normal', !compact);
        document.getElementById('kb-density').setAttribute('aria-pressed', String(compact));
    }

    var dialog = document.getElementById('kb-dialog');
    var activeCard = null;
    function refresh() {
        var scroll = Array.from(boardEl.querySelectorAll('.kb-cards')).map(function(el) { return el.scrollTop; });
        renderBoard(); renderQueue();
        boardEl.querySelectorAll('.kb-cards').forEach(function(el, i) { el.scrollTop = scroll[i] || 0; });
        document.dispatchEvent(new CustomEvent('kb:documents', { detail: cards }));
        document.dispatchEvent(new CustomEvent('kb:groups', { detail: KBData.groups }));
    }
    document.addEventListener('fin:originals', function(e) {
        var c = cards.find(function(card) { return card.id === e.detail.cardId; });
        if (!c || !c.docs[e.detail.index]) return;
        c.docs[e.detail.index][e.detail.field] = e.detail.checked;
    });
    // Групповая выписка: выбранные в очереди карточки закрываются ОДНОЙ накладной
    // (аналог «групп списаний» на проде). Отмена возвращает остаток каждой.
    var groupPick = [];
    var editingDoc = null; // индекс накладной в открытой карточке, которую правят
    function renderQueue() {
        // Очередь — по остатку к выписке (как на проде): полностью выписанная
        // карточка не висит в «На списание», даже если статус ещё там же,
        // а в «Списано» попадает и без смены статуса.
        var list = visibleCards().filter(function (c) {
            if (state.queueMode === 'pending') return c.stage === 'writeoff' && remaining(c) > 0;
            return c.stage === 'done' || (c.stage === 'writeoff' && c.issued > 0 && remaining(c) <= 0);
        });
        document.getElementById('kb-queue-count').textContent = cards.filter(function(c) { return c.stage === 'writeoff' && remaining(c) > 0; }).length;
        groupPick = groupPick.filter(function (id) {
            var c = cards.find(function (x) { return x.id === id; });
            return c && c.stage === 'writeoff' && remaining(c) > 0;
        });
        var pending = state.queueMode === 'pending';
        var allPicked = pending && list.length > 0 && list.every(function (c) { return groupPick.indexOf(c.id) >= 0; });
        var toolbar = pending ?
            '<div class="kb-q-toolbar"><label class="kb-q-check"><input type="checkbox" id="kb-group-all"' + (allPicked ? ' checked' : '') + (list.length ? '' : ' disabled') + '> Выбрать все</label>' +
            '<button type="button" class="btn btn-primary btn-sm" id="kb-group-issue"' + (groupPick.length > 1 ? '' : ' disabled') + '>Накладная на группу' + (groupPick.length > 1 ? ' (' + groupPick.length + ')' : '') + '</button></div>' : '';
        document.getElementById('kb-queue-body').innerHTML = '<p class="kb-note">Поиск и магазин общие с доской. Найдено: ' + list.length + '</p>' + toolbar + list.map(function(c) {
            return '<div class="kb-q-row" role="button" tabindex="0" data-card="' + esc(c.id) + '" aria-haspopup="dialog" aria-label="Открыть ' + esc(c.id + ' · ' + c.title) + '">' +
                (pending ? '<input type="checkbox" class="kb-q-pick" data-group-pick="' + esc(c.id) + '" aria-label="Включить ' + esc(c.id) + ' в групповую накладную"' + (groupPick.indexOf(c.id) >= 0 ? ' checked' : '') + '>' : '') +
                '<div><b>' + esc(c.id + ' · ' + c.title) + '</b><div>' + esc(KBData.storeName(c.store)) + ' · К выписке ' + money(remaining(c)) + ' BYN</div></div></div>';
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
    function notify(text) {
        var toast = document.getElementById('kb-toast');
        toast.hidden = false;
        toast.innerHTML = '<span>' + esc(text) + '</span><button class="btn-quiet" id="kb-see-history">Списано</button><button class="btn-quiet" id="kb-dismiss" aria-label="Закрыть уведомление">×</button>';
        document.getElementById('kb-dismiss').onclick = function() { toast.hidden = true; };
        document.getElementById('kb-see-history').onclick = function() {
            queueMode('history');
            document.getElementById('kb-q-history').focus(); toast.hidden = true;
        };
    }
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
        return dealField(key, label, '<input id="kb-deal-' + key + '" data-deal-field="' + key + '" type="' + (type || 'text') + '" value="' + esc(key === 'amount' ? money(c.amount) : c[key]) + '"' + (key === 'amount' ? ' inputmode="decimal"' : ' maxlength="240"') + (key === 'amount' && c.stage === 'done' ? ' disabled' : '') + '>');
    }
    function dealSelect(key, label, values, selected, disabled) {
        return dealField(key, label, '<select id="kb-deal-' + key + '" data-deal-field="' + key + '"' + (disabled ? ' disabled' : '') + '>' + optionsHTML(values, selected) + '</select>');
    }
    window.KBPayment.escape = esc;
    // Save only validated, normalized values. Raw input remains in KBPayment's
    // per-card WeakMap, including inactive date/day/prepay fields.
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
            status.textContent = result.valid ? 'Сохранено' + (result.due ? ' · Оплатить до ' + result.due : '') +
                (result.prepay !== null ? ' · Предоплата ' + money(result.prepay) + ' BYN' : '') :
                'Не сохранено · ' + Object.values(result.errors).join(' ');
        }
        // Sync validation on the visible combobox, not only its hidden native.
        var mode = dialog.querySelector('#kb-payment-mode');
        if (mode) window.KBSelect.enhance(mode);
    }
    function renderPayment(c, reveal) {
        window.KBSelect.close();
        document.getElementById('kb-payment-details').outerHTML = window.KBPayment.render(c);
        updatePayment(c);
        window.KBSelect.enhance(document.getElementById('kb-payment-details'));
        if (reveal) {
            document.getElementById('kb-tab-overview').click();
            dialog.querySelector('.kb-detail-content').scrollTop = 0;
        }
    }
    // Card-only rendering; documents describe demo issuance, never payment events.
    // Preview-only supplier IDs and per-item files; no production uploads.
    var procurementSuppliers = [['demo-1', 'Люстра Опт'], ['demo-2', 'СветКомплект'], ['demo-3', 'ЭлектроСнаб']];
    var procurementFiles = new Map();
    function procurementFields(c, item) {
        var id = esc(item.id), disabled = c.stage === 'done' ? ' disabled' : '';
        var attachment = procurementFiles.get(item);
        return '<div class="kb-procurement-fields"><label class="kb-edit-field" for="kb-supplier-' + id + '"><span>Поставщик</span><select id="kb-supplier-' + id + '" data-item-supplier="' + id + '"' + disabled + '>' +
            [['', 'Выберите поставщика']].concat(window.KBSuppliers ? window.KBSuppliers.list() : procurementSuppliers).map(function(s) {
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

    function cardTabContent(c, currentStage) {
        var cl = checklistSummary(c);
        var info = dealInput(c, 'title', 'Название') + dealInput(c, 'client', 'Клиент') +
            dealSelect('store', 'Магазин', KBData.stores.map(function (s) { return [s.id, s.name]; }), c.store) +
            dealSelect('manager', 'Менеджер', KBData.users.map(function (u, i) { return [i, u.full_name]; }), KBData.users.indexOf(c.manager)) +
            dealInput(c, 'deadline', 'Срок · ДД.ММ.ГГГГ') + dealInput(c, 'amount', 'Сумма, BYN') +
            dealField('note', 'Заметка', '<textarea id="kb-deal-note" data-deal-field="note" rows="2" maxlength="4000">' + esc(c.note) + '</textarea>');
        var checks = c.checklist.map(function(item, i) {
            return '<div class="kb-check-item" data-procurement-item="' + esc(item.id) + '"><b>' + esc(item.label) + '</b>' + procurementFields(c, item) + '<div class="kb-check-row"><label><input type="checkbox" data-check="' + i + '" data-flag="ordered" ' + (item.ordered ? 'checked' : '') + (item.received || c.stage === 'done' ? ' disabled' : '') + '> Заказано</label><label><input type="checkbox" data-check="' + i + '" data-flag="received" ' + (item.received ? 'checked' : '') + (!item.ordered || c.stage === 'done' ? ' disabled' : '') + '> Получено</label></div></div>';
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
            return '<li class="kb-detail-doc"><div><b>' + esc((d.series ? d.series + ' ' : '') + d.number) + '</b><span>' + esc(d.date) + '</span><label class="kb-originals"><input type="checkbox" data-originals="' + i + '"' + (d.originalsReturned ? ' checked' : '') + '> Оригинал ТН возвращён</label></div><div class="kb-doc-side"><strong>' + money(d.amount) + ' <small>BYN</small></strong>' +
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
            '<h3 class="kb-detail-section-title">Закупка у поставщиков <span>' + cl.received + '/' + cl.total + '</span></h3>' +
            '<p class="kb-detail-hint">Файл до 25 МБ</p>' +
            (checks || '<p class="kb-detail-empty">Закупка не требуется.</p>') +
            '<h3 class="kb-detail-section-title">Вложения сделки <span>' + (c.attachments || []).length + '</span></h3>' +
            ((c.attachments || []).length ? c.attachments.map(function (a) {
                return '<div class="kb-check-row"><span class="grow trunc">' + esc(a.name || '') + '</span></div>';
            }).join('') : '<p class="kb-detail-empty">Вложений нет.</p>') +
            '</section>' +
            '<section id="kb-panel-invoices" role="tabpanel" aria-labelledby="kb-tab-invoices" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">Выписанные накладные <span>' + (c.docs.length + (c.groupId ? 1 : 0)) + '</span></h3>' +
            (groupEntry + docs ? '<ul class="kb-detail-docs">' + groupEntry + docs + '</ul>' : '<div class="kb-detail-empty"><b>Накладных пока нет</b><p>Демо-выписка доступна на этапе «На списание».</p></div>') +
            '<p class="kb-detail-hint">Выписанные ТН доступны в «Финансы → Документы». Оплата учитывается отдельно.</p></section>' +
            '<section id="kb-panel-history" role="tabpanel" aria-labelledby="kb-tab-history" tabindex="0" hidden>' +
            '<h3 class="kb-detail-section-title">История в этом демо</h3><ol class="kb-detail-history">' + history +
            '<li><span class="kb-detail-event-dot" aria-hidden="true"></span><div><b>Текущий этап: ' + esc(currentStage) + '</b><p>Состояние карточки сейчас; время перехода не записывается.</p></div></li></ol>' +
            '<p class="kb-detail-hint">Показаны выписки текущей сессии, не полный журнал изменений.</p></section>';
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
            input.closest('.kb-edit-field').hidden = hidden;
            if (!hidden) visible++;
        });
        var toggle = document.getElementById('kb-hide-filled');
        toggle.setAttribute('aria-pressed', String(state.hideFilled));
        toggle.textContent = state.hideFilled ? 'Показать все поля' : 'Скрыть заполненные поля';
        document.getElementById('kb-overview-empty').hidden = visible !== 0;
    }
    var renderingCard = false;
    function openCard(c) {
        window.KBSelect.close();
        window.KBPayment.draft(c);
        if (!activeCard || activeCard.id !== c.id) editingDoc = null;
        var preserved = null;
        if (dialog.open && activeCard === c && dialog.querySelector('#kb-overview-fields')) {
            preserved = {};
            dialog.querySelectorAll('#kb-overview-fields [data-deal-field]').forEach(function(input) {
                preserved[input.dataset.dealField] = { value: input.value, hidden: input.closest('.kb-edit-field').hidden,
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
            '<div class="kb-detail-summary"><dl class="kb-detail-money"><div class="kb-detail-total"><dt>Сумма сделки</dt><dd>' + money(c.amount) + ' <small>BYN</small></dd></div><div><dt>Оплачено</dt><dd>' + money(c.paidAmount) + ' <small>BYN</small></dd></div><div><dt>Выписано</dt><dd>' + money(c.issued) + ' <small>BYN</small></dd></div><div><dt>Осталось выписать</dt><dd>' + money(remaining(c)) + ' <small>BYN</small></dd></div></dl>' +
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
            '<button class="btn btn-ghost" data-close>Закрыть</button></div></footer></div>';
        renderingCard = false;
        function selectTab(name, focus) {
            dialog.dataset.cardTab = name;
            dialog.querySelectorAll('[data-card-tab]').forEach(function(button) {
                var selected = button.dataset.cardTab === name;
                button.setAttribute('aria-selected', String(selected));
                button.tabIndex = selected ? 0 : -1;
                document.getElementById(button.getAttribute('aria-controls')).hidden = !selected;
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
        updatePayment(c);
        window.KBSelect.enhance(dialog);
        document.getElementById('kb-hide-filled').onclick = function() {
            state.hideFilled = !state.hideFilled;
            updateOverviewVisibility(null);
        };
        selectTab(selectedTab, false);
        if (!dialog.open) dialog.showModal();
        dialog.querySelector('.kb-detail-content').scrollTop = scrollTop;
        var send = document.getElementById('kb-send');
        if (send) send.onclick = function() { c.stage = 'writeoff'; apiMutate('status', { id: Number(c.id), status: stageName('writeoff') }); dialog.close(); refresh(); notify(c.id + ' — передана в очередь выписки.'); };
        var issue = document.getElementById('kb-issue');
        if (issue) issue.onclick = function() { openIssue(c); };
        var pay = document.getElementById('kb-pay');
        if (pay) pay.onclick = function() { openPay(c); };
    }

    // Оплата — факт денег (paid_amount + статус), отдельно от условий и выписки.
    function openPay(c) {
        window.KBSelect.close();
        var debt = c.amount - c.paidAmount;
        dialog.innerHTML = '<h2 id="kb-dialog-title">Внести оплату · ' + esc(c.id) + '</h2>' +
            '<p class="kb-detail-hint">Долг: ' + money(debt) + ' BYN · Оплачено: ' + money(c.paidAmount) + ' BYN</p>' +
            '<form id="kb-pay-form" class="kb-form"><div class="kb-field"><label for="kb-pay-amount">Сумма, BYN</label>' +
            '<input id="kb-pay-amount" inputmode="decimal" value="' + money(debt) + '" required></div>' +
            '<p class="kb-form-error" id="kb-pay-error" role="alert"></p>' +
            '<p class="kb-form-hint">Оплата — факт денег. Условия оплаты задаются отдельным полем и на оплату не влияют.</p>' +
            '<button class="btn btn-primary" type="submit">Провести оплату</button><button class="btn btn-ghost" type="button" data-close>Отмена</button></form>';
        document.getElementById('kb-pay-amount').focus();
        var submitted = false;
        document.getElementById('kb-pay-form').onsubmit = function (e) {
            e.preventDefault();
            if (submitted) return;
            var sum = parseMoney(document.getElementById('kb-pay-amount').value);
            var error = !Number.isSafeInteger(sum) || sum <= 0 || sum > debt ? 'Сумма должна быть больше нуля и не превышать долг.' : '';
            if (error) { document.getElementById('kb-pay-error').textContent = error; return; }
            submitted = true;
            c.paidAmount += sum;
            apiMutate('register-payment', { id: Number(c.id), paid: c.paidAmount / 100, status: c.paidAmount >= c.amount ? 'Оплачен' : 'Частично' });
            dialog.close(); refresh();
            notify(c.id + ' — оплата ' + money(sum) + ' BYN проведена.');
        };
    }

    function openIssue(c) {
        window.KBSelect.close();
        dialog.innerHTML = '<h2 id="kb-dialog-title">Выписать накладную · ' + esc(c.id) + '</h2><p>Остаток: ' + money(remaining(c)) + ' BYN</p><form id="kb-issue-form" class="kb-form"><div class="kb-field"><label for="kb-date">Дата выписки</label><input id="kb-date" type="date" required></div><div class="kb-field"><label for="kb-series">Серия</label><input id="kb-series" maxlength="30" required></div><div class="kb-field"><label for="kb-number">Номер</label><input id="kb-number" maxlength="50" required></div><div class="kb-field"><label for="kb-amount">Сумма, BYN</label><input id="kb-amount" inputmode="decimal" value="' + money(remaining(c)) + '" required></div><p class="kb-form-error" id="kb-error" role="alert"></p><p class="kb-form-hint">Частичная выписка оставляет остаток в очереди. Полная — переносит в «Списано». ТН появится в финансовых документах.</p><button class="btn btn-primary" type="submit">Подтвердить выписку</button><button class="btn btn-ghost" type="button" data-close>Отмена</button></form>';
        document.getElementById('kb-date').focus();
        var submitted = false;
        document.getElementById('kb-issue-form').onsubmit = function(e) {
            e.preventDefault();
            if (submitted) return;
            var amount = parseMoney(document.getElementById('kb-amount').value);
            var series = document.getElementById('kb-series').value.trim();
            var number = document.getElementById('kb-number').value.trim();
            var date = document.getElementById('kb-date').value;
            var error = !series || !number || !date ? 'Заполните дату, серию и номер.' :
                !Number.isSafeInteger(amount) || amount <= 0 || amount > remaining(c) ? 'Сумма должна быть больше нуля и не превышать остаток.' :
                c.docs.some(function(d) { return d.series.toLowerCase() === series.toLowerCase() && d.number.toLowerCase() === number.toLowerCase(); }) ? 'Эта накладная уже выписана по карточке.' : '';
            if (error) { document.getElementById('kb-error').textContent = error; return; }
            document.getElementById('kb-error').textContent = '';
            submitted = true;
            const doc = { series: series, number: number, date: ruFromIso(date), amount: amount, originalsReturned: false };
            c.docs.push(doc);
            apiMutate('issue-invoice', { id: Number(c.id), number: number, date: date, amount: amount / 100 }).then(function (saved) {
                if (saved && saved.id) doc.txId = saved.id;
            });
            c.issued += amount;
            if (remaining(c) === 0) c.stage = 'done';
            dialog.close(); refresh();
            var nextCard = document.querySelector('#kb-queue-body [data-card="' + c.id + '"]');
            (nextCard || document.getElementById('kb-q-pending')).focus({ preventScroll: true });
            notify(c.id + (c.stage === 'done' ? ' — выписана полностью. Перенесена в «Списано».' : ' — частичная выписка. Осталось ' + money(remaining(c)) + ' BYN.'));
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
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
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
                group.covers.forEach(function (cov) {
                    var card = cards.find(function (x) { return x.id === cov.cardId; });
                    if (!card) return;
                    card.issued = Math.max(0, card.issued - cov.amount);
                    card.groupId = null;
                    var w = writeoffStatus();
                    if (remaining(card) > 0 && card.stage === 'done' && w) card.stage = w.id;
                });
                if (group.serverTxId) apiMutate('annul-tx', { txId: group.serverTxId });
                KBData.groups.splice(KBData.groups.indexOf(group), 1);
                refresh();
                notify('Групповая ТН ' + group.series + ' ' + group.number + ' отменена. Остатки возвращены ' + group.covers.length + ' карточкам.');
                openCard(activeCard);
                var groupInvoicesTab = document.getElementById('kb-tab-invoices');
                if (groupInvoicesTab) groupInvoicesTab.click();
                return;
            }
            var index = Number(target);
            var doc = activeCard.docs[index];
            if (!doc) return;
            activeCard.docs.splice(index, 1);
            activeCard.issued = Math.max(0, activeCard.issued - doc.amount);
            if (doc.txId) apiMutate('annul-tx', { txId: doc.txId });
            var writeoff = writeoffStatus();
            if (activeCard.stage === 'done' && remaining(activeCard) > 0 && writeoff) activeCard.stage = writeoff.id;
            refresh();
            notify(activeCard.id + ' — ТН ' + doc.series + ' ' + doc.number + ' отменена. Остаток к выписке ' + money(remaining(activeCard)) + ' BYN.');
            openCard(activeCard);
            var invoicesTab = document.getElementById('kb-tab-invoices');
            if (invoicesTab) invoicesTab.click();
            return;
        }
        if (e.target.closest('[data-close]')) dialog.close();
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
            activeCard.docs[Number(input.dataset.originals)].originalsReturned = input.checked;
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
                if (!Number.isSafeInteger(value) || value <= 0 || value < activeCard.issued || (activeCard.issued > 0 && value === activeCard.issued)) error = 'Сумма должна быть больше нуля и уже выписанной суммы.';
            } else if ((key === 'title' || key === 'client') && !value) error = 'Заполните поле.';
            else if (key === 'deadline') {
                var parts = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(value);
                var date = parts && new Date(Number(parts[3]), Number(parts[2]) - 1, Number(parts[1]));
                if (!parts || date.getFullYear() !== Number(parts[3]) || date.getMonth() !== Number(parts[2]) - 1 || date.getDate() !== Number(parts[1])) error = 'Введите дату в формате ДД.ММ.ГГГГ.';
            } else if (key === 'manager') value = KBData.users[Number(value)];
            else if (key === 'stage') {
                if (activeCard.stage === 'done' || !KBData.statuses.some(function(st) { return st.id === value; })) return;
                moveCard(activeCard, value, null);
            }
            input.setCustomValidity(error);
            input.setAttribute('aria-invalid', String(!!error));
            if (error) { input.reportValidity(); return; }
            activeCard[key] = value;
            if (key === 'title') apiMutate('card', { id: Number(activeCard.id), fields: { title: value } });
            else if (key === 'note') apiMutate('card', { id: Number(activeCard.id), fields: { description: value } });
            else if (key === 'amount') apiMutate('card', { id: Number(activeCard.id), fields: { total_amount: value / 100 } });
            else if (key === 'deadline') apiMutate('card', { id: Number(activeCard.id), fields: { due_date: isoFromRu(value) } });
            else if (key === 'store') apiMutate('card', { id: Number(activeCard.id), fields: { store_location: KBData.storeName(value) } });
            else if (key === 'manager') apiMutate('card', { id: Number(activeCard.id), fields: { owner_id: value ? Number(String(value.id).replace('us-', '')) || null : null } });
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
        openCard(activeCard); refresh();
        dialog.querySelector('[data-check="' + index + '"][data-flag="' + flag + '"]').focus();
    });
    document.getElementById('kb-q-board').onclick = function() { queueMode('board'); };
    document.getElementById('kb-q-pending').onclick = function() { queueMode('pending'); };
    document.getElementById('kb-q-history').onclick = function() { queueMode('history'); };
    // Делегирование для панели группы: чекбоксы пересобираются при каждой отрисовке.
    document.getElementById('kb-queue-body').addEventListener('change', function (e) {
        var pick = e.target.closest('[data-group-pick]');
        if (pick) {
            if (pick.checked) groupPick.push(pick.dataset.groupPick);
            else groupPick = groupPick.filter(function (id) { return id !== pick.dataset.groupPick; });
            renderQueue();
            return;
        }
        if (e.target.id === 'kb-group-all') {
            groupPick = e.target.checked ? visibleCards().filter(function (c) { return c.stage === 'writeoff' && remaining(c) > 0; }).map(function (c) { return c.id; }) : [];
            renderQueue();
        }
    });
    document.getElementById('kb-queue-body').addEventListener('click', function (e) {
        if (e.target.closest('#kb-group-issue')) openGroupIssue();
    });
    function openGroupIssue() {
        var picks = groupPick.map(function (id) { return cards.find(function (c) { return c.id === id; }); }).filter(Boolean);
        if (picks.length < 2) return;
        var total = picks.reduce(function (a, c) { return a + remaining(c); }, 0);
        var clients = picks.map(function (c) { return c.client; }).filter(function (v, i, arr) { return arr.indexOf(v) === i; });
        var stores = picks.map(function (c) { return KBData.storeName(c.store); }).filter(function (v, i, arr) { return arr.indexOf(v) === i; });
        window.KBSelect.close();
        dialog.innerHTML = '<h2 id="kb-dialog-title">Групповая накладная · ' + picks.length + ' карточек</h2>' +
            '<dl class="kb-kv kb-group-kv"><dt>Карточки</dt><dd>' + picks.map(function (c) { return esc(c.id + ' · ' + money(remaining(c)) + ' BYN'); }).join('<br>') + '</dd>' +
            '<dt>Клиент</dt><dd>' + esc(clients.length === 1 ? clients[0] : 'Разные клиенты') + '</dd>' +
            '<dt>Магазин</dt><dd>' + esc(stores.length === 1 ? stores[0] : 'Разные магазины') + '</dd></dl>' +
            '<p class="kb-detail-hint">Одна накладная на всю группу — как «группы списаний» на проде. Отмена вернёт остаток каждой карточке.</p>' +
            '<form id="kb-group-form" class="kb-form"><div class="kb-field"><label for="kb-g-date">Дата выписки</label><input id="kb-g-date" type="date" required></div>' +
            '<div class="kb-field"><label for="kb-g-series">Серия</label><input id="kb-g-series" maxlength="30" required></div>' +
            '<div class="kb-field"><label for="kb-g-number">Номер</label><input id="kb-g-number" maxlength="50" required></div>' +
            '<div class="kb-field"><label for="kb-g-amount">Сумма, BYN</label><input id="kb-g-amount" value="' + money(total) + '" disabled></div>' +
            '<p class="kb-form-error" id="kb-g-error" role="alert"></p>' +
            '<button class="btn btn-primary" type="submit">Выписать на группу</button><button class="btn btn-ghost" type="button" data-close>Отмена</button></form>';
        document.getElementById('kb-g-date').focus();
        var submitted = false;
        document.getElementById('kb-group-form').onsubmit = function (e) {
            e.preventDefault();
            if (submitted) return;
            var series = document.getElementById('kb-g-series').value.trim();
            var number = document.getElementById('kb-g-number').value.trim();
            var date = document.getElementById('kb-g-date').value;
            var error = !series || !number || !date ? 'Заполните дату, серию и номер.' :
                KBData.groups.some(function (g) { return g.series.toLowerCase() === series.toLowerCase() && g.number.toLowerCase() === number.toLowerCase(); }) ? 'Накладная с таким номером уже выписана.' : '';
            if (error) { document.getElementById('kb-g-error').textContent = error; return; }
            submitted = true;
            issueGroup(picks, clients, stores, series, number, date, total);
        };
        if (!dialog.open) dialog.showModal();
    }
    function issueGroup(picks, clients, stores, series, number, date, total) {
        var gid = 'G' + KBData.nextGroupId;
        var group = {
            id: gid, name: 'Группа ' + KBData.nextGroupId,
            client: clients.length === 1 ? clients[0] : 'Разные клиенты',
            store: stores.length === 1 ? stores[0] : 'Разные магазины',
            series: series, number: number, date: date, amount: total,
            covers: picks.map(function (c) { return { cardId: c.id, amount: remaining(c) }; })
        };
        group.covers.forEach(function (cov) {
            var card = cards.find(function (x) { return x.id === cov.cardId; });
            card.issued += cov.amount;
            card.groupId = gid;
            if (remaining(card) <= 0) card.stage = 'done';
        });
        KBData.nextGroupId++;
        KBData.groups.push(group);
        groupPick = [];
        apiMutate('group-issue', {
            cards: picks.map(function (c) { return Number(c.id); }),
            name: group.name, number: number, date: date, amount: total / 100
        }).then(function (saved) {
            if (saved && saved.id) { group.serverId = saved.id; group.serverTxId = saved.txId; }
        });
        dialog.close(); refresh();
        notify('Групповая ТН ' + series + ' ' + number + ' выписана на ' + picks.length + ' карточек · ' + money(total) + ' BYN.');
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
