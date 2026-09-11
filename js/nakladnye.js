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

const NakladnyeUI = {
    editingId: null,

    openModal(id) {
        this.editingId = id || null;
        const modal = document.getElementById('nakladnaya-modal');
        const title = document.getElementById('nakladnaya-modal-title');

        this._populateSuppliers();
        this._populateStores();

        // Reset all fields
        document.getElementById('nak-supplier-select').value = '';
        document.getElementById('nak-doc-type').value = '';
        document.getElementById('nak-doc-series').value = '';
        document.getElementById('nak-doc-number').value = '';
        document.getElementById('nak-doc-date').value = '';
        document.getElementById('nak-amount').value = '';
        document.getElementById('nak-vat-amount').value = '';
        document.getElementById('nak-store').value = '';
        document.getElementById('nak-unload-address').value = '';

        // Rebuild enhanced dropdowns
        if (typeof enhanceSelectToDropdown === 'function') {
            ['nak-doc-type', 'nak-store'].forEach(sid => {
                const sel = document.getElementById(sid);
                if (sel) {
                    sel.dataset.enhanced = '';
                    const wrap = sel.nextElementSibling;
                    if (wrap && wrap.classList.contains('select-dd-wrap')) wrap.remove();
                    enhanceSelectToDropdown(sel);
                }
            });
        }

        // Supplier dropdown with search
        if (typeof createDropdown === 'function') {
            const supSel = document.getElementById('nak-supplier-select');
            if (supSel) {
                const wrap = supSel.nextElementSibling;
                if (wrap && wrap.classList.contains('select-dd-wrap')) wrap.remove();
                supSel.dataset.enhanced = '';
                const options = Array.from(supSel.options).map(o => ({ value: o.value, label: o.textContent }));
                const ddWrap = document.createElement('span');
                ddWrap.className = 'select-dd-wrap';
                ddWrap.appendChild(createDropdown({
                    options,
                    value: supSel.value,
                    searchable: true,
                    onChange: (v) => {
                        supSel.value = v;
                        supSel.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }));
                supSel.insertAdjacentElement('afterend', ddWrap);
                supSel.classList.add('native-select-hidden');
            }
        }

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
                document.getElementById('nak-vat-amount').value = nak.vat_amount || '';
                document.getElementById('nak-store').value = nak.store || '';
                document.getElementById('nak-unload-address').value = nak.unload_address || '';
                if (typeof syncEnhancedSelect === 'function') {
                    ['nak-supplier-select', 'nak-doc-type', 'nak-store'].forEach(sid => syncEnhancedSelect(sid));
                }
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
        const supplier = nakladnyeSuppliers.find(s => String(s.id) === String(supplierId));
        const data = {
            supplier_id: supplierId ? parseInt(supplierId) : null,
            supplier_name: supplier ? supplier.name : '',
            doc_type: document.getElementById('nak-doc-type').value || null,
            doc_series: document.getElementById('nak-doc-series').value || null,
            doc_number: document.getElementById('nak-doc-number').value || null,
            doc_date: document.getElementById('nak-doc-date').value || null,
            amount: parseFloat(document.getElementById('nak-amount').value) || null,
            vat_amount: parseFloat(document.getElementById('nak-vat-amount').value) || null,
            store: document.getElementById('nak-store').value || null,
            unload_address: document.getElementById('nak-unload-address').value || null,
            status: 'new',
        };

        try {
            if (this.editingId) {
                await apiFetch(`/nakladnye/${this.editingId}`, {
                    method: 'PATCH',
                    body: JSON.stringify(data),
                });
                // Photos for existing
                const photosInput = document.getElementById('nak-photos');
                if (photosInput && photosInput.files.length > 0) {
                    for (const file of photosInput.files) {
                        const fd = new FormData();
                        fd.append('file', file);
                        await apiFetch(`/nakladnye/${this.editingId}/photos`, { method: 'POST', body: fd });
                    }
                }
            } else {
                const created = await apiFetch('/nakladnye', {
                    method: 'POST',
                    body: JSON.stringify(data),
                });
                // Photos for new
                const photosInput = document.getElementById('nak-photos');
                if (photosInput && photosInput.files.length > 0 && created && created.id) {
                    for (const file of photosInput.files) {
                        const fd = new FormData();
                        fd.append('file', file);
                        await apiFetch(`/nakladnye/${created.id}/photos`, { method: 'POST', body: fd });
                    }
                }
            }

            this.closeModal();
            loadNakladnyeTable();
            showToast(this.editingId ? 'Накладная обновлена' : 'Накладная создана', 'success');
        } catch (err) {
            showToast('Ошибка: ' + err.message, 'error');
            console.error('Save nakladnaya:', err);
        }
    },

    showPhotos(nakId) {
        const nak = allNakladnye.find(n => n.id === nakId);
        if (!nak || !nak.photo_paths || nak.photo_paths.length === 0) {
            showToast('Фото отсутствуют', 'info');
            return;
        }
        this._openLightbox(nak.photo_paths.map(p => `/nakladnye/photos/${p}`), 0);
    },

    _openLightbox(urls, index) {
        let lb = document.getElementById('nak-lightbox');
        if (!lb) {
            lb = document.createElement('div');
            lb.id = 'nak-lightbox';
            lb.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;cursor:zoom-out';
            lb.innerHTML = `
                <button id="lb-close" style="position:absolute;top:16px;right:20px;background:none;border:none;color:#fff;font-size:32px;cursor:pointer;z-index:10">&times;</button>
                <button id="lb-prev" style="position:absolute;left:16px;top:50%;transform:translateY(-50%);background:none;border:none;color:#fff;font-size:40px;cursor:pointer;z-index:10">&#8249;</button>
                <button id="lb-next" style="position:absolute;right:16px;top:50%;transform:translateY(-50%);background:none;border:none;color:#fff;font-size:40px;cursor:pointer;z-index:10">&#8250;</button>
                <img id="lb-img" style="max-width:90vw;max-height:90vh;object-fit:contain;border-radius:8px;box-shadow:0 4px 40px rgba(0,0,0,.5)">
                <div id="lb-counter" style="position:absolute;bottom:20px;left:50%;transform:translateX(-50%);color:#fff;font-size:14px;opacity:.7"></div>
            `;
            document.body.appendChild(lb);
        }

        this._lbUrls = urls;
        this._lbIndex = index;
        const img = document.getElementById('lb-img');
        const counter = document.getElementById('lb-counter');
        img.src = urls[index];
        counter.textContent = urls.length > 1 ? `${index + 1} / ${urls.length}` : '';

        document.getElementById('lb-prev').style.display = urls.length > 1 ? 'block' : 'none';
        document.getElementById('lb-next').style.display = urls.length > 1 ? 'block' : 'none';

        const show = (i) => {
            this._lbIndex = (i + urls.length) % urls.length;
            img.src = urls[this._lbIndex];
            counter.textContent = `${this._lbIndex + 1} / ${urls.length}`;
        };

        lb.onclick = (e) => { if (e.target === lb) lb.style.display = 'none'; };
        document.getElementById('lb-close').onclick = () => lb.style.display = 'none';
        document.getElementById('lb-prev').onclick = (e) => { e.stopPropagation(); show(this._lbIndex - 1); };
        document.getElementById('lb-next').onclick = (e) => { e.stopPropagation(); show(this._lbIndex + 1); };

        lb.style.display = 'flex';
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

    document.getElementById('nak-save-btn')?.addEventListener('click', () => NakladnyeUI.save());

    document.getElementById('nakladnye-add-btn')?.addEventListener('click', () => NakladnyeUI.openModal());

    document.getElementById('nakladnye-export')?.addEventListener('click', () => exportNakladnyeCsv());

    document.getElementById('nakladnye-excel-products')?.addEventListener('click', () => {
        const params = new URLSearchParams();
        const store = document.getElementById('nakladnye-filter-store')?.value;
        if (store) params.set('store', store);
        if (nakladnyeMonth && nakladnyeMonth !== 'all') {
            params.set('date_from', nakladnyeMonth + '-01');
            params.set('date_to', nakladnyeMonth + '-31');
        }
        window.location.href = `/nakladnye/export/products-excel?${params.toString()}`;
    });

    // Filter: store
    const storeFilter = document.getElementById('nakladnye-filter-store');
    if (storeFilter) {
        APP_STORES.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.value;
            opt.textContent = s.label;
            storeFilter.appendChild(opt);
        });
        storeFilter.addEventListener('change', () => renderNakladnye());
        if (typeof enhanceSelectToDropdown === 'function') enhanceSelectToDropdown(storeFilter);
    }

    // Filter: type
    const typeFilter = document.getElementById('nakladnye-filter-type');
    if (typeFilter) {
        typeFilter.addEventListener('change', () => renderNakladnye());
        if (typeof enhanceSelectToDropdown === 'function') enhanceSelectToDropdown(typeFilter);
    }

    // Filter: status
    const statusFilter = document.getElementById('nakladnye-filter-status');
    if (statusFilter) {
        statusFilter.addEventListener('change', () => renderNakladnye());
        if (typeof enhanceSelectToDropdown === 'function') enhanceSelectToDropdown(statusFilter);
    }

    setupTableScrollShadow('page-nakladnye');
});

async function loadNakladnyeTable() {
    if (!hasToken()) return;
    const tbody = document.querySelector('#nakladnye-table tbody');
    if (!tbody) return;

    try {
        const isFirstLoad = !tbody.querySelector('tr[data-id]');
        if (isFirstLoad) tbody.innerHTML = getSkeletonHTML(10, 15);

        const [data, suppliers] = await Promise.all([
            apiFetch('/nakladnye'),
            nakladnyeSuppliers.length === 0 ? apiFetch('/suppliers') : Promise.resolve(nakladnyeSuppliers),
        ]);

        allNakladnye = Array.isArray(data) ? data : [];
        nakladnyeSuppliers = Array.isArray(suppliers) ? suppliers : [];

        tbody.querySelectorAll('.skeleton-row').forEach(r => r.remove());

        buildMonthFilter('nakladnye-month', allNakladnye, t => t.doc_date, nakladnyeMonth, (m) => {
            nakladnyeMonth = m;
            renderNakladnye();
        });

        renderNakladnye();
    } catch (error) {
        if (!tbody.querySelector('tr[data-id]')) {
            tbody.innerHTML = '<tr><td colspan="15"></td></tr>';
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
        tbody.innerHTML = `<tr><td colspan="15">
            <div class="empty-state-wrapper">
                <div class="empty-state-icon"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></div>
                <div class="empty-state-title">Накладные не найдены</div>
                <div class="empty-state-desc">Нажмите «Добавить» или дождитесь данных от бота.</div>
            </div></td></tr>`;
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

        const photos = Array.isArray(n.photo_paths) ? n.photo_paths : [];
        const photosHtml = photos.length > 0
            ? `<span class="badge-neutral nak-photo-badge" title="${photos.length} фото" style="cursor:pointer" onclick="NakladnyeUI.showPhotos(${n.id})">📷 ${photos.length}</span>`
            : '<span class="text-muted">—</span>';

        const hasProducts = Array.isArray(n.products) && n.products.length > 0;
        const excelHtml = hasProducts
            ? `<a href="/nakladnye/${n.id}/excel" class="btn-secondary btn-sm" style="padding:2px 8px;font-size:11px" title="Скачать Excel">📊</a>`
            : '<span class="text-muted">—</span>';

        tr.innerHTML = `
            <td>${dateStr}</td>
            <td title="${escapeHtml(n.supplier_name || '')}">${escapeHtml(n.supplier_name) || '<span class="text-muted">—</span>'}</td>
            <td><span class="badge-neutral">${escapeHtml(n.doc_type) || '—'}</span></td>
            <td>${escapeHtml((n.doc_series ? n.doc_series + '-' : '') + (n.doc_number || '')) || '—'}</td>
            <td class="amount-cell"><b class="tabular-nums">${n.amount != null ? formatMoneyBYN(n.amount) : '—'}</b></td>
            <td class="amount-cell"><b class="tabular-nums">${n.vat_amount != null ? formatMoneyBYN(n.vat_amount) : '—'}</b></td>
            <td>${n.store ? `<span class="badge-neutral" data-kind="store">${escapeHtml(n.store)}</span>` : '—'}</td>
            <td>${escapeHtml(n.unload_address) || '—'}</td>
            <td class="td-center cb-col"><input type="checkbox" class="cb-verified cb-custom" data-id="${n.id}" ${n.is_verified ? 'checked' : ''} aria-label="Проверена"></td>
            <td class="td-center cb-col"><input type="checkbox" class="cb-arrived cb-custom" data-id="${n.id}" ${n.is_arrived ? 'checked' : ''} aria-label="Пришла"></td>
            <td class="td-center cb-col"><input type="checkbox" class="cb-paid cb-custom" data-id="${n.id}" ${n.is_paid ? 'checked' : ''} aria-label="Оплачена"></td>
            <td>${photosHtml}</td>
            <td class="td-center">${excelHtml}</td>
            <td class="td-center">
                <button class="btn-edit-row" data-nak-id="${n.id}" title="Редактировать" style="margin-right:4px">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                <button class="btn-delete-row" data-nak-id="${n.id}" title="Удалить">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:auto"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            </td>`;
        tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.cb-verified').forEach(cb => { cb.onchange = () => _updateCb(cb); });
    tbody.querySelectorAll('.cb-arrived').forEach(cb => { cb.onchange = () => _updateCb(cb); });
    tbody.querySelectorAll('.cb-paid').forEach(cb => { cb.onchange = () => _updateCb(cb); });
    tbody.querySelectorAll('.btn-edit-row').forEach(btn => {
        btn.onclick = () => NakladnyeUI.openModal(parseInt(btn.dataset.nakId));
    });
    tbody.querySelectorAll('.btn-delete-row').forEach(btn => {
        btn.onclick = async () => {
            if (!await confirmDialog('Удалить накладную?', { okText: 'Удалить', danger: true })) return;
            try {
                await apiFetch(`/nakladnye/${btn.dataset.nakId}`, { method: 'DELETE' });
                showToast('Удалена', 'success');
                loadNakladnyeTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };
    });

    const search = document.getElementById('nakladnye-search');
    if (search && search.value) filterTableRows('nakladnye-table', search.value);
}

async function _updateCb(cb) {
    const id = cb.dataset.id;
    let field, value = cb.checked;
    if (cb.classList.contains('cb-verified')) field = 'is_verified';
    else if (cb.classList.contains('cb-arrived')) field = 'is_arrived';
    else field = 'is_paid';

    const updateData = { [field]: value };

    // Cascade: оплачена → пришла → проверена
    if (field === 'is_paid' && value) { updateData.is_arrived = true; updateData.is_verified = true; updateData.status = 'paid'; }
    else if (field === 'is_arrived' && value) { updateData.is_verified = true; updateData.status = value ? 'arrived' : 'verified'; }
    else if (field === 'is_verified' && value) { updateData.status = 'verified'; }

    // Uncheck cascade: сняли проверена → снять пришла и оплачена
    if (field === 'is_verified' && !value) { updateData.is_arrived = false; updateData.is_paid = false; updateData.status = 'new'; }
    if (field === 'is_arrived' && !value) { updateData.is_paid = false; updateData.status = updateData.is_verified ? 'verified' : 'new'; }

    try {
        await apiFetch(`/nakladnye/${id}`, { method: 'PATCH', body: JSON.stringify(updateData) });
        // Sync visual cascade
        const row = cb.closest('tr');
        if (row) {
            if (updateData.is_verified !== undefined) row.querySelector('.cb-verified').checked = updateData.is_verified;
            if (updateData.is_arrived !== undefined) row.querySelector('.cb-arrived').checked = updateData.is_arrived;
            if (updateData.is_paid !== undefined) row.querySelector('.cb-paid').checked = updateData.is_paid;
        }
    } catch (err) {
        showToast('Ошибка: ' + err.message, 'error');
        cb.checked = !cb.checked;
    }
}

function updateNakladnyeTotals(rows) {
    const countEl = document.getElementById('nakladnye-count');
    const totalEl = document.getElementById('nakladnye-total');
    if (countEl) countEl.textContent = `${rows.length} шт.`;
    const total = rows.reduce((sum, r) => sum + (parseFloat(r.amount) || 0), 0);
    if (totalEl) totalEl.textContent = formatMoneyBYN(total);
}

function exportNakladnyeCsv() {
    const headers = ['Дата', 'Поставщик', 'Тип', 'Серия', 'Номер', 'Сумма с НДС', 'НДС', 'Магазин', 'Адрес разгрузки', 'Проверена', 'Пришла', 'Оплачена'];
    const rows = currentNakladnye.map(n => [
        n.doc_date || '', n.supplier_name || '', n.doc_type || '',
        n.doc_series || '', n.doc_number || '',
        n.amount || '', n.vat_amount || '',
        n.store || '', n.unload_address || '',
        n.is_verified ? 'Да' : 'Нет',
        n.is_arrived ? 'Да' : 'Нет',
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
