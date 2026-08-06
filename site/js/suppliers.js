let allSuppliers = [];
let editingSupplierId = null;

document.addEventListener('DOMContentLoaded', () => {
    const suppliersBtn = document.querySelector('[data-target="page-suppliers"]');
    if (suppliersBtn) suppliersBtn.addEventListener('click', loadSuppliersTable);
    if (document.getElementById('page-suppliers')?.classList.contains('active')) loadSuppliersTable();

    const search = document.getElementById('suppliers-search');
    if (search) search.addEventListener('input', (e) => filterTableRows('suppliers-table', e.target.value));

    document.getElementById('btn-create-supplier').onclick = () => openSupplierModal();
    document.getElementById('supplier-cancel').onclick = () => document.getElementById('supplier-modal').classList.add('hidden');
    document.querySelector('#supplier-modal .close-btn').onclick = () => document.getElementById('supplier-modal').classList.add('hidden');
    document.getElementById('supplier-save').onclick = saveSupplier;

    // Закрытие по клику на подложку — только если и НАЖАТИЕ было на подложке.
    // Иначе выделение текста мышью с выходом за край карточки закрывало модалку.
    const sModal = document.getElementById('supplier-modal');
    let sMouseDown = null;
    sModal.addEventListener('mousedown', (e) => { sMouseDown = e.target; });
    sModal.addEventListener('click', (e) => {
        if (e.target === sModal && sMouseDown === sModal) sModal.classList.add('hidden');
    });

    const addBtn = document.getElementById('supplier-contact-add');
    if (addBtn) addBtn.onclick = () => CRM_CONTACTS.add('supplier-contacts');
});

async function loadSuppliersTable() {
    if (!hasToken()) return;
    const tbody = document.querySelector('#suppliers-table tbody');
    if (!tbody) return;

    try {
        // Скелетон только при первой загрузке. Авто-опрос каждые 15 с
        // подменял таблицу скелетоном — это и выглядело как мигание экрана.
        const isFirstLoad = !tbody.querySelector('tr[data-id]');
        if (isFirstLoad) tbody.innerHTML = getSkeletonHTML(8, 5);
        allSuppliers = await apiFetch('/suppliers');
        if (window.CRM_STORE) {
            CRM_STORE.set('suppliers', allSuppliers);
            crmEmit('suppliers:loaded', { count: allSuppliers.length });
        }
        renderSuppliers();
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="8" class="td-error">Ошибка: ${escapeHtml(error.message)}</td></tr>`;
    }
}

function renderSuppliers() {
    const tbody = document.querySelector('#suppliers-table tbody');
    if (!tbody) return;

    if (allSuppliers.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8">
                    <div class="empty-state-wrapper">
                        <div class="empty-state-icon">
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>
                        </div>
                        <div class="empty-state-title">Поставщики не найдены</div>
                        <div class="empty-state-desc">В базе данных пока нет ни одного поставщика. Добавьте первого поставщика, чтобы начать работу.</div>
                        <button class="empty-state-btn" onclick="openSupplierModal()">
                            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                            Новый поставщик
                        </button>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = '';
    allSuppliers.forEach(s => {
        const row = document.createElement('tr');
        row.dataset.id = s.id;
        const ct = CRM_CONTACTS.summary(s.contact_person, s.phone, s.email);
        const ctCell = escapeHtml(ct.text) +
            (ct.extra > 0 ? ` <span class="contact-more">+${ct.extra}</span>` : '');
        row.innerHTML = `
            <td><b class="supplier-name-link" data-id="${s.id}" title="Показать закупки этого поставщика">${escapeHtml(s.name)}</b></td>
            <td>${escapeHtml(s.unp) || '—'}</td>
            <td>${escapeHtml(s.phone) || '—'}</td>
            <td>${escapeHtml(s.email) || '—'}</td>
            <td title="${escapeHtml(ct.title)}">${ctCell}</td>
            <td>${escapeHtml(s.address) || '—'}</td>
            <td>${escapeHtml(s.note) || ''}</td>
            <td class="td-center" style="display: inline-flex; gap: 6px; border: none; align-items: center; justify-content: center; height: 100%;">
                <button class="btn-delete-row btn-edit-supplier" data-id="${s.id}" title="Редактировать">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"/></svg>
                </button>
                <button class="btn-delete-row btn-delete-supplier" data-id="${s.id}" title="Удалить">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(row);
    });

    // Клик по названию — все закупки этого поставщика,
    // так же, как у клиента открывается список его сделок.
    tbody.querySelectorAll('.supplier-name-link').forEach(el => {
        el.onclick = () => openSupplierPurchases(parseInt(el.dataset.id));
    });

    tbody.querySelectorAll('.btn-edit-supplier').forEach(btn => {
        btn.onclick = () => openSupplierModal(parseInt(btn.dataset.id));
    });

    tbody.querySelectorAll('.btn-delete-supplier').forEach(btn => {
        btn.onclick = async () => {
            const id = btn.dataset.id;
            const supplier = allSuppliers.find(s => s.id == id);
            if (!await confirmDialog(`Удалить поставщика "${supplier?.name}"?`)) return;
            try {
                await apiFetch(`/suppliers/${id}`, { method: 'DELETE' });
                showToast('Поставщик удалён', 'success');
                loadSuppliersTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };
    });
}

function openSupplierModal(supplierId = null) {
    editingSupplierId = supplierId;
    const modal = document.getElementById('supplier-modal');
    const title = document.getElementById('supplier-modal-title');

    if (supplierId) {
        const supplier = allSuppliers.find(s => s.id === supplierId);
        if (!supplier) return;
        title.textContent = 'Редактировать поставщика';
        document.getElementById('supplier-name').value = supplier.name || '';
        document.getElementById('supplier-unp').value = supplier.unp || '';
        document.getElementById('supplier-address').value = supplier.address || '';
        document.getElementById('supplier-note').value = supplier.note || '';
        CRM_CONTACTS.render('supplier-contacts',
            CRM_CONTACTS.parse(supplier.contact_person, supplier.phone, supplier.email));
    } else {
        title.textContent = 'Новый поставщик';
        document.getElementById('supplier-name').value = '';
        document.getElementById('supplier-unp').value = '';
        document.getElementById('supplier-address').value = '';
        document.getElementById('supplier-note').value = '';
        CRM_CONTACTS.render('supplier-contacts', []);
    }

    modal.classList.remove('hidden');
    document.getElementById('supplier-name').focus();
}

async function saveSupplier() {
    const name = document.getElementById('supplier-name').value.trim();
    if (!name) {
        showToast('Введите название поставщика', 'error');
        return;
    }

    const contacts = CRM_CONTACTS.serialize(CRM_CONTACTS.collect('supplier-contacts'));
    const data = {
        name: name,
        unp: document.getElementById('supplier-unp').value.trim() || null,
        phone: contacts.phone,
        email: contacts.email,
        contact_person: contacts.contact_person,
        address: document.getElementById('supplier-address').value.trim() || null,
        note: document.getElementById('supplier-note').value.trim() || null,
    };

    try {
        if (editingSupplierId) {
            await apiFetch(`/suppliers/${editingSupplierId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            showToast('Поставщик обновлён', 'success');
        } else {
            await apiFetch('/suppliers', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            showToast('Поставщик создан', 'success');
        }
        document.getElementById('supplier-modal').classList.add('hidden');
        loadSuppliersTable();
    } catch (err) {
        showToast('Ошибка: ' + err.message, 'error');
    }
}



/* ============================================================
   ЗАКУПКИ ПОСТАВЩИКА (06.08.2026)
   Каждый пункт чек-листа закреплён за поставщиком — здесь
   видно все его заказы, сколько ему должны и что уже оплачено.
   ============================================================ */
async function openSupplierPurchases(supplierId) {
    let modal = document.getElementById('supplier-purchases-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'supplier-purchases-modal';
        modal.className = 'modal hidden';
        modal.innerHTML = `
            <div class="modal-content modal-content-auto">
                <button class="close-btn" aria-label="Закрыть">&times;</button>
                <h2 id="sup-pur-title">Закупки</h2>
                <div id="sup-pur-summary"></div>
                <div id="sup-pur-body"></div>
            </div>`;
        document.body.appendChild(modal);
        modal.querySelector('.close-btn').onclick = () => modal.classList.add('hidden');
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });
    }

    const body = modal.querySelector('#sup-pur-body');
    const summary = modal.querySelector('#sup-pur-summary');
    summary.innerHTML = '';
    body.innerHTML = '<div class="sup-pur-empty">Загрузка...</div>';
    modal.classList.remove('hidden');

    let data;
    try {
        data = await apiFetch(`/suppliers/${supplierId}/purchases`);
    } catch (e) {
        body.innerHTML = `<div class="sup-pur-empty">Не удалось загрузить: ${escapeHtml(e.message)}</div>`;
        return;
    }

    modal.querySelector('#sup-pur-title').textContent = `Закупки — ${data.supplier_name}`;

    if (!data.count) {
        summary.innerHTML = '';
        body.innerHTML = '<div class="sup-pur-empty">За этим поставщиком пока нет закупок.<br>Они появятся, когда вы выберете его в чек-листе сделки.</div>';
        return;
    }

    const pct = data.total_amount > 0
        ? Math.round(data.paid_amount / data.total_amount * 100) : 0;
    summary.innerHTML = `
        <div class="sup-pur-stats">
            <div class="sup-pur-stat">
                <div class="sup-pur-stat-label">Всего закупок</div>
                <div class="sup-pur-stat-value">${data.count}</div>
            </div>
            <div class="sup-pur-stat">
                <div class="sup-pur-stat-label">Сумма</div>
                <div class="sup-pur-stat-value">${data.total_amount.toFixed(2)}</div>
            </div>
            <div class="sup-pur-stat">
                <div class="sup-pur-stat-label">Оплачено</div>
                <div class="sup-pur-stat-value ok">${data.paid_amount.toFixed(2)}</div>
            </div>
            <div class="sup-pur-stat">
                <div class="sup-pur-stat-label">Осталось</div>
                <div class="sup-pur-stat-value ${data.unpaid_amount > 0.01 ? 'warn' : ''}">${data.unpaid_amount.toFixed(2)}</div>
            </div>
        </div>
        <div class="inv-progress"><div class="inv-progress-fill${pct >= 100 ? ' fill-done' : ''}" style="width:${pct}%"></div></div>
    `;

    body.innerHTML = `
        <table class="data-table sup-pur-table">
            <thead>
                <tr>
                    <th>Сделка</th>
                    <th>Статус</th>
                    <th class="td-right">Сумма</th>
                    <th class="td-center">Заказано</th>
                    <th class="td-center">Пришло</th>
                    <th class="td-center">Счёт</th>
                </tr>
            </thead>
            <tbody>
                ${data.items.map(i => `
                    <tr class="sup-pur-row" data-card-id="${i.card_id}">
                        <td><b>${escapeHtml(i.card_title || '—')}</b>${i.note ? `<div class="sup-pur-note">${escapeHtml(i.note)}</div>` : ''}</td>
                        <td><span class="sup-pur-status">${escapeHtml(i.card_status || '—')}</span></td>
                        <td class="td-right font-mono">${i.amount.toFixed(2)}</td>
                        <td class="td-center">${i.is_paid ? '✓' : '—'}</td>
                        <td class="td-center">${i.is_secondary_check ? '✓' : '—'}</td>
                        <td class="td-center">${i.has_invoice ? '📎' : '—'}</td>
                    </tr>`).join('')}
            </tbody>
        </table>`;

    body.querySelectorAll('.sup-pur-row').forEach(row => {
        row.onclick = () => {
            const cardId = parseInt(row.dataset.cardId);
            if (!cardId) return;
            modal.classList.add('hidden');
            if (typeof openCardModal === 'function') openCardModal(cardId);
        };
    });
}
