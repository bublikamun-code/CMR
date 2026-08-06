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

async function loadEmailSettings() {
    if (!hasToken()) return;
    
    try {
        const settings = await apiFetch('/email-parser/settings');
        
        document.getElementById('email-imap-server').value = settings.imap_server || 'imap.yandex.ru';
        document.getElementById('email-username').value = settings.email || '';
        document.getElementById('email-password').value = settings.password || '';
        document.getElementById('email-target-status').value = settings.target_status || 'Новый запрос';
        
        const lastSyncEl = document.getElementById('email-last-sync-time');
        const statusTextEl = document.getElementById('email-sync-status-text');
        
        if (settings.last_sync) {
            const date = new Date(settings.last_sync);
            lastSyncEl.textContent = date.toLocaleString('ru-RU');
            statusTextEl.textContent = 'Активна';
            statusTextEl.className = 'status-active';
        } else {
            lastSyncEl.textContent = 'Никогда';
            statusTextEl.textContent = 'Не выполнялась';
            statusTextEl.className = 'status-never';
        }
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
                            <span class="imported-icon">✉</span>
                            <div class="imported-details">
                                <span class="imported-title">${escapeHtml(card.title)}</span>
                                <span class="imported-sender">Отправитель: ${escapeHtml(card.sender)}</span>
                            </div>
                            <span class="imported-link-arrow">Открыть карту →</span>
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
