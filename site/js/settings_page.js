// === settings_page.js — логика страницы Настроек ===
// Бывший инлайн-<script> settings.html; вынесен для CSP без 'unsafe-inline'
// (аудит 06.09, С5). Загружается как обычный (не defer) скрипт на месте блока.
    // === ТАБЫ ===
    // Обработчик вешается в handlers.js по data-tab; btn передаётся оттуда же.
    function switchTab(tab, btn) {
        document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.settings-pane').forEach(p => p.classList.remove('active'));
        (btn || document.querySelector(`.settings-tab[data-tab="${tab}"]`)).classList.add('active');
        document.getElementById('tab-' + tab).classList.add('active');
    }

    // === ОБЪЕКТЫ ===
    let curObj = null, curFields = [], curRecords = [];
    const ObjAPI = {
        async load() {
            const objs = await apiFetch('/custom/objects');
            const c = document.getElementById('objects-container');
            c.innerHTML = objs.length === 0 ? '<p class="empty-state">Нет объектов</p>' : '';
            objs.forEach(o => {
                const d = document.createElement('div'); d.className = 'list-item';
                d.innerHTML = `<div class="list-item-info"><strong>${escapeHtml(o.label)}</strong><span class="text-muted-sm">${escapeHtml(o.name)}</span></div>
                <div class="list-item-actions"><button class="btn btn-secondary btn-sm obj-open-btn">Открыть</button><button class="btn btn-danger btn-sm obj-del-btn">Удалить</button></div>`;
                // У «Удалить» два аргумента (id + имя) — биндим программно,
                // чтобы не кодировать их в атрибуты (CSP: без inline-onclick).
                d.querySelector('.obj-open-btn').addEventListener('click', () => ObjAPI.open(o.id));
                d.querySelector('.obj-del-btn').addEventListener('click', () => ObjAPI.del(o.id, o.name));
                c.appendChild(d);
            });
        },
        async create() {
            const label = document.getElementById('obj-label').value.trim();
            const name = document.getElementById('obj-name').value.trim().toLowerCase().replace(/[^a-z0-9_]/g,'_');
            if (!label || !name) { showToast('Заполните все поля','error'); return; }
            await apiFetch('/custom/objects', { method:'POST', body:JSON.stringify({name,label}) });
            document.getElementById('obj-label').value=''; document.getElementById('obj-name').value='';
            showToast('Создано','success'); await this.load();
        },
        async del(id, name) { if (!confirm(`Удалить "${name}"?`)) return; await apiFetch(`/custom/objects/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.load(); },
        async open(id) {
            const objs = await apiFetch('/custom/objects'); curObj = objs.find(o=>o.id===id);
            document.getElementById('obj-list-view').classList.add('hidden');
            document.getElementById('obj-detail-view').classList.remove('hidden');
            document.getElementById('obj-detail-title').textContent = curObj.label;
            await this.loadFields(); await this.loadRecords();
        },
        backToList() { curObj=null; document.getElementById('obj-list-view').classList.remove('hidden'); document.getElementById('obj-detail-view').classList.add('hidden'); },
        async loadFields() { curFields = await apiFetch(`/custom/objects/${curObj.id}/fields`); const c=document.getElementById('fields-container'); c.innerHTML=''; curFields.forEach(f=>{const d=document.createElement('div');d.className='list-item';d.innerHTML=`<span><b>${escapeHtml(f.label)}</b> <span class="text-muted-sm">(${escapeHtml(f.name)}, ${f.field_type})</span></span><button class="btn btn-danger btn-sm" data-handler="ObjAPI.delField" data-arg="${f.id}">Удалить</button>`;c.appendChild(d);}); },
        async addField() { const label=document.getElementById('field-label').value.trim(),name=document.getElementById('field-name').value.trim().toLowerCase().replace(/[^a-z0-9_]/g,'_'),type=document.getElementById('field-type').value; if(!label||!name){showToast('Заполните все поля','error');return;} await apiFetch(`/custom/objects/${curObj.id}/fields`,{method:'POST',body:JSON.stringify({name,label,field_type:type,position:curFields.length})}); document.getElementById('field-label').value='';document.getElementById('field-name').value='';showToast('Поле добавлено','success');await this.loadFields(); },
        async delField(id) { if(!confirm('Удалить поле?'))return; await apiFetch(`/custom/fields/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadFields(); },
        async loadRecords() { curRecords = await apiFetch(`/custom/objects/${curObj.id}/records`); const c=document.getElementById('records-container'); if(curFields.length===0){c.innerHTML='<p class="empty-state">Добавьте поля</p>';return;} if(curRecords.length===0){c.innerHTML='<p class="empty-state">Нет записей</p>';return;} let h='<table style="width:100%;border-collapse:collapse;"><thead><tr>';curFields.forEach(f=>{h+=`<th style="padding:8px;text-align:left;border-bottom:2px solid var(--border-color);font-size:13px;color:var(--text-muted);">${escapeHtml(f.label)}</th>`;});h+='<th style="padding:8px;"></th></tr></thead><tbody>';curRecords.forEach(r=>{h+='<tr>';curFields.forEach(f=>{const v=r.data[f.name];let d='';if(v!==undefined&&v!==null){if(f.field_type==='boolean')d=`<span class=\"boolean-icon\">${v?'✓':'✕'}</span>`;else if(f.field_type==='currency')d=formatMoneyBYN(v);else d=escapeHtml(String(v));}h+=`<td style="padding:8px;border-bottom:1px solid var(--border-color);">${d}</td>`;});h+=`<td style="padding:8px;border-bottom:1px solid var(--border-color);"><button class="btn btn-danger btn-sm" data-handler="ObjAPI.delRecord" data-arg="${r.id}">Удалить</button></td></tr>`;});h+='</tbody></table>';c.innerHTML=h; },
        showNewRecord() { const f=document.getElementById('new-record-form'); f.classList.toggle('hidden'); if(!f.classList.contains('hidden')){f.innerHTML='';curFields.forEach(inp=>{let i='';if(inp.field_type==='boolean')i=`<input type="checkbox" id="rf-${inp.id}">`;else if(inp.field_type==='date')i=`<input type="date" id="rf-${inp.id}">`;else if(inp.field_type==='number'||inp.field_type==='currency')i=`<input type="number" step="0.01" id="rf-${inp.id}">`;else i=`<input type="text" id="rf-${inp.id}">`;f.innerHTML+=`<div class="form-group"><label>${escapeHtml(inp.label)}</label>${i}</div>`;});f.innerHTML+='<button class="btn btn-primary" data-handler="ObjAPI.createRecord" style="margin-top:8px">Создать</button>';} },
        async createRecord() { const data={};curFields.forEach(f=>{const el=document.getElementById(`rf-${f.id}`);if(!el)return;if(f.field_type==='boolean')data[f.name]=el.checked;else if(f.field_type==='number'||f.field_type==='currency')data[f.name]=el.value?parseFloat(el.value):null;else data[f.name]=el.value||null;}); await apiFetch(`/custom/objects/${curObj.id}/records`,{method:'POST',body:JSON.stringify({data})}); showToast('Создано','success');document.getElementById('new-record-form').classList.add('hidden');await this.loadRecords(); },
        async delRecord(id) { if(!confirm('Удалить?'))return; await apiFetch(`/custom/records/${id}`,{method:'DELETE'}); showToast('Удалено','success'); await this.loadRecords(); }
    };

    // === ВОРКФЛОУ ===
    let curWf = null;
    const WfAPI = {
        async load() {
            const wfs = await apiFetch('/workflows');
            const c = document.getElementById('workflows-container');
            c.innerHTML = wfs.length === 0 ? '<p class="empty-state">Нет воркфлоу</p>' : '';
            wfs.forEach(w => {
                const d = document.createElement('div'); d.className = 'wf-item';
                d.innerHTML = `<div><strong>${escapeHtml(w.name)}</strong><br><span class="text-muted-sm">${escapeHtml(w.description||'')}</span></div>
                <div style="display:flex;gap:8px;align-items:center"><span class="wf-badge ${w.is_active?'wf-active':'wf-inactive'}">${w.is_active?'Активен':'Выкл'}</span><button class="btn btn-secondary btn-sm" data-handler="WfAPI.open" data-arg="${w.id}">Открыть</button></div>`;
                c.appendChild(d);
            });
        },
        async create() { const name=document.getElementById('wf-name').value.trim(); if(!name){showToast('Введите название','error');return;} await apiFetch('/workflows',{method:'POST',body:JSON.stringify({name})}); document.getElementById('wf-name').value='';showToast('Создано','success');await this.load(); },
        async open(id) { const wfs=await apiFetch('/workflows');curWf=wfs.find(w=>w.id===id); document.getElementById('wf-list-view').classList.add('hidden');document.getElementById('wf-detail-view').classList.remove('hidden');document.getElementById('wf-detail-title').textContent=curWf.name;document.getElementById('wf-toggle-btn').textContent=curWf.is_active?'Выключить':'Включить'; await this.loadTriggers();await this.loadSteps();await this.loadRuns(); },
        backToList() { curWf=null;document.getElementById('wf-list-view').classList.remove('hidden');document.getElementById('wf-detail-view').classList.add('hidden'); },
        async toggleActive() { await apiFetch(`/workflows/${curWf.id}`,{method:'PATCH',body:JSON.stringify({is_active:!curWf.is_active})}); curWf.is_active=!curWf.is_active;document.getElementById('wf-toggle-btn').textContent=curWf.is_active?'Выключить':'Включить';showToast(curWf.is_active?'Включён':'Выключен','success'); },
        async deleteWf() { if(!confirm('Удалить?'))return; await apiFetch(`/workflows/${curWf.id}`,{method:'DELETE'});showToast('Удалено','success');this.backToList();await this.load(); },
        async loadTriggers() { const ts=await apiFetch(`/workflows/${curWf.id}/triggers`);const c=document.getElementById('triggers-container');c.innerHTML='';const icons={record_event:'📋',schedule:'⏰',webhook:'🌐',manual:'👆'};const cls={record_event:'trigger-record',schedule:'trigger-schedule',webhook:'trigger-webhook',manual:'trigger-manual'};const lbl={record_event:'Изменение записи',schedule:'По расписанию',webhook:'Webhook',manual:'Ручной'};const obj={card:'Сделка',client:'Клиент',payment:'Оплата'};const evt={created:'создана',updated:'обновлена',deleted:'удалена'};ts.forEach(t=>{const cfg=t.config||{};const d=document.createElement('div');d.className='list-item';d.innerHTML=`<div class="flex-row" style="gap:0;"><span class="trigger-icon wf-action-icon ${cls[t.trigger_type]||''}">${icons[t.trigger_type]||'?'}</span><strong>${lbl[t.trigger_type]||t.trigger_type}</strong>${cfg.object?` <span class="text-muted-sm"> — ${obj[cfg.object]||cfg.object} ${evt[cfg.event]||''}</span>`:''}</div><button class="btn btn-danger btn-sm" data-handler="WfAPI.delTrigger" data-arg="${t.id}">Удалить</button>`;c.appendChild(d);}); },
        async addTrigger() { const type=document.getElementById('tr-type').value;const cfg={};if(type==='record_event'){cfg.object=document.getElementById('tr-object').value;cfg.event=document.getElementById('tr-event').value;} await apiFetch(`/workflows/${curWf.id}/triggers`,{method:'POST',body:JSON.stringify({trigger_type:type,config:cfg})});showToast('Добавлено','success');await this.loadTriggers(); },
        async delTrigger(id) { if(!confirm('Удалить?'))return;await apiFetch(`/workflows/triggers/${id}`,{method:'DELETE'});showToast('Удалено','success');await this.loadTriggers(); },
        async loadSteps() { const ss=await apiFetch(`/workflows/${curWf.id}/steps`);const c=document.getElementById('steps-container');c.innerHTML='';const icons={create_record:'➕',update_record:'✏️',send_email:'📧',http_request:'🌐'};const al={create_record:'Создать запись',update_record:'Обновить',send_email:'Email',http_request:'HTTP'};ss.forEach((s,i)=>{if(i>0){const conn=document.createElement('div');conn.className='step-connector';conn.textContent='↓';c.appendChild(conn);}const d=document.createElement('div');d.className='list-item';d.innerHTML=`<div style="display:flex;align-items:center;gap:8px;"><span style="color:var(--text-muted);font-size:13px">#${i+1}</span><span><span class="wf-action-icon">${icons[s.action_type]||'⚡'}</span> <strong>${s.step_type}</strong> — ${al[s.action_type]||s.action_type}</span></div><button class="btn btn-danger btn-sm" data-handler="WfAPI.delStep" data-arg="${s.id}">Удалить</button>`;c.appendChild(d);}); },
        async addStep() { const st=document.getElementById('st-type').value,sa=document.getElementById('st-action').value;let cfg={};const cs=document.getElementById('st-config').value.trim();if(cs){try{cfg=JSON.parse(cs);}catch(e){showToast('Неверный JSON','error');return;}} const steps=await apiFetch(`/workflows/${curWf.id}/steps`);await apiFetch(`/workflows/${curWf.id}/steps`,{method:'POST',body:JSON.stringify({step_type:st,action_type:sa,config:cfg,position:steps.length})});document.getElementById('st-config').value='';showToast('Добавлено','success');await this.loadSteps(); },
        async delStep(id) { if(!confirm('Удалить?'))return;await apiFetch(`/workflows/steps/${id}`,{method:'DELETE'});showToast('Удалено','success');await this.loadSteps(); },
        async loadRuns() { const rs=await apiFetch(`/workflows/${curWf.id}/runs`);const c=document.getElementById('runs-container');if(rs.length===0){c.innerHTML='<p class="empty-state empty-state--sm">Запусков пока нет</p>';return;} c.innerHTML='';rs.forEach(r=>{const sc=r.status==='completed'?'background:#d1fae5;color:#065f46':r.status==='failed'?'background:#fee2e2;color:#991b1b':'background:#fef3c7;color:#92400e';const d=document.createElement('div');d.style.cssText='display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border-color);';d.innerHTML=`<span>${r.started_at?new Date(r.started_at).toLocaleString('ru-RU'):'—'}</span><span style="padding:4px 8px;border-radius:4px;font-size:12px;font-weight:600;${sc}">${r.status}</span>${r.error?`<span style="color:var(--danger-color);font-size:12px">${escapeHtml(r.error)}</span>`:''}`;c.appendChild(d);}); }
    };

    // === ПОЧТА ===
    const EmailAPI = {
        async save() {
            const settings = {
                imap_server: document.getElementById('email-imap').value,
                username: document.getElementById('email-user').value,
                password: document.getElementById('email-pass').value,
                target_status: document.getElementById('email-target').value
            };
            await apiFetch('/email-parser/settings', { method: 'POST', body: JSON.stringify(settings) });
            showToast('Настройки сохранены', 'success');
        },
        async sync() {
            showToast('Синхронизация...', 'info');
            const r = await apiFetch('/email-parser/sync', { method: 'POST' });
            document.getElementById('email-status-text').textContent = new Date().toLocaleString('ru-RU');
            document.getElementById('email-results').innerHTML = `<p style="margin-top:8px;">Импортировано: <strong>${r.imported || 0}</strong> писем</p>`;
            showToast('Синхронизация завершена', 'success');
        },
        async loadSettings() {
            try {
                const s = await apiFetch('/email-parser/settings');
                if (s.imap_server) document.getElementById('email-imap').value = s.imap_server;
                if (s.username) document.getElementById('email-user').value = s.username;
                if (s.target_status) document.getElementById('email-target').value = s.target_status;
            } catch(e) {}
        }
    };

    // ИНИЦИАЛИЗАЦИЯ
    // (Дублирующий initTheme вырезан: переключатель темы уже вешает
    // js/features.js на всех страницах; второй слушатель давал двойное
    // переключение.)
    document.addEventListener('DOMContentLoaded', () => {
        ObjAPI.load();
        WfAPI.load();
        EmailAPI.loadSettings();
    });
