document.addEventListener('DOMContentLoaded', () => {
    initKanbanDensityToggle();
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

function renderPaymentBadge(card) {
    const total = parseFloat(card.total_amount) || 0;
    if (total <= 0) return '';
    const paid = parseFloat(card.paid_amount) || 0;
    const status = card.payment_status || 'Не оплачен';
    const cls = PAYMENT_STATUS_CLASSES[status] || 'pay-unpaid';
    const text = `${formatMoney(paid)} / ${formatMoney(total)} BYN`;
    return `<span class="pay-badge pay-badge-kanban ${cls}" title="Оплата: ${escapeHtml(status)}, ${formatMoney(paid)} / ${formatMoney(total)} BYN"><span class="pay-icon">${ICON_WALLET}</span><span class="tabular-nums">${text}</span></span>`;
}

let _isDropping = false;
let _kanbanSearchQuery = '';
let _kanbanFilters = { store: '', amountMin: '', amountMax: '', client: '', priority: '' };
let _kanbanView = 'board'; // 'board' or 'list'
let _trashSearchQuery = '';
let _kanbanDensity = localStorage.getItem('crm_kanban_density') || 'detailed';
let _allCards = [];

function getKanbanDensity() { return _kanbanDensity; }
function setKanbanDensity(density) {
    _kanbanDensity = density === 'compact' ? 'compact' : 'detailed';
    localStorage.setItem('crm_kanban_density', _kanbanDensity);
    const board = document.getElementById('kanban-board');
    if (board) {
        board.classList.toggle('kanban-density-compact', _kanbanDensity === 'compact');
        board.classList.toggle('kanban-density-detailed', _kanbanDensity === 'detailed');
    }
    document.querySelectorAll('#kanban-density-toggle .density-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.density === _kanbanDensity);
    });
}
function initKanbanDensityToggle() {
    const toggle = document.getElementById('kanban-density-toggle');
    if (!toggle) return;
    toggle.querySelectorAll('.density-btn').forEach(btn => {
        btn.addEventListener('click', () => setKanbanDensity(btn.dataset.density));
    });
    setKanbanDensity(_kanbanDensity);
}

async function loadKanbanBoard() {
    const board = document.getElementById('kanban-board');
    if (!board) return;
    board.classList.add('kanban-density-' + _kanbanDensity);

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

        if (typeof window.revealRefresh === 'function') window.revealRefresh();

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
                col.className = 'kanban-column reveal reveal-fast';
                col.setAttribute('data-status', colName);
                col.innerHTML = `
                    <div class="column-header">
                        <div class="column-header-main">
                            <span class="column-title">${colName}</span>
                            <span class="col-count" title="0">0</span>
                        </div>
                        <div class="col-sum"></div>
                    </div>
                    <div class="kanban-cards"></div>
                `;
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

            // Порядок карточек задаёт сервер (поле position). Просроченные
            // всё ещё подсвечиваются классом card-overdue, но не сдвигаются
            // вглубь колонки — иначе пользователь не видит результат
            // перетаскивания: после перерисовки карточка возвращалась в
            // сортированную позицию, а не на место броска.

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
                countEl.textContent = visibleCards.length !== colCards.length ? `${visibleCards.length} из ${colCards.length}` : String(colCards.length);
                countEl.title = `${visibleCards.length} из ${colCards.length}`;
            }
            const sumEl = col.querySelector('.col-sum');
            if (sumEl) {
                const colSum = visibleCards.reduce((acc, c) => acc + (parseFloat(c.total_amount) || 0), 0);
                sumEl.textContent = colSum > 0 ? formatMoneyBYN(colSum) : '';
            }

            const emptyState = container.querySelector('.empty-state');
            if (visibleCards.length === 0) {
                container.innerHTML = '';
                container.appendChild(renderEmptyState({
                    icon: '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>',
                    title: 'Нет сделок',
                    description: (_kanbanSearchQuery || Object.values(_kanbanFilters).some(Boolean))
                        ? 'Попробуйте изменить фильтры или поиск'
                        : 'Перетащите сюда карточку или создайте новую'
                }));
            } else {
                if (emptyState) emptyState.remove();
                const newIds = new Set(visibleCards.map(c => String(c.id)));
                container.querySelectorAll('.kanban-card').forEach(el => {
                    if (!newIds.has(el.dataset.id)) el.remove();
                });
                visibleCards.forEach(card => {
                    const existing = container.querySelector(`.kanban-card[data-id="${card.id}"]`);
                    if (existing) fillCardHTML(existing, card);
                    else renderCard(card, container);
                });
            }

            const ICON_CHEVRON = '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
            const oldBtn = col.querySelector('.btn-show-old');
            const hasOld = colCards.some(c => Math.ceil((new Date() - new Date(c.created_at)) / (1000 * 60 * 60 * 24)) > OLD_CARD_DAYS);
            const oldCount = colCards.filter(c => Math.ceil((new Date() - new Date(c.created_at)) / (1000 * 60 * 60 * 24)) > OLD_CARD_DAYS).length;
            if (hasOld) {
                const label = showOldCards[colName] ? 'Скрыть старые' : `${oldCount} старых`;
                if (!oldBtn) {
                    const btn = document.createElement('button');
                    btn.className = 'btn-show-old';
                    btn.innerHTML = `<span class="btn-show-old__text">${label}</span>${ICON_CHEVRON}`;
                    btn.title = showOldCards[colName] ? 'Скрыть карточки старше 15 дней' : `Показать ${oldCount} карточек старше 15 дней`;
                    btn.onclick = () => { showOldCards[colName] = !showOldCards[colName]; loadKanbanBoard(); };
                    col.appendChild(btn);
                } else {
                    oldBtn.innerHTML = `<span class="btn-show-old__text">${label}</span>${ICON_CHEVRON}`;
                    oldBtn.title = showOldCards[colName] ? 'Скрыть карточки старше 15 дней' : `Показать ${oldCount} карточек старше 15 дней`;
                    oldBtn.classList.toggle('is-open', showOldCards[colName]);
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
            overdueBar.innerHTML = `<span class="overdue-summary-icon">${ICON_ALERT}</span> Просрочено: <strong>${overdueCards.length}</strong> ${overdueCards.length === 1 ? 'карточка' : 'карточек'}`;
            overdueBar.style.display = '';
        } else if (overdueBar) {
            overdueBar.style.display = 'none';
        }
    } catch (error) {
        console.error("Ошибка загрузки карточек:", error);
        if (board) {
            board.innerHTML = '';
            board.appendChild(renderAlert({
                type: 'error',
                title: 'Не удалось загрузить карточки',
                message: error.message || 'Проверьте соединение и попробуйте снова.',
                onRetry: () => loadKanbanBoard()
            }));
        }
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
    cardEl.classList.remove('deal-rotting', 'deal-rotting-danger');
    if (isRottingDanger) cardEl.classList.add('deal-rotting-danger');
    else if (isRotting) cardEl.classList.add('deal-rotting');

    const total = card.checklists ? card.checklists.length : 0;
    const paidCount = card.checklists ? card.checklists.filter(c => c.is_paid).length : 0;
    const secCount = card.checklists ? card.checklists.filter(c => c.is_secondary_check).length : 0;

    const creatorName = card.owner ? card.owner.username : 'Неизвестно';

    const paidPct = total > 0 ? Math.round((paidCount / total) * 100) : 0;
    const secPct = total > 0 ? Math.round((secCount / total) * 100) : 0;

    const now = new Date();
    const dueDate = card.due_date ? new Date(card.due_date + 'T00:00:00') : null;
    const isOverdue = dueDate && dueDate < now && card.status !== 'Закрыто';
    const isDueSoon = dueDate && !isOverdue && (dueDate - now) < (3 * 24 * 60 * 60 * 1000);

    cardEl.classList.remove('card-overdue', 'card-due-soon');
    if (isOverdue) cardEl.classList.add('card-overdue');
    else if (isDueSoon) cardEl.classList.add('card-due-soon');

    const tagsHtml = (card.tags && card.tags.length > 0)
        ? `<div class="card-tags">${card.tags.map(t => {
            const c = sanitizeColor(t.color);
            const r = parseInt(c.slice(1,3),16), g = parseInt(c.slice(3,5),16), b = parseInt(c.slice(5,7),16);
            const bg = `rgb(${Math.round(r+(255-r)*0.8)},${Math.round(g+(255-g)*0.8)},${Math.round(b+(255-b)*0.8)})`;
            return `<span class="card-tag" style="color:${c};background:${bg}">${escapeHtml(t.name)}</span>`;
        }).join('')}</div>`
        : '';

    const progressHtml = total > 0 ? `
        <div class="card-progress">
            <div class="progress-meta">
                <span>Заказано <b class="tabular-nums">${paidCount}/${total}</b></span>
                <span class="progress-dot">·</span>
                <span>Пришло <b class="tabular-nums">${secCount}/${total}</b></span>
            </div>
            <div class="progress-track single" title="Заказано ${paidCount}/${total} · Пришло ${secCount}/${total}">
                <div class="progress-fill paid" style="width:${paidPct}%"></div>
                <div class="progress-fill came" style="width:${Math.min(secPct, paidPct)}%"></div>
            </div>
        </div>
    ` : '';

    cardEl.innerHTML = `
        <div class="card-hover-actions" data-card-menu>
            <button class="card-hover-btn btn-edit-card" title="Редактировать" data-card-id="${card.id}">${ICON_PENCIL}</button>
            <button class="card-hover-btn btn-delete-card" title="Удалить">${ICON_CROSS}</button>
        </div>
        <button class="card-menu-trigger" title="Действия с карточкой" aria-label="Действия с карточкой" aria-haspopup="menu">${ICON_ELLIPSIS}</button>
        <div class="card-main">
            <div class="card-title" title="${escapeHtml(card.title)}">${escapeHtml(card.title)}</div>
            <div class="card-amount"><span class="tabular-nums">${formatMoneyBYN(card.total_amount || 0)}</span></div>
            <div class="card-meta">
                ${card.store_location ? `<span class="store-badge store-${escapeHtml(card.store_location)}">${escapeHtml(card.store_location)}</span>` : '<span class="store-badge store-empty">Без склада</span>'}
                <span class="card-manager">${escapeHtml(creatorName)}</span>
            </div>
            <div class="card-payment">${renderPaymentBadge(card)}</div>
            ${tagsHtml}
            ${progressHtml}
        </div>
    `;

    // UI Audit (2026-08-09, C.5): ⋯-триггер для touch-устройств.
    // На десктопе он скрыт (display:none), на тачах заменяет постоянно
    // видимые hover-actions. Открывает popover с теми же кнопками.
    const menuTrigger = cardEl.querySelector('.card-menu-trigger');
    if (menuTrigger) {
        menuTrigger.onclick = (e) => {
            e.stopPropagation();
            const actions = cardEl.querySelector('.card-hover-actions');
            if (actions) {
                const isOpen = actions.classList.toggle('popover-open');
                menuTrigger.setAttribute('aria-expanded', isOpen);
            }
        };
    }

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

// Закрытие popover при клике вне карточки (чтобы не оставался открытым)
document.addEventListener('click', (e) => {
    if (!e.target.closest('.kanban-card')) {
        document.querySelectorAll('.card-hover-actions.popover-open').forEach(el => {
            el.classList.remove('popover-open');
        });
    }
    if (!e.target.closest('.list-actions-cell')) {
        document.querySelectorAll('.list-actions-menu.open').forEach(el => {
            el.classList.remove('open');
            const trigger = el.parentElement?.querySelector('.list-menu-trigger');
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        });
    }
});

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

    // Визначаємо позицію вставки: шукаємо картку, над якою відпустили
    const cards = Array.from(targetContainer.querySelectorAll('.kanban-card'));
    const afterElement = cards.reduce((closest, child) => {
        if (child === cardEl) return closest;
        const box = child.getBoundingClientRect();
        const offset = e.clientY - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) {
            return { offset: offset, element: child };
        } else {
            return closest;
        }
    }, { offset: Number.NEGATIVE_INFINITY }).element;

    if (afterElement == null) {
        targetContainer.appendChild(cardEl);
    } else {
        targetContainer.insertBefore(cardEl, afterElement);
    }
    cardEl.classList.toggle('card-assembly', newStatus === 'Сборка');

    try {
        // Зміна статусу, якщо потрібно
        if (oldStatus !== newStatus) {
            await apiFetch(`/kanban/cards/${cardId}/status`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ status: newStatus }) });
        }

        // Сохранение позиции внутри колонки
        const allCardsInColumn = Array.from(targetContainer.querySelectorAll('.kanban-card'));
        const cardIds = allCardsInColumn.map(c => parseInt(c.dataset.id));
        await apiFetch('/kanban/cards/reorder', {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ status: newStatus, card_ids: cardIds })
        });

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

async function setupCreateCardButton() {
    const btn = document.getElementById('btn-create-card');
    const modal = document.getElementById('create-modal');
    if (!modal) return;
    const confirmBtn = document.getElementById('confirm-create');
    const titleInput = document.getElementById('new-card-title');
    const amountInput = document.getElementById('new-card-amount');
    const closeBtn = modal.querySelector('.close-btn');
    const cancelBtn = document.getElementById('cancel-create');

    // Меню «Магазин» и «Приоритет» — общий компонент .dropdown,
    // тот же, что в карточке сделки и фильтрах.
    const STORE_OPTIONS = [
        { value: '', label: 'Не выбран' },
        ...APP_STORES
    ];
    const PRIORITY_OPTIONS = [
        { value: '0', label: 'Без приоритета' },
        { value: '1', label: 'Низкий', html: '<span class="priority-dot priority-dot--low"></span> Низкий' },
        { value: '2', label: 'Средний', html: '<span class="priority-dot priority-dot--medium"></span> Средний' },
        { value: '3', label: 'Высокий', html: '<span class="priority-dot priority-dot--high"></span> Высокий' }
    ];

    let storeVal = '';
    let priorityVal = '0';
    let clientVal = '';
    let managerVal = '';
    let currentUserId = '';

    const storeMount = document.getElementById('new-card-store-mount');
    const priorityMount = document.getElementById('new-card-priority-mount');
    const clientMount = document.getElementById('new-card-client-mount');
    const managerMount = document.getElementById('new-card-manager-mount');

    let clientOptions = [{ value: '', label: '— Не привязан —' }];
    let managerOptions = [];

    try {
        const me = await apiFetch('/auth/me');
        currentUserId = String(me.id);
        managerVal = currentUserId;
    } catch (e) {
        console.error('Не удалось определить текущего пользователя:', e);
    }

    try {
        const clients = await apiFetch('/clients');
        clientOptions = [{ value: '', label: '— Не привязан —' }, ...clients.map(c => ({
            value: String(c.id),
            label: c.name + (c.unp ? ' (УНП: ' + c.unp + ')' : '')
        }))];
    } catch (e) {
        console.error('Не удалось загрузить клиентов:', e);
    }

    try {
        const users = await apiFetch('/auth/users');
        managerOptions = users.map(u => ({ value: String(u.id), label: u.username }));
        if (!managerVal && managerOptions.length > 0) managerVal = managerOptions[0].value;
    } catch (e) {
        console.error('Не удалось загрузить пользователей:', e);
        if (currentUserId) managerOptions = [{ value: currentUserId, label: 'Я' }];
    }

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
        if (clientMount) {
            clientMount.innerHTML = '';
            clientMount.appendChild(createDropdown({
                options: clientOptions,
                value: clientVal,
                searchable: true,
                onChange: (v) => { clientVal = v; }
            }));
        }
        if (managerMount) {
            managerMount.innerHTML = '';
            if (managerOptions.length > 0) {
                managerMount.appendChild(createDropdown({
                    options: managerOptions,
                    value: managerVal,
                    onChange: (v) => { managerVal = v; }
                }));
            } else {
                managerMount.innerHTML = '<span class="text-muted-sm">Загрузка пользователей...</span>';
            }
        }
    }
    mountDropdowns();

    const closeModal = () => {
        if (window.closeModalSmooth) window.closeModalSmooth(modal);
        else modal.classList.add('hidden');
    };

    if (btn) btn.onclick = () => {
        titleInput.value = '';
        if (amountInput) amountInput.value = '';
        storeVal = '';
        priorityVal = '0';
        clientVal = '';
        managerVal = currentUserId;
        mountDropdowns();
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
                    priority: parseInt(priorityVal) || 0,
                    client_id: clientVal ? parseInt(clientVal) : null,
                    owner_id: managerVal ? parseInt(managerVal) : null
                })
            });

            closeModal();
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
        if (e.key === 'Enter' && e.ctrlKey) {
            e.preventDefault();
            confirmBtn.click();
        }
    });

    modal.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });
}

function setupTrashButton() {
    const btn = document.getElementById('btn-trash');
    const modal = document.getElementById('trash-modal');
    const closeBtn = modal.querySelector('.close-btn');
    const search = document.getElementById('trash-search');

    if (btn) btn.onclick = () => {
        modal.classList.remove('hidden');
        loadTrash();
    };

    const closeTrash = () => {
        if (window.closeModalSmooth) window.closeModalSmooth(modal);
        else modal.classList.add('hidden');
    };
    if (closeBtn) closeBtn.onclick = closeTrash;
    modal.addEventListener('click', (e) => { if (e.target === modal) closeTrash(); });

    let _trashSearchDebounce = null;
    if (search) search.addEventListener('input', (e) => {
        _trashSearchQuery = e.target.value;
        clearTimeout(_trashSearchDebounce);
        _trashSearchDebounce = setTimeout(() => loadTrash(), 200);
    });
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
        const q = (_trashSearchQuery || '').trim().toLowerCase();
        const filtered = cards.filter(c => {
            if (!q) return true;
            const title = (c.title || '').toLowerCase();
            const status = (c.status || '').toLowerCase();
            const amount = String(c.total_amount || '');
            return title.includes(q) || status.includes(q) || amount.includes(q);
        });

        if (filtered.length === 0) {
            list.innerHTML = `<div class="trash-empty">${q ? 'Ничего не найдено' : 'Архив пуст'}</div>`;
            return;
        }

        list.innerHTML = '';
        filtered.forEach(card => {
            const item = document.createElement('div');
            item.className = 'trash-item';
            const dateStr = new Date(card.created_at).toLocaleDateString('ru-RU');

            item.innerHTML = `
                <div class="trash-item-info">
                    <span class="trash-item-title">${escapeHtml(card.title || '')}</span>
                    <span class="trash-item-meta">
                        <span class="tabular-nums">${formatMoneyBYN(card.total_amount || 0)}</span>
                        <span class="meta-dot">·</span>
                        <span class="trash-item-status">${escapeHtml(card.status || '')}</span>
                        <span class="meta-dot">·</span>
                        <span>${escapeHtml(dateStr)}</span>
                    </span>
                </div>
                <div class="trash-item-actions">
                    <button class="btn-restore" data-id="${card.id}" title="Восстановить сделку">${ICON_CHECK} Восстановить</button>
                    <button class="btn-permanent-delete" data-id="${card.id}" title="Удалить навсегда">Удалить</button>
                </div>
            `;

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
    const emptyCell = '<span class="list-empty-dim">—</span>';

    tbody.innerHTML = '';
    const totalPages = Math.max(1, Math.ceil(cards.length / LIST_PAGE_SIZE));
    if (_listPage > totalPages) _listPage = totalPages;
    const pageCards = cards.slice((_listPage - 1) * LIST_PAGE_SIZE, _listPage * LIST_PAGE_SIZE);

    pageCards.forEach(c => {
        const dueDate = c.due_date ? new Date(c.due_date + 'T00:00:00').toLocaleDateString('ru-RU') : null;
        const isOverdue = c.due_date && new Date(c.due_date + 'T00:00:00') < now && c.status !== 'Закрыто';
        const tr = document.createElement('tr');
        tr.className = 'kanban-list-row';
        tr.onclick = () => openCardModal(c.id);

        const managerName = c.owner?.username;
        const managerCell = managerName
            ? escapeHtml(managerName)
            : `<span class="status-badge badge-warning">Не назначен</span>`;

        const cells = [
            { b: true, text: c.title || '' },
            { html: c.status ? `<span class="status-badge" data-status="${escapeHtml(c.status)}">${escapeHtml(c.status)}</span>` : emptyCell },
            { html: `<span class="tabular-nums">${formatMoneyBYN(c.total_amount || 0)}</span>` },
            { html: renderPaymentBadge(c) || emptyCell },
            { html: c.store_location ? escapeHtml(c.store_location) : emptyCell },
            { html: managerCell },
            { html: dueDate ? `<span${isOverdue ? ' style="color:var(--danger-color);font-weight:600;"' : ''}>${escapeHtml(dueDate)}</span>` : emptyCell },
            { html: priorityLabels[c.priority || 0] || emptyCell },
        ];
        cells.forEach((cell) => {
            const td = document.createElement('td');
            if (cell.b) {
                const b = document.createElement('b');
                b.textContent = cell.text;
                b.title = cell.text;
                td.appendChild(b);
            } else if (cell.html) {
                td.innerHTML = cell.html;
            } else {
                td.textContent = cell.text;
            }
            tr.appendChild(td);
        });
        // Hover-меню действий: ⋯ справа, Открыть / В архив.
        const actionsTd = document.createElement('td');
        actionsTd.className = 'list-actions-cell';
        actionsTd.innerHTML = `
            <button class="list-menu-trigger" title="Действия" aria-label="Действия" aria-haspopup="menu">${ICON_ELLIPSIS}</button>
            <div class="list-actions-menu">
                <button class="list-action-open">Открыть</button>
                <button class="list-action-archive">В архив</button>
            </div>
        `;
        actionsTd.querySelector('.list-menu-trigger').onclick = (e) => {
            e.stopPropagation();
            const menu = actionsTd.querySelector('.list-actions-menu');
            const isOpen = menu.classList.toggle('open');
            actionsTd.querySelector('.list-menu-trigger').setAttribute('aria-expanded', isOpen);
        };
        actionsTd.querySelector('.list-action-open').onclick = (e) => {
            e.stopPropagation();
            openCardModal(c.id);
        };
        actionsTd.querySelector('.list-action-archive').onclick = async (e) => {
            e.stopPropagation();
            if (await confirmDialog('Переместить сделку в архив?')) {
                await archiveCardFromList(c.id);
            }
        };
        tr.appendChild(actionsTd);
        tbody.appendChild(tr);
    });

    if (cards.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty-msg">Нет сделок</td></tr>';
    }

    // Pagination controls (top + bottom)
    const paginators = [
        document.getElementById('kanban-list-pagination-top'),
        document.getElementById('kanban-list-pagination')
    ].filter(Boolean);
    paginators.forEach(el => el.innerHTML = '');

    if (paginators.length) {
        const shownCount = pageCards.length;
        const buildControls = () => {
            const frag = document.createDocumentFragment();
            const info = document.createElement('span');
            info.className = 'pagination-info';
            info.textContent = `Показано ${shownCount} из ${cards.length}`;
            frag.appendChild(info);

            if (totalPages > 1) {
                if (_listPage > 1) {
                    const prev = document.createElement('button');
                    prev.className = 'btn btn-sm btn-secondary pagination-btn';
                    prev.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>';
                    prev.onclick = () => { _listPage--; renderListView(); };
                    frag.appendChild(prev);
                }
                for (let p = 1; p <= totalPages; p++) {
                    if (totalPages > 7 && Math.abs(p - _listPage) > 2 && p !== 1 && p !== totalPages) {
                        if (p === _listPage - 3 || p === _listPage + 3) {
                            const dots = document.createElement('span');
                            dots.className = 'pagination-dots';
                            dots.textContent = '...';
                            frag.appendChild(dots);
                        }
                        continue;
                    }
                    const pg = document.createElement('button');
                    pg.className = 'btn btn-sm pagination-btn' + (p === _listPage ? ' pagination-active' : '');
                    pg.textContent = p;
                    pg.onclick = () => { _listPage = p; renderListView(); };
                    frag.appendChild(pg);
                }
                if (_listPage < totalPages) {
                    const next = document.createElement('button');
                    next.className = 'btn btn-sm btn-secondary pagination-btn';
                    next.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';
                    next.onclick = () => { _listPage++; renderListView(); };
                    frag.appendChild(next);
                }
            }
            return frag;
        };
        const controls = buildControls();
        paginators.forEach(el => el.appendChild(controls.cloneNode(true)));
    }
}

async function archiveCardFromList(id) {
    try {
        await apiFetch(`/kanban/cards/${id}`, { method: 'DELETE' });
        showToast('Сделка перенесена в архив', 'success');
        loadKanbanBoard();
    } catch(e) { showToast(e.message, 'error'); }
}

// === ПЕРЕКЛЮЧЕНИЕ ФИЛЬТРОВ (мобильная версия) ===
function toggleFilters() {
    const filters = document.getElementById('kanban-filters');
    const btn = document.querySelector('.filter-toggle');
    if (filters) {
        filters.classList.toggle('collapsed');
        if (btn) btn.innerHTML = filters.classList.contains('collapsed') ? `${ICON_FILTER} Фильтры ${ICON_CHEVRON_DOWN}` : `${ICON_FILTER} Фильтры ${ICON_CHEVRON_UP}`;
    }
}
