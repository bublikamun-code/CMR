let allPayments = [];        // все транзакции реестра (полный набор)
let currentPayments = [];    // то, что сейчас показано (для экспорта)
let paymentsMonth = 'all';   // выбранный месяц фильтра
let paymentsSort = { key: null, dir: 1 };

document.addEventListener('DOMContentLoaded', () => {
    const paymentsBtn = document.querySelector('[data-target="page-payments"]');
    if (paymentsBtn) paymentsBtn.addEventListener('click', loadPaymentsTable);
    if (document.getElementById('page-payments')?.classList.contains('active')) loadPaymentsTable();

    setupSorting('payments-table', (key, dir) => {
        paymentsSort = { key, dir };
        renderPayments();
    });

    const search = document.getElementById('payments-search');
    if (search) search.addEventListener('input', (e) => filterTableRows('payments-table', e.target.value));

    const exportBtn = document.getElementById('payments-export');
    if (exportBtn) exportBtn.addEventListener('click', () => exportTransactionsToCsv(currentPayments, 'reestr_oplat.csv'));
});

async function loadPaymentsTable() {
    if (!hasToken()) return;
    const tbody = document.querySelector('#payments-table tbody');
    if (!tbody) return;

    try {
        const isFirstLoad = !tbody.querySelector('tr[data-id]');
        if (isFirstLoad) {
            tbody.innerHTML = getSkeletonHTML(10, 5);
        }

        allPayments = await apiFetch('/payments/transactions');
        if (window.CRM_STORE) {
            CRM_STORE.set('transactions', allPayments);
            crmEmit('payments:loaded', { count: allPayments.length });
        }

        const loadingRow = tbody.querySelector('.td-loading');
        if (loadingRow) loadingRow.closest('tr').remove();
        tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());

        buildMonthFilter('payments-month', allPayments, t => t.date, paymentsMonth, (m) => {
            paymentsMonth = m;
            renderPayments();
        });

        renderPayments();
    } catch (error) {
        if (!tbody.querySelector('tr[data-id]')) {
            tbody.innerHTML = `<tr><td colspan="10" class="td-error">Ошибка: ${escapeHtml(error.message)}</td></tr>`;
        }
    }
}

function renderPayments() {
    const tbody = document.querySelector('#payments-table tbody');
    if (!tbody) return;

    const loadingRow = tbody.querySelector('.td-loading');
    if (loadingRow) loadingRow.closest('tr').remove();
    tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());

    let rows = paymentsMonth === 'all'
        ? allPayments
        : allPayments.filter(t => monthKeyOf(t.date) === paymentsMonth);

    if (paymentsSort.key) rows = sortRows(rows, paymentsSort.key, paymentsSort.dir);

    currentPayments = rows;
    updatePaymentsTotals(rows);

    if (rows.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10">
                    <div class="empty-state-wrapper">
                        <div class="empty-state-icon">
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>
                        </div>
                        <div class="empty-state-title">Реестр оплат пуст</div>
                        <div class="empty-state-desc">В выбранном периоде нет ни одной транзакции оплат. Все расчеты совершены!</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    const existingRows = {};
    tbody.querySelectorAll('tr[data-id]').forEach(tr => existingRows[tr.dataset.id] = tr);
    const newIds = new Set(rows.map(t => String(t.id)));

    // Удаляем строки, которых больше нет
    tbody.querySelectorAll('tr[data-id]').forEach(tr => {
        if (!newIds.has(tr.dataset.id)) tr.remove();
    });

    // Обновляем/добавляем строки
    rows.forEach(tr => {
        const dateStr = new Date(tr.date).toLocaleDateString('ru-RU');
        const existing = existingRows[tr.id];

        if (existing) {
            existing.querySelector('.cb-calc').checked = tr.is_calculated;
            existing.querySelector('.cb-invoice').checked = tr.is_invoice_issued;
            existing.querySelector('.cb-written-off').checked = tr.is_written_off;
            existing.querySelector('.input-note').value = tr.note || '';
            // Реестр оплат показывает счёт клиента. Закупка у поставщиков
            // (чек-лист карточки) сюда не подмешивается.
            existing.querySelector('.clickable-company').textContent = tr.company_name;
            tbody.appendChild(existing);
        } else {
            const row = document.createElement('tr');
            row.setAttribute('data-id', tr.id);
            row.innerHTML = `
                <td>${dateStr}</td>
                <td class="clickable-company" data-card-id="${tr.card_id || ''}">
                    ${escapeHtml(tr.company_name)}
                </td>
                <td class="inline-edit-cell" data-field="amount" data-id="${tr.id}">
                    <span class="inline-edit font-mono text-right">${escapeHtml(String(tr.amount || 0))}</span>
                    <span class="text-muted text-sm">BYN</span>
                </td>
                <td>${escapeHtml(tr.store_location) || '—'}</td>
                <td class="td-center"><input type="checkbox" class="cb-calc" data-id="${tr.id}" ${tr.is_calculated ? 'checked' : ''} aria-label="Просчет"></td>
                <td class="td-center"><input type="checkbox" class="cb-invoice" data-id="${tr.id}" ${tr.is_invoice_issued ? 'checked' : ''} aria-label="Выписка ТН"></td>
                <td class="td-center"><input type="checkbox" class="cb-written-off" data-id="${tr.id}" ${tr.is_written_off ? 'checked' : ''} aria-label="Списание с магазина"></td>
                <td class="print-cell"></td>
                <td class="inline-edit-cell" data-field="note" data-id="${tr.id}"><span class="inline-edit">${escapeHtml(tr.note) || '<span class="text-muted">Нет данных</span>'}</span></td>
                <td class="td-center">
                    <button class="btn-delete-row" data-tx-id="${tr.id}" data-card-id="${tr.card_id || ''}" title="Удалить">
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: block; margin: auto;"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </td>
            `;
            row.querySelector('.print-cell').appendChild(createDropdown({
                options: PRINT_OPTIONS,
                value: tr.print_status,
                onChange: (val) => savePrintStatus(tr.id, val)
            }));
            tbody.appendChild(row);
        }
    });

    document.querySelectorAll('#payments-table .btn-delete-row').forEach(btn => {
        btn.onclick = async (e) => {
            e.stopPropagation();
            const txId = btn.dataset.txId;
            const cardId = btn.dataset.cardId;
            if (!await confirmDialog(
                'Удалить эту запись из реестра?\n\nБудут удалены её накладные и копии в «Документах». Сделка вернётся в «Сборку».',
                { okText: 'Удалить', danger: true }
            )) return;
            try {
                // Удаляем ВСЁ по сделке разом, иначе документы оставались висеть,
                // а карточка застревала в «Закрыто» и пропадала со всех досок.
                if (cardId) {
                    await apiFetch(`/payments/cards/${cardId}/writeoff`, { method: 'DELETE' });
                } else if (txId) {
                    await apiFetch(`/payments/transactions/${txId}`, { method: 'DELETE' });
                }
                loadPaymentsTable();
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                showToast('Запись удалена', 'success');
            } catch (err) {
                showToast('Ошибка удаления: ' + err.message, 'error');
            }
        };
    });

    document.querySelectorAll('#payments-table .clickable-company').forEach(td => {
        td.onclick = (e) => {
            const cardId = e.currentTarget.getAttribute('data-card-id');
            if (cardId) openCardModal(parseInt(cardId));
            else showToast('К этой старой записи ещё не привязана карточка.', 'info');
        };
    });

    setupPaymentsAutoSave();

    const search = document.getElementById('payments-search');
    if (search && search.value) filterTableRows('payments-table', search.value);
}

async function savePrintStatus(txId, value) {
    try {
        await apiFetch(`/payments/transactions/${txId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ print_status: value })
        });
        // Документы (стр.4) просто подтягивают это значение — обновим их, если функция есть
        if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
    } catch (error) {
        showToast('Не удалось сохранить статус печати: ' + error.message, 'error');
    }
}

function updatePaymentsTotals(transactions) {
    const countEl = document.getElementById('payments-count');
    const totalEl = document.getElementById('payments-total');
    if (countEl) countEl.textContent = `${transactions.length} шт.`;
    if (totalEl) totalEl.textContent = `${formatMoney(sumAmount(transactions))} BYN`;
}


// === INLINE EDIT ===
document.addEventListener('click', (e) => {
    const cell = e.target.closest('.inline-edit-cell');
    if (!cell || cell.querySelector('input')) return;
    
    const field = cell.dataset.field;
    const id = cell.dataset.id;
    const span = cell.querySelector('.inline-edit');
    if (!span) return;
    
    const currentValue = span.textContent.trim();
    const input = document.createElement('input');
    input.type = field === 'amount' ? 'number' : 'text';
    input.className = 'inline-edit-input';
    input.value = field === 'amount' ? currentValue.replace(/[^0-9.]/g, '') : currentValue;
    if (field === 'amount') { input.step = '0.01'; input.min = '0'; }
    
    span.replaceWith(input);
    input.focus();
    input.select();
    
    const save = async () => {
        const newVal = input.value.trim();
        const spanNew = document.createElement('span');
        spanNew.className = 'inline-edit';
        
        if (field === 'amount') {
            const numVal = parseFloat(newVal) || 0;
            spanNew.textContent = numVal;
            if (String(numVal) !== currentValue) {
                try {
                    await apiFetch('/payments/transactions/' + id, {
                        method: 'PATCH',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ amount: numVal })
                    });
                    showToast('Сумма сохранена', 'success');
                    if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
            }
        } else {
            spanNew.textContent = newVal || '';
            if (!newVal) spanNew.innerHTML = '<span class="text-muted">Нет данных</span>';
            if (newVal !== currentValue) {
                try {
                    await apiFetch('/payments/transactions/' + id, {
                        method: 'PATCH',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ note: newVal })
                    });
                    showToast('Примечание сохранено', 'success');
                } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
            }
        }
        input.replaceWith(spanNew);
    };
    
    input.addEventListener('blur', save);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.blur(); if (e.key === 'Escape') { input.replaceWith(span); } });
});

function setupPaymentsAutoSave() {
    const tbody = document.querySelector('#payments-table tbody');
    tbody.onchange = async (e) => {
        const id = e.target.dataset.id;
        if (!id) return;

        let updateData = {};
        if (e.target.classList.contains('cb-calc')) updateData.is_calculated = e.target.checked;
        else if (e.target.classList.contains('cb-invoice')) updateData.is_invoice_issued = e.target.checked;
        else if (e.target.classList.contains('cb-written-off')) {
            // Ручная галочка реестра (стр.2) — независима от доски списаний (стр.3)
            updateData.is_written_off = e.target.checked;
        } else if (e.target.classList.contains('input-note')) updateData.note = e.target.value;

        if (Object.keys(updateData).length > 0) {
            try {
                await apiFetch(`/payments/transactions/${id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updateData)
                });
            } catch (error) {
                showToast("Не удалось сохранить: " + error.message, 'error');
                if (e.target.type === 'checkbox') e.target.checked = !e.target.checked;
            }
        }
    };
}
