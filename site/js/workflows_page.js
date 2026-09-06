// === workflows_page.js — логика страницы Воркфлоу ===
// Бывший инлайн-<script> workflows.html; вынесен для CSP без 'unsafe-inline'
// (аудит 06.09, С5).
    const WORKFLOWS_API = '/workflows';
    let currentWF = null;

    // Тонкая обёртка над каноническим apiFetch: добавляет префикс /workflows.
    // Теперь все вызовы wfFetch(path) получают таймаут, 401-обработку,
    // 422-декодирование и проверку content-type из api.js.
    async function wfFetch(path, options = {}) {
        return apiFetch(WORKFLOWS_API + path, options);
    }

    const workflowsUI = {
        async loadWorkflows() {
            const wfs = await wfFetch('/');
            const list = document.getElementById('workflows-list');
            list.innerHTML = '';
            if (wfs.length === 0) {
                list.innerHTML = '<p class="empty-state">Нет воркфлоу. Создайте первый!</p>';
                return;
            }
            wfs.forEach(wf => {
                const div = document.createElement('div');
                div.className = 'wf-item';
                div.innerHTML = `
                    <div class="wf-info">
                        <strong>${escapeHtml(wf.name)}</strong>
                        <span class="text-muted-sm">${escapeHtml(wf.description || '')}</span>
                    </div>
                    <div class="wf-actions">
                        <span class="wf-badge ${wf.is_active ? 'wf-active' : 'wf-inactive'}">${wf.is_active ? 'Активен' : 'Выкл'}</span>
                        <button class="btn btn-secondary btn-sm" data-handler="workflowsUI.openWorkflow" data-arg="${wf.id}">Открыть</button>
                    </div>
                `;
                list.appendChild(div);
            });
        },

        async createWorkflow() {
            const name = document.getElementById('new-wf-name').value.trim();
            if (!name) { showToast('Введите название', 'error'); return; }
            await wfFetch('/', { method: 'POST', body: JSON.stringify({ name }) });
            document.getElementById('new-wf-name').value = '';
            showToast('Воркфлоу создан', 'success');
            await this.loadWorkflows();
        },

        async openWorkflow(id) {
            const wfs = await wfFetch('/');
            currentWF = wfs.find(w => w.id === id);
            if (!currentWF) return;

            document.getElementById('wf-list').classList.add('hidden');
            document.getElementById('wf-detail').classList.remove('hidden');
            document.getElementById('wf-detail-title').textContent = currentWF.name;
            document.getElementById('wf-toggle-btn').textContent = currentWF.is_active ? 'Выключить' : 'Включить';

            await this.loadTriggers();
            await this.loadSteps();
            await this.loadRuns();
        },

        backToList() {
            currentWF = null;
            document.getElementById('wf-list').classList.remove('hidden');
            document.getElementById('wf-detail').classList.add('hidden');
        },

        async toggleActive() {
            if (!currentWF) return;
            await wfFetch(`/${currentWF.id}`, {
                method: 'PATCH', body: JSON.stringify({ is_active: !currentWF.is_active })
            });
            currentWF.is_active = !currentWF.is_active;
            document.getElementById('wf-toggle-btn').textContent = currentWF.is_active ? 'Выключить' : 'Включить';
            showToast(currentWF.is_active ? 'Включён' : 'Выключен', 'success');
        },

        async deleteWorkflow() {
            if (!currentWF || !confirm(`Удалить "${currentWF.name}"?`)) return;
            await wfFetch(`/${currentWF.id}`, { method: 'DELETE' });
            showToast('Удалено', 'success');
            this.backToList();
            await this.loadWorkflows();
        },

        async loadTriggers() {
            if (!currentWF) return;
            const triggers = await wfFetch(`/${currentWF.id}/triggers`);
            const list = document.getElementById('triggers-list');
            list.innerHTML = '';
            const icons = { record_event: '📋', schedule: '⏰', webhook: '🌐', manual: '👆' };
            const classes = { record_event: 'trigger-record', schedule: 'trigger-schedule', webhook: 'trigger-webhook', manual: 'trigger-manual' };
            const labels = { record_event: 'Изменение записи', schedule: 'По расписанию', webhook: 'Webhook', manual: 'Ручной запуск' };
            const objLabels = { card: 'Сделка', client: 'Клиент', payment: 'Оплата' };
            const eventLabels = { created: 'создана', updated: 'обновлена', deleted: 'удалена' };

            triggers.forEach(t => {
                const config = t.config || {};
                const div = document.createElement('div');
                div.className = 'trigger-item';
                div.innerHTML = `
                    <div class="flex-row" style="gap:0;">
                        <div class="trigger-icon ${classes[t.trigger_type] || ''}">${icons[t.trigger_type] || '?'}</div>
                        <div>
                            <strong>${labels[t.trigger_type] || t.trigger_type}</strong>
                            ${config.object ? `<span class="text-muted-sm"> — ${objLabels[config.object] || config.object} ${eventLabels[config.event] || ''}</span>` : ''}
                            ${config.cron ? `<span class="text-muted-sm"> — ${config.cron}</span>` : ''}
                        </div>
                    </div>
                    <button class="btn btn-danger btn-sm" data-handler="workflowsUI.deleteTrigger" data-arg="${t.id}">Удалить</button>
                `;
                list.appendChild(div);
            });
        },

        async createTrigger() {
            const type = document.getElementById('new-trigger-type').value;
            const config = {};
            if (type === 'record_event') {
                config.object = document.getElementById('new-trigger-object').value;
                config.event = document.getElementById('new-trigger-event').value;
            }
            await wfFetch(`/${currentWF.id}/triggers`, {
                method: 'POST', body: JSON.stringify({ trigger_type: type, config })
            });
            showToast('Триггер добавлен', 'success');
            await this.loadTriggers();
        },

        async deleteTrigger(id) {
            if (!confirm('Удалить триггер?')) return;
            await wfFetch(`/triggers/${id}`, { method: 'DELETE' });
            showToast('Удалено', 'success');
            await this.loadTriggers();
        },

        async loadSteps() {
            if (!currentWF) return;
            const steps = await wfFetch(`/${currentWF.id}/steps`);
            const list = document.getElementById('steps-list');
            list.innerHTML = '';
            const icons = { create_record: '➕', update_record: '✏️', send_email: '📧', http_request: '🌐', code: '💻' };
            const typeLabels = { action: 'Действие', condition: 'Условие', delay: 'Задержка' };
            const actionLabels = { create_record: 'Создать запись', update_record: 'Обновить запись', send_email: 'Отправить email', http_request: 'HTTP-запрос', code: 'Код' };

            steps.forEach((s, i) => {
                if (i > 0) {
                    const connector = document.createElement('div');
                    connector.className = 'step-connector';
                    connector.textContent = '↓';
                    list.appendChild(connector);
                }
                const div = document.createElement('div');
                div.className = 'step-item';
                div.innerHTML = `
                    <div class="flex-row" style="gap:12px;">
                        <span class="text-muted-md">#${i + 1}</span>
                        <span>${icons[s.action_type] || '⚡'} <strong>${typeLabels[s.step_type] || s.step_type}</strong> — ${actionLabels[s.action_type] || s.action_type}</span>
                        ${s.config && Object.keys(s.config).length > 0 ? `<span class="text-muted-sm">${JSON.stringify(s.config).substring(0, 60)}...</span>` : ''}
                    </div>
                    <button class="btn btn-danger btn-sm" data-handler="workflowsUI.deleteStep" data-arg="${s.id}">Удалить</button>
                `;
                list.appendChild(div);
            });
        },

        async createStep() {
            const stepType = document.getElementById('new-step-type').value;
            const actionType = document.getElementById('new-step-action').value;
            let config = {};
            const configStr = document.getElementById('new-step-config').value.trim();
            if (configStr) {
                try { config = JSON.parse(configStr); }
                catch(e) { showToast('Неверный JSON в параметрах', 'error'); return; }
            }
            const steps = await wfFetch(`/${currentWF.id}/steps`);
            await wfFetch(`/${currentWF.id}/steps`, {
                method: 'POST', body: JSON.stringify({
                    step_type: stepType, action_type: actionType, config, position: steps.length
                })
            });
            document.getElementById('new-step-config').value = '';
            showToast('Шаг добавлен', 'success');
            await this.loadSteps();
        },

        async deleteStep(id) {
            if (!confirm('Удалить шаг?')) return;
            await wfFetch(`/steps/${id}`, { method: 'DELETE' });
            showToast('Удалено', 'success');
            await this.loadSteps();
        },

        async loadRuns() {
            if (!currentWF) return;
            const runs = await wfFetch(`/${currentWF.id}/runs`);
            const list = document.getElementById('runs-list');
            if (runs.length === 0) {
                list.innerHTML = '<p class="empty-state empty-state--sm">Запусков пока нет</p>';
                return;
            }
            list.innerHTML = '';
            runs.forEach(r => {
                const div = document.createElement('div');
                div.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border-color);';
                const statusClass = r.status === 'completed' ? 'run-completed' : r.status === 'failed' ? 'run-failed' : 'run-running';
                div.innerHTML = `
                    <span>${r.started_at ? new Date(r.started_at).toLocaleString('ru-RU') : '—'}</span>
                    <span class="run-status ${statusClass}">${r.status}</span>
                    ${r.error ? `<span style="color:var(--danger-color);font-size:12px;">${escapeHtml(r.error)}</span>` : ''}
                `;
                list.appendChild(div);
            });
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        // (Дублирующий initTheme вырезан: переключатель темы уже вешает
        // js/features.js на всех страницах; второй слушатель давал двойное
        // переключение.)
        workflowsUI.loadWorkflows();
    });
