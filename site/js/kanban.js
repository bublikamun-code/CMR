document.addEventListener('DOMContentLoaded', () => {
    loadKanbanBoard();
    setupCreateCardButton();
    setupTrashButton();
    setupExportCSV();

    let _searchDebounce = null;
    const search = document.getElementById('kanban-search');
    if (search) search.addEventListener('input', (e) => {
        _kanbanSearchQuery = e.target.value;
        clearTimeout(_searchDebounce);
        _searchDebounce = setTimeout(() => loadKanbanBoard(), 200);
    });
});

let showOldCards = { "Новый запрос": false, "В работе": false, "Ждет оплаты": false, "Сборка": false };
// «На списание» и «Закрыто» — рабочие статусы разделов «Списание» и «Документы»,
// на канбан-доске колонок для них нет. Такие сделки живут в своих разделах,
// а при возврате из архива статус пересчитывается сервером.
const KANBAN_COLUMNS = ["Новый запрос", "В работе", "Ждет оплаты", "Сборка"];
const OLD_CARD_DAYS = 15;
let _isDropping = false;
let _kanbanSearchQuery = '';
let _kanbanFilters = { store: '', amountMin: '', amountMax: '', client: '', priority: '' };
let _kanbanView = 'board'; // 'board' or 'list'
let _allCards = [];

async function loadKanbanBoard() {
    const board = document.getElementById('kanban-board');
    if (!board) return;

    if (!hasToken()) return;

    if (!board.querySelector('.kanban-column')) {
        board.innerHTML = `<div style="display:flex;gap:16px;">
            ${KANBAN_COLUMNS.map(() => `<div style="flex:1;background:var(--bg-color);border-radius:var(--radius-lg);padding:12px;">
                <div class="skeleton-line" style="height:18px;width:60%;margin-bottom:16px;"></div>
                ${Array(3).fill('').map(() => `<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:var(--radius-md);padding:14px;margin-bottom:10px;">
                    <div class="skeleton-line" style="height:14px;width:70%;margin-bottom:8px;"></div>
                    <div class="skeleton-line short" style="height:12px;width:40%;margin-bottom:8px;"></div>
                    <div class="skeleton-line" style="height:10px;width:50%;margin-bottom:6px;"></div>
                    <div style="display:flex;gap:4px;margin-top:8px;">
                        <div class="skeleton-line" style="height:6px;flex:1;border-radius:99px;"></div>
                        <div class="skeleton-line" style="height:6px;flex:1;border-radius:99px;"></div>
                    </div>
                </div>`).join('')}
            </div>`).join('')}
        </div>`;
    }

    try {
        const cards = await apiFetch('/kanban/cards');
        _allCards = cards;
        if (window.CRM_STORE) {
            CRM_STORE.set('cards', cards);
            crmEmit('kanban:loaded', { count: cards.length });
        }

        if (!board.querySelector('.kanban-column')) {
            board.innerHTML = '';
            const columnsContainer = document.createElement('div');
            columnsContainer.style.display = "flex";
            columnsContainer.style.gap = "16px";
            columnsContainer.style.flex = "1";
            columnsContainer.style.minWidth = "0";
            columnsContainer.id = 'columns-container';
            board.appendChild(columnsContainer);

            KANBAN_COLUMNS.forEach(colName => {
                const col = document.createElement('div');
                col.className = 'kanban-column';
                col.setAttribute('data-status', colName);
                col.innerHTML = `<h3><span>${colName}</span><span class="col-count">0</span></h3><div class="col-sum"></div><div class="kanban-cards"></div>`;
                col.ondragover = (e) => { e.preventDefault(); col.classList.add('drag-over'); };
                col.ondragleave = () => col.classList.remove('drag-over');
                col.ondrop = handleDrop;
                columnsContainer.appendChild(col);
            });
        }

        KANBAN_COLUMNS.forEach(colName => {
            const col = board.querySelector(`.kanban-column[data-status="${colName}"]`);
            if (!col) return;
            const container = col.querySelector('.kanban-cards');
            if (!container) return;

            let colCards = cards.filter(c => c.status === colName);

            // Сортировка: новые сверху, просроченные внизу
            const now = new Date();
            colCards.sort((a, b) => {
                const aOverdue = a.due_date && new Date(a.due_date + 'T00:00:00') < now && a.status !== 'Закрыто';
                const bOverdue = b.due_date && new Date(b.due_date + 'T00:00:00') < now && b.status !== 'Закрыто';
                if (aOverdue !== bOverdue) return aOverdue ? 1 : -1;
                return new Date(b.created_at) - new Date(a.created_at);
            });

            const visibleCards = colCards.filter(c => {
                const diffDays = Math.ceil((new Date() - new Date(c.created_at)) / (1000 * 60 * 60 * 24));
                const isOld = diffDays > OLD_CARD_DAYS;
                if (isOld && !showOldCards[colName]) return false;

                // Фильтрация по поиску
                const q = (_kanbanSearchQuery || '').trim().toLowerCase();
                if (q) {
                    const title = (c.title || '').toLowerCase();
                    const tags = (c.tags || []).map(t => (t.name || '').toLowerCase()).join(' ');
                    const manager = (c.owner?.username || '').toLowerCase();
                    const store = (c.store_location || '').toLowerCase();
                    const amount = String(c.total_amount || '');
                    const client = (c.client?.name || '').toLowerCase();
                    const desc = (c.description || '').toLowerCase();
                    if (!title.includes(q) && !tags.includes(q) && !manager.includes(q)
                        && !store.includes(q) && !amount.includes(q) && !client.includes(q)
                        && !desc.includes(q)) {
                        return false;
                    }
                }

                // Расширенные фильтры
                const f = _kanbanFilters;
                if (f.store && c.store_location !== f.store) return false;
                if (f.amountMin && (parseFloat(c.total_amount) || 0) < parseFloat(f.amountMin)) return false;
                if (f.amountMax && (parseFloat(c.total_amount) || 0) > parseFloat(f.amountMax)) return false;
                if (f.client && !(c.client?.name || '').toLowerCase().includes(f.client.toLowerCase())) return false;
                if (f.priority && String(c.priority || 0) !== f.priority) return false;

                return true;
            });

            const countEl = col.querySelector('.col-count');
            if (countEl) {
                if (visibleCards.length !== colCards.length) {
                    countEl.textContent = `${visibleCards.length}/${colCards.length}`;
                } else {
                    countEl.textContent = colCards.length;
                }
            }
            const sumEl = col.querySelector('.col-sum');
            if (sumEl) {
                const colSum = visibleCards.reduce((acc, c) => acc + (parseFloat(c.total_amount) || 0), 0);
                sumEl.textContent = colSum > 0 ? `${formatMoney(colSum)} BYN` : '';
            }

            const existingIds = new Set();
            container.querySelectorAll('.kanban-card').forEach(el => existingIds.add(el.dataset.id));
            const newIds = new Set(visibleCards.map(c => String(c.id)));

            container.querySelectorAll('.kanban-card').forEach(el => {
                if (!newIds.has(el.dataset.id)) el.remove();
            });

            visibleCards.forEach(card => {
                const existing = container.querySelector(`.kanban-card[data-id="${card.id}"]`);
                if (existing) {
                    fillCardHTML(existing, card);
                } else {
                    renderCard(card, container);
                }
            });

            const oldBtn = col.querySelector('.btn-show-old');
            const hasOld = colCards.some(c => Math.ceil((new Date() - new Date(c.created_at)) / (1000 * 60 * 60 * 24)) > OLD_CARD_DAYS);
            if (hasOld) {
                if (!oldBtn) {
                    const btn = document.createElement('button');
                    btn.className = 'btn-show-old';
                    btn.textContent = showOldCards[colName] ? "Скрыть старые" : `Показать старые (>${OLD_CARD_DAYS}д)`;
                    btn.onclick = () => { showOldCards[colName] = !showOldCards[colName]; loadKanbanBoard(); };
                    col.appendChild(btn);
                } else {
                    oldBtn.textContent = showOldCards[colName] ? "Скрыть старые" : `Показать старые (>${OLD_CARD_DAYS}д)`;
                }
            } else if (oldBtn) {
                oldBtn.remove();
            }
        });

        // Сводка по просроченным карточкам
        const now = new Date();
        const overdueCards = cards.filter(c =>
            c.due_date && new Date(c.due_date + 'T00:00:00') < now && c.status !== 'Закрыто'
        );
        let overdueBar = board.querySelector('.overdue-summary-bar');
        if (overdueCards.length > 0) {
            if (!overdueBar) {
                overdueBar = document.createElement('div');
                overdueBar.className = 'overdue-summary-bar';
                board.prepend(overdueBar);
            }
            overdueBar.innerHTML = `<span class="overdue-summary-icon">⚠</span> Просрочено: <strong>${overdueCards.length}</strong> ${overdueCards.length === 1 ? 'карточка' : 'карточек'}`;
            overdueBar.style.display = '';
        } else if (overdueBar) {
            overdueBar.style.display = 'none';
        }
    } catch (error) {
        console.error("Ошибка загрузки карточек:", error);
    }
}

function renderCard(card, columnContainer) {
    if (!columnContainer) return;

    const diffDays = Math.ceil((new Date() - new Date(card.created_at)) / (1000 * 60 * 60 * 24));
    const isOld = diffDays > OLD_CARD_DAYS;
    if (isOld && !showOldCards[card.status]) return;

    const cardEl = document.createElement('div');
    cardEl.className = 'kanban-card' + (isOld ? ' old-card' : '');
    if (card.status === "Сборка") cardEl.classList.add('card-assembly');
    cardEl.setAttribute('draggable', 'true');
    cardEl.setAttribute('data-id', card.id);
    
    fillCardHTML(cardEl, card);

    cardEl.onclick = (e) => { if (!e.target.closest('.btn-delete-card')) openCardModal(card.id); };
    cardEl.ondragstart = (e) => {
        cardEl.classList.add('dragging');
        e.dataTransfer.setData('text/plain', card.id);
    };
    cardEl.ondragend = () => cardEl.classList.remove('dragging');

    columnContainer.appendChild(cardEl);
}

function fillCardHTML(cardEl, card) {
    const diffDays = Math.ceil((new Date() - new Date(card.created_at)) / (1000 * 60 * 60 * 24));
    const isOld = diffDays > OLD_CARD_DAYS;

    // Deal Rotting: warning if >3 days in active columns
    const updatedDiff = card.updated_at ? Math.ceil((new Date() - new Date(card.updated_at)) / (1000 * 60 * 60 * 24)) : 0;
    const isActiveColumn = ['В работе', 'Сборка'].includes(card.status);
    const isRotting = isActiveColumn && updatedDiff > 3;
    const isRottingDanger = isActiveColumn && updatedDiff > 7;
    if (isRottingDanger) cardEl.classList.add('deal-rotting-danger');
    else if (isRotting) cardEl.classList.add('deal-rotting');

    const total = card.checklists ? card.checklists.length : 0;
    const paidCount = card.checklists ? card.checklists.filter(c => c.is_paid).length : 0;
    const secCount = card.checklists ? card.checklists.filter(c => c.is_secondary_check).length : 0;
    const invoiceCount = card.checklists ? card.checklists.filter(c => c.invoice_file_path).length : 0;

    const creatorName = card.owner ? card.owner.username : 'Неизвестно';

    const paidPct = total > 0 ? Math.round((paidCount / total) * 100) : 0;
    const secPct = total > 0 ? Math.round((secCount / total) * 100) : 0;

    const now = new Date();
    const dueDate = card.due_date ? new Date(card.due_date + 'T00:00:00') : null;
    const isOverdue = dueDate && dueDate < now && card.status !== 'Закрыто';
    const isDueSoon = dueDate && !isOverdue && (dueDate - now) < (3 * 24 * 60 * 60 * 1000);

    if (isOverdue) cardEl.classList.add('card-overdue');
    else if (isDueSoon) cardEl.classList.add('card-due-soon');
    const dueDateStr = card.due_date ? new Date(card.due_date + 'T00:00:00').toLocaleDateString('ru-RU') : '';

    const tagsHtml = (card.tags && card.tags.length > 0)
        ? `<div class="card-tags">${card.tags.map(t => {
            const c = sanitizeColor(t.color);
            const r = parseInt(c.slice(1,3),16), g = parseInt(c.slice(3,5),16), b = parseInt(c.slice(5,7),16);
            const bg = `rgb(${Math.round(r+(255-r)*0.8)},${Math.round(g+(255-g)*0.8)},${Math.round(b+(255-b)*0.8)})`;
            return `<span class="card-tag" style="color:${c};background:${bg}">${escapeHtml(t.name)}</span>`;
        }).join('')}</div>`
        : '';

    cardEl.innerHTML = `
        <div class="card-hover-actions">
            <button class="card-hover-btn btn-edit-card" title="Редактировать" data-card-id="${card.id}">✎</button>
            <button class="card-hover-btn btn-delete-card" title="Удалить">&times;</button>
        </div>
        <div class="card-header">
            <strong class="card-title">${escapeHtml(card.title)}</strong>
        </div>
        <div class="card-amount">${escapeHtml(String(card.total_amount || 0))} BYN</div>
        <div class="card-badges">
            ${card.store_location ? `<span class="store-badge store-${escapeHtml(card.store_location)}">${escapeHtml(card.store_location)}</span>` : ''}
            ${invoiceCount > 0 ? `<span class="paperclip-badge" title="Прикреплённых счетов: ${invoiceCount}">${ICON_CLIP}${invoiceCount}</span>` : ''}
            ${isOld ? `<span class="old-card-badge" title="Создана более ${OLD_CARD_DAYS} дней назад">СТАРАЯ (${diffDays}д)</span>` : ''}
            ${isOverdue ? `<span class="overdue-badge-card">ПРОСРОЧЕНО</span>` : ''}
            ${isDueSoon && !isOverdue ? `<span class="due-soon-badge">до ${dueDateStr}</span>` : ''}
        </div>
        ${tagsHtml}
        <div class="card-manager">Менеджер: ${escapeHtml(creatorName)}</div>
        ${total > 0 ? `<div class="card-progress">
            <div class="progress-row">
                <span class="progress-label ${paidCount === total ? 'done' : ''}" style="${paidCount === total ? 'color:var(--success-color);font-weight:700' : ''}">Заказано ${paidCount}/${total}</span>
                <div class="progress-track"><div class="progress-fill paid" style="width:${paidPct}%"></div></div>
            </div>
            <div class="progress-row">
                <span class="progress-label ${secCount === total ? 'done blue' : ''}" style="${secCount === total ? 'color:var(--primary-color);font-weight:700' : ''}">Пришло ${secCount}/${total}</span>
                <div class="progress-track"><div class="progress-fill came" style="width:${secPct}%"></div></div>
            </div>
        </div>` : ''}
    `;

    cardEl.querySelector('.btn-delete-card').onclick = async (e) => {
        e.stopPropagation();
        if (await confirmDialog("Удалить карточку?")) {
            await apiFetch(`/kanban/cards/${card.id}`, { method: 'DELETE' });
            cardEl.remove();
            showToast('Карточка удалена', 'success');
        }
    };

    cardEl.querySelector('.btn-edit-card').onclick = (e) => {
        e.stopPropagation();
        openCardModal(card.id);
    };
}

window.refreshCardOnBoard = async function(cardId) {
    try {
        const cards = await apiFetch('/kanban/cards');
        const card = cards.find(c => c.id === cardId);
        if (!card) return;

        const cardEl = document.querySelector(`div[data-id="${cardId}"]`);
        if (cardEl) {
            fillCardHTML(cardEl, card);
        }
    } catch (e) {
        console.error("Ошибка обновления карточки на доске:", e);
    }
};

async function handleDrop(e) {
    e.preventDefault();
    if (_isDropping) return;
    _isDropping = true;
    const column = e.currentTarget;
    column.classList.remove('drag-over');
    const cardId = e.dataTransfer.getData('text/plain');
    const newStatus = column.getAttribute('data-status');

    const cardEl = document.querySelector(`.kanban-card[data-id="${cardId}"]`);
    const targetContainer = column.querySelector('.kanban-cards');
    if (!cardEl || !targetContainer) { _isDropping = false; return; }

    const oldColumn = cardEl.closest('.kanban-column');
    const oldStatus = oldColumn ? oldColumn.getAttribute('data-status') : null;

    targetContainer.appendChild(cardEl);
    cardEl.classList.toggle('card-assembly', newStatus === 'Сборка');

    try {
        await apiFetch(`/kanban/cards/${cardId}/status`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ status: newStatus }) });

        // Уход из «Сборки» ВПЕРЁД (на списание / закрытие) — это нормальный ход
        // сделки, реестр трогать нельзя. Раньше запись удалялась при любом
        // переезде, и сделка теряла свои накладные.
        const FORWARD = ['На списание', 'Закрыто'];
        if (oldStatus === 'Сборка' && newStatus !== 'Сборка' && !FORWARD.includes(newStatus)) {
            try {
                const transactions = await apiFetch('/payments/transactions?grouped=false');
                const tx = transactions.find(t => t.card_id == cardId && !t.is_document);
                if (tx) {
                    await apiFetch(`/payments/transactions/${tx.id}`, { method: 'DELETE' });
                    showToast('Транзакция удалена из реестра', 'info');
                }
            } catch (txErr) { console.error('Ошибка удаления транзакции:', txErr); }
        }

        if (typeof loadDocumentsTable === 'function') loadDocumentsTable();

        loadKanbanBoard();
        if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
        if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
    } catch (err) {
        showToast('Не удалось переместить карточку: ' + err.message, 'error');
        loadKanbanBoard();
    } finally {
        _isDropping = false;
    }
}

function setupCreateCardButton() {
    const btn = document.getElementById('btn-create-card');
    const modal = document.getElementById('create-modal');
    const confirmBtn = document.getElementById('confirm-create');
    const titleInput = document.getElementById('new-card-title');
    const amountInput = document.getElementById('new-card-amount');
    const closeBtn = modal.querySelector('.close-btn');
    const cancelBtn = document.getElementById('cancel-create');

    // Меню «Магазин» и «Приоритет» — общий компонент .dropdown,
    // тот же, что в карточке сделки и фильтрах. Раньше здесь стоял
    // нативный <select>, который выпадал из оформления сайта.
    const STORE_OPTIONS = [
        { value: '', label: 'Не выбран' },
        ...APP_STORES
    ];
    const PRIORITY_OPTIONS = [
        { value: '0', label: 'Без приоритета' },
        { value: '1', label: '🟢 Низкий' },
        { value: '2', label: '🟡 Средний' },
        { value: '3', label: '🔴 Высокий' }
    ];

    let storeVal = '';
    let priorityVal = '0';
    const storeMount = document.getElementById('new-card-store-mount');
    const priorityMount = document.getElementById('new-card-priority-mount');

    function mountDropdowns() {
        if (typeof createDropdown !== 'function') return;
        if (storeMount) {
            storeMount.innerHTML = '';
            storeMount.appendChild(createDropdown({
                options: STORE_OPTIONS, value: storeVal,
                onChange: (v) => { storeVal = v; }
            }));
        }
        if (priorityMount) {
            priorityMount.innerHTML = '';
            priorityMount.appendChild(createDropdown({
                options: PRIORITY_OPTIONS, value: priorityVal,
                onChange: (v) => { priorityVal = v; }
            }));
        }
    }
    mountDropdowns();

    const closeModal = () => modal.classList.add('hidden');

    if (btn) btn.onclick = () => {
        titleInput.value = '';
        if (amountInput) amountInput.value = '';
        storeVal = '';
        priorityVal = '0';
        mountDropdowns();          // сбрасываем выбранные значения
        modal.classList.remove('hidden');
        titleInput.focus();
    };

    if (closeBtn) closeBtn.onclick = closeModal;
    if (cancelBtn) cancelBtn.onclick = closeModal;

    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.add('hidden');
    });

    if (confirmBtn) confirmBtn.onclick = async () => {
        const title = titleInput.value.trim();
        if (!title) {
            showToast("Введите название сделки", 'error');
            return;
        }

        const amountVal = amountInput ? parseFloat(amountInput.value) : 0.0;

        try {
            confirmBtn.innerText = "Создание...";
            confirmBtn.disabled = true;

            await apiFetch('/kanban/cards', { 
                method: 'POST', 
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ 
                    title: title, 
                    status: "Новый запрос",
                    store_location: storeVal || null,
                    total_amount: isNaN(amountVal) ? 0.0 : amountVal,
                    priority: parseInt(priorityVal) || 0
                }) 
            });
            
            modal.classList.add('hidden');
            loadKanbanBoard();
            showToast('Сделка создана', 'success');
        } catch (error) {
            showToast("Ошибка при создании: " + error.message, 'error');
        } finally {
            confirmBtn.innerText = "Создать";
            confirmBtn.disabled = false;
        }
    };

    if (titleInput) titleInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') confirmBtn.click();
    });
}

function setupTrashButton() {
    const btn = document.getElementById('btn-trash');
    const modal = document.getElementById('trash-modal');
    const closeBtn = modal.querySelector('.close-btn');

    if (btn) btn.onclick = () => {
        modal.classList.remove('hidden');
        loadTrash();
    };

    if (closeBtn) closeBtn.onclick = () => modal.classList.add('hidden');
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });
}

function setupExportCSV() {
    const btn = document.getElementById('btn-export-csv');
    if (btn) btn.onclick = async () => {
        try {
            const token = getToken();
            const r = await fetch('/kanban/export/csv', {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {}
            });
            if (!r.ok) throw new Error('Ошибка экспорта');
            const blob = await r.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'cards_export.csv';
            a.click();
            URL.revokeObjectURL(url);
            showToast('CSV экспортирован', 'success');
        } catch (e) {
            showToast('Ошибка: ' + e.message, 'error');
        }
    };
}

async function loadTrash() {
    const list = document.getElementById('trash-list');
    if (!list) return;
    list.innerHTML = '<div class="trash-loading">Загрузка...</div>';

    try {
        const cards = await apiFetch('/kanban/trash');
        if (cards.length === 0) {
            list.innerHTML = '<div class="trash-empty">Архив пуст</div>';
            return;
        }

        list.innerHTML = '';
        cards.forEach(card => {
            const item = document.createElement('div');
            item.className = 'trash-item';
            const dateStr = new Date(card.created_at).toLocaleDateString('ru-RU');

            const info = document.createElement('div');
            info.className = 'trash-item-info';
            const titleSpan = document.createElement('span');
            titleSpan.className = 'trash-item-title';
            titleSpan.textContent = card.title || '';
            const metaSpan = document.createElement('span');
            metaSpan.className = 'trash-item-meta';
            metaSpan.textContent = (card.total_amount || 0) + ' BYN · ' + (card.status || '') + ' · ' + dateStr;
            info.appendChild(titleSpan);
            info.appendChild(metaSpan);

            const actions = document.createElement('div');
            actions.className = 'trash-item-actions';
            const restoreBtn = document.createElement('button');
            restoreBtn.className = 'btn-restore';
            restoreBtn.dataset.id = card.id;
            restoreBtn.textContent = 'Восстановить';
            const permBtn = document.createElement('button');
            permBtn.className = 'btn-permanent-delete';
            permBtn.dataset.id = card.id;
            permBtn.textContent = 'Удалить навсегда';
            actions.appendChild(restoreBtn);
            actions.appendChild(permBtn);

            item.appendChild(info);
            item.appendChild(actions);

            item.querySelector('.btn-restore').onclick = async () => {
                try {
                    await apiFetch(`/kanban/cards/${card.id}/restore`, { method: 'PATCH' });
                    showToast('Сделка восстановлена', 'success');
                    loadTrash();
                    loadKanbanBoard();
                } catch (err) {
                    showToast('Ошибка: ' + err.message, 'error');
                }
            };

            item.querySelector('.btn-permanent-delete').onclick = async () => {
                if (!await confirmDialog(`Удалить "${card.title}" навсегда?`, { okText: 'Удалить', danger: true })) return;
                try {
                    await apiFetch(`/kanban/cards/${card.id}/permanent`, { method: 'DELETE' });
                    showToast('Удалено навсегда', 'success');
                    loadTrash();
                } catch (err) {
                    showToast('Ошибка: ' + err.message, 'error');
                }
            };

            list.appendChild(item);
        });
    } catch (err) {
        list.innerHTML = `<div class="trash-error">Ошибка: ${escapeHtml(err.message)}</div>`;
    }
}

// === ФИЛЬТРЫ ===
function applyKanbanFilters() {
    _kanbanFilters.store = document.getElementById('filter-store')?.value || '';
    _kanbanFilters.amountMin = document.getElementById('filter-amount-min')?.value || '';
    _kanbanFilters.amountMax = document.getElementById('filter-amount-max')?.value || '';
    _kanbanFilters.client = document.getElementById('filter-client')?.value || '';
    _kanbanFilters.priority = document.getElementById('filter-priority')?.value || '';
    loadKanbanBoard();
}

function resetKanbanFilters() {
    _kanbanFilters = { store: '', amountMin: '', amountMax: '', client: '', priority: '' };
    document.getElementById('filter-store').value = '';
    document.getElementById('filter-amount-min').value = '';
    document.getElementById('filter-amount-max').value = '';
    document.getElementById('filter-client').value = '';
    document.getElementById('filter-priority').value = '';
    loadKanbanBoard();
}

// === LIST VIEW ===
function switchKanbanView(view) {
    _kanbanView = view;
    const boardEl = document.getElementById('kanban-board');
    const listEl = document.getElementById('kanban-list');
    const btnBoard = document.getElementById('view-board');
    const btnList = document.getElementById('view-list');

    if (view === 'board') {
        boardEl.classList.remove('hidden');
        listEl.classList.add('hidden');
        btnBoard.classList.add('active');
        btnList.classList.remove('active');
        loadKanbanBoard();
    } else {
        boardEl.classList.add('hidden');
        listEl.classList.remove('hidden');
        btnList.classList.add('active');
        btnBoard.classList.remove('active');
        renderListView();
    }
}

    const PRIORITY_LABELS_CLASS = { 0: '', 1: 'priority-low', 2: 'priority-mid', 3: 'priority-high' };
    const LIST_PAGE_SIZE = 25;
    let _listPage = 1;

function renderListView() {
    const tbody = document.getElementById('kanban-list-body');
    if (!tbody) return;

    let cards = _allCards;

    // Применяем фильтры
    const q = (_kanbanSearchQuery || '').trim().toLowerCase();
    const f = _kanbanFilters;

    cards = cards.filter(c => {
        if (c.is_deleted) return false;
        if (q) {
            const title = (c.title || '').toLowerCase();
            const tags = (c.tags || []).map(t => (t.name || '').toLowerCase()).join(' ');
            const manager = (c.owner?.username || '').toLowerCase();
            const client = (c.client?.name || '').toLowerCase();
            if (!title.includes(q) && !tags.includes(q) && !manager.includes(q) && !client.includes(q)) return false;
        }
        if (f.store && c.store_location !== f.store) return false;
        if (f.amountMin && (parseFloat(c.total_amount) || 0) < parseFloat(f.amountMin)) return false;
        if (f.amountMax && (parseFloat(c.total_amount) || 0) > parseFloat(f.amountMax)) return false;
        if (f.client && !(c.client?.name || '').toLowerCase().includes(f.client.toLowerCase())) return false;
        if (f.priority && String(c.priority || 0) !== f.priority) return false;
        return true;
    });

    // Сортировка
    const now = new Date();
    cards.sort((a, b) => {
        const aOverdue = a.due_date && new Date(a.due_date + 'T00:00:00') < now;
        const bOverdue = b.due_date && new Date(b.due_date + 'T00:00:00') < now;
        if (aOverdue !== bOverdue) return aOverdue ? 1 : -1;
        return new Date(b.created_at) - new Date(a.created_at);
    });

    const priorityLabels = { 0: '', 1: '<span class="priority-low">Низкий</span>', 2: '<span class="priority-mid">Средний</span>', 3: '<span class="priority-high">Высокий</span>' };

    tbody.innerHTML = '';
    const totalPages = Math.max(1, Math.ceil(cards.length / LIST_PAGE_SIZE));
    if (_listPage > totalPages) _listPage = totalPages;
    const pageCards = cards.slice((_listPage - 1) * LIST_PAGE_SIZE, _listPage * LIST_PAGE_SIZE);

    pageCards.forEach(c => {
        const dueDate = c.due_date ? new Date(c.due_date + 'T00:00:00').toLocaleDateString('ru-RU') : '—';
        const isOverdue = c.due_date && new Date(c.due_date + 'T00:00:00') < now && c.status !== 'Закрыто';
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.onclick = () => openCardModal(c.id);
        const cells = [
            { b: true, text: c.title || '' },
            { text: c.status || '' },
            { text: formatMoney(c.total_amount || 0) + ' BYN' },
            { text: c.store_location || '—' },
            { text: c.owner?.username || '—' },
            { text: dueDate, style: isOverdue ? 'color:var(--danger-color);font-weight:600;' : '' },
            { html: priorityLabels[c.priority || 0] || '—' },
        ];
        cells.forEach(cell => {
            const td = document.createElement('td');
            if (cell.style) td.style.cssText = cell.style;
            if (cell.b) {
                const b = document.createElement('b');
                b.textContent = cell.text;
                td.appendChild(b);
            } else if (cell.html) {
                td.innerHTML = cell.html;  // priorityLabels are static, no user data
            } else {
                td.textContent = cell.text;
            }
            tr.appendChild(td);
        });
        // Delete button cell
        const delTd = document.createElement('td');
        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn-danger btn-sm';
        delBtn.textContent = '✕';
        delBtn.onclick = (e) => { e.stopPropagation(); deleteCardFromList(c.id); };
        delTd.appendChild(delBtn);
        tr.appendChild(delTd);
        tbody.appendChild(tr);
    });

    if (cards.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-msg">Нет сделок</td></tr>';
    }

    // Pagination controls
    const paginationEl = document.getElementById('kanban-list-pagination');
    if (paginationEl) {
        paginationEl.innerHTML = '';
        if (totalPages > 1) {
            const info = document.createElement('span');
            info.className = 'pagination-info';
            info.textContent = `${cards.length} сделок, стр. ${_listPage}/${totalPages}`;
            paginationEl.appendChild(info);

            if (_listPage > 1) {
                const prev = document.createElement('button');
                prev.className = 'btn btn-sm btn-secondary pagination-btn';
                prev.textContent = '←';
                prev.onclick = () => { _listPage--; renderListView(); };
                paginationEl.appendChild(prev);
            }
            for (let p = 1; p <= totalPages; p++) {
                if (totalPages > 7 && Math.abs(p - _listPage) > 2 && p !== 1 && p !== totalPages) {
                    if (p === _listPage - 3 || p === _listPage + 3) {
                        const dots = document.createElement('span');
                        dots.className = 'pagination-dots';
                        dots.textContent = '...';
                        paginationEl.appendChild(dots);
                    }
                    continue;
                }
                const pg = document.createElement('button');
                pg.className = 'btn btn-sm pagination-btn' + (p === _listPage ? ' pagination-active' : '');
                pg.textContent = p;
                pg.onclick = () => { _listPage = p; renderListView(); };
                paginationEl.appendChild(pg);
            }
            if (_listPage < totalPages) {
                const next = document.createElement('button');
                next.className = 'btn btn-sm btn-secondary pagination-btn';
                next.textContent = '→';
                next.onclick = () => { _listPage++; renderListView(); };
                paginationEl.appendChild(next);
            }
        }
    }
}

async function deleteCardFromList(id) {
    if (!confirm('Удалить карточку?')) return;
    try {
        await apiFetch(`/kanban/cards/${id}`, { method: 'DELETE' });
        showToast('Удалено', 'success');
        loadKanbanBoard();
    } catch(e) { showToast(e.message, 'error'); }
}

// === ПЕРЕКЛЮЧЕНИЕ ФИЛЬТРОВ (мобильная версия) ===
function toggleFilters() {
    const filters = document.getElementById('kanban-filters');
    const btn = document.querySelector('.filter-toggle');
    if (filters) {
        filters.classList.toggle('collapsed');
        if (btn) btn.textContent = filters.classList.contains('collapsed') ? '🔍 Фильтры ▾' : '🔍 Фильтры ▴';
    }
}
