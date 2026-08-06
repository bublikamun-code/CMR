/**
 * Настройки — вкладки: Кастомные объекты, Воркфлоу, Почта.
 */
(function() {
    let curObj = null, curFields = [], curRecords = [], curWf = null;

    const SettingsUI = {
        switchTab(tab) {
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.settings-pane').forEach(p => p.classList.remove('active'));
            const tabs = {objects:1,workflows:2,email:3,webhooks:4};
            document.querySelector(`.settings-tab:nth-child(${tabs[tab]||1})`).classList.add('active');
            document.getElementById('stab-' + tab).classList.add('active');
            if (tab === 'objects') this.loadObjs();
            if (tab === 'workflows') this.loadWfs();
            if (tab === 'email') this.loadEmailSettings();
            if (tab === 'webhooks') this.loadWebhooks();
        },

        // === ОБЪЕКТЫ ===
        async loadObjs() {
            try {
                const objs = await apiFetch('/custom/objects');
                const c = document.getElementById('sobj-container');
                c.innerHTML = objs.length === 0 ? '<p class="empty-msg">Нет объектов</p>' : '';
                objs.forEach(o => {
                    const d = document.createElement('div'); d.className = 'list-item';
                    d.innerHTML = `<div class="list-item-info"><strong>${escapeHtml(o.label)}</strong><span class="text-muted-sm">${escapeHtml(o.name)}</span></div>
                    <div class="list-item-actions"><button class="btn btn-secondary btn-sm" onclick="SettingsUI.openObj(${o.id})">Открыть</button><button class="btn btn-danger btn-sm" onclick="SettingsUI.delObj(${o.id},'${escapeHtml(o.name).replace(/'/g,"\\'")}')">Удалить</button></div>`;
                    c.appendChild(d);
                });
            } catch(e) { console.error(e); }
        },
        async createObj() {
            const label = document.getElementById('sobj-label').value.trim();
            const name = document.getElementById('sobj-name').value.trim().toLowerCase().replace(/[^a-z0-9_]/g,'_');
            if (!label || !name) { showToast('Заполните все поля','error'); return; }
            await apiFetch('/custom/objects', { method:'POST', body:JSON.stringify({name,label}) });
            document.getElementById('sobj-label').value=''; document.getElementById('sobj-name').value='';
            showToast('Создано','success'); await this.loadObjs();
        },
        async delObj(id, name) { if (!confirm(`Удалить "${name}"?`)) return; await apiFetch(`/custom/objects/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadObjs(); },
        async openObj(id) {
            const objs = await apiFetch('/custom/objects'); curObj = objs.find(o=>o.id===id);
            document.getElementById('sobj-list').classList.add('hidden');
            document.getElementById('sobj-detail').classList.remove('hidden');
            document.getElementById('sobj-detail-title').textContent = curObj.label;
            await this.loadFields(); await this.loadRecords();
        },
        backObj() { curObj=null; document.getElementById('sobj-list').classList.remove('hidden'); document.getElementById('sobj-detail').classList.add('hidden'); },
        async loadFields() {
            curFields = await apiFetch(`/custom/objects/${curObj.id}/fields`);
            const c = document.getElementById('sfields-container'); c.innerHTML = '';
            curFields.forEach(f => {
                const d = document.createElement('div'); d.className = 'list-item';
                d.innerHTML = `<span><b>${escapeHtml(f.label)}</b> <span class="text-muted-sm">(${escapeHtml(f.name)}, ${f.field_type})</span></span><button class="btn btn-danger btn-sm" onclick="SettingsUI.delField(${f.id})">Удалить</button>`;
                c.appendChild(d);
            });
        },
        async addField() {
            const label = document.getElementById('sfield-label').value.trim();
            const name = document.getElementById('sfield-name').value.trim().toLowerCase().replace(/[^a-z0-9_]/g,'_');
            const type = document.getElementById('sfield-type').value;
            if (!label || !name) { showToast('Заполните все поля','error'); return; }
            await apiFetch(`/custom/objects/${curObj.id}/fields`, { method:'POST', body:JSON.stringify({name,label,field_type:type,position:curFields.length}) });
            document.getElementById('sfield-label').value=''; document.getElementById('sfield-name').value='';
            showToast('Поле добавлено','success'); await this.loadFields();
        },
        async delField(id) { if(!confirm('Удалить поле?'))return; await apiFetch(`/custom/fields/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadFields(); },
        async loadRecords() {
            curRecords = await apiFetch(`/custom/objects/${curObj.id}/records`);
            const c = document.getElementById('srecords-container');
            if (curFields.length === 0) { c.innerHTML = '<p class="empty-msg">Добавьте поля</p>'; return; }
            if (curRecords.length === 0) { c.innerHTML = '<p class="empty-msg">Нет записей</p>'; return; }
            let h = '<table class="settings-table"><thead><tr>';
            curFields.forEach(f => { h += `<th>${escapeHtml(f.label)}</th>`; });
            h += '<th></th></tr></thead><tbody>';
            curRecords.forEach(r => {
                h += '<tr>';
                curFields.forEach(f => {
                    const v = r.data[f.name]; let d = '';
                    if (v !== undefined && v !== null) {
                        if (f.field_type === 'boolean') d = v ? '✓' : '✕';
                        else if (f.field_type === 'currency') d = Number(v).toLocaleString('ru-RU') + ' BYN';
                        else d = escapeHtml(String(v));
                    }
                    h += `<td>${d}</td>`;
                });
                h += `<td><button class="btn btn-danger btn-sm" onclick="SettingsUI.delRec(${r.id})">✕</button></td></tr>`;
            });
            h += '</tbody></table>';
            c.innerHTML = h;
        },
        showNewRec() {
            const f = document.getElementById('snew-rec-form');
            f.classList.toggle('hidden');
            if (!f.classList.contains('hidden')) {
                f.innerHTML = '';
                curFields.forEach(inp => {
                    let i = '';
                    if (inp.field_type === 'boolean') i = `<input type="checkbox" id="srf-${inp.id}">`;
                    else if (inp.field_type === 'date') i = `<input type="date" id="srf-${inp.id}">`;
                    else if (inp.field_type === 'number' || inp.field_type === 'currency') i = `<input type="number" step="0.01" id="srf-${inp.id}">`;
                    else i = `<input type="text" id="srf-${inp.id}">`;
                    f.innerHTML += `<div class="form-group-sm"><label class="form-label-sm">${escapeHtml(inp.label)}</label>${i}</div>`;
                });
                f.innerHTML += '<button class="btn btn-primary" onclick="SettingsUI.createRec()" style="margin-top:8px;">Создать</button>';
            }
        },
        async createRec() {
            const data = {};
            curFields.forEach(f => {
                const el = document.getElementById(`srf-${f.id}`);
                if (!el) return;
                if (f.field_type === 'boolean') data[f.name] = el.checked;
                else if (f.field_type === 'number' || f.field_type === 'currency') data[f.name] = el.value ? parseFloat(el.value) : null;
                else data[f.name] = el.value || null;
            });
            await apiFetch(`/custom/objects/${curObj.id}/records`, { method:'POST', body:JSON.stringify({data}) });
            showToast('Создано','success');
            document.getElementById('snew-rec-form').classList.add('hidden');
            await this.loadRecords();
        },
        async delRec(id) { if(!confirm('Удалить?'))return; await apiFetch(`/custom/records/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadRecords(); },

        // === ВОРКФЛОУ ===
        async loadWfs() {
            try {
                const wfs = await apiFetch('/workflows');
                const c = document.getElementById('swf-container');
                c.innerHTML = wfs.length === 0 ? '<p class="empty-msg">Нет воркфлоу</p>' : '';
                wfs.forEach(w => {
                    const d = document.createElement('div'); d.className = 'list-item';
                    d.innerHTML = `<div class="list-item-info"><strong>${escapeHtml(w.name)}</strong><span class="text-muted-sm">${escapeHtml(w.description||'')}</span></div>
                    <div class="list-item-actions"><span class="wf-badge ${w.is_active?'wf-active':'wf-inactive'}">${w.is_active?'Активен':'Выкл'}</span><button class="btn btn-secondary btn-sm" onclick="SettingsUI.openWf(${w.id})">Открыть</button></div>`;
                    c.appendChild(d);
                });
            } catch(e) { console.error(e); }
        },
        async createWf() {
            const name = document.getElementById('swf-name').value.trim();
            if (!name) { showToast('Введите название','error'); return; }
            await apiFetch('/workflows', { method:'POST', body:JSON.stringify({name}) });
            document.getElementById('swf-name').value = '';
            showToast('Создано','success'); await this.loadWfs();
        },
        async openWf(id) {
            const wfs = await apiFetch('/workflows'); curWf = wfs.find(w=>w.id===id);
            document.getElementById('swf-list').classList.add('hidden');
            document.getElementById('swf-detail').classList.remove('hidden');
            document.getElementById('swf-detail-title').textContent = curWf.name;
            document.getElementById('swf-toggle-btn').textContent = curWf.is_active ? 'Выключить' : 'Включить';
            await this.loadTriggers(); await this.loadSteps(); await this.loadRuns();
        },
        backWf() { curWf=null; document.getElementById('swf-list').classList.remove('hidden'); document.getElementById('swf-detail').classList.add('hidden'); },
        async toggleWf() {
            await apiFetch(`/workflows/${curWf.id}`, { method:'PATCH', body:JSON.stringify({is_active:!curWf.is_active}) });
            curWf.is_active = !curWf.is_active;
            document.getElementById('swf-toggle-btn').textContent = curWf.is_active ? 'Выключить' : 'Включить';
            showToast(curWf.is_active ? 'Включён' : 'Выключен', 'success');
        },
        async delWf() { if(!confirm('Удалить?'))return; await apiFetch(`/workflows/${curWf.id}`,{method:'DELETE'}); showToast('Удалено','success'); this.backWf(); await this.loadWfs(); },
        async loadTriggers() {
            const ts = await apiFetch(`/workflows/${curWf.id}/triggers`);
            const c = document.getElementById('str-container'); c.innerHTML = '';
            const icons = {record_event:'📋',schedule:'⏰',webhook:'🌐',manual:'👆'};
            const lbl = {record_event:'Изменение записи',schedule:'По расписанию',webhook:'Webhook',manual:'Ручной'};
            const obj = {card:'Сделка',client:'Клиент',payment:'Оплата'};
            const evt = {created:'создана',updated:'обновлена',deleted:'удалена'};
            ts.forEach(t => {
                const cfg = t.config || {};
                const d = document.createElement('div'); d.className = 'list-item';
                d.innerHTML = `<div class="flex-center-gap"><span style="font-size:18px;">${icons[t.trigger_type]||'?'}</span><strong>${lbl[t.trigger_type]||t.trigger_type}</strong>${cfg.object?` <span class="text-muted-sm"> — ${obj[cfg.object]||cfg.object} ${evt[cfg.event]||''}</span>`:''}</div><button class="btn btn-danger btn-sm" onclick="SettingsUI.delTrigger(${t.id})">Удалить</button>`;
                c.appendChild(d);
            });
        },
        async addTrigger() {
            const type = document.getElementById('str-type').value;
            const cfg = {};
            if (type === 'record_event') { cfg.object = document.getElementById('str-object').value; cfg.event = document.getElementById('str-event').value; }
            await apiFetch(`/workflows/${curWf.id}/triggers`, { method:'POST', body:JSON.stringify({trigger_type:type,config:cfg}) });
            showToast('Добавлено','success'); await this.loadTriggers();
        },
        async delTrigger(id) { if(!confirm('Удалить?'))return; await apiFetch(`/workflows/triggers/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadTriggers(); },
        async loadSteps() {
            const ss = await apiFetch(`/workflows/${curWf.id}/steps`);
            const c = document.getElementById('sst-container'); c.innerHTML = '';
            const icons = {create_record:'➕',update_record:'✏️',send_email:'📧',http_request:'🌐'};
            const al = {create_record:'Создать запись',update_record:'Обновить',send_email:'Email',http_request:'HTTP'};
            ss.forEach((s, i) => {
                if (i > 0) { const conn = document.createElement('div'); conn.className='wf-connector'; conn.textContent='↓'; c.appendChild(conn); }
                const d = document.createElement('div'); d.className = 'list-item';
                d.innerHTML = `<div class="flex-center-gap"><span class="text-muted-sm">#${i+1}</span><span>${icons[s.action_type]||'⚡'} <strong>${s.step_type}</strong> — ${al[s.action_type]||s.action_type}</span></div><button class="btn btn-danger btn-sm" onclick="SettingsUI.delStep(${s.id})">Удалить</button>`;
                c.appendChild(d);
            });
        },
        async addStep() {
            const st = document.getElementById('sst-type').value;
            const sa = document.getElementById('sst-action').value;
            let cfg = {};
            const cs = document.getElementById('sst-config').value.trim();
            if (cs) { try { cfg = JSON.parse(cs); } catch(e) { showToast('Неверный JSON','error'); return; } }
            const steps = await apiFetch(`/workflows/${curWf.id}/steps`);
            await apiFetch(`/workflows/${curWf.id}/steps`, { method:'POST', body:JSON.stringify({step_type:st,action_type:sa,config:cfg,position:steps.length}) });
            document.getElementById('sst-config').value = '';
            showToast('Добавлено','success'); await this.loadSteps();
        },
        async delStep(id) { if(!confirm('Удалить?'))return; await apiFetch(`/workflows/steps/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadSteps(); },
        async loadRuns() {
            const rs = await apiFetch(`/workflows/${curWf.id}/runs`);
            const c = document.getElementById('sruns-container');
            if (rs.length === 0) { c.innerHTML = '<p class="empty-msg-sm">Запусков пока нет</p>'; return; }
            c.innerHTML = '';
            rs.forEach(r => {
                const d = document.createElement('div');
                d.className = 'wf-run-row';
                d.innerHTML = `<span>${r.started_at?new Date(r.started_at).toLocaleString('ru-RU'):'—'}</span><span class="wf-status-badge wf-status-${r.status}">${r.status}</span>${r.error?`<span class="wf-error-msg">${escapeHtml(r.error)}</span>`:''}`;
                c.appendChild(d);
            });
        },

        // === ПОЧТА ===
        async loadEmailSettings() {
            try {
                const s = await apiFetch('/email-parser/settings');
                if (s.imap_server) document.getElementById('email-imap-server').value = s.imap_server;
                if (s.email || s.username) document.getElementById('email-username').value = s.email || s.username || '';
                if (s.target_status) document.getElementById('email-target-status').value = s.target_status;
                if (s.last_sync) document.getElementById('email-sync-status-text').textContent = new Date(s.last_sync).toLocaleString('ru-RU');
            } catch(e) { console.error(e); }
        },
        async saveEmail() {
            try {
                await apiFetch('/email-parser/settings', { method:'POST', body:JSON.stringify({
                    imap_server: document.getElementById('email-imap-server').value,
                    email: document.getElementById('email-username').value,
                    password: document.getElementById('email-password').value,
                    target_status: document.getElementById('email-target-status').value
                })});
                showToast('Настройки сохранены','success');
            } catch(e) { showToast(e.message,'error'); }
        },
        async syncEmail() {
            try {
                showToast('Синхронизация...','info');
                const r = await apiFetch('/email-parser/sync', { method:'POST' });
                document.getElementById('email-sync-status-text').textContent = new Date().toLocaleString('ru-RU');
                document.getElementById('email-sync-results').innerHTML = `<p class="sync-result-msg">Импортировано: <strong>${r.imported || 0}</strong> писем</p>`;
                showToast('Готово','success');
            } catch(e) { showToast(e.message,'error'); }
        },

        // === WEBHOOKS ===
        async loadWebhooks() {
            try {
                const hooks = await apiFetch('/webhooks/');
                const c = document.getElementById('webhooks-container');
                c.innerHTML = hooks.length === 0 ? '<p class="empty-msg-sm">Нет webhooks</p>' : '';
                hooks.forEach(h => {
                    const d = document.createElement('div'); d.className = 'list-item';
                    d.innerHTML = `<div class="list-item-info"><strong class="word-break-all">${escapeHtml(h.url)}</strong><span class="text-muted-sm">События: ${h.events.join(', ')}</span></div>
                    <div class="list-item-actions">
                        <span class="wf-badge ${h.is_active?'wf-active':'wf-inactive'}">${h.is_active?'Активен':'Выкл'}</span>
                        <button class="btn btn-secondary btn-sm" onclick="SettingsUI.testWebhook(${h.id})">Тест</button>
                        <button class="btn btn-danger btn-sm" onclick="SettingsUI.delWebhook(${h.id})">Удалить</button>
                    </div>`;
                    c.appendChild(d);
                });
            } catch(e) { console.error(e); }
        },
        async createWebhook() {
            const url = document.getElementById('wh-url').value.trim();
            if (!url) { showToast('Введите URL','error'); return; }
            const events = [];
            document.querySelectorAll('#wh-events input:checked').forEach(cb => events.push(cb.value));
            if (events.length === 0) { showToast('Выберите хотя бы одно событие','error'); return; }
            await apiFetch('/webhooks/', { method:'POST', body:JSON.stringify({url, events}) });
            document.getElementById('wh-url').value = '';
            document.querySelectorAll('#wh-events input').forEach(cb => cb.checked = false);
            showToast('Webhook создан','success');
            await this.loadWebhooks();
        },
        async delWebhook(id) {
            if (!confirm('Удалить webhook?')) return;
            await apiFetch(`/webhooks/${id}`, { method:'DELETE' });
            showToast('Удалено','success');
            await this.loadWebhooks();
        },
        async testWebhook(id) {
            showToast('Отправка теста...','info');
            const r = await apiFetch(`/webhooks/${id}/test`, { method:'POST' });
            if (r.success) showToast('Тест отправлен успешно!','success');
            else showToast('Ошибка: ' + (r.error || 'Статус ' + r.status),'error');
        }
    };

    window.SettingsUI = SettingsUI;
})();
