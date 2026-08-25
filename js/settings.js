/**
 * Настройки — вкладки: Кастомные объекты, Воркфлоу, Почта.
 */
(function() {
    let curObj = null, curFields = [], curRecords = [], curWf = null;

    const ICON_CHECK = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    const ICON_CROSS = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    const ICON_ARROW_DOWN = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>';
    const ICON_TRIGGER_RECORD = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
    const ICON_TRIGGER_SCHEDULE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
    const ICON_TRIGGER_WEBHOOK = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
    const ICON_TRIGGER_MANUAL = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    const ICON_ACTION_CREATE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
    const ICON_ACTION_UPDATE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>';
    const ICON_ACTION_EMAIL = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1 .9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>';
    const ICON_ACTION_HTTP = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
    const ICON_LIGHTNING = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10"/></svg>';
    const ICON_CONSTRUCTION = '<svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 22h20"/><path d="M6.36 9.05L3 22h18l-3.36-12.95a2 2 0 0 0-3.64 0L12 17l-2-7.95a2 2 0 0 0-3.64 0z"/><path d="M12 2v5"/><path d="M9 5h6"/></svg>';

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
                if (objs.length === 0) {
                    c.innerHTML = '';
                    c.appendChild(renderEmptyState({
                        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
                        title: 'Нет кастомных объектов',
                        description: 'Создайте первый объект, чтобы хранить собственные данные: товары, заявки, контракты и т.д.',
                        action: { text: 'Создать объект', onClick: () => document.getElementById('sobj-label').focus() }
                    }));
                } else {
                    c.innerHTML = '';
                }
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
            if (curFields.length === 0) {
                c.innerHTML = '';
                c.appendChild(renderEmptyState({
                    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
                    title: 'Добавьте поля',
                    description: 'Чтобы создавать записи объекта, сначала определите его поля.'
                }));
                return;
            }
            if (curRecords.length === 0) {
                c.innerHTML = '';
                c.appendChild(renderEmptyState({
                    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
                    title: 'Нет записей',
                    description: 'Нажмите «+ Новая», чтобы добавить первую запись.',
                    action: { text: 'Новая запись', onClick: () => this.showNewRec() }
                }));
                return;
            }
            let h = '<table class="settings-table"><thead><tr>';
            curFields.forEach(f => { h += `<th>${escapeHtml(f.label)}</th>`; });
            h += '<th></th></tr></thead><tbody>';
            curRecords.forEach(r => {
                h += '<tr>';
                curFields.forEach(f => {
                    const v = r.data[f.name]; let d = '';
                    if (v !== undefined && v !== null) {
                        if (f.field_type === 'boolean') d = v ? ICON_CHECK : ICON_CROSS;
                        else if (f.field_type === 'currency') d = formatMoneyBYN(v);
                        else d = escapeHtml(String(v));
                    }
                    h += `<td>${d}</td>`;
                });
                h += `<td><button class="btn btn-danger btn-sm" onclick="SettingsUI.delRec(${r.id})" title="Удалить">${ICON_CROSS}</button></td></tr>`;
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
                if (wfs.length === 0) {
                    c.innerHTML = '';
                    c.appendChild(renderEmptyState({
                        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 1 0 10 10H12V2z"/><path d="M12 2a10 10 0 0 1 10 10"/><path d="M22 12h-2"/><path d="M12 22v-2"/></svg>',
                        title: 'Нет воркфлоу',
                        description: 'Автоматизация пока не настроена. Создайте первый сценарий.'
                    }));
                } else {
                    c.innerHTML = '';
                }
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
            const icons = {record_event:ICON_TRIGGER_RECORD,schedule:ICON_TRIGGER_SCHEDULE,webhook:ICON_TRIGGER_WEBHOOK,manual:ICON_TRIGGER_MANUAL};
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
            const icons = {create_record:ICON_ACTION_CREATE,update_record:ICON_ACTION_UPDATE,send_email:ICON_ACTION_EMAIL,http_request:ICON_ACTION_HTTP};
            const al = {create_record:'Создать запись',update_record:'Обновить',send_email:'Email',http_request:'HTTP'};
            ss.forEach((s, i) => {
                if (i > 0) { const conn = document.createElement('div'); conn.className='wf-connector'; conn.innerHTML=ICON_ARROW_DOWN; c.appendChild(conn); }
                const d = document.createElement('div'); d.className = 'list-item';
                d.innerHTML = `<div class="flex-center-gap"><span class="text-muted-sm">#${i+1}</span><span>${icons[s.action_type]||ICON_LIGHTNING} <strong>${s.step_type}</strong> — ${al[s.action_type]||s.action_type}</span></div><button class="btn btn-danger btn-sm" onclick="SettingsUI.delStep(${s.id})">Удалить</button>`;
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
            if (rs.length === 0) {
                c.innerHTML = '';
                c.appendChild(renderEmptyState({
                    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
                    title: 'Запусков пока нет',
                    description: 'История запусков появится после первого срабатывания воркфлоу.'
                }));
                return;
            }
            c.innerHTML = '';
            rs.forEach(r => {
                const d = document.createElement('div');
                d.className = 'wf-run-row';
                d.innerHTML = `<span>${r.started_at?new Date(r.started_at).toLocaleString('ru-RU'):'—'}</span><span class="wf-status-badge wf-status-${r.status}">${r.status}</span>${r.error?`<span class="wf-error-msg">${escapeHtml(r.error)}</span>`:''}`;
                c.appendChild(d);
            });
        },

        // === ПОЧТА ===
        _updateEmailSyncUI(lastSync, prevSync) {
            const lastEl = document.getElementById('email-last-sync');
            const prevEl = document.getElementById('email-prev-sync');
            const badge = document.getElementById('email-sync-badge');
            if (lastEl) {
                lastEl.textContent = lastSync ? new Date(lastSync).toLocaleString('ru-RU') : '—';
                lastEl.dataset.iso = lastSync || '';
            }
            if (prevEl) {
                prevEl.textContent = prevSync ? new Date(prevSync).toLocaleString('ru-RU') : '—';
                prevEl.dataset.iso = prevSync || '';
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
        },
        async loadEmailSettings() {
            try {
                const s = await apiFetch('/email-parser/settings');
                if (s.imap_server) document.getElementById('email-imap-server').value = s.imap_server;
                if (s.email || s.username) document.getElementById('email-username').value = s.email || s.username || '';
                if (s.target_status) document.getElementById('email-target-status').value = s.target_status;
                const prevSync = localStorage.getItem('crm_email_prev_sync');
                this._updateEmailSyncUI(s.last_sync, prevSync);
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
                const lastEl = document.getElementById('email-last-sync');
                const lastBefore = lastEl && lastEl.dataset.iso ? lastEl.dataset.iso : null;
                const prevBefore = localStorage.getItem('crm_email_prev_sync');
                const r = await apiFetch('/email-parser/sync', { method:'POST' });
                const nowIso = new Date().toISOString();
                const prevToStore = lastBefore || prevBefore || null;
                if (prevToStore) localStorage.setItem('crm_email_prev_sync', prevToStore);
                this._updateEmailSyncUI(nowIso, prevToStore);
                const results = document.getElementById('email-sync-results');
                results.innerHTML = `
                    <div class="email-sync-result">
                        <span class="email-sync-result__icon">${ICON_CHECK}</span>
                        <span>Импортировано писем: <strong>${Number(r.imported || 0).toLocaleString('ru-RU')}</strong></span>
                    </div>`;
                showToast('Готово','success');
            } catch(e) { showToast(e.message,'error'); }
        },

        // === WEBHOOKS ===
        async loadWebhooks() {
            try {
                const hooks = await apiFetch('/webhooks/');
                const c = document.getElementById('webhooks-container');
                if (hooks.length === 0) {
                    c.innerHTML = '';
                    c.appendChild(renderEmptyState({
                        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
                        title: 'Нет webhooks',
                        description: 'Настройте интеграцию: выберите события и URL для получения уведомлений.',
                        action: { text: 'Создать webhook', onClick: () => document.getElementById('wh-url').focus() }
                    }));
                } else {
                    c.innerHTML = '';
                }
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
