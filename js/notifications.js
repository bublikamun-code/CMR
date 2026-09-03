/**
 * Колокольчик уведомлений: счётчик непрочитанных, панель последних уведомлений.
 * Обновление счётчика подписано на общий realtime-тик (realtime.js) —
 * отдельный таймер не создаётся.
 */
let _notifTimer = null;

function _escape(s) {
    return escapeHtml(String(s ?? ''));
}

const NOTIF_ICONS = {
    task_assigned: '📋', task_due: '📅', task_done: '✅', task_overdue: '⚠️',
    card_created: '🆕', card_updated: '💰', card_overdue: '⚠️', card_payment: '⏰',
    client_created: '👤'
};

async function loadNotifications() {
    // На экране входа не опрашиваем: с устаревшим токеном apiFetch отдаст 401
    // и устроит «Сессия истекла»-релоад прямо посреди формы логина.
    if (!hasToken()) return;
    if (document.getElementById('login-screen')) return;
    const badge = document.getElementById('notif-badge');
    if (!badge) return;
    try {
        const res = await apiFetch('/notifications?limit=1');
        setNotifBadge(res.unread_count || 0);
    } catch (e) { /* колокольчик не должен шуметь об ошибках polling'а */ }
}

function setNotifBadge(count) {
    const badge = document.getElementById('notif-badge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.classList.remove('hidden');
    } else {
        badge.classList.add('hidden');
    }
}

async function openNotifPanel() {
    const panel = document.getElementById('notif-panel');
    const list = document.getElementById('notif-list');
    panel.classList.toggle('hidden');
    if (panel.classList.contains('hidden')) return;
    list.innerHTML = '<div class="notif-empty">Загрузка...</div>';
    try {
        const res = await apiFetch('/notifications');
        setNotifBadge(res.unread_count || 0);
        renderNotifList(res.items || []);
    } catch (e) {
        list.innerHTML = `<div class="notif-empty">Ошибка загрузки</div>`;
    }
}

function notifTime(iso) {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    if (diff < 60e3) return 'только что';
    if (diff < 3600e3) return Math.floor(diff / 60e3) + ' мин назад';
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function renderNotifList(items) {
    const list = document.getElementById('notif-list');
    if (!items.length) {
        list.innerHTML = '<div class="notif-empty">Нет уведомлений</div>';
        return;
    }
    list.innerHTML = items.map(n => `
        <div class="notif-item ${n.is_read ? '' : 'notif-unread'}" data-id="${n.id}"
             data-type="${_escape(n.type)}" data-entity-type="${_escape(n.entity_type)}" data-entity-id="${n.entity_id || ''}">
            <span class="notif-icon">${NOTIF_ICONS[n.type] || '🔔'}</span>
            <span class="notif-body">
                <span class="notif-title">${_escape(n.title)}</span>
                ${n.details ? `<span class="notif-details">${_escape(n.details)}</span>` : ''}
            </span>
            <span class="notif-time">${notifTime(n.created_at)}</span>
            <button type="button" class="notif-close" title="Удалить" aria-label="Удалить">✕</button>
        </div>`).join('');
}

async function markAndOpen(itemEl) {
    const id = parseInt(itemEl.dataset.id);
    const entityType = itemEl.dataset.entityType;
    const entityId = parseInt(itemEl.dataset.entityId);
    try {
        await apiFetch('/notifications/read', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: [id] })
        });
        itemEl.classList.remove('notif-unread');
        loadNotifications();
    } catch (e) {}
    // Переход к объекту
    if (entityType === 'card' && entityId && typeof openCardModal === 'function') {
        closeNotifPanel();
        openCardModal(entityId);
    } else if (entityType === 'task') {
        closeNotifPanel();
        document.querySelector('[data-target="page-tasks"]')?.click();
    }
}

function closeNotifPanel() {
    document.getElementById('notif-panel')?.classList.add('hidden');
}

/** Перенести колокольчик в строку действий шапки активного раздела.
 *  FIX 2026-09-03 (аудит): всегда последний в строке — на всех страницах
 *  справа (раньше prepend ставил его слева на канбане и справа на остальных). */
function placeBell() {
    const wrap = document.getElementById('notif-bell-wrap');
    if (!wrap) return;
    const target = document.querySelector('.page-section.active .page-header .header-actions');
    if (target && wrap.parentElement !== target) {
        target.append(wrap);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const bell = document.getElementById('notif-bell');
    if (!bell) return;

    bell.addEventListener('click', (e) => { openNotifPanel(); });

    // Закрытие по клику вне — именно на capture-фазе: некоторые контролы
    // (например, дропдауны фильтров канбана) вызывают stopPropagation на
    // пузыре, и обычный document-обработчик их клики не видит.
    document.addEventListener('click', (e) => {
        const wrap = document.getElementById('notif-bell-wrap');
        if (wrap && !wrap.contains(e.target)) closeNotifPanel();
    }, true);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeNotifPanel(); });

    document.getElementById('notif-list').addEventListener('click', async (e) => {
        const delBtn = e.target.closest('.notif-close');
        const item = e.target.closest('.notif-item');
        if (!item) return;
        e.stopPropagation();
        const id = parseInt(item.dataset.id);
        if (delBtn) {
            try {
                await apiFetch(`/notifications/${id}`, { method: 'DELETE' });
                item.remove();
                loadNotifications();
            } catch (err) {}
            return;
        }
        markAndOpen(item);
    });

    document.getElementById('notif-read-all').addEventListener('click', async () => {
        try {
            await apiFetch('/notifications/read', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: [] })
            });
            document.querySelectorAll('#notif-list .notif-unread')
                .forEach(el => el.classList.remove('notif-unread'));
            setNotifBadge(0);
        } catch (e) {}
    });

    // Первичная загрузка и обновление в общем polling-такте
    loadNotifications();
    if (typeof crmOn !== 'undefined') {
        crmOn('realtime:tick', loadNotifications);
    } else {
        setInterval(loadNotifications, 15000);
    }

    // Колокольчик следует за активным разделом
    placeBell();
    document.addEventListener('click', (e) => {
        if (e.target.closest('.nav-btn')) setTimeout(placeBell, 60);
    });
});
