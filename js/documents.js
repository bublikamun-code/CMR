let allDocuments = [];
let currentDocuments = [];
let documentsMonth = 'all';
let documentsSort = { key: null, dir: 1 };

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('page-finance')?.classList.contains('active')
        && document.getElementById('page-documents')?.classList.contains('active')) loadDocumentsTable();

    setupSorting('documents-table', (key, dir) => {
        documentsSort = { key, dir };
        renderDocuments();
    });

    // D7 (UX-аудит): итоги футера — по видимым строкам, как в реестре оплат.
    bindTableSearch('documents-search', 'documents-table', 200, (value) => {
        filterTableRows('documents-table', value);
        updateDocumentsTotals(getVisibleTableRows(currentDocuments, 'documents-table'));
    });

    const exportBtn = document.getElementById('documents-export');
    // Фикс аудита 10.09: экспорт уважает поле поиска — как в реестре оплат.
    if (exportBtn) exportBtn.addEventListener('click', () => {
        const q = (document.getElementById('documents-search')?.value || '').trim().toLowerCase();
        const visible = q
            ? currentDocuments.filter(t => {
                const tr = document.querySelector(`#documents-table tbody tr[data-id="${t.id}"]`);
                return tr && tr.style.display !== 'none';
            })
            : currentDocuments;
        exportTransactionsToCsv(visible, 'dokumenty.csv');
    });

    setupTableScrollShadow('page-documents');
});

async function loadDocumentsTable() {
    if (!hasToken()) return;
    const tbody = document.querySelector('#documents-table tbody');
    if (!tbody) return;

    try {
        const isFirstLoad = !tbody.querySelector('tr[data-id]');
        if (isFirstLoad) {
            tbody.innerHTML = getSkeletonHTML(10, 5);
        }

        allDocuments = await apiFetch('/payments/documents');
        if (window.CRM_STORE) {
            CRM_STORE.set('documents', allDocuments);
            crmEmit('documents:loaded', { count: allDocuments.length });
        }

        // Убираем строку загрузки и лоадеры-скелетоны
        const loadingRow = tbody.querySelector('.td-loading');
        if (loadingRow) loadingRow.closest('tr').remove();
        tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());

        buildMonthFilter('documents-month', allDocuments, t => t.invoice_date, documentsMonth, (m) => {
            documentsMonth = m;
            renderDocuments();
        });

        renderDocuments();
    } catch (error) {
        if (!tbody.querySelector('tr[data-id]')) {
            // FIX 2026-09-03 (аудит): outerHTML сериализовал алерт и убивал
            // onclick — кнопка «Повторить» была мёртвой. Вставляем узлом.
            tbody.innerHTML = '<tr><td colspan="10"></td></tr>';
            tbody.querySelector('td').appendChild(renderAlert({ type: 'error', title: 'Ошибка загрузки', message: error.message, onRetry: () => loadDocumentsTable() }));
        }
    }
}

function renderDocuments() {
    const tbody = document.querySelector('#documents-table tbody');
    if (!tbody) return;

    const loadingRow = tbody.querySelector('.td-loading');
    if (loadingRow) loadingRow.closest('tr').remove();
    tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());

    let rows = documentsMonth === 'all'
        ? allDocuments
        : allDocuments.filter(t => monthKeyOf(t.invoice_date) === documentsMonth);

    if (documentsSort.key) rows = sortRows(rows, documentsSort.key, documentsSort.dir);

    currentDocuments = rows;
    updateDocumentsTotals(rows);

    // Сколько накладных выписано с одной карточки — чтобы связь была видна.
    // Фикс аудита 10.09: считаем по всемDocuments, а не по строкам после
    // фильтра месяца — иначе у сделки с накладными за разные месяцы каждый
    // месяц показывал «выписано 1 из 1».
    const groupCount = {};
    allDocuments.forEach(t => {
        if (t.card_id) groupCount[t.card_id] = (groupCount[t.card_id] || 0) + 1;
    });
    const groupIndex = {};

    if (rows.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10">
                    <div class="empty-state-wrapper">
                        <div class="empty-state-icon">
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                        </div>
                        <div class="empty-state-title">Документы не найдены</div>
                        <div class="empty-state-desc">В выбранном периоде нет оформленных документов. Все готово к работе!</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    const existingRows = {};
    tbody.querySelectorAll('tr[data-id]').forEach(tr => existingRows[tr.dataset.id] = tr);
    const newIds = new Set(rows.map(t => String(t.id)));

    tbody.querySelectorAll('tr[data-id]').forEach(tr => {
        if (!newIds.has(tr.dataset.id)) tr.remove();
    });

    rows.forEach(tr => {
        const grpTotal = tr.card_id ? (groupCount[tr.card_id] || 1) : 1;
        let grpIdx = 1;
        if (tr.card_id) {
            groupIndex[tr.card_id] = (groupIndex[tr.card_id] || 0) + 1;
            grpIdx = groupIndex[tr.card_id];
        }
        const existing = existingRows[tr.id];
        if (existing) {
            existing.classList.toggle('doc-linked', grpTotal > 1);
            if (tr.card_id) existing.dataset.group = tr.card_id;
            existing.querySelector('.cb-nakladnaya').checked = tr.is_invoice_doc;
            existing.querySelector('.cb-schet').checked = tr.is_bill_doc;
            existing.querySelector('.input-note').value = tr.note || '';
            const markersCell = existing.querySelector('.markers-cell');
            if (markersCell) {
                const storeBadge = markersCell.querySelector('.badge-neutral[data-kind="store"]') || markersCell.querySelector('.store-badge');
                const printBadge = markersCell.querySelector('.badge-neutral[data-kind="print"]') || markersCell.querySelector('.print-badge');
                if (storeBadge) {
                    if (tr.store_location) {
                        storeBadge.textContent = tr.store_location;
                        storeBadge.className = 'badge-neutral';
                        storeBadge.setAttribute('data-kind', 'store');
                        storeBadge.setAttribute('title', tr.store_location);
                    } else {
                        storeBadge.remove();
                    }
                } else if (tr.store_location) {
                    const badge = document.createElement('span');
                    badge.className = 'badge-neutral';
                    badge.setAttribute('data-kind', 'store');
                    badge.setAttribute('title', tr.store_location);
                    badge.textContent = tr.store_location;
                    markersCell.insertBefore(badge, printBadge || markersCell.firstChild);
                }
                if (printBadge) {
                    printBadge.textContent = tr.print_status || '—';
                    printBadge.setAttribute('data-print', tr.print_status || '');
                    printBadge.setAttribute('data-kind', 'print');
                    printBadge.setAttribute('title', tr.print_status || 'Не выбрано');
                    printBadge.className = 'badge-neutral';
                }
            }
            tbody.appendChild(existing);
        } else {
            const row = document.createElement('tr');
            row.setAttribute('data-id', tr.id);
            if (grpTotal > 1) {
                row.classList.add('doc-linked');
                row.dataset.group = tr.card_id;
            }
            const dateStr = new Date(tr.date).toLocaleDateString('ru-RU');
            const invoiceDateStr = tr.invoice_date ? (() => { const d = new Date(tr.invoice_date + 'T00:00:00'); return d.toLocaleDateString('ru-RU'); })() : '—';
            row.innerHTML = `
                <td>${dateStr}</td>
                <td class="clickable-company" data-card-id="${tr.card_id || ''}" title="${escapeHtml(tr.company_name)}">
                    ${escapeHtml(tr.company_name)}
                    ${grpTotal > 1 ? `<span class="doc-group" title="С этой сделки выписано ${grpTotal} накладных">${grpIdx}/${grpTotal}</span>` : ''}
                </td>
                <td class="amount-cell"><b class="tabular-nums">${formatMoneyBYN(tr.amount || 0)}</b></td>
                <td>${escapeHtml(tr.invoice_number) || '—'}</td>
                <td>${invoiceDateStr}</td>
                <td class="td-center cb-col"><input type="checkbox" class="cb-nakladnaya cb-custom" data-id="${tr.id}" ${tr.is_invoice_doc ? 'checked' : ''} aria-label="Накладная"></td>
                <td class="td-center cb-col"><input type="checkbox" class="cb-schet cb-custom" data-id="${tr.id}" ${tr.is_bill_doc ? 'checked' : ''} aria-label="Счет на оплату"></td>
                <td class="markers-cell">
                    ${tr.store_location ? `<span class="badge-neutral" data-kind="store" title="${escapeHtml(tr.store_location)}">${escapeHtml(tr.store_location)}</span>` : ''}
                    <span class="badge-neutral" data-kind="print" data-print="${escapeHtml(tr.print_status)}" title="${escapeHtml(tr.print_status || 'Не выбрано')}">${escapeHtml(tr.print_status) || '—'}</span>
                </td>
                <td><input type="text" class="input-note" data-id="${tr.id}" value="${escapeHtml(tr.note)}" placeholder="Заметка..."></td>
                <td class="td-center">
                    <button class="btn-delete-row" data-tx-id="${tr.id}" data-card-id="${tr.card_id || ''}" title="Удалить">
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: block; margin: auto;"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        }
    });

    // При наведении подсвечиваем все накладные одной сделки
    document.querySelectorAll('#documents-table tr.doc-linked').forEach(tr => {
        tr.onmouseenter = () => {
            document.querySelectorAll(`#documents-table tr[data-group="${tr.dataset.group}"]`)
                .forEach(x => x.classList.add('doc-group-hl'));
        };
        tr.onmouseleave = () => {
            document.querySelectorAll('#documents-table tr.doc-group-hl')
                .forEach(x => x.classList.remove('doc-group-hl'));
        };
    });

    document.querySelectorAll('#documents-table .clickable-company').forEach(td => {
        td.onclick = (e) => {
            const cardId = e.currentTarget.getAttribute('data-card-id');
            if (cardId) openCardModal(parseInt(cardId));
            else showToast('К этой записи не привязана карточка.', 'info');
        };
    });

    document.querySelectorAll('#documents-table .btn-delete-row').forEach(btn => {
        btn.onclick = async (e) => {
            e.stopPropagation();
            const txId = btn.dataset.txId;
            const cardId = btn.dataset.cardId;
            if (!await confirmDialog(
                'Удалить эту накладную?\n\nОна будет удалена и из складского списания. Сумма вернётся в остаток по сделке.',
                { okText: 'Удалить', danger: true }
            )) return;
            // Фикс аудита 10.09: повторный клик во время запроса давал 404
            // и «ошибку» сразу после успешного удаления.
            if (btn.disabled) return;
            btn.disabled = true;
            try {
                // Удаление сносит и накладную, и её копию — сервер держит их парой.
                if (txId) await apiFetch(`/payments/transactions/${txId}`, { method: 'DELETE' });

                // Статус карточки НЕ назначаем вслепую: раньше здесь стоял
                // безусловный перевод в «Сборку», из-за чего сделка пропадала
                // с доски списания, хотя внутри у неё оставались накладные.
                let status = null;
                if (cardId) {
                    try {
                        const r = await apiFetch(`/payments/cards/${cardId}/sync-writeoff-status`, { method: 'POST' });
                        status = r.status;
                    } catch (e) {}
                }
                loadDocumentsTable();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                showToast(status
                    ? `Накладная удалена. Сделка: «${status}»`
                    : 'Накладная удалена', 'success');
            } catch (err) {
                btn.disabled = false;
                showToast('Ошибка удаления: ' + err.message, 'error');
            }
        };
    });

    setupDocumentsAutoSave();

    const search = document.getElementById('documents-search');
    if (search && search.value) {
        filterTableRows('documents-table', search.value);
        updateDocumentsTotals(getVisibleTableRows(currentDocuments, 'documents-table'));
    }
}

function updateDocumentsTotals(transactions) {
    const countEl = document.getElementById('documents-count');
    const totalEl = document.getElementById('documents-total');
    if (countEl) countEl.textContent = `${transactions.length} шт.`;
    if (totalEl) totalEl.textContent = formatMoneyBYN(sumAmount(transactions));
}

function setupDocumentsAutoSave() {
    const tbody = document.querySelector('#documents-table tbody');
    tbody.onchange = async (e) => {
        const id = e.target.dataset.id;
        if (!id) return;

        let updateData = {};
        if (e.target.classList.contains('cb-nakladnaya')) updateData.is_invoice_doc = e.target.checked;
        else if (e.target.classList.contains('cb-schet')) updateData.is_bill_doc = e.target.checked;
        else if (e.target.classList.contains('input-note')) updateData.note = e.target.value;

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
