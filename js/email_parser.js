let emailAutoSyncInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    loadEmailSettings();
    startEmailAutoSync();

    const saveSettingsBtn = document.getElementById('btn-save-email-settings');
    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener('click', saveEmailSettings);
    }

    const runSyncBtn = document.getElementById('btn-run-email-sync');
    if (runSyncBtn) {
        runSyncBtn.addEventListener('click', runEmailSync);
    }
});

function updateEmailSyncStatus(lastSync) {
    // Старый UI (если ещё используется где-то)
    const oldLastSyncEl = document.getElementById('email-last-sync-time');
    const oldStatusTextEl = document.getElementById('email-sync-status-text');
    if (oldLastSyncEl && oldStatusTextEl) {
        if (lastSync) {
            oldLastSyncEl.textContent = new Date(lastSync).toLocaleString('ru-RU');
            oldStatusTextEl.textContent = 'Активна';
            oldStatusTextEl.className = 'status-active';
        } else {
            oldLastSyncEl.textContent = 'Никогда';
            oldStatusTextEl.textContent = 'Не выполнялась';
            oldStatusTextEl.className = 'status-never';
        }
    }

    // Новый UI настроек
    const lastEl = document.getElementById('email-last-sync');
    const prevEl = document.getElementById('email-prev-sync');
    const badge = document.getElementById('email-sync-badge');
    if (lastEl) {
        lastEl.textContent = lastSync ? new Date(lastSync).toLocaleString('ru-RU') : '—';
        lastEl.dataset.iso = lastSync || '';
    }
    if (badge) {
        if (lastSync) {
            badge.textContent = 'Активна';
            badge.className = 'email-sync-badge email-sync-badge--active';
        } else {
            badge.textContent = 'Не выполнялась';
            badge.className = 'email-sync-badge email-sync-badge--never';
        }
    }
    // Предыдущая синхронизация берётся из localStorage, если есть
    if (prevEl) {
        const prevSync = localStorage.getItem('crm_email_prev_sync');
        prevEl.textContent = prevSync ? new Date(prevSync).toLocaleString('ru-RU') : '—';
        prevEl.dataset.iso = prevSync || '';
    }
}

async function loadEmailSettings() {
    if (!hasToken()) return;

    try {
        const settings = await apiFetch('/email-parser/settings');

        const imapServerEl = document.getElementById('email-imap-server');
        const usernameEl = document.getElementById('email-username');
        const passwordEl = document.getElementById('email-password');
        const targetStatusEl = document.getElementById('email-target-status');

        if (imapServerEl) imapServerEl.value = settings.imap_server || 'imap.yandex.ru';
        if (usernameEl) usernameEl.value = settings.email || '';
        if (passwordEl) passwordEl.value = settings.password || '';
        if (targetStatusEl) targetStatusEl.value = settings.target_status || 'Новый запрос';

        updateEmailSyncStatus(settings.last_sync);
    } catch (err) {
        showToast('Не удалось загрузить настройки почты: ' + err.message, 'error');
    }
}

async function saveEmailSettings() {
    const imapServer = document.getElementById('email-imap-server').value.trim();
    const emailAddr = document.getElementById('email-username').value.trim();
    const password = document.getElementById('email-password').value;
    const targetStatus = document.getElementById('email-target-status').value;
    
    if (!imapServer || !emailAddr || !password) {
        showToast('Пожалуйста, заполните все обязательные поля (*)', 'error');
        return;
    }
    
    try {
        const res = await apiFetch('/email-parser/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                imap_server: imapServer,
                email: emailAddr,
                password: password,
                target_status: targetStatus
            })
        });
        
        showToast(res.detail || 'Настройки успешно сохранены', 'success');
        loadEmailSettings();
    } catch (err) {
        showToast('Ошибка сохранения настроек: ' + err.message, 'error');
    }
}

async function runEmailSync() {
    const syncBtn = document.getElementById('btn-run-email-sync');
    const loader = document.getElementById('email-sync-loader');
    const resultsContainer = document.getElementById('email-sync-results');
    
    if (syncBtn.disabled) return;
    
    syncBtn.disabled = true;
    if (loader) loader.classList.remove('hidden');
    resultsContainer.innerHTML = '<div class="sync-loading-text">Подключение к почте, проверка писем... Это может занять до 15 секунд...</div>';
    
    try {
        const res = await apiFetch('/email-parser/sync', {
            method: 'POST'
        });
        
        if (res.success) {
            showToast(`Проверка завершена! Импортировано сделок: ${res.count}`, 'success');
            
            // Update time
            loadEmailSettings();
            
            // Reload kanban board in background if the function is available
            if (typeof loadKanbanBoard === 'function') {
                loadKanbanBoard();
            }
            
            if (res.count === 0) {
                resultsContainer.innerHTML = `
                    <div class="sync-results-empty">
                        <b>Проверка завершена успешно!</b><br>
                        Новых непрочитанных писем не найдено. На сервере нет новых обращений.
                    </div>
                `;
            } else {
                let html = `
                    <div class="sync-results-success">
                        <p><b>Успешно импортировано сделок: ${res.count}</b></p>
                        <div class="imported-list">
                `;
                
                res.cards.forEach(card => {
                    html += `
                        <div class="imported-item clickable-card-row" onclick="openCardModal(${card.id})">
                            <span class="imported-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg></span>
                            <div class="imported-details">
                                <span class="imported-title">${escapeHtml(card.title)}</span>
                                <span class="imported-sender">Отправитель: ${escapeHtml(card.sender)}</span>
                            </div>
                            <span class="imported-link-arrow">Открыть карту <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg></span>
                        </div>
                    `;
                });
                
                html += `
                        </div>
                    </div>
                `;
                resultsContainer.innerHTML = html;
            }
        } else {
            resultsContainer.innerHTML = `<div class="sync-results-error">Ошибка синхронизации. Попробуйте еще раз.</div>`;
        }
    } catch (err) {
        showToast('Ошибка синхронизации: ' + err.message, 'error');
        resultsContainer.innerHTML = `<div class="sync-results-error">Ошибка подключения: ${escapeHtml(err.message)}</div>`;
    } finally {
        syncBtn.disabled = false;
        if (loader) loader.classList.add('hidden');
    }
}

function startEmailAutoSync() {
    if (emailAutoSyncInterval) clearInterval(emailAutoSyncInterval);
    emailAutoSyncInterval = setInterval(async () => {
        if (!hasToken()) return;
        try {
            const res = await apiFetch('/email-parser/sync', { method: 'POST' });
            if (res.success && res.count > 0) {
                showToast(`Автоимпорт: ${res.count} новых писем`, 'success');
                loadEmailSettings();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
            }
        } catch (e) {
            // Silent fail for auto-sync
        }
    }, 180000); // every 3 minutes
}
