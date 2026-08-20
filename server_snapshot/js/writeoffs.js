document.addEventListener('DOMContentLoaded', () => {
    setupWriteoffDragAndDrop();
    const writeoffsBtn = document.querySelector('[data-target="page-writeoffs"]');
    if (writeoffsBtn) writeoffsBtn.addEventListener('click', loadWriteoffsBoard);
    loadWriteoffsBoard();
});

async function loadWriteoffsBoard() {
    if (!hasToken()) return;
    const boardExists = document.querySelector('.writeoff-column');
    if (!boardExists) return;

    try {
        const [transactions, cards] = await Promise.all([
            // grouped=false — доске нужны СЫРЫЕ записи (накладные + остаток),
            // а не схлопнутые строки реестра оплат
            apiFetch('/payments/transactions?grouped=false'),
            apiFetch('/kanban/cards')
        ]);

        document.querySelectorAll('.writeoff-cards').forEach(col => col.innerHTML = '');

        // Накладные одной сделки держим вместе: сортируем по card_id, внутри — по id.
        // Раньше сортировка шла по дате, и новая накладная разрывала группу.
        const ordered = [...transactions].sort((a, b) => {
            if ((a.card_id || 0) !== (b.card_id || 0)) return (b.card_id || 0) - (a.card_id || 0);
            return (a.id || 0) - (b.id || 0);
        });

        ordered.forEach(t => {
            const knownStores = Array.from(document.querySelectorAll('.writeoff-store-block'))
                .map(b => b.dataset.store);
            if (!knownStores.includes(t.store_location)) return;
            if (t.is_document) return;
            if ((parseFloat(t.amount) || 0) <= 0) return;

            const linkedCard = cards.find(c => c.id === t.card_id);
            const siblings = t.card_id
                ? ordered.filter(x => x.card_id === t.card_id && !x.is_document)
                : [];
            const siblingCount = siblings.length;
            const siblingIndex = siblingCount > 1 ? siblings.findIndex(x => x.id === t.id) + 1 : 1;
            if (!linkedCard || (linkedCard.status !== 'На списание' && linkedCard.status !== 'Закрыто')) return;

            const selector = `.writeoff-column[data-store="${t.store_location}"][data-status="${t.is_warehouse_writeoff}"] .writeoff-cards`;
            const container = document.querySelector(selector);
            if (!container) return;

            const cardEl = document.createElement('div');
            cardEl.className = 'kanban-card writeoff-card'
                + (t.is_warehouse_writeoff ? ' writeoff-done' : '')
                + (siblingCount > 1 ? ' has-siblings' : '');
            if (t.card_id) cardEl.dataset.group = t.card_id;
            cardEl.setAttribute('draggable', 'true');
            cardEl.setAttribute('data-id', t.id);
            cardEl.setAttribute('data-card-id', t.card_id || '');

            const dateStr = t.date ? new Date(t.date).toLocaleDateString('ru-RU') : '—';

            // На доске — ТОЛЬКО информация. Номер и сумма накладной
            // редактируются исключительно внутри карточки сделки.
            const invNum = (t.invoice_number || '').trim();
            const invDateStr = t.invoice_date
                ? new Date(t.invoice_date + 'T00:00:00').toLocaleDateString('ru-RU')
                : '';
            const issued = siblings.filter(x => x.is_warehouse_writeoff || (x.invoice_number || '').trim());
            const issuedSum = issued.reduce((a, x) => a + (parseFloat(x.amount) || 0), 0);

            cardEl.innerHTML = `
                <div class="card-header">
                    <strong class="card-title">${escapeHtml(t.company_name)}</strong>
                    <button class="btn-delete-writeoff" data-tx-id="${t.id}" data-card-id="${t.card_id || ''}" title="Удалить">&times;</button>
                </div>
                <div class="card-amount">${(parseFloat(t.amount) || 0).toFixed(2)} BYN</div>
                <div class="card-date">Оплата: ${dateStr}</div>
                ${invNum
                    ? `<div class="wo-invoice"><span class="wo-inv-num">${escapeHtml(invNum)}</span>${invDateStr ? `<span class="wo-inv-date">${invDateStr}</span>` : ''}</div>`
                    : (issued.length
                        ? `<div class="wo-note">выписано ${issued.length} ${issued.length === 1 ? 'накладная' : 'накладных'} на ${issuedSum.toFixed(2)} BYN · ждёт остаток</div>`
                        : `<div class="wo-note wo-note-empty">накладная не выписана</div>`)}
            `;

            cardEl.style.cursor = 'pointer';
            cardEl.onclick = (e) => {
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
                if (t.card_id) openCardModal(t.card_id);
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
                    // Один запрос сносит ВСЁ по сделке: остаток, накладные и документы.
                    // Плитки в «Списано» приходят без card_id, поэтому раньше
                    // здесь падала ошибка — теперь есть запасной путь по txId.
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

            if (!t.is_warehouse_writeoff) {
                const btn = document.createElement('button');
                if (invNum) {
                    // накладная уже выписана — можно списать прямо с доски
                    btn.className = 'btn-writeoff';
                    btn.innerText = 'Списать';
                    btn.onclick = async (e) => {
                        e.stopPropagation();
                        if (await confirmDialog(`Подтвердить списание накладной ${invNum}?`, { okText: 'Списать', danger: false })) {
                            await executeWriteoff(t.id, true, null, t.card_id);
                            showToast('Списано со склада', 'success');
                        }
                    };
                } else {
                    // остаток без накладной — выписывать только внутри карточки
                    btn.className = 'btn-writeoff btn-writeoff-open';
                    btn.innerText = 'Выписать накладную';
                    btn.onclick = (e) => {
                        e.stopPropagation();
                        if (t.card_id) openCardModal(t.card_id);
                    };
                }
                cardEl.appendChild(btn);
            }

            cardEl.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', t.id);
                setTimeout(() => cardEl.classList.add('dragging'), 0);
            });
            cardEl.addEventListener('dragend', () => cardEl.classList.remove('dragging'));

            container.appendChild(cardEl);
        });

        updateWriteoffCounters();
    } catch (error) {
        console.error(error);
    }
}

// saveFieldWithFeedback() удалена: на доске списаний нет редактируемых
// полей (см. комментарий выше — номер и сумма накладной правятся только
// внутри карточки сделки), поэтому функция не вызывалась ни разу.
// Вместе с ней снят мёртвый CSS .writeoff-fields в style.css.

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
            .reduce((acc, el) => acc + (parseFloat(el.textContent) || 0), 0);
        const h3 = block.querySelector('h3');
        if (!h3) return;
        let total = h3.querySelector('.store-total');
        if (!total) {
            total = document.createElement('span');
            total.className = 'store-total';
            h3.appendChild(total);
        }
        total.textContent = sum > 0 ? `к списанию ${sum.toFixed(2)} BYN` : '';
    });
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
