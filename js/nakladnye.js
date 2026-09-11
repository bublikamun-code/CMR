let allNakladnye = [];
let currentNakladnye = [];
let nakladnyeMonth = 'all';
let nakladnyeSort = { key: null, dir: 1 };
let nakladnyeSuppliers = [];

const NAK_STATUS_LABELS = {
    'new': 'Новая',
    'verified': 'Проверена',
    'arrived': 'Пришла',
    'paid': 'Оплачена',
};
const NAK_STATUS_CLASSES = {
    'new': 'status-new',
    'verified': 'status-verified',
    'arrived': 'status-arrived',
    'paid': 'status-paid',
};

const NakladnyeUI = {
    editingId: null,

    openModal(id) {
        this.editingId = id || null;
        const modal = document.getElementById('nakladnaya-modal');
        const title = document.getElementById('nakladnaya-modal-title');
        const form = document.getElementById('nakladnaya-form');
        form.reset();

        this._populateSuppliers();
        this._populateStores();

        if (id) {
            title.textContent = 'Редактировать накладную';
            const nak = allNakladnye.find(n => n.id === id);
            if (nak) {
                document.getElementById('nak-supplier-select').value = nak.supplier_id || '';
                document.getElementById('nak-doc-type').value = nak.doc_type || '';
                document.getElementById('nak-doc-series').value = nak.doc_series || '';
                document.getElementById('nak-doc-number').value = nak.doc_number || '';
                document.getElementById('nak-doc-date').value = nak.doc_date || '';
                document.getElementById('nak-amount').value = nak.amount || '';
                document.getElementById('nak-store').value = nak.store || '';
                document.getElementById('nak-unload-address').value = nak.unload_address || '';
                document.getElementById('nak-status').value = nak.status || 'new';
            }
        } else {
            title.textContent = 'Новая накладная';
        }
        modal.classList.remove('hidden');
    },

    closeModal() {
        document.getElementById('nakladnaya-modal').classList.add('hidden');
        this.editingId = null;
    },

    _populateSuppliers() {
        const sel = document.getElementById('nak-supplier-select');
        sel.innerHTML = '<option value="">— выберите —</option>';
        nakladnyeSuppliers.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.name;
            sel.appendChild(opt);
        });
    },

    _populateStores() {
        const sel = document.getElementById('nak-store');
        sel.innerHTML = '<option value="">—</option>';
        APP_STORES.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.value;
            opt.textContent = s.label;
            sel.appendChild(opt);
        });
    },

    async save() {
        const supplierId = document.getElementById('nak-supplier-select').value;
        const supplier = nakladnyeSuppliers.find(s => s.id == supplierId);
        const data = {
            supplier_id: supplierId ? parseInt(supplierId) : null,
            supplier_name: supplier ? supplier.name : '',
            doc_type: document.getElementById('nak-doc-type').value || null,
            doc_series: document.getElementById('nak-doc-series').value || null,
            doc_number: document.getElementById('nak-doc-number').value || null,
            doc_date: document.getElementById('nak-doc-date').value || null,
            amount: parseFloat(document.getElementById('nak-amount').value) || null,
            store: document.getElementById('nak-store').value || null,
            unload_address: document.getElementById('nak-unload-address').value || null,
            status: document.getElementById('nak-status').value || 'new',
        };

        if (!data.doc_number) {
            showToast('Укажите номер накладной', 'error');
            return;
        }

        try {
            if (this.editingId) {
                await apiFetch(`/nakladnye/${this.editingId}`, {
                    method: 'PATCH',
                    body: JSON.stringify(data),
                });
            } else {
                await apiFetch('/nakladnye', {
                    method: 'POST',
                    body: JSON.stringify(data),
                });
            }

            const photosInput = document.getElementById('nak-photos');
            if (photosInput.files.length > 0 && this.editingId) {
                for (const file of photosInput.files) {
                    const fd = new FormData();
                    fd.append('file', file);
                    await apiFetch(`/nakladnye/${this.editingId}/photos`, {
                        method: 'POST',
                        body: fd,
                    });
                }
            }

            this.closeModal();
            loadNakladnyeTable();
            showToast(this.editingId ? 'Накладная обновлена' : 'Накладная создана', 'success');
        } catch (err) {
            showToast('Ошибка: ' + err.message, 'error');
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('page-finance')?.classList.contains('active')
        && document.getElementById('page-nakladnye')?.classList.contains('active')) loadNakladnyeTable();

    setupSorting('nakladnye-table', (key, dir) => {
        nakladnyeSort = { key, dir };
        renderNakladnye();
    });

    bindTableSearch('nakladnye-search', 'nakladnye-table');

    const addBtn = document.getElementById('nakladnye-add-btn');
    if (addBtn) addBtn.addEventListener('click', () => NakladnyeUI.openModal());

    const exportBtn = document.getElementById('nakladnye-export');
    if (exportBtn) exportBtn.addEventListener('click', () => exportNakladnyeCsv());

    const form = document.getElementById('nakladnaya-form');
    if (form) form.addEventListener('submit', (e) => {
        e.preventDefault();
        NakladnyeUI.save();
    });

    const storeFilter = document.getElementById('nakladnye-filter-store');
    if (storeFilter) {
        APP_STORES.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.value;
            opt.textContent = s.label;
            storeFilter.appendChild(opt);
        });
        storeFilter.addEventListener('change', () => renderNakladnye());
    }

    const typeFilter = document.getElementById('nakladnye-filter-type');
    if (typeFilter) typeFilter.addEventListener('change', () => renderNakladnye());

    const statusFilter = document.getElementById('nakladnye-filter-status');
    if (statusFilter) statusFilter.addEventListener('change', () => renderNakladnye());

    setupTableScrollShadow('page-nakladnye');
});

async function loadNakladnyeTable() {
    if (!hasToken()) return;
    const tbody = document.querySelector('#nakladnye-table tbody');
    if (!tbody) return;

    try {
        const isFirstLoad = !tbody.querySelector('tr[data-id]');
        if (isFirstLoad) {
            tbody.innerHTML = getSkeletonHTML(10, 11);
        }

        const [data, suppliers] = await Promise.all([
            apiFetch('/nakladnye'),
            nakladnyeSuppliers.length === 0 ? apiFetch('/suppliers') : Promise.resolve(nakladnyeSuppliers),
        ]);

        allNakladnye = Array.isArray(data) ? data : [];
        nakladnyeSuppliers = Array.isArray(suppliers) ? suppliers : [];

        if (window.CRM_STORE) {
            CRM_STORE.set('nakladnye', allNakladnye);
        }

        tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());

        buildMonthFilter('nakladnye-month', allNakladnye, t => t.doc_date, nakladnyeMonth, (m) => {
            nakladnyeMonth = m;
            renderNakladnye();
        });

        renderNakladnye();
    } catch (error) {
        if (!tbody.querySelector('tr[data-id]')) {
            tbody.innerHTML = '<tr><td colspan="11"></td></tr>';
            tbody.querySelector('td').appendChild(renderAlert({
                type: 'error', title: 'Ошибка загрузки',
                message: error.message, onRetry: () => loadNakladnyeTable()
            }));
        }
    }
}

function renderNakladnye() {
    const tbody = document.querySelector('#nakladnye-table tbody');
    if (!tbody) return;

    let rows = nakladnyeMonth === 'all'
        ? allNakladnye
        : allNakladnye.filter(t => monthKeyOf(t.doc_date) === nakladnyeMonth);

    const storeVal = document.getElementById('nakladnye-filter-store')?.value;
    const typeVal = document.getElementById('nakladnye-filter-type')?.value;
    const statusVal = document.getElementById('nakladnye-filter-status')?.value;

    if (storeVal) rows = rows.filter(r => r.store === storeVal);
    if (typeVal) rows = rows.filter(r => r.doc_type === typeVal);
    if (statusVal) rows = rows.filter(r => r.status === statusVal);

    if (nakladnyeSort.key) rows = sortRows(rows, nakladnyeSort.key, nakladnyeSort.dir);

    currentNakladnye = rows;
    updateNakladnyeTotals(rows);

    if (rows.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="11">
                    <div class="empty-state-wrapper">
                        <div class="empty-state-icon">
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                        </div>
                        <div class="empty-state-title">Накладные не найдены</div>
                        <div class="empty-state-desc">Нажмите «Добавить» или дождитесь данных от бота.</div>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = '';
    rows.forEach(n => {
        const tr = document.createElement('tr');
        tr.setAttribute('data-id', n.id);

        const dateStr = n.doc_date ? (() => {
            try { return new Date(n.doc_date + 'T00:00:00').toLocaleDateString('ru-RU'); }
            catch(e) { return n.doc_date; }
        })() : '—';

        const statusLabel = NAK_STATUS_LABELS[n.status] || n.status;
        const statusClass = NAK_STATUS_CLASSES[n.status] || '';

        const photos = Array.isArray(n.photo_paths) ? n.photo_paths : [];
        const photosHtml = photos.length > 0
            ? `<span class="badge-neutral nak-photo-badge" title="${photos.length} фото" onclick="NakladnyeUI.showPhotos(${n.id})">📷 ${photos.length}</span>`
            : '<span class="text-muted">—</span>';

        tr.innerHTML = `
            <td>${dateStr}</td>
            <td title="${escapeHtml(n.supplier_name || '')}">${escapeHtml(n.supplier_name) || '<span class="text-muted">—</span>'}</td>
            <td><span class="badge-neutral">${escapeHtml(n.doc_type) || '—'}</span></td>
            <td>${escapeHtml(n.doc_series ? n.doc_series + '-' : '')}${escapeHtml(n.doc_number) || '—'}</td>
            <td class="amount-cell"><b class="tabular-nums">${n.amount != null ? formatMoneyBYN(n.amount) : '—'}</b></td>
            <td>${n.store ? `<span class="badge-neutral" data-kind="store" title="${escapeHtml(n.store)}">${escapeHtml(n.store)}</span>` : '—'}</td>
            <td>${escapeHtml(n.unload_address) || '—'}</td>
            <td class="td-center cb-col"><input type="checkbox" class="cb-verified cb-custom" data-id="${n.id}" ${n.is_verified ? 'checked' : ''} aria-label="Проверена"></td>
            <td class="td-center cb-col"><input type="checkbox" class="cb-paid cb-custom" data-id="${n.id}" ${n.is_paid ? 'checked' : ''} aria-label="Оплачена"></td>
            <td>${photosHtml}</td>
            <td class="td-center">
                <button class="btn-edit-row" data-nak-id="${n.id}" title="Редактировать" style="margin-right:4px">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                <button class="btn-delete-row" data-nak-id="${n.id}" title="Удалить">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:auto"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.cb-verified').forEach(cb => {
        cb.addEventListener('change', () => updateCheckbox(cb));
    });
    tbody.querySelectorAll('.cb-paid').forEach(cb => {
        cb.addEventListener('change', () => updateCheckbox(cb));
    });
    tbody.querySelectorAll('.btn-edit-row').forEach(btn => {
        btn.addEventListener('click', () => NakladnyeUI.openModal(parseInt(btn.dataset.nakId)));
    });
    tbody.querySelectorAll('.btn-delete-row').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!await confirmDialog('Удалить эту накладную?', { okText: 'Удалить', danger: true })) return;
            try {
                await apiFetch(`/nakladnye/${btn.dataset.nakId}`, { method: 'DELETE' });
                showToast('Накладная удалена', 'success');
                loadNakladnyeTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        });
    });

    const search = document.getElementById('nakladnye-search');
    if (search && search.value) filterTableRows('nakladnye-table', search.value);
}

async function updateCheckbox(cb) {
    const id = cb.dataset.id;
    const field = cb.classList.contains('cb-verified') ? 'is_verified' : 'is_paid';
    const value = cb.checked;

    const statusMap = { is_verified: 'verified', is_paid: 'paid' };
    const updateData = { [field]: value };

    if (field === 'is_verified' && value) updateData.status = 'verified';
    if (field === 'is_paid' && value) updateData.status = 'paid';

    try {
        await apiFetch(`/nakladnye/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(updateData),
        });
    } catch (err) {
        showToast('Не удалось сохранить: ' + err.message, 'error');
        cb.checked = !cb.checked;
    }
}

NakladnyeUI.showPhotos = function(nakId) {
    const nak = allNakladnye.find(n => n.id === nakId);
    if (!nak || !nak.photo_paths || nak.photo_paths.length === 0) {
        showToast('Фото отсутствуют', 'info');
        return;
    }

    let overlay = document.getElementById('nak-photos-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'nak-photos-overlay';
        overlay.className = 'cmd-palette-overlay';
        overlay.innerHTML = `
            <div class="cmd-palette" style="max-width:900px;max-height:90vh;overflow:auto;padding:20px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                    <h3 style="margin:0">Фото накладной</h3>
                    <button class="btn-secondary" onclick="document.getElementById('nak-photos-overlay').classList.add('hidden')">Закрыть</button>
                </div>
                <div id="nak-photos-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px"></div>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    const grid = document.getElementById('nak-photos-grid');
    grid.innerHTML = '';
    nak.photo_paths.forEach(p => {
        const img = document.createElement('img');
        img.src = `/nakladnye/photos/${p}`;
        img.style.cssText = 'width:100%;border-radius:8px;cursor:pointer';
        img.onclick = () => window.open(img.src, '_blank');
        grid.appendChild(img);
    });

    overlay.classList.remove('hidden');
};

function updateNakladnyeTotals(rows) {
    const countEl = document.getElementById('nakladnye-count');
    const totalEl = document.getElementById('nakladnye-total');
    if (countEl) countEl.textContent = `${rows.length} шт.`;
    const total = rows.reduce((sum, r) => sum + (parseFloat(r.amount) || 0), 0);
    if (totalEl) totalEl.textContent = formatMoneyBYN(total);
}

function exportNakladnyeCsv() {
    const headers = ['Дата', 'Поставщик', 'Тип', 'Серия', 'Номер', 'Сумма', 'Магазин', 'Адрес разгрузки', 'Статус', 'Проверена', 'Оплачена'];
    const rows = currentNakladnye.map(n => [
        n.doc_date || '',
        n.supplier_name || '',
        n.doc_type || '',
        n.doc_series || '',
        n.doc_number || '',
        n.amount || '',
        n.store || '',
        n.unload_address || '',
        NAK_STATUS_LABELS[n.status] || n.status || '',
        n.is_verified ? 'Да' : 'Нет',
        n.is_paid ? 'Да' : 'Нет',
    ]);
    const bom = '\uFEFF';
    const csv = bom + [headers, ...rows]
        .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
        .join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'nakladnye.csv';
    a.click();
    URL.revokeObjectURL(a.href);
}
