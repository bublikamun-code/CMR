let allPayments = [];        // все транзакции реестра (полный набор)
let currentPayments = [];    // то, что сейчас показано (для экспорта)
let paymentsMonth = 'all';   // выбранный месяц фильтра
let paymentsSort = { key: null, dir: 1 };

function renderPaymentStatusBadge(status) {
    const s = status || 'Не оплачен';
    const cls = PAYMENT_STATUS_CLASSES[s] || 'pay-unpaid';
    return `<span class="pay-badge ${cls}">${escapeHtml(s)}</span>`;
}

function renderPaymentCell(tr) {
    const total = parseFloat(tr.amount) || 0;
    const paid = parseFloat(tr.paid_amount) || 0;
    const status = tr.payment_status || 'Не оплачен';
    const cls = PAYMENT_STATUS_CLASSES[status] || 'pay-unpaid';
    const rest = Math.max(0, total - paid).toFixed(2);
    const percent = total > 0 ? Math.min(100, (paid / total) * 100) : (status === 'Оплачен' ? 100 : 0);

    const title = `Оплачено ${formatMoneyBYN(paid)} из ${formatMoneyBYN(total)}. Остаток: ${formatMoneyBYN(rest)} (${percent.toFixed(0)}%)`;

    // UI FIX 2026-08-29: ячейка была центрирована и собрана из трёх
    // разностильных строк (бейдж / сумма / полоска). Теперь всё прижато
    // к левому краю: бейдж статуса, под ним полоса прогресса и сумма.
    let amountText;
    if (status === 'Оплачен') {
        // Полная сумма и так видна в колонке «Сумма» — дублирование «X из X»
        // не влезало в узкую колонку при крупных числах.
        amountText = formatMoney(paid);
    } else if (status === 'Не оплачен') {
        amountText = `0,00 из ${formatMoney(total)}`;
    } else {
        amountText = `${formatMoney(paid)} из ${formatMoney(total)}`;
    }

    return `
        <span class="pay-badge pay-badge-payments ${cls}">${escapeHtml(status)}</span>
        <div class="pay-cell-progress" title="${escapeHtml(title)}">
            <div class="payment-mini-bar"><div class="payment-mini-bar-fill" style="width:${percent.toFixed(0)}%"></div></div>
            <span class="pay-cell-amount">${amountText}</span>
        </div>
    `;
}

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('page-finance')?.classList.contains('active')
        && document.getElementById('page-payments')?.classList.contains('active')) loadPaymentsTable();

    setupSorting('payments-table', (key, dir) => {
        paymentsSort = { key, dir };
        renderPayments();
    });

    bindTableSearch('payments-search', 'payments-table');

    const exportBtn = document.getElementById('payments-export');
    if (exportBtn) exportBtn.addEventListener('click', () => exportTransactionsToCsv(currentPayments, 'reestr_oplat.csv'));

    setupTableScrollShadow('page-payments');
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

        const raw = await apiFetch('/payments/transactions');
        allPayments = Array.isArray(raw) ? raw : [];
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
        console.error('loadPaymentsTable error:', error);
        if (!tbody.querySelector('tr[data-id]')) {
            // FIX 2026-09-03 (аудит): outerHTML сериализовал алерт и убивал
            // onclick — кнопка «Повторить» была мёртвой. Вставляем узлом.
            tbody.innerHTML = '<tr><td colspan="11"></td></tr>';
            tbody.querySelector('td').appendChild(renderAlert({ type: 'error', title: 'Ошибка загрузки', message: error.message, onRetry: () => loadPaymentsTable() }));
        }
    }
}

function renderPayments() {
    const tbody = document.querySelector('#payments-table tbody');
    if (!tbody) return;

    try {
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
                    <td colspan="11">
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
            // ISO-дата для инлайн-редактора (<input type="date">): берём из
            // показанной dateStr, чтобы редактор открывался ровно с той датой,
            // что видит пользователь (серверное время может отличаться)
            const isoFromParts = (() => {
                const [d, m, y] = dateStr.split('.');
                return dateStr.includes('.') ? `${y}-${m}-${d}` : '';
            })();
            const existing = existingRows[tr.id];

            if (existing) {
                // Каждый querySelector проверяется: раньше здесь висел несуществующий
                // .input-note, из-за него бросался TypeError, обрывался цикл и реестр
                // молча перестал обновляться со второго опроса.
                const cbCalc = existing.querySelector('.cb-calc');
                if (cbCalc) cbCalc.checked = tr.is_calculated;
                const cbInvoice = existing.querySelector('.cb-invoice');
                if (cbInvoice) cbInvoice.checked = tr.is_invoice_issued;
                const cbWrittenOff = existing.querySelector('.cb-written-off');
                if (cbWrittenOff) cbWrittenOff.checked = tr.is_written_off;

                // Дата, сумма и примечание — inline-edit ячейки. Пока
                // пользователь их редактирует, <span> подменён на <input>:
                // в этот момент не трогаем, иначе опрос затрёт ввод.
                const dateCell = existing.querySelector('.inline-edit-cell[data-field="date"]');
                if (dateCell && !dateCell.querySelector('input')) {
                    const dateSpan = dateCell.querySelector('.inline-edit');
                    if (dateSpan) {
                        dateSpan.textContent = dateStr;
                        dateCell.dataset.iso = isoFromParts;
                    }
                }

                const amountCell = existing.querySelector('.inline-edit-cell[data-field="amount"]');
                if (amountCell && !amountCell.querySelector('input')) {
                    const amountSpan = amountCell.querySelector('.inline-edit');
                    if (amountSpan) amountSpan.textContent = formatMoneyBYN(tr.amount || 0);
                }

                const paymentCell = existing.querySelector('td:nth-child(4)');
                if (paymentCell) paymentCell.innerHTML = renderPaymentCell(tr);

                const noteCell = existing.querySelector('.inline-edit-cell[data-field="note"]');
                if (noteCell && !noteCell.querySelector('input')) {
                    const noteSpan = noteCell.querySelector('.inline-edit');
                    if (noteSpan) {
                        if (tr.note) noteSpan.textContent = tr.note;
                        else noteSpan.innerHTML = '<span class="text-muted">Нет данных</span>';
                    }
                }

                // Реестр оплат показывает счёт клиента. Закупка у поставщиков
                // (чек-лист карточки) сюда не подмешивается.
                const companyCell = existing.querySelector('.clickable-company');
                if (companyCell) companyCell.textContent = tr.company_name;
                tbody.appendChild(existing);
            } else {
                const row = document.createElement('tr');
                row.setAttribute('data-id', tr.id);
                row.className = 'reveal reveal-fast';
                row.innerHTML = `
                    <td class="inline-edit-cell" data-field="date" data-id="${tr.id}" data-iso="${isoFromParts}" title="Дата оплаты — нажмите, чтобы изменить">
                        <span class="inline-edit">${escapeHtml(dateStr)}</span>
                    </td>
                    <td class="clickable-company" data-card-id="${tr.card_id || ''}" title="${escapeHtml(tr.company_name)}">
                        ${escapeHtml(tr.company_name)}
                    </td>
                    <td class="inline-edit-cell amount-cell" data-field="amount" data-id="${tr.id}" data-card-id="${tr.card_id || ''}" title="${tr.card_id ? 'Правка ведёт сумму сделки' : ''}">
                        <span class="inline-edit font-mono text-right tabular-nums">${escapeHtml(formatMoneyBYN(tr.amount || 0))}</span>
                    </td>
                    <td class="payment-cell">${renderPaymentCell(tr)}</td>
                    <td>${escapeHtml(tr.store_location) || '—'}</td>
                    <td class="td-center cb-col"><input type="checkbox" class="cb-calc cb-custom" data-id="${tr.id}" data-part-ids='${JSON.stringify(tr.part_ids || [tr.id])}' ${tr.is_calculated ? 'checked' : ''} aria-label="Просчет"></td>
                    <td class="td-center cb-col"><input type="checkbox" class="cb-invoice cb-custom" data-id="${tr.id}" data-part-ids='${JSON.stringify(tr.part_ids || [tr.id])}' ${tr.is_invoice_issued ? 'checked' : ''} aria-label="Выписка ТН"></td>
                    <td class="td-center cb-col"><input type="checkbox" class="cb-written-off cb-custom" data-id="${tr.id}" data-part-ids='${JSON.stringify(tr.part_ids || [tr.id])}' ${tr.is_written_off ? 'checked' : ''} aria-label="Списание с магазина"></td>
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

        if (typeof window.revealRefresh === 'function') window.revealRefresh();
    } catch (err) {
        console.error('renderPayments error:', err);
        tbody.innerHTML = `<tr><td colspan="11" class="td-error">Ошибка отрисовки: ${escapeHtml(err.message)}</td></tr>`;
    }
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
    if (totalEl) totalEl.textContent = formatMoneyBYN(sumAmount(transactions));
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
    // Запоминаем классы: у суммы это 'inline-edit font-mono text-right'.
    // Ниже span пересоздаётся, и без этого после правки число теряло
    // выравнивание по правому краю и уезжало влево.
    const spanClass = span.className;
    const input = document.createElement('input');
    input.type = field === 'amount' ? 'number' : (field === 'date' ? 'date' : 'text');
    input.className = 'inline-edit-input';
    // ПРЕФИЛЛ СУММЫ: показанное «5 735,00» превращалось в «573500» —
    // пробелы и запятая просто вырезались, и редактор открывался с суммой
    // ×100. Именно так на бою появились записи-остатки с миллионами
    // («ПроШоу Технологии», «Рацио Домус»). Теперь запятая становится
    // точкой, пробелы (в т.ч. неразрывные) и «BYN» вычищаются.
    input.value = field === 'amount' ? currentValue.replace(/[\s\u00a0]/g, '').replace('BYN', '').replace(',', '.').replace(/[^0-9.]/g, '')
        : (field === 'date' ? (cell.dataset.iso || '') : currentValue);
    if (field === 'amount') { input.step = '0.01'; input.min = '0'; }
    
    span.replaceWith(input);
    input.focus();
    input.select();
    
    // FIX 2026-09-03 (аудит): отмена по Esc не должна сохранять, а при
    // ошибке PATCH экран должен оставаться со старым значением.
    let cancelled = false;
    const save = async () => {
        if (cancelled) return;
        const newVal = input.value.trim();
        const spanNew = document.createElement('span');
        spanNew.className = spanClass;
        let failed = false;

        if (field === 'amount') {
            const numVal = parseFloat(newVal) || 0;
            spanNew.textContent = formatMoneyBYN(numVal);
            if (formatMoneyBYN(numVal) !== currentValue) {
                // Строка реестра показывает СУММУ СДЕЛКИ, поэтому и правка
                // ведёт в сумму сделки (карточка пересчитает остаток к
                // выписке). Править сумму отдельной записи вручную нельзя —
                // это ломало остатки. Записи без сделки (старые) правятся
                // как раньше — напрямую.
                const cardId = cell.dataset.cardId;
                try {
                    if (numVal <= 0) throw new Error('Сумма должна быть больше нуля');
                    if (cardId) {
                        await apiFetch('/cards/' + cardId, {
                            method: 'PATCH',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ total_amount: numVal })
                        });
                        showToast('Сумма сделки сохранена', 'success');
                    } else {
                        await apiFetch('/payments/transactions/' + id, {
                            method: 'PATCH',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ amount: numVal })
                        });
                        showToast('Сумма сохранена', 'success');
                    }
                    if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                } catch (err) { showToast('Ошибка: ' + err.message, 'error'); failed = true; }
            }
        } else if (field === 'date') {
            // Дата обязательна: пустое поле просто закрывается без правки
            if (!newVal) {
                input.replaceWith(span);
                return;
            }
            if (newVal !== (cell.dataset.iso || '')) {
                const [yy, mm, dd] = newVal.split('-');
                spanNew.textContent = `${dd}.${mm}.${yy}`;
                try {
                    await apiFetch('/payments/transactions/' + id, {
                        method: 'PATCH',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ date: newVal })
                    });
                    cell.dataset.iso = newVal;
                    showToast('Дата оплаты сохранена', 'success');
                } catch (err) {
                    showToast('Ошибка: ' + err.message, 'error');
                    failed = true;
                    input.replaceWith(span);
                    return;
                }
            } else {
                spanNew.textContent = currentValue;
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
                } catch (err) { showToast('Ошибка: ' + err.message, 'error'); failed = true; }
            }
        }
        input.replaceWith(failed ? span : spanNew);
    };

    input.addEventListener('blur', save);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
        if (e.key === 'Escape') {
            cancelled = true;
            input.removeEventListener('blur', save);
            input.replaceWith(span);
        }
    });
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
        }
        // Примечание сохраняется не здесь, а через inline-edit (см. блок
        // INLINE EDIT выше): в разметке строки нет поля .input-note.

        if (Object.keys(updateData).length > 0) {
            try {
                const partIds = JSON.parse(e.target.dataset.partIds || '[]');
                const ids = Array.isArray(partIds) && partIds.length > 0 ? partIds : [id];
                await Promise.all(ids.map(txId => apiFetch(`/payments/transactions/${txId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updateData)
                })));
            } catch (error) {
                showToast("Не удалось сохранить: " + error.message, 'error');
                if (e.target.type === 'checkbox') e.target.checked = !e.target.checked;
            }
        }
    };
}
