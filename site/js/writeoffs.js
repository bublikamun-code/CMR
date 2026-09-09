let _writeoffsSearchQuery = '';
let _writeoffsData = { transactions: [], cards: [], groups: [] };

let _writeoffsSearchDebounce = null;

const ICON_WARNING = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

document.addEventListener('DOMContentLoaded', () => {
    setupWriteoffDragAndDrop();
    if (document.getElementById('page-finance')?.classList.contains('active')
        && document.getElementById('page-writeoffs')?.classList.contains('active')) loadWriteoffsBoard();

    const search = document.getElementById('writeoffs-search');
    const clearBtn = document.getElementById('writeoffs-search-clear');
    if (search) {
        search.addEventListener('input', (e) => {
            _writeoffsSearchQuery = e.target.value;
            if (clearBtn) clearBtn.classList.toggle('hidden', !_writeoffsSearchQuery);
            clearTimeout(_writeoffsSearchDebounce);
            _writeoffsSearchDebounce = setTimeout(() => renderWriteoffsBoard(), 200);
        });
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                search.value = '';
                _writeoffsSearchQuery = '';
                clearBtn.classList.add('hidden');
                renderWriteoffsBoard();
                search.focus();
            });
        }
    }

    loadWriteoffsBoard();
});

// Склады для доски списаний берутся из данных (store_location транзакций
// и групп), порядок — по APP_STORES, подпись — label оттуда, если есть.
// Пустых блоков нет: склад появляется на доске, когда по нему есть хоть
// одна сделка к списанию/списанная или группа. «БН» — способ оплаты,
// а не точка: данных со складом «БН» нет, и блок он не создаёт.
function computeWriteoffStores() {
    const { transactions, cards, groups } = _writeoffsData;
    const names = new Set();
    (transactions || []).forEach(t => {
        if (t.is_document) return;
        if ((parseFloat(t.amount) || 0) <= 0) return;
        if (!t.card_id || !t.store_location) return;
        const c = (cards || []).find(x => x.id === t.card_id);
        if (c && (c.status === 'На списание' || c.status === 'Закрыто') && !c.writeoff_group_id) {
            names.add(t.store_location);
        }
    });
    (groups || []).forEach(g => { if (g.store_location) names.add(g.store_location); });
    const order = (typeof APP_STORES !== 'undefined' ? APP_STORES : []).map(s => s.value);
    const labels = new Map((typeof APP_STORES !== 'undefined' ? APP_STORES : []).map(s => [s.value, s.label]));
    return Array.from(names).sort((a, b) => {
        const ia = order.indexOf(a), ib = order.indexOf(b);
        return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b, 'ru');
    }).map(value => ({ value, label: labels.get(value) || value }));
}

function ensureStoreBlocks() {
    const container = document.querySelector('.writeoffs-container');
    if (!container) return;
    const wanted = computeWriteoffStores();
    if (!wanted.length) {
        // данных нет вообще — показываем блоки всех магазинов с пустыми колонками
        const all = (typeof APP_STORES !== 'undefined' ? APP_STORES : []).filter(s => s.value !== 'БН');
        if (all.length) { renderStoreBlocks(container, all); return; }
        container.innerHTML = '';
        return;
    }
    renderStoreBlocks(container, wanted);
}

function renderStoreBlocks(container, stores) {
    const existing = Array.from(container.querySelectorAll('.writeoff-store-block')).map(b => b.dataset.store);
    const wantedValues = stores.map(s => s.value);
    if (existing.length === wantedValues.length && existing.every((s, i) => s === wantedValues[i])) return;
    container.innerHTML = stores.map(s => `
        <div class="writeoff-store-block" data-store="${escapeHtml(s.value)}">
            <h3>${escapeHtml(s.label)}</h3>
            <div class="writeoff-columns">
                <div class="kanban-column writeoff-column" data-store="${escapeHtml(s.value)}" data-status="false">
                    <h4>На списание</h4>
                    <div class="writeoff-cards"></div>
                </div>
                <div class="kanban-column writeoff-column" data-store="${escapeHtml(s.value)}" data-status="true">
                    <h4>Списано</h4>
                    <div class="writeoff-cards"></div>
                </div>
            </div>
        </div>`).join('');
}

async function loadWriteoffsBoard() {
    if (!hasToken()) return;
    if (!document.querySelector('.writeoffs-container')) return;
    ensureStoreBlocks();
    const boardExists = document.querySelector('.writeoff-column');
    if (!boardExists) return;

    try {
        const [transactions, cards, groups] = await Promise.all([
            // grouped=false — доске нужны СЫРЫЕ записи (накладные + остаток),
            // а не схлопнутые строки реестра оплат
            apiFetch('/payments/transactions?grouped=false'),
            apiFetch('/kanban/cards'),
            apiFetch('/writeoffs/groups/')
        ]);

        _writeoffsData = { transactions, cards, groups };
        renderWriteoffsBoard();
    } catch (error) {
        console.error(error);
        const container = document.querySelector('.writeoffs-container');
        if (container) {
            container.innerHTML = '';
            container.appendChild(renderAlert({ type: 'error', title: 'Ошибка загрузки списания', message: error.message, onRetry: () => loadWriteoffsBoard() }));
        }
    }
}

// --- Месячные группы колонки «Списано» (2026-09-03) ---
// Плитки списанных накладных группируются по месяцу даты накладной
// (иначе — даты оплаты). По умолчанию развёрнут только текущий месяц,
// выбор пользователя запоминается в localStorage.
const WO_MONTH_PAGE = 20;

function monthKeyOf(dateStr) {
    if (!dateStr) return null;
    const d = new Date(dateStr.length === 10 ? dateStr + 'T00:00:00' : dateStr);
    if (isNaN(d)) return null;
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}

function monthLabel(key) {
    if (key === 'none') return 'Без даты';
    let s = new Date(key + '-02T00:00:00').toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
    // «сентябрь 2026 г.» → «Сентябрь 2026»: хвост « г.» в узких шапках
    // месячных групп повисал отдельной строкой
    s = s.replace(/\s*г\.?$/, '').trim();
    return s.charAt(0).toUpperCase() + s.slice(1);
}

// key месяца -> true (развёрнут). Отсутствие ключа = дефолт.
function woMonthStates() {
    try { return JSON.parse(localStorage.getItem('crm_writeoffs_months')) || {}; } catch (e) { return {}; }
}
function woSaveMonthStates(states) {
    try { localStorage.setItem('crm_writeoffs_months', JSON.stringify(states)); } catch (e) {}
}

// Сумма с видимого текста плитки или самого элемента суммы: у списанных
// карточек data-amount равен нулю (остаток), поэтому dataset не подходит.
function parseTileMoney(el) {
    const amountEl = el.classList && el.classList.contains('card-amount')
        ? el
        : el.querySelector('.card-amount');
    const t = (amountEl?.textContent || '').replace(/[\s\u00a0]/g, '').replace('BYN', '').replace(',', '.');
    return parseFloat(t) || 0;
}

function renderDoneColumn(container, items) {
    const buckets = new Map();
    items.forEach(({ el, month }) => {
        const key = month || 'none';
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(el);
    });

    const states = woMonthStates();
    const nowKey = monthKeyOf(new Date().toISOString().slice(0, 10));
    const keys = Array.from(buckets.keys()).sort().reverse(); // новые месяцы сверху

    keys.forEach(key => {
        const els = buckets.get(key);
        const sum = els.reduce((s, el) => s + parseTileMoney(el), 0);
        const groupEl = document.createElement('div');
        groupEl.className = 'wo-month-group';
        groupEl.dataset.month = key;
        const collapsed = states[key] !== undefined ? !states[key] : (key === 'none' || key !== nowKey);
        if (collapsed) groupEl.classList.add('collapsed');

        const header = document.createElement('div');
        header.className = 'wo-month-header';
        // « BYN» в шапке месяца не показываем: валюта видна в заголовке
        // склада и на каждой плитке, а ширины колонки не хватало — сумма
        // резалась краем («…BY»). Полное значение — в title.
        const sumStr = formatMoneyBYN(sum);
        header.innerHTML = `
            <span class="wo-month-arrow">${collapsed ? '&#9656;' : '&#9662;'}</span>
            <span class="wo-month-name">${monthLabel(key)}</span>
            <span class="wo-month-count">${els.length}</span>
            <span class="wo-month-sum tabular-nums" title="${sumStr}">${sumStr.replace(/\s*BYN$/, '')}</span>`;
        header.onclick = () => {
            const isCollapsed = groupEl.classList.toggle('collapsed');
            header.querySelector('.wo-month-arrow').innerHTML = isCollapsed ? '&#9656;' : '&#9662;';
            const st = woMonthStates();
            st[key] = !isCollapsed;
            woSaveMonthStates(st);
        };

        const cardsWrap = document.createElement('div');
        cardsWrap.className = 'wo-month-cards';
        els.forEach(el => cardsWrap.appendChild(el));
        groupEl.appendChild(header);
        groupEl.appendChild(cardsWrap);

        // Ленивая подгрузка: первые WO_MONTH_PAGE видны сразу, остальные — кнопкой.
        if (els.length > WO_MONTH_PAGE) {
            els.forEach((el, i) => { if (i >= WO_MONTH_PAGE) el.classList.add('wo-hidden-extra'); });
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'wo-show-more';
            const updateBtn = () => {
                const hidden = cardsWrap.querySelectorAll('.wo-hidden-extra').length;
                if (!hidden) { btn.remove(); return; }
                btn.textContent = `Показать ещё ${Math.min(WO_MONTH_PAGE, hidden)} (всего ${els.length})`;
            };
            btn.onclick = () => {
                cardsWrap.querySelectorAll('.wo-hidden-extra').forEach((el, i) => {
                    if (i < WO_MONTH_PAGE) el.classList.remove('wo-hidden-extra');
                });
                updateBtn();
            };
            updateBtn();
            groupEl.appendChild(btn);
        }

        container.appendChild(groupEl);
    });
}

function renderWriteoffsBoard() {
    const { transactions, cards, groups } = _writeoffsData;
    const q = (_writeoffsSearchQuery || '').trim().toLowerCase();

    // набор складов мог измениться после загрузки данных
    ensureStoreBlocks();
    document.querySelectorAll('.writeoff-cards').forEach(col => col.innerHTML = '');

    const knownStores = Array.from(document.querySelectorAll('.writeoff-store-block'))
        .map(b => b.dataset.store);

    const cardMap = new Map();
    transactions.forEach(t => {
        if (t.is_document) return;
        if ((parseFloat(t.amount) || 0) <= 0) return;
        if (!knownStores.includes(t.store_location)) return;
        if (!t.card_id) return;
        const linkedCard = cards.find(c => c.id === t.card_id);
        if (!linkedCard || (linkedCard.status !== 'На списание' && linkedCard.status !== 'Закрыто')) return;
        if (linkedCard.writeoff_group_id) return;
        if (!cardMap.has(t.card_id)) cardMap.set(t.card_id, { card: linkedCard, txs: [] });
        cardMap.get(t.card_id).txs.push(t);
    });

    const totalItems = (groups || []).length + cardMap.size;
    let visibleItems = 0;

    // Элементы складываем по колонкам; колонку «Списано» потом раскладываем
    // по месячным группам (renderDoneColumn).
    const byColumn = new Map();
    const putTile = (store, status, el, month) => {
        const key = `${store}|${status}`;
        if (!byColumn.has(key)) byColumn.set(key, []);
        byColumn.get(key).push({ el, month });
    };

    // Сначала групповые плитки — они отображают несколько сделок одной накладной.
    // Группа без участников (все карточки выведены в обход API) не должна
    // рисоваться: плитка-призрак с устаревшей суммой раздувала итог
    // «к списанию» в шапке склада (баг-отчёт 09.09: «остаток сумашедший»).
    (groups || []).forEach(g => {
        if (!g.store_location) return;
        if (!g.cards || !g.cards.length) return;
        if (q && !groupMatchesSearch(g, q)) return;
        const el = renderGroupTile(g);
        if (!el) return;
        visibleItems++;
        putTile(g.store_location, !!g.written_off, el, monthKeyOf(g.invoice_date));
    });

    // Карточки
    cardMap.forEach(({ card, txs }) => {
        if (q && !cardMatchesSearch(card, txs, q)) return;
        visibleItems++;
            const store = txs[0].store_location;
            const totalAmount = parseFloat(card.total_amount) || txs.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
            // Выписанное = накладные с номером + складские списания. Остаток
            // к выписке = сумма сделки минус выписанное. Полностью выписанная
            // сделка готова и рисуется в «Списано», даже если складские
            // флажки ещё не проставлены (фидбек 09.09, «Свидеал»).
            const invoicedTxs = txs.filter(t => (t.invoice_number || '').trim() || t.is_warehouse_writeoff);
            const invoicedSum = invoicedTxs.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
            const rest = Math.max(0, totalAmount - invoicedSum);
            const isDone = rest <= 0.005;

            const invoices = txs.filter(t => (t.invoice_number || '').trim());
            const pendingInvoiceTx = invoices.find(t => !t.is_warehouse_writeoff);
            const repTx = pendingInvoiceTx || txs[0];

            const cardEl = document.createElement('div');
            cardEl.className = 'kanban-card writeoff-card reveal reveal-fast' + (isDone ? ' writeoff-done' : '');
            cardEl.dataset.group = card.id;
            cardEl.setAttribute('draggable', 'true');
            cardEl.setAttribute('data-id', repTx.id);
            cardEl.setAttribute('data-card-id', card.id);

            const displayAmount = totalAmount;
            const dateStr = repTx.date ? new Date(repTx.date).toLocaleDateString('ru-RU') : '—';

            let invoiceHtml = '';
            let invoiceMissing = false;
            let pendingInvNum = '';
            if (isDone) {
                const inv = invoices[0];
                if (inv) {
                    const invDateStr = inv.invoice_date
                        ? new Date(inv.invoice_date + 'T00:00:00').toLocaleDateString('ru-RU')
                        : '';
                    invoiceHtml = `<div class="wo-invoice"><span class="wo-inv-num">${escapeHtml(inv.invoice_number)}</span>${invDateStr ? `<span class="wo-inv-date">${invDateStr}</span>` : ''}</div>`;
                }
            } else if (pendingInvoiceTx) {
                // Номер ТН уходит в строку даты — отдельная строка не нужна
                pendingInvNum = pendingInvoiceTx.invoice_number.trim();
                if (invoices.length > 1) {
                    invoiceHtml = `<div class="wo-note wo-note-empty">ещё ${invoices.length - 1} ${invoices.length - 1 === 1 ? 'накладная' : 'накладных'} · ждёт остаток</div>`;
                }
            } else if (invoices.length) {
                invoiceHtml = `<div class="wo-note wo-note-empty">выписано ${invoices.length} ${invoices.length === 1 ? 'накладная' : 'накладных'} · ждёт остаток</div>`;
            } else {
                // Бейдж «Накладная не выписана» и кнопка «Выписать накладную»
                // дублировали друг друга и растягивали карточку вниз (фидбек
                // 2026-09-04): сигнал перенесён на саму кнопку — она станет
                // янтарной (.btn-invoice-missing), отдельная строка не нужна.
                invoiceMissing = true;
            }

            // Компактная карточка (фидбек 00:56: на 100% колонка из 5 плиток
            // не влезала в экран): сумма и «к списанию» — одна строка, дата
            // и номер ТН — одна строка. Частичная оплата показывает «из …».
            // На плитке «Списано» — сумма выписанных накладных, а не сумма
            // сделки: после правки суммы сделки они расходились, и «Списано»
            // приписывало лишнее (а месячный итог склада — тем более).
            const doneAmount = invoicedSum > 0 ? invoicedSum : displayAmount;
            const amountValue = isDone ? doneAmount : rest;
            const partialNote = (!isDone && invoicedSum > 0.005)
                ? `<span class="wo-from">из ${formatMoneyBYN(totalAmount)}</span>` : '';
            const amountRow = `
                <div class="card-amount-row">
                    <span class="card-amount tabular-nums" data-amount="${rest}">${formatMoneyBYN(amountValue)}</span>
                    ${isDone ? '' : '<span class="wo-amount-label">к списанию</span>'}
                    ${partialNote}
                </div>`;
            const dateLine = `<div class="card-date">${pendingInvNum ? `<span class="wo-meta-inv">ТН ${escapeHtml(pendingInvNum)}</span>` : ''}<span>Оплата: ${dateStr}</span></div>`;

            cardEl.innerHTML = `
                <div class="card-header">
                    <strong class="card-title">${escapeHtml(card.title || repTx.company_name)}</strong>
                    <button class="btn-delete-writeoff" data-tx-id="${repTx.id}" data-card-id="${card.id}" title="Убрать из списания" data-tooltip="Убрать из списания">${ICON_CROSS}</button>
                </div>
                ${amountRow}
                ${dateLine}
                ${invoiceHtml}
            `;

            cardEl.style.cursor = 'pointer';
            cardEl.onclick = (e) => {
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
                openCardModal(card.id);
            };

            cardEl.querySelector('.btn-delete-writeoff').addEventListener('click', async (e) => {
                e.stopPropagation();
                const txId = e.currentTarget.dataset.txId;
                const cId = e.currentTarget.dataset.cardId;
                if (!await confirmDialog(
                    'Убрать сделку из списания?\n\nБудут удалены все её накладные и их копии в «Документах». Карточка вернётся в «Сборку».',
                    { okText: 'Убрать', danger: true }
                )) return;
                try {
                    let done = false;
                    if (cId) {
                        try {
                            const r = await apiFetch(`/payments/cards/${cId}/writeoff`, { method: 'DELETE' });
                            showToast(`Сделка убрана из списания (записей: ${r.deleted}, документов: ${r.documents_deleted})`, 'success');
                            done = true;
                        } catch (e1) {
                            console.warn('Групповое удаление не сработало, удаляю запись:', e1.message);
                        }
                    }
                    if (!done && txId) {
                        await apiFetch(`/payments/transactions/${txId}`, { method: 'DELETE' });
                        showToast('Запись удалена', 'success');
                    } else if (!done && !txId) {
                        throw new Error('не удалось определить запись для удаления');
                    }
                    loadWriteoffsBoard();
                    if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                    if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                    if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
                } catch (err) {
                    showToast('Ошибка удаления: ' + err.message, 'error');
                }
            });

            if (!isDone) {
                // Фидбек 2026-09-06: кнопки действий плитки появляются при
                // наведении/фокусе (как панель переноса на канбане)
                const actions = document.createElement('div');
                actions.className = 'writeoff-tile-actions';
                const btn = document.createElement('button');
                if (pendingInvoiceTx) {
                    btn.className = 'btn-writeoff';
                    btn.innerText = 'Списать';
                    btn.onclick = async (e) => {
                        e.stopPropagation();
                        const invNum = (pendingInvoiceTx.invoice_number || '').trim();
                        if (await confirmDialog(`Подтвердить списание накладной ${invNum}?`, { okText: 'Списать', danger: false })) {
                            await executeWriteoff(pendingInvoiceTx.id, true, null, card.id);
                            showToast('Списано со склада', 'success');
                        }
                    };
                } else {
                    btn.className = 'btn-secondary' + (invoiceMissing ? ' btn-invoice-missing' : '');
                    btn.innerHTML = `${invoiceMissing ? ICON_WARNING : ICON_FILE} Выписать накладную`;
                    btn.onclick = (e) => {
                        e.stopPropagation();
                        openCardModal(card.id);
                    };
                }
                actions.appendChild(btn);

                // P3-A: «Прикрепить сделку» — общая накладная на 2+ счета.
                // Кандидаты: тот же клиент + тот же склад, статус Сборка/На
                // списание, без своей группы (правила writeoff_groups API).
                const attach = document.createElement('button');
                attach.className = 'wo-attach';
                attach.type = 'button';
                attach.title = 'Прикрепить другую сделку: одна накладная на два счёта';
                attach.textContent = '⧉ Прикрепить сделку';
                attach.onclick = (e) => {
                    e.stopPropagation();
                    openAttachPicker(card);
                };
                actions.appendChild(attach);
                cardEl.appendChild(actions);
            }

            cardEl.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', repTx.id);
                setTimeout(() => cardEl.classList.add('dragging'), 0);
            });
            cardEl.addEventListener('dragend', () => cardEl.classList.remove('dragging'));

            const doneDate = isDone
                ? ((invoices.find(t => t.invoice_date) || {}).invoice_date || repTx.date)
                : null;
            putTile(store, isDone, cardEl, monthKeyOf(doneDate));
        });

    // Раскладка по колонкам: «На списание» — напрямую, «Списано» — по месяцам.
    byColumn.forEach((items, key) => {
        const sep = key.lastIndexOf('|');
        const store = key.slice(0, sep);
        const status = key.slice(sep + 1);
        const container = document.querySelector(`.writeoff-column[data-store="${store}"][data-status="${status}"] .writeoff-cards`);
        if (!container) return;
        if (status === 'false') {
            items.forEach(({ el }) => container.appendChild(el));
        } else {
            renderDoneColumn(container, items);
        }
    });

    renderWriteoffEmptyStates();
    updateWriteoffCounters();
    updateWriteoffSearchCount(visibleItems, totalItems);
}

function updateWriteoffSearchCount(visible, total) {
    const countEl = document.getElementById('writeoffs-search-count');
    if (!countEl) return;
    if (!_writeoffsSearchQuery || !total) {
        countEl.classList.add('hidden');
        return;
    }
    countEl.classList.remove('hidden');
    if (visible === 0) {
        countEl.textContent = 'Ничего не найдено';
    } else {
        countEl.textContent = `Найдено ${visible} из ${total}`;
    }
}

function groupMatchesSearch(group, q) {
    const name = (group.name || '').toLowerCase();
    const members = (group.cards || []).map(c => (c.title || '').toLowerCase()).join(' ');
    const invoice = (group.invoice_number || '').toLowerCase();
    return name.includes(q) || members.includes(q) || invoice.includes(q);
}

function cardMatchesSearch(card, txs, q) {
    const title = (card.title || '').toLowerCase();
    const company = (card.client?.name || '').toLowerCase();
    const invoiceNumbers = txs.map(t => (t.invoice_number || '').toLowerCase()).join(' ');
    const store = (card.store_location || '').toLowerCase();
    return title.includes(q) || company.includes(q) || invoiceNumbers.includes(q) || store.includes(q);
}

function renderGroupTile(group) {
    // Сумма группы — по ТЕКУЩИМ суммам сделок-участников: group.total_amount
    // фиксируется при создании и не следует за правками сделок, плитка
    // начинала врать после первой же корректировки суммы в карточке.
    const total = (group.cards && group.cards.length)
        ? group.cards.reduce((s, c) => s + (parseFloat(c.total_amount) || 0), 0)
        : (parseFloat(group.total_amount) || 0);
    const count = group.cards ? group.cards.length : 0;
    const dateStr = group.invoice_date
        ? new Date(group.invoice_date + 'T00:00:00').toLocaleDateString('ru-RU')
        : '';

    const el = document.createElement('div');
    el.className = 'kanban-card writeoff-card writeoff-group reveal reveal-fast' + (group.written_off ? ' writeoff-done' : '');
    el.dataset.groupId = group.id;

    const membersHtml = (group.cards || []).map(c =>
        `<div class="wo-group-member"><span>${escapeHtml(c.title)}</span><span class="tabular-nums">${formatMoneyBYN(parseFloat(c.total_amount) || 0)}</span></div>`
    ).join('');

    el.innerHTML = `
        <div class="card-header">
            <strong class="card-title">${escapeHtml(group.name)}</strong>
            <span class="group-count-badge" title="Сделок в группе">+${count}</span>
        </div>
        <div class="card-amount tabular-nums" data-amount="${total}">${formatMoneyBYN(total)}</div>
        <div class="wo-group-members">${membersHtml}</div>
        ${group.written_off
            ? `<div class="wo-invoice"><span class="wo-inv-num">${escapeHtml(group.invoice_number || '—')}</span>${dateStr ? `<span class="wo-inv-date">${dateStr}</span>` : ''}</div>`
            : `<div class="badge-warning">${ICON_WARNING} Общая накладная не выписана</div>`}
    `;

    if (!group.written_off) {
        // Фидбек 2026-09-06: кнопки группы — в той же всплывающей панели
        const actions = document.createElement('div');
        actions.className = 'writeoff-tile-actions';
        const btn = document.createElement('button');
        btn.className = 'btn-secondary';
        btn.innerHTML = `${ICON_FILE} Выписать накладную`;
        btn.onclick = async (e) => {
            e.stopPropagation();
            const number = window.prompt(`Номер общей накладной для «${group.name}»`);
            if (!number || !number.trim()) return;
            try {
                await apiFetch(`/writeoffs/groups/${group.id}/issue-invoice`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ invoice_number: number.trim() })
                });
                showToast('Общая накладная выписана', 'success');
                loadWriteoffsBoard();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };
        actions.appendChild(btn);

        // P3-A: прикрепить ещё одну сделку к группе (ещё один счёт под
        // той же накладной). Работает, пока группа не закрыта.
        const addBtn = document.createElement('button');
        addBtn.className = 'wo-attach';
        addBtn.type = 'button';
        addBtn.title = 'Прикрепить другую сделку к этой накладной';
        addBtn.textContent = '⧉ Прикрепить сделку';
        addBtn.onclick = (e) => {
            e.stopPropagation();
            openAttachPicker(null, group);
        };
        actions.appendChild(addBtn);
        el.appendChild(actions);
    }

    el.onclick = (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
        if (group.cards && group.cards.length) openCardModal(group.cards[0].id);
    };

    return el;
}

// saveFieldWithFeedback() удалена: на доске списаний нет редактируемых
// полей (см. комментарий выше — номер и сумма накладной правятся только
// внутри карточки сделки), поэтому функция не вызывалась ни разу.
// Вместе с ней снят мёртвий CSS .writeoff-fields в style.css.

function renderWriteoffEmptyStates() {
    document.querySelectorAll('.writeoff-cards').forEach(col => {
        col.querySelectorAll('.writeoff-empty-state').forEach(el => el.remove());
        if (!col.querySelector('.writeoff-card')) {
            const el = document.createElement('div');
            el.className = 'writeoff-empty-state';
            el.innerHTML = `
                <div class="empty-state-icon">
                    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/></svg>
                </div>
                <div class="empty-state-title">Нет элементов</div>
                <div class="empty-state-desc">Перетащите сюда сделку или выпишите накладную в карточке.</div>
            `;
            col.appendChild(el);
        }
    });
}

function updateWriteoffCounters() {
    document.querySelectorAll('.writeoff-column').forEach(col => {
        const n = col.querySelectorAll('.writeoff-card').length;
        const h4 = col.querySelector('h4');
        if (!h4) return;
        let badge = h4.querySelector('.col-count');
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'col-count';
            h4.appendChild(badge);
        }
        badge.textContent = n;
    });
    document.querySelectorAll('.writeoff-store-block').forEach(block => {
        const sum = Array.from(block.querySelectorAll('.writeoff-column[data-status="false"] .card-amount'))
            .reduce((acc, el) => acc + (parseFloat(el.dataset.amount) || 0), 0);
        const h3 = block.querySelector('h3');
        if (!h3) return;
        // Итоги живут в своём столбике справа от названия склада: без обёртки
        // span'ы наследовали 16px заголовка и ломали строку на три части.
        let totals = h3.querySelector('.store-totals');
        if (!totals) {
            totals = document.createElement('span');
            totals.className = 'store-totals';
            h3.appendChild(totals);
        }
        let total = totals.querySelector('.store-total');
        if (!total) {
            total = document.createElement('span');
            total.className = 'store-total';
            totals.appendChild(total);
        }
        total.textContent = sum > 0 ? `к списанию ${formatMoneyBYN(sum)}` : '';

        // Списано за текущий месяц — по месячной группе колонки «Списано».
        const nowKey = monthKeyOf(new Date().toISOString().slice(0, 10));
        const writtenSum = Array.from(block.querySelectorAll(`.writeoff-column[data-status="true"] .wo-month-group[data-month="${nowKey}"] .card-amount`))
            .reduce((acc, el) => acc + parseTileMoney(el), 0);
        let written = totals.querySelector('.store-written-month');
        if (!written) {
            written = document.createElement('span');
            written.className = 'store-written-month';
            totals.appendChild(written);
        }
        written.textContent = writtenSum > 0 ? `списано за ${monthLabel(nowKey).toLowerCase()} · ${formatMoneyBYN(writtenSum)}` : '';
    });

    updateScrollHints();

    if (typeof window.revealRefresh === 'function') window.revealRefresh();
}

// --- Подсказка «ниже есть ещё» у скроллящихся колонок (2026-09-04):
// полосы прокрутки убраны со всех досок (фидбек: «не нравится нигде,
// листаю мышкой»), поэтому нижний край скроллящейся колонки канбана
// или списания плавно затухает, у дна затухание снимается. ---
function updateScrollHints() {
    document.querySelectorAll('.writeoff-cards, .kanban-cards').forEach(w => {
        const max = w.scrollHeight - w.clientHeight;
        w.classList.toggle('is-scrollable', max > 2);
        w.classList.toggle('wo-at-bottom', w.scrollTop >= max - 4);
    });
}
window.updateScrollHints = updateScrollHints;
document.addEventListener('scroll', (e) => {
    const t = e.target;
    if (t && t.classList && t.classList.contains('writeoff-cards')) updateScrollHints();
}, true);
window.addEventListener('resize', updateScrollHints);

async function updateTransactionData(transactionId, data) {    try {
        await apiFetch(`/payments/transactions/${transactionId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
    } catch (error) {
        showToast('Ошибка сохранения: ' + error.message, 'error');
    }
}

async function executeWriteoff(transactionId, isWrittenOff, targetStore = null, cardId = null) {
    try {
        // Поля накладной с доски больше не читаем — их там нет,
        // номер и дата задаются только внутри карточки сделки.
        // is_written_off — синхронно с is_warehouse_writeoff: галочка
        // «Списание с магазина» в реестре оплат отражает списание с доски
        // (фидбек 2026-09-04).
        const patchData = { is_warehouse_writeoff: isWrittenOff, is_written_off: isWrittenOff };
        if (targetStore) patchData.store_location = targetStore;

        await apiFetch(`/payments/transactions/${transactionId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patchData)
        });

        if (isWrittenOff) {
            await apiFetch(`/payments/transactions/${transactionId}/duplicate_as_document`, {
                method: 'POST'
            });
            // Статус сделки считает сервер по факту выписки
            // (sync-writeoff-status): складские флажки больше не держат
            // сделку в «На списание» и не закрывают её раньше времени.
            if (cardId) {
                let st = null;
                try { st = await apiFetch(`/payments/cards/${cardId}/writeoff-status`); } catch (e) {}
                try { await apiFetch(`/payments/cards/${cardId}/sync-writeoff-status`, { method: 'POST' }); } catch (e) {}
                if (st && st.total_invoices > 1 && st.pending > 0) {
                    showToast(`Списано ${st.written_off} из ${st.total_invoices} накладных`, 'info');
                }
            }
        }
        
        loadWriteoffsBoard();
        if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
        if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
    } catch (error) { showToast("Ошибка: " + error.message, 'error'); }
}

function setupWriteoffDragAndDrop() {
    const container = document.querySelector('.writeoffs-container');
    if (!container) return;
    // Делегирование: блоки складов создаются динамически, вешать обработчики
    // на колонки напрямую нельзя — доска пересобирается без них.
    const clearHighlights = () => container.querySelectorAll('.writeoff-column').forEach(c => { c.style.boxShadow = ''; });

    container.addEventListener('dragover', e => {
        const col = e.target.closest('.writeoff-column');
        if (!col || !container.contains(col)) return;
        e.preventDefault();
        if (col.style.boxShadow) return;
        clearHighlights();
        col.style.boxShadow = '0 0 10px rgba(0,0,0,0.1) inset';
    });
    container.addEventListener('drop', async e => {
        const col = e.target.closest('.writeoff-column');
        if (!col || !container.contains(col)) return;
        e.preventDefault();
        clearHighlights();
        const transactionId = e.dataTransfer.getData('text/plain');
        if (!transactionId) return;
        const targetStatus = col.getAttribute('data-status') === 'true';
        const targetStore = col.getAttribute('data-store');
        const cardEl = container.querySelector(`.kanban-card[data-id="${transactionId}"]`);
        const cardId = cardEl ? cardEl.dataset.cardId : null;
        await executeWriteoff(transactionId, targetStatus, targetStore, cardId);
    });
    // dragend с карточки не всегда попадает в контейнер — чистим глобально.
    document.addEventListener('dragend', clearHighlights);
}

// --- P3-A: прикрепление сделки к накладной (одна накладная на 2+ счёта).
// Кандидаты — сделки того же клиента и склада в статусе «Сборка»/«На
// списание», не состоящие ни в какой группе (правила writeoff_groups API).
// Для группы-источника — добавление в неё; для карточки — создание группы
// из двух, после чего на плитке группы выписывается одна общая накладная.
function openAttachPicker(srcCard, group) {
    const groupId = group ? group.id : null;
    const baseClient = srcCard ? srcCard.client_id : group.client_id;
    const baseStore = srcCard ? srcCard.store_location : group.store_location;
    const memberIds = new Set(group ? (group.cards || []).map(c => c.id) : []);

    const candidates = (_writeoffsData.cards || []).filter(c => {
        if (srcCard && c.id === srcCard.id) return false;
        if (memberIds.has(c.id)) return false;
        if (c.writeoff_group_id) return false;
        if (c.status !== 'Сборка' && c.status !== 'На списание') return false;
        if ((c.client_id || null) !== (baseClient || null)) return false;
        if ((c.store_location || '') !== (baseStore || '')) return false;
        return true;
    });

    const title = srcCard
        ? `Прикрепить сделку к «${srcCard.title || 'сделке'}»`
        : `Прикрепить сделку к «${group.name}»`;

    const overlay = document.createElement('div');
    overlay.className = 'wo-picker-overlay';
    overlay.innerHTML = `
        <div class="wo-picker" role="dialog" aria-label="${escapeHtml(title)}">
            <div class="wo-picker-head">
                <strong>${escapeHtml(title)}</strong>
                <button type="button" class="wo-picker-close" aria-label="Закрыть">✕</button>
            </div>
            <div class="wo-picker-sub">Одна накладная закроет обе сделки. Клиент и склад должны совпадать${baseStore ? ' — склад: ' + escapeHtml(baseStore) : ''}.</div>
            <input type="text" class="wo-picker-search" placeholder="Поиск по названию...">
            <div class="wo-picker-list"></div>
        </div>`;

    const close = () => overlay.remove();
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('.wo-picker-close').onclick = close;

    const listEl = overlay.querySelector('.wo-picker-list');
    const searchEl = overlay.querySelector('.wo-picker-search');

    const renderList = (q) => {
        const query = (q || '').trim().toLowerCase();
        const rows = candidates.filter(c => !query || (c.title || '').toLowerCase().includes(query));
        listEl.innerHTML = rows.length ? '' : '<div class="wo-picker-empty">Нет сделок этого клиента и склада для прикрепления</div>';
        rows.forEach(c => {
            const hasInv = (_writeoffsData.transactions || []).some(t =>
                String(t.card_id) === String(c.id) && (t.invoice_number || '').trim() && !t.is_document && !t.is_warehouse_writeoff);
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'wo-picker-row';
            row.innerHTML = `
                <span class="wo-picker-title">${escapeHtml(c.title || 'Без названия')}${hasInv ? ' <em class="wo-picker-warn">· ТН уже выписана</em>' : ''}</span>
                <span class="wo-picker-sum tabular-nums">${formatMoneyBYN(parseFloat(c.total_amount) || 0)}</span>`;
            row.onclick = async () => {
                row.disabled = true;
                try {
                    if (groupId) {
                        await apiFetch(`/writeoffs/groups/${groupId}/cards/${c.id}`, { method: 'POST' });
                    } else {
                        await apiFetch('/writeoffs/groups/', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ card_ids: [srcCard.id, c.id] })
                        });
                    }
                    close();
                    showToast('Сделка прикреплена. Теперь выписывайте общую накладную с плитки группы.', 'success');
                    loadWriteoffsBoard();
                } catch (err) {
                    row.disabled = false;
                    showToast('Ошибка: ' + err.message, 'error');
                }
            };
            listEl.appendChild(row);
        });
    };
    renderList('');
    searchEl.addEventListener('input', () => renderList(searchEl.value));
    document.body.appendChild(overlay);
    searchEl.focus();
}
