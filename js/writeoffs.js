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

async function loadWriteoffsBoard() {
    if (!hasToken()) return;
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

function renderWriteoffsBoard() {
    const { transactions, cards, groups } = _writeoffsData;
    const q = (_writeoffsSearchQuery || '').trim().toLowerCase();

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

    // Сначала групповые плитки — они отображают несколько сделок одной накладной.
    (groups || []).forEach(g => {
        if (!g.store_location) return;
        if (q && !groupMatchesSearch(g, q)) return;
        const container = document.querySelector(`.writeoff-column[data-store="${g.store_location}"][data-status="${g.written_off}"] .writeoff-cards`);
        if (!container) return;
        renderGroupTile(g, container);
        visibleItems++;
    });

    // Карточки
    cardMap.forEach(({ card, txs }) => {
        if (q && !cardMatchesSearch(card, txs, q)) return;
        visibleItems++;
            const store = txs[0].store_location;
            const totalAmount = parseFloat(card.total_amount) || txs.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
            const writtenTxs = txs.filter(t => t.is_warehouse_writeoff);
            const writtenAmount = writtenTxs.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
            const pendingTxs = txs.filter(t => !t.is_warehouse_writeoff);
            const pendingAmount = Math.max(0, totalAmount - writtenAmount);
            const isDone = pendingTxs.length === 0 || pendingAmount <= 0;

            const invoices = txs.filter(t => (t.invoice_number || '').trim());
            const pendingInvoiceTx = pendingTxs.find(t => (t.invoice_number || '').trim());
            const repTx = pendingInvoiceTx || pendingTxs[0] || txs[0];

            const selector = `.writeoff-column[data-store="${store}"][data-status="${isDone}"] .writeoff-cards`;
            const container = document.querySelector(selector);
            if (!container) return;

            const cardEl = document.createElement('div');
            cardEl.className = 'kanban-card writeoff-card reveal reveal-fast' + (isDone ? ' writeoff-done' : '');
            cardEl.dataset.group = card.id;
            cardEl.setAttribute('draggable', 'true');
            cardEl.setAttribute('data-id', repTx.id);
            cardEl.setAttribute('data-card-id', card.id);

            const displayAmount = totalAmount;
            const dateStr = repTx.date ? new Date(repTx.date).toLocaleDateString('ru-RU') : '—';

            let invoiceHtml = '';
            if (isDone) {
                const inv = writtenTxs.find(t => (t.invoice_number || '').trim()) || invoices[0];
                if (inv) {
                    const invDateStr = inv.invoice_date
                        ? new Date(inv.invoice_date + 'T00:00:00').toLocaleDateString('ru-RU')
                        : '';
                    invoiceHtml = `<div class="wo-invoice"><span class="wo-inv-num">${escapeHtml(inv.invoice_number)}</span>${invDateStr ? `<span class="wo-inv-date">${invDateStr}</span>` : ''}</div>`;
                }
            } else if (pendingInvoiceTx) {
                const invDateStr = pendingInvoiceTx.invoice_date
                    ? new Date(pendingInvoiceTx.invoice_date + 'T00:00:00').toLocaleDateString('ru-RU')
                    : '';
                invoiceHtml = `<div class="wo-invoice"><span class="wo-inv-num">${escapeHtml(pendingInvoiceTx.invoice_number)}</span>${invDateStr ? `<span class="wo-inv-date">${invDateStr}</span>` : ''}</div>`;
            } else if (invoices.length) {
                invoiceHtml = `<div class="wo-note wo-note-empty">выписано ${invoices.length} ${invoices.length === 1 ? 'накладная' : 'накладных'} · ждёт остаток</div>`;
            } else {
                invoiceHtml = `<div class="badge-warning">${ICON_WARNING} Накладная не выписана</div>`;
            }

            const remainderHtml = isDone
                ? ''
                : `<div class="wo-remainder">к списанию: ${formatMoneyBYN(pendingAmount)}</div>`;

            cardEl.innerHTML = `
                <div class="card-header">
                    <strong class="card-title">${escapeHtml(card.title || repTx.company_name)}</strong>
                    <button class="btn-delete-writeoff" data-tx-id="${repTx.id}" data-card-id="${card.id}" title="Убрать из списания" data-tooltip="Убрать из списания">${ICON_CROSS}</button>
                </div>
                <div class="card-amount tabular-nums" data-amount="${pendingAmount}">${formatMoneyBYN(displayAmount)}</div>
                ${remainderHtml}
                <div class="card-date">Оплата: ${dateStr}</div>
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
                    btn.className = 'btn-secondary';
                    btn.innerHTML = `${ICON_FILE} Выписать накладную`;
                    btn.onclick = (e) => {
                        e.stopPropagation();
                        openCardModal(card.id);
                    };
                }
                cardEl.appendChild(btn);
            }

            cardEl.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', repTx.id);
                setTimeout(() => cardEl.classList.add('dragging'), 0);
            });
            cardEl.addEventListener('dragend', () => cardEl.classList.remove('dragging'));

            container.appendChild(cardEl);
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

function renderGroupTile(group, container) {
    const total = parseFloat(group.total_amount) || 0;
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
        el.appendChild(btn);
    }

    el.onclick = (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
        if (group.cards && group.cards.length) openCardModal(group.cards[0].id);
    };

    container.appendChild(el);
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
        let total = h3.querySelector('.store-total');
        if (!total) {
            total = document.createElement('span');
            total.className = 'store-total';
            h3.appendChild(total);
        }
        total.textContent = sum > 0 ? `к списанию ${formatMoneyBYN(sum)}` : '';
    });

    if (typeof window.revealRefresh === 'function') window.revealRefresh();
}

async function updateTransactionData(transactionId, data) {
    try {
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
        const patchData = { is_warehouse_writeoff: isWrittenOff };
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
            // Карточку закрываем ТОЛЬКО когда списаны все её накладные.
            // Раньше первая же накладная закрывала сделку целиком.
            if (cardId) {
                let st = null;
                try { st = await apiFetch(`/payments/cards/${cardId}/writeoff-status`); } catch (e) {}
                if (!st || st.pending === 0) {
                    await apiFetch(`/kanban/cards/${cardId}/status`, {
                        method: 'PATCH',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ status: 'Закрыто' })
                    });
                    if (st && st.total_invoices > 1) {
                        showToast(`Сделка закрыта: списаны все ${st.total_invoices} накладные`, 'success');
                    }
                } else {
                    showToast(`Списано ${st.written_off} из ${st.total_invoices}. Осталось: ${st.pending}`, 'info');
                }
            }
        }
        
        loadWriteoffsBoard();
        if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
        if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
    } catch (error) { showToast("Ошибка: " + error.message, 'error'); }
}

function setupWriteoffDragAndDrop() {
    const columns = document.querySelectorAll('.writeoff-column');
    columns.forEach(column => {
        column.addEventListener('dragover', e => { e.preventDefault(); column.style.boxShadow = '0 0 10px rgba(0,0,0,0.1) inset'; });
        column.addEventListener('dragleave', () => { column.style.boxShadow = 'none'; });
        column.addEventListener('drop', async e => {
            e.preventDefault();
            column.style.boxShadow = 'none';
            const transactionId = e.dataTransfer.getData('text/plain');
            if (!transactionId) return;
            const targetStatus = column.getAttribute('data-status') === 'true'; 
            const targetStore = column.getAttribute('data-store');
            const cardEl = document.querySelector(`.kanban-card[data-id="${transactionId}"]`);
            const cardId = cardEl ? cardEl.dataset.cardId : null;
            await executeWriteoff(transactionId, targetStatus, targetStore, cardId);
        });
    });
}
