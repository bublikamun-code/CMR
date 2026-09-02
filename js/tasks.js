/**
 * Раздел «Задачи» — todo-доска: канбан с перетаскиванием + список-чеклист.
 * Подзадачи-чеклист с прогрессом, подсветка просрочек.
 * Данные — /tasks (см. server_snapshot/routers/tasks_router.py).
 */
let allTasks = [];
let tasksUsers = [];
let taskLinks = [];          // [{id, label, type:'card'|'client', ref_id}]
let tasksScope = 'all';      // all | my
let tasksStatus = '';        // '' | todo | in_work | done | overdue
let tasksSearch = '';
let tasksView = 'board';     // board | list
let _tasksSearchDebounce = null;
let _meId = null;

const TASK_STATUS_LABELS = { todo: 'К выполнению', in_work: 'В работе', done: 'Выполнена' };

async function ensureMeId() {
    if (_meId) return _meId;
    try { const me = await apiFetch('/auth/me'); _meId = me.id; } catch (e) { _meId = null; }
    return _meId;
}

function isTaskMine(t) {
    return _meId && t.assignee_id === _meId;
}

function isTaskOverdue(t) {
    return t.status !== 'done' && t.due_date && new Date(t.due_date) < new Date();
}

async function loadTasks() {
    if (!hasToken()) return;
    const container = document.getElementById('page-tasks');
    if (!container) return;

    await ensureMeId();

    try {
        [allTasks, tasksUsers] = await Promise.all([
            apiFetch('/tasks'),
            apiFetch('/tasks/assignees').catch(() => [])
        ]);
        renderTasks();
        window.CRM_FRESHNESS && window.CRM_FRESHNESS.markFresh('page-tasks');
    } catch (e) {
        const board = document.getElementById('tasks-board');
        if (board) board.innerHTML = `<div class="empty-state" style="padding:32px 16px;margin:0 auto">
            <div class="empty-state-title">Ошибка загрузки задач</div>
            <div class="empty-state-desc">${escapeHtml(e.message || 'Сервер недоступен')}</div>
            <button class="btn-secondary btn-sm" onclick="loadTasks()">Повторить</button>
        </div>`;
    }
}

function tasksFiltered() {
    const q = tasksSearch.toLowerCase().trim();
    return allTasks.filter(t => {
        if (tasksStatus === 'overdue') {
            if (!isTaskOverdue(t)) return false;
        } else if (tasksStatus && t.status !== tasksStatus) return false;
        if (tasksScope === 'my' && !isTaskMine(t)) return false;
        if (q) {
            const hay = `${t.title} ${t.description || ''} ${t.assignee_username || ''} ${t.card_title || ''} ${(t.checklist || []).map(i => i.title).join(' ')}`.toLowerCase();
            if (!hay.includes(q)) return false;
        }
        return true;
    });
}

function taskProgress(t) {
    const items = t.checklist || [];
    const done = items.filter(i => i.is_done).length;
    return { done, total: items.length };
}

function initials(name) {
    if (!name) return '—';
    return name.trim().slice(0, 2).toUpperCase();
}

const SVG_CALENDAR = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
const SVG_CLOCK = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 15.5 13.5"/></svg>';
const SVG_WARN = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
const SVG_PEN = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
const SVG_TRASH = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

function dueChipHtml(t) {
    if (!t.due_date) return '';
    const d = new Date(t.due_date);
    const today = new Date(); today.setHours(23, 59, 59, 0);
    const cls = t.status === 'done' ? 'task-due task-due-done'
        : (d < new Date() ? 'task-due task-due-overdue'
        : (d < today ? 'task-due task-due-today' : 'task-due'));
    const str = d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
    const icon = cls.includes('overdue') ? SVG_WARN : (cls.includes('today') ? SVG_CLOCK : SVG_CALENDAR);
    return `<span class="${cls}" title="Срок: ${d.toLocaleString('ru-RU')}">${icon} ${str}</span>`;
}

function progressHtml(t) {
    const { done, total } = taskProgress(t);
    if (!total) return '';
    const pct = Math.round(done / total * 100);
    return `<div class="task-progress" title="Подзадачи: ${done} из ${total}">
        <div class="task-progress-bar"><div class="task-progress-fill" style="width:${pct}%"></div></div>
        <span class="task-progress-num">${done}/${total}</span>
    </div>`;
}

function avatarHtml(t) {
    return `<span class="task-avatar" title="${escapeHtml(t.assignee_username || 'Не назначен')}">${escapeHtml(initials(t.assignee_username))}</span>`;
}

const SVG_LINK = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';

function taskCardHtml(t) {
    const doneCls = t.status === 'done' ? ' task-card-done' : '';
    const overdueCls = isTaskOverdue(t) ? ' task-card-overdue' : '';
    const doneChecked = t.status === 'done' ? 'checked' : '';
    const link = t.card_id
        ? `<a class="task-chip task-chip-link" href="#" onclick="return TasksUI.openCard(event, ${t.card_id})" title="Открыть сделку: ${escapeHtml(t.card_title || '#' + t.card_id)}">${SVG_LINK}<span>${escapeHtml(t.card_title || '#' + t.card_id)}</span></a>`
        : (t.client_id ? `<span class="task-chip task-chip-client" title="Клиент">${escapeHtml(t.client_name || '')}</span>` : '');
    return `<div class="task-card${doneCls}${overdueCls}" draggable="true" data-task-id="${t.id}">
        <div class="task-card-row">
            <input type="checkbox" class="task-check-circle" aria-label="Выполнена" ${doneChecked}
                   onchange="TasksUI.toggleDone(${t.id}, this.checked)" title="Отметить выполненной">
            <div class="task-card-body">
                <div class="task-card-title" title="${escapeHtml(t.description || '')}">${escapeHtml(t.title)}</div>
                <div class="task-card-meta">
                    ${dueChipHtml(t)}
                    ${link}
                    <span class="task-card-actions">
                        <span class="task-card-edit" title="Редактировать">${SVG_PEN}</span>
                        <span class="task-card-delete" title="Удалить">${SVG_TRASH}</span>
                    </span>
                </div>
                ${progressHtml(t)}
            </div>
            ${avatarHtml(t)}
        </div>
    </div>`;
}

function taskRowHtml(t) {
    const { done, total } = taskProgress(t);
    const doneChecked = t.status === 'done' ? 'checked' : '';
    const overdue = isTaskOverdue(t);
    const dueStr = t.due_date ? new Date(t.due_date).toLocaleString('ru-RU',
        { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
    const linkHtml = t.card_id
        ? `<a href="#" onclick="return TasksUI.openCard(event, ${t.card_id})">${escapeHtml(t.card_title || '#' + t.card_id)}</a>`
        : (t.client_id ? `<span class="muted">${escapeHtml(t.client_name || '')}</span>` : '<span class="muted">—</span>');
    return `<tr data-task-id="${t.id}" class="${t.status === 'done' ? 'task-row-done' : ''}">
        <td><input type="checkbox" aria-label="Выполнена" ${doneChecked} onchange="TasksUI.toggleDone(${t.id}, this.checked)"></td>
        <td>
            <div class="task-title-cell" title="${escapeHtml(t.description || '')}">${escapeHtml(t.title)}</div>
            <select class="task-status-select task-status-${t.status}" onchange="TasksUI.setStatus(${t.id}, this.value)">
                <option value="todo" ${t.status === 'todo' ? 'selected' : ''}>К выполнению</option>
                <option value="in_work" ${t.status === 'in_work' ? 'selected' : ''}>В работе</option>
                <option value="done" ${t.status === 'done' ? 'selected' : ''}>Выполнена</option>
            </select>
        </td>
        <td>${escapeHtml(t.assignee_username || '—')}${t.creator_username && t.creator_username !== t.assignee_username ? `<div class="muted" style="font-size:12px">автор: ${escapeHtml(t.creator_username)}</div>` : ''}</td>
        <td class="${overdue ? 'task-overdue-date' : ''}">${dueStr}${overdue ? ' ⚠' : ''}</td>
        <td>${total ? `${done}/${total}` : '—'}</td>
        <td>${linkHtml}</td>
        <td><button class="btn-icon-delete" title="Удалить задачу" aria-label="Удалить"
            onclick="TasksUI.remove(${t.id})"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></td>
    </tr>`;
}

function renderTasks() {
    const board = document.getElementById('tasks-board');
    const listView = document.getElementById('tasks-list-view');
    const empty = document.getElementById('tasks-empty');
    if (!board || !listView) return;

    // Вид
    board.classList.toggle('hidden', tasksView !== 'board');
    listView.classList.toggle('hidden', tasksView !== 'list');

    const list = tasksFiltered();

    // Общий прогресс
    const doneAll = allTasks.filter(t => t.status === 'done').length;
    const label = document.getElementById('tasks-progress-label');
    if (label) {
        label.textContent = allTasks.length ? `· выполнено ${doneAll} из ${allTasks.length}` : '';
    }

    // Доска
    if (tasksView === 'board') {
        // UI FIX 2026-08-29: при полном отсутствии задач доска с тремя
        // пустыми колонками-заглушками дублировала баннер «Задач нет» ниже —
        // скрываем доску целиком, остаётся один баннер.
        const boardEmpty = list.length === 0;
        board.classList.toggle('hidden', boardEmpty);
        if (!boardEmpty) {
            // Фильтр статуса управляет тем, какие колонки показывать на доске
            board.querySelectorAll('.task-column').forEach(col => {
                const st = col.dataset.status;
                const visible = !tasksStatus || tasksStatus === 'overdue' || tasksStatus === st;
                col.classList.toggle('hidden', !visible);
                const cards = col.querySelector('.task-cards');
                const items = list.filter(t => t.status === st)
                    .sort((a, b) => {
                        const da = a.due_date ? new Date(a.due_date).getTime() : Infinity;
                        const db_ = b.due_date ? new Date(b.due_date).getTime() : Infinity;
                        return da - db_;
                    });
                cards.innerHTML = items.map(taskCardHtml).join('') ||
                    '<div class="task-col-empty">—</div>';
                const counter = board.querySelector(`[data-count="${st}"]`);
                if (counter) counter.textContent = items.length;
            });
            setupTaskDragAndDrop();
        }
    }

    // Список
    if (tasksView === 'list') {
        const tbody = listView.querySelector('tbody');
        const sorted = list.slice().sort((a, b) => {
            const doneA = a.status === 'done' ? 1 : 0, doneB = b.status === 'done' ? 1 : 0;
            if (doneA !== doneB) return doneA - doneB;
            const da = a.due_date ? new Date(a.due_date).getTime() : Infinity;
            const db_ = b.due_date ? new Date(b.due_date).getTime() : Infinity;
            return da - db_;
        });
        tbody.innerHTML = sorted.map(taskRowHtml).join('') ||
            '<tr><td colspan="7"><div class="empty-state" style="padding:32px"><div class="empty-state-title">Задач нет</div></div></td></tr>';
    }

    // Баннер «Задач нет» — только для доски (в списке своя пустая строка)
    empty.classList.toggle('hidden', tasksView !== 'board' || list.length > 0);
}

function setupTaskDragAndDrop() {
    const board = document.getElementById('tasks-board');
    if (!board || board.dataset.dndBound === '1') return;
    board.dataset.dndBound = '1';

    board.addEventListener('dragstart', (e) => {
        const card = e.target.closest('.task-card');
        if (!card) return;
        e.dataTransfer.setData('text/plain', card.dataset.taskId);
        e.dataTransfer.effectAllowed = 'move';
        card.classList.add('task-card-dragging');
    });
    board.addEventListener('dragend', (e) => {
        e.target.closest?.('.task-card')?.classList.remove('task-card-dragging');
    });
    board.querySelectorAll('.task-cards').forEach(zone => {
        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.parentElement.classList.add('task-column-over');
        });
        zone.addEventListener('dragleave', () => {
            zone.parentElement.classList.remove('task-column-over');
        });
        zone.addEventListener('drop', async (e) => {
            e.preventDefault();
            zone.parentElement.classList.remove('task-column-over');
            const id = parseInt(e.dataTransfer.getData('text/plain'));
            if (!id) return;
            const targetStatus = zone.dataset.status;
            const task = allTasks.find(t => t.id === id);
            if (!task || task.status === targetStatus) return;
            await TasksUI.setStatus(id, targetStatus);
        });
    });
}

window.TasksUI = {
    setScope(v) { tasksScope = v; renderTasks(); },
    setView(v) {
        tasksView = v;
        document.querySelectorAll('#tasks-view-toggle .view-toggle-btn')
            .forEach(b => b.classList.toggle('active', b.dataset.view === v));
        renderTasks();
        try { localStorage.setItem('crm_tasks_view', v); } catch (e) {}
    },
    async toggleDone(id, checked) {
        await TasksUI.setStatus(id, checked ? 'done' : 'todo');
    },
    async setStatus(id, status) {
        try {
            const idx = allTasks.findIndex(t => t.id === id);
            const updated = await apiFetch(`/tasks/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
            if (idx >= 0) allTasks[idx] = Object.assign(allTasks[idx], updated);
            renderTasks();
            // Если модалка редактирования открыта для этой задачи — обновим её данные
            if (document.getElementById('task-id-input').value == id) {
                /* статус меняли из модалки — список подзадач не трогаем */
            }
        } catch (e) { showToast('Не удалось изменить статус: ' + e.message, 'error'); loadTasks(); }
    },
    async remove(id) {
        if (!confirm('Удалить задачу вместе с подзадачами?')) return;
        try {
            await apiFetch(`/tasks/${id}`, { method: 'DELETE' });
            allTasks = allTasks.filter(t => t.id !== id);
            renderTasks();
            showToast('Задача удалена');
        } catch (e) { showToast('Не удалось удалить: ' + e.message, 'error'); }
    },
    openCard(evt, cardId) {
        evt.preventDefault();
        openCardModal(cardId);
        return false;
    },

    // --- Чек-лист в модалке ---
    async checklistAdd() {
        const taskId = document.getElementById('task-id-input').value;
        const input = document.getElementById('task-checklist-input');
        const title = input.value.trim();
        if (!taskId || !title) return;
        try {
            const item = await apiFetch(`/tasks/${taskId}/checklist`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) });
            const t = allTasks.find(x => x.id == taskId);
            if (t) { t.checklist = t.checklist || []; t.checklist.push(item); }
            input.value = '';
            TasksUI.checklistRender(t);
            renderTasks();
        } catch (e) { showToast('Не удалось добавить подзадачу: ' + e.message, 'error'); }
    },
    async checklistToggle(itemId, checked) {
        const taskId = document.getElementById('task-id-input').value;
        try {
            await apiFetch(`/tasks/checklist/${itemId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_done: checked }) });
            const t = allTasks.find(x => x.id == taskId);
            if (t) {
                const item = (t.checklist || []).find(i => i.id === itemId);
                if (item) item.is_done = checked;
                TasksUI.checklistRender(t);
                renderTasks();
            }
        } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
    },
    async checklistDelete(itemId) {
        const taskId = document.getElementById('task-id-input').value;
        try {
            await apiFetch(`/tasks/checklist/${itemId}`, { method: 'DELETE' });
            const t = allTasks.find(x => x.id == taskId);
            if (t) { t.checklist = (t.checklist || []).filter(i => i.id !== itemId); TasksUI.checklistRender(t); renderTasks(); }
        } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
    },
    checklistRender(t) {
        const box = document.getElementById('task-checklist-items');
        const progress = document.getElementById('task-checklist-progress');
        if (!box) return;
        const items = (t && t.checklist) || [];
        const done = items.filter(i => i.is_done).length;
        if (progress) progress.textContent = items.length ? `${done} из ${items.length}` : '';
        box.innerHTML = items.map(i => `
            <div class="task-checklist-item ${i.is_done ? 'done' : ''}">
                <input type="checkbox" ${i.is_done ? 'checked' : ''} onchange="TasksUI.checklistToggle(${i.id}, this.checked)">
                <span class="task-checklist-title">${escapeHtml(i.title)}</span>
                <button type="button" class="task-checklist-del" title="Удалить" onclick="TasksUI.checklistDelete(${i.id})">✕</button>
            </div>`).join('');
    },

    async openModal(taskId) {
        const modal = document.getElementById('task-modal');
        document.getElementById('task-id-input').value = taskId || '';
        document.getElementById('task-modal-title').textContent = taskId ? 'Редактировать задачу' : 'Новая задача';
        const sel = document.getElementById('task-assignee-input');
        sel.innerHTML = '<option value="">— не назначен —</option>' +
            tasksUsers.map(u => `<option value="${u.id}">${escapeHtml(u.username)}</option>`).join('');

        // Связи со сделками и клиентами (лениво)
        if (!taskLinks.length) {
            try {
                const [cards, clients] = await Promise.all([
                    apiFetch('/kanban/cards').catch(() => []),
                    apiFetch('/clients').catch(() => [])
                ]);
                taskLinks = [
                    ...cards.map(c => ({ label: `Сделка: ${c.title}`, type: 'card', ref_id: c.id })),
                    ...clients.map(c => ({ label: `Клиент: ${c.name}`, type: 'client', ref_id: c.id }))
                ];
            } catch (e) {}
        }
        document.getElementById('task-link-list').innerHTML =
            taskLinks.map(l => `<option value="${escapeHtml(l.label)}">`).join('');

        const editor = document.getElementById('task-checklist-editor');
        const t = taskId ? allTasks.find(x => x.id == taskId) : null;
        if (t) {
            document.getElementById('task-title-input').value = t.title;
            document.getElementById('task-desc-input').value = t.description || '';
            document.getElementById('task-due-input').value = t.due_date ? toLocalInputValue(t.due_date) : '';
            sel.value = t.assignee_id || '';
            let linkedLabel = '';
            if (t.card_id) linkedLabel = `Сделка: ${t.card_title || ('#' + t.card_id)}`;
            else if (t.client_id) linkedLabel = `Клиент: ${t.client_name || ('#' + t.client_id)}`;
            document.getElementById('task-link-input').value = linkedLabel;
            editor.classList.remove('hidden');
            TasksUI.checklistRender(t);
        } else {
            document.getElementById('task-title-input').value = '';
            document.getElementById('task-desc-input').value = '';
            document.getElementById('task-due-input').value = '';
            sel.value = '';
            document.getElementById('task-link-input').value = '';
            editor.classList.add('hidden');
        }
        modal.classList.remove('hidden');
        setTimeout(() => document.getElementById('task-title-input').focus(), 50);
    },
    closeModal() {
        document.getElementById('task-modal').classList.add('hidden');
    }
};

function toLocalInputValue(iso) {
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

document.addEventListener('DOMContentLoaded', () => {
    // Восстановить сохранённый вид (доска/список)
    try {
        const v = localStorage.getItem('crm_tasks_view');
        if (v === 'list' || v === 'board') TasksUI.setView(v);
    } catch (e) {}

    document.getElementById('task-add-btn')?.addEventListener('click', () => TasksUI.openModal());

    // Карточка доски: клик по ✎ / 🗑 / названию (чекбокс живёт отдельно)
    document.getElementById('tasks-board')?.addEventListener('click', (e) => {
        const card = e.target.closest('.task-card');
        if (!card) return;
        const id = parseInt(card.dataset.taskId);
        if (e.target.closest('.task-card-delete')) return TasksUI.remove(id);
        if (e.target.closest('.task-check-circle')) return; // обрабатывает onchange
        if (e.target.closest('.task-card-edit') || e.target.closest('.task-card-title')) return TasksUI.openModal(id);
    });

    // Список: двойной клик по строке — редактирование
    document.querySelector('#tasks-table tbody')?.addEventListener('dblclick', (e) => {
        const row = e.target.closest('tr[data-task-id]');
        if (row && !e.target.closest('input,select,button,a')) TasksUI.openModal(parseInt(row.dataset.taskId));
    });

    const search = document.getElementById('tasks-search');
    const clearBtn = document.getElementById('tasks-search-clear');
    if (search) {
        search.addEventListener('input', (e) => {
            tasksSearch = e.target.value;
            if (clearBtn) clearBtn.classList.toggle('hidden', !tasksSearch);
            clearTimeout(_tasksSearchDebounce);
            _tasksSearchDebounce = setTimeout(renderTasks, 200);
        });
    }
    clearBtn?.addEventListener('click', () => {
        search.value = ''; tasksSearch = ''; clearBtn.classList.add('hidden'); renderTasks();
    });

    document.getElementById('tasks-scope')?.addEventListener('change', (e) => TasksUI.setScope(e.target.value));

    document.getElementById('tasks-view-toggle')?.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-view]');
        if (btn) TasksUI.setView(btn.dataset.view);
    });

    document.getElementById('tasks-status-chips')?.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-status]');
        if (!btn) return;
        tasksStatus = btn.dataset.status;
        document.querySelectorAll('#tasks-status-chips .settings-tab')
            .forEach(b => b.classList.toggle('active', b === btn));
        renderTasks();
    });

    document.getElementById('task-checklist-add-btn')?.addEventListener('click', () => TasksUI.checklistAdd());
    document.getElementById('task-checklist-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); TasksUI.checklistAdd(); }
    });

    document.getElementById('task-save-btn')?.addEventListener('click', async (e) => {
        e.preventDefault();
        const idVal = document.getElementById('task-id-input').value;
        const payload = {
            title: document.getElementById('task-title-input').value.trim(),
            description: document.getElementById('task-desc-input').value.trim() || null,
            assignee_id: parseInt(document.getElementById('task-assignee-input').value) || null,
            due_date: document.getElementById('task-due-input').value
                ? new Date(document.getElementById('task-due-input').value).toISOString() : null,
        };
        const linkText = document.getElementById('task-link-input').value.trim();
        const link = taskLinks.find(l => l.label === linkText);
        payload.card_id = link && link.type === 'card' ? link.ref_id : null;
        payload.client_id = link && link.type === 'client' ? link.ref_id : null;
        if (!payload.title) return showToast('Укажите название задачи', 'error');
        const saveBtn = document.getElementById('task-save-btn');
        saveBtn.disabled = true;
        try {
            if (idVal) {
                await apiFetch(`/tasks/${idVal}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                showToast('Задача обновлена');
            } else {
                await apiFetch('/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                showToast('Задача создана');
            }
            TasksUI.closeModal();
            loadTasks();
        } catch (err) {
            showToast('Ошибка сохранения: ' + err.message, 'error');
        } finally {
            saveBtn.disabled = false;
        }
    });

    // Открытая вкладка «Задачи» грузится сразу (initNavigation ставит active до DOMContentLoaded)
    const tasksNavActive = document.querySelector('.nav-btn[data-target="page-tasks"]')?.classList.contains('active');
    if (tasksNavActive) loadTasks();
});
