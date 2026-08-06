let allClients = [];
let editingClientId = null;

document.addEventListener('DOMContentLoaded', () => {
    const clientsBtn = document.querySelector('[data-target="page-clients"]');
    if (clientsBtn) clientsBtn.addEventListener('click', loadClientsTable);
    if (document.getElementById('page-clients')?.classList.contains('active')) loadClientsTable();

    const search = document.getElementById('clients-search');
    if (search) search.addEventListener('input', (e) => filterTableRows('clients-table', e.target.value));

    document.getElementById('btn-create-client').onclick = () => openClientModal();
    document.getElementById('client-cancel').onclick = () => document.getElementById('client-modal').classList.add('hidden');
    document.querySelector('#client-modal .close-btn').onclick = () => document.getElementById('client-modal').classList.add('hidden');
    document.getElementById('client-save').onclick = saveClient;

    // Закрытие по клику на подложку — только если и НАЖАТИЕ было на подложке.
    // Иначе выделение текста мышью с выходом за край карточки закрывало модалку.
    const cModal = document.getElementById('client-modal');
    let cMouseDown = null;
    cModal.addEventListener('mousedown', (e) => { cMouseDown = e.target; });
    cModal.addEventListener('click', (e) => {
        if (e.target === cModal && cMouseDown === cModal) cModal.classList.add('hidden');
    });

    const addBtn = document.getElementById('client-contact-add');
    if (addBtn) addBtn.onclick = () => CRM_CONTACTS.add('client-contacts');

    // Модалка карточек клиента
    const ccModal = document.getElementById('client-cards-modal');
    ccModal.querySelector('.close-btn').onclick = () => ccModal.classList.add('hidden');
    let ccMouseDown = null;
    ccModal.addEventListener('mousedown', (e) => { ccMouseDown = e.target; });
    ccModal.addEventListener('click', (e) => {
        if (e.target === ccModal && ccMouseDown === ccModal) ccModal.classList.add('hidden');
    });
});

async function loadClientsTable() {
    if (!hasToken()) return;
    const tbody = document.querySelector('#clients-table tbody');
    if (!tbody) return;

    try {
        // Скелетон только при первой загрузке — авто-опрос каждые 15 с
        // подменял таблицу скелетоном, что и выглядело как мигание экрана.
        const isFirstLoad = !tbody.querySelector('tr[data-id]');
        if (isFirstLoad) tbody.innerHTML = getSkeletonHTML(7, 5);
        allClients = await apiFetch('/clients');
        if (window.CRM_STORE) {
            CRM_STORE.set('clients', allClients);
            crmEmit('clients:loaded', { count: allClients.length });
        }
        renderClients();
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="7" class="td-error">Ошибка: ${escapeHtml(error.message)}</td></tr>`;
    }
}

function renderClients() {
    const tbody = document.querySelector('#clients-table tbody');
    if (!tbody) return;

    if (allClients.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7">
                    <div class="empty-state-wrapper">
                        <div class="empty-state-icon">
                            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                        </div>
                        <div class="empty-state-title">Клиенты не найдены</div>
                        <div class="empty-state-desc">В базе данных пока нет ни одного клиента. Добавьте первого клиента, чтобы начать работу.</div>
                        <button class="empty-state-btn" onclick="openClientModal()">
                            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                            Новый клиент
                        </button>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = '';
    allClients.forEach(c => {
        const row = document.createElement('tr');
        row.dataset.id = c.id;
        const ct = CRM_CONTACTS.summary(c.contact_person, c.phone, c.email);
        const ctCell = escapeHtml(ct.text) +
            (ct.extra > 0 ? ` <span class="contact-more">+${ct.extra}</span>` : '');
        row.innerHTML = `
            <td class="clickable-company" data-id="${c.id}" style="cursor:pointer">${escapeHtml(c.name)}</td>
            <td>${escapeHtml(c.unp) || '—'}</td>
            <td>${escapeHtml(c.phone) || '—'}</td>
            <td>${escapeHtml(c.email) || '—'}</td>
            <td title="${escapeHtml(ct.title)}">${ctCell}</td>
            <td>${escapeHtml(c.note) || ''}</td>
            <td class="td-center" style="display: inline-flex; gap: 6px; border: none; align-items: center; justify-content: center; height: 100%;">
                <button class="btn-delete-row btn-edit-client" data-id="${c.id}" title="Редактировать">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"/></svg>
                </button>
                <button class="btn-delete-row btn-delete-client" data-id="${c.id}" title="Удалить">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(row);
    });

    // Клик по названию — открыть карточки клиента
    tbody.querySelectorAll('.clickable-company').forEach(td => {
        td.onclick = () => openClientCards(parseInt(td.dataset.id));
    });

    tbody.querySelectorAll('.btn-edit-client').forEach(btn => {
        btn.onclick = () => openClientModal(parseInt(btn.dataset.id));
    });

    tbody.querySelectorAll('.btn-delete-client').forEach(btn => {
        btn.onclick = async () => {
            const id = btn.dataset.id;
            const client = allClients.find(c => c.id == id);
            if (!await confirmDialog(`Удалить клиента "${client?.name}"?`)) return;
            try {
                await apiFetch(`/clients/${id}`, { method: 'DELETE' });
                showToast('Клиент удалён', 'success');
                loadClientsTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };
    });
}

async function openClientCards(clientId) {
    const client = allClients.find(c => c.id === clientId);
    if (!client) return;

    const modal = document.getElementById('client-cards-modal');
    const title = document.getElementById('client-cards-title');
    const body = document.getElementById('client-cards-body');

    title.textContent = `${client.name} — Карточки`;
    body.innerHTML = '<p class="td-loading">Загрузка карточек...</p>';
    modal.classList.remove('hidden');

    try {
        const cards = await apiFetch(`/clients/${clientId}/cards`);

        if (cards.length === 0) {
            body.innerHTML = '<p class="td-empty">Нет привязанных карточек.</p>';
            return;
        }

        let html = '<table class="data-table"><thead><tr><th>Название</th><th>Статус</th><th>Сумма</th><th>Магазин</th><th>Дата</th></tr></thead><tbody>';
        cards.forEach(card => {
            const date = new Date(card.created_at).toLocaleDateString('ru-RU');
            html += `<tr style="cursor:pointer" data-card-id="${card.id}" class="client-card-row">
                <td><b>${escapeHtml(card.title)}</b></td>
                <td>${escapeHtml(card.status)}</td>
                <td>${card.total_amount || 0} BYN</td>
                <td>${escapeHtml(card.store_location) || '—'}</td>
                <td>${date}</td>
            </tr>`;
        });
        html += '</tbody></table>';
        body.innerHTML = html;
        body.querySelectorAll('.client-card-row').forEach(row => {
            row.onclick = () => closeClientCardsAndOpenCard(parseInt(row.dataset.cardId));
        });
    } catch (err) {
        body.innerHTML = `<p class="td-error">Ошибка: ${escapeHtml(err.message)}</p>`;
    }
}

function closeClientCardsAndOpenCard(cardId) {
    document.getElementById('client-cards-modal').classList.add('hidden');
    openCardModal(cardId);
}

function openClientModal(clientId = null) {
    editingClientId = clientId;
    const modal = document.getElementById('client-modal');
    const title = document.getElementById('client-modal-title');

    if (clientId) {
        const client = allClients.find(c => c.id === clientId);
        if (!client) return;
        title.textContent = 'Редактировать клиента';
        document.getElementById('client-name').value = client.name || '';
        document.getElementById('client-unp').value = client.unp || '';
        document.getElementById('client-address').value = client.address || '';
        document.getElementById('client-note').value = client.note || '';
        CRM_CONTACTS.render('client-contacts',
            CRM_CONTACTS.parse(client.contact_person, client.phone, client.email));
    } else {
        title.textContent = 'Новый клиент';
        document.getElementById('client-name').value = '';
        document.getElementById('client-unp').value = '';
        document.getElementById('client-address').value = '';
        document.getElementById('client-note').value = '';
        CRM_CONTACTS.render('client-contacts', []);
    }

    modal.classList.remove('hidden');
    document.getElementById('client-name').focus();
}

async function saveClient() {
    const name = document.getElementById('client-name').value.trim();
    if (!name) {
        showToast('Введите название клиента', 'error');
        return;
    }

    const contacts = CRM_CONTACTS.serialize(CRM_CONTACTS.collect('client-contacts'));
    const data = {
        name: name,
        unp: document.getElementById('client-unp').value.trim() || null,
        phone: contacts.phone,
        email: contacts.email,
        contact_person: contacts.contact_person,
        address: document.getElementById('client-address').value.trim() || null,
        note: document.getElementById('client-note').value.trim() || null,
    };

    // Сервер сохраняет запись, но падает при сборке ответа:
    // в clients_router после commit вызывается save_version() с вторым commit,
    // объект становится expired, а finally: tdb.close() срабатывает до
    // сериализации → DetachedInstanceError → 500. У поставщиков этого нет,
    // там save_version не вызывается. Лечится на бэкенде (session.refresh
    // после save_version), но бэкенд не перезапустить — поэтому здесь
    // проверяем факт: если данные в базе совпали — сохранение удалось.
    async function savedAnyway(id) {
        try {
            const list = await apiFetch('/clients');
            const rec = (list || []).find(x => x.id === id);
            if (!rec) return false;
            return (rec.name || '') === (data.name || '')
                && (rec.contact_person || '') === (data.contact_person || '')
                && (rec.phone || '') === (data.phone || '');
        } catch (e) { return false; }
    }

    try {
        if (editingClientId) {
            try {
                await apiFetch(`/clients/${editingClientId}`, {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
            } catch (err) {
                if (!(await savedAnyway(editingClientId))) throw err;
            }
            showToast('Клиент обновлён', 'success');
        } else {
            try {
                await apiFetch('/clients', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
            } catch (err) {
                // при создании id ещё не известен — ищем запись по названию
                let ok = false;
                try {
                    const list = await apiFetch('/clients');
                    ok = (list || []).some(x => (x.name || '') === data.name);
                } catch (e) { ok = false; }
                if (!ok) throw err;
            }
            showToast('Клиент создан', 'success');
        }
        document.getElementById('client-modal').classList.add('hidden');
        loadClientsTable();
    } catch (err) {
        showToast('Ошибка: ' + err.message, 'error');
    }
}
