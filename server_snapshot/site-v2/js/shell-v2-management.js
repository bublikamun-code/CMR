/* Local preview only: no credentials, network or production permissions. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    // Те же справочники, что читают доска и пульт (shell-v2-data.js):
    // правки здесь меняют доску, фильтры и «Клиент 360» через событие
    // kb:dictionaries-changed. Превью: до перезагрузки, без сети.
    const data = {
        supplier: KBData.suppliers,
        user: KBData.users,
        store: KBData.stores,
        status: KBData.statuses
    };
    const roles = {manager:'Менеджер', admin:'Администратор'};
    const roleLabels = {board: 'Колонка доски', writeoff: 'Очередь списания'};
    const fields = {
        supplier: [['name','Название','text',true],['unp','УНП'],['contact_person','Контактное лицо'],['phone','Телефон','tel'],['email','Email','email'],['address','Адрес'],['note','Примечание','textarea']],
        user: [['username','Логин','text',true],['full_name','Полное имя'],['password','Пароль (для создания)','password'],['role','Роль','select',true]],
        store: [['name','Название','text',true],['address','Адрес'],['phone','Телефон','tel']],
        status: [['name','Название','text',true],['color','Цвет','color'],['position','Позиция','number',true]]
    };
    const titles = {supplier:'Поставщик', user:'Пользователь', store:'Магазин', status:'Статус сделки'};
    let sequence = 10, editing, opener;
    window.KBSuppliers = {list: () => data.supplier.map(s => [s.id, s.name])};
    function apiMutate(kind, payload) {
        if (!KBData.mutate) return Promise.resolve(null);
        return KBData.mutate(kind, payload);
    }
    function numericId(id) {
        const str = String(id || '');
        if (str.startsWith('local-')) return null; // ещё не сохранено на сервере
        const m = /-(\d+)$/.exec(str);
        return m ? Number(m[1]) : null;
    }
    // Сохранение в CRM (site-v2): id из ответа возвращается локальной записи,
    // чтобы последующие правки попадали в ту же строку сервера.
    function persist(kind, record, values) {
        const prefix = {supplier:'sup', user:'us', store:'store', status:'st'}[kind];
        const numeric = numericId(record && record.id);
        const payload = { id: kind === 'status' ? (record && record.serverId) || numeric : numeric, fields: values };
        apiMutate(kind + '-save', payload).then(function (saved) {
            if (saved && saved.id != null && record && String(record.id).startsWith('local-')) {
                record.id = prefix + '-' + saved.id;
                if (saved.slug) record.id = saved.slug;
                if (kind === 'status') record.serverId = saved.id;
                render(kind);
                document.dispatchEvent(new Event(kind === 'supplier' ? 'kb:suppliers-changed' : 'kb:dictionaries-changed'));
            }
        });
    }
    const dialog = document.createElement('dialog');
    dialog.id = 'mgmt-dialog';
    dialog.setAttribute('aria-labelledby', 'mgmt-title');
    document.body.append(dialog);
    function cells(kind, record) {
        if (kind === 'supplier') return [record.name, record.unp, [record.contact_person,record.phone,record.email].filter(Boolean).join(' · '), record.address];
        if (kind === 'user') return [record.username, record.full_name, roles[record.role]];
        if (kind === 'store') return [record.name, record.address, record.phone];
        return [record.name, record.color, record.position, roleLabels[record.role] || 'Колонка доски'];
    }
    function render(kind) {
        const query = kind === 'supplier' ? $('mgmt-supplier-search').value.trim().toLocaleLowerCase('ru') : '';
        const rows = data[kind].filter(r => Object.values(r).join(' ').toLocaleLowerCase('ru').includes(query));
        $('mgmt-'+kind+'-rows').innerHTML = rows.map(r => '<tr data-id="'+esc(r.id)+'">'+cells(kind,r).map(v=>'<td>'+esc(v ?? '—')+'</td>').join('')+
            '<td><button type="button" class="btn btn-ghost btn-sm" data-edit="'+esc(r.id)+'" aria-label="Редактировать '+esc(r.name || r.username)+'">Изменить</button></td></tr>').join('');
        if (kind === 'supplier') {
            $('mgmt-supplier-count').textContent = rows.length;
            $('mgmt-supplier-empty').hidden = rows.length > 0;
        }
    }

    function open(kind, id, button) {
        const record = data[kind].find(r=>r.id===id) || {role:'manager', color:'#626962', position:data.status.length};
        editing = {kind, id}; opener = button;
        dialog.innerHTML = '<form id="mgmt-form"><h2 id="mgmt-title">'+titles[kind]+'</h2><div class="mgmt-fields">'+fields[kind].map(([key,label,type='text',required])=>{
            const attrs = ' id="mgmt-'+key+'" name="'+key+'"'+(required?' required':'');
            let control;
            if (type==='select') control='<select'+attrs+'>'+Object.entries(roles).map(([value,text])=>'<option value="'+value+'"'+(record[key]===value?' selected':'')+'>'+text+'</option>').join('')+'</select>';
            else if (type==='textarea') control='<textarea'+attrs+' maxlength="2000" rows="3">'+esc(record[key])+'</textarea>';
            else control='<input'+attrs+' type="'+type+'" value="'+esc(record[key])+'"'+(type==='number'?' min="0" max="999" step="1"':' maxlength="300"')+'>';
            return '<label for="mgmt-'+key+'"><span>'+label+'</span>'+control+'</label>';
        }).join('')+'</div><p id="mgmt-error" role="alert"></p><div class="mgmt-actions"><button class="btn btn-ghost" type="button" id="mgmt-cancel">Отмена</button><button class="btn btn-ghost" id="mgmt-save" type="submit">Сохранить</button></div></form>';
        $('mgmt-cancel').onclick=()=>dialog.close();
        $('mgmt-form').onsubmit=save;
        dialog.showModal();
        if (window.KBSelect) window.KBSelect.enhance(dialog);
    }
    function save(event) {
        event.preventDefault();
        const {kind,id}=editing;
        const values=Object.fromEntries(new FormData(event.target));
        Object.keys(values).forEach(k=>values[k]=values[k].trim());
        const key=kind==='user'?'username':'name';
        if (!values[key]) { $('mgmt-error').textContent='Заполните '+(kind==='user'?'логин.':'название.'); return; }
        if (data[kind].some(r=>r.id!==id && r[key].toLocaleLowerCase('ru')===values[key].toLocaleLowerCase('ru'))) {
            $('mgmt-error').textContent='Такая запись уже есть.'; return;
        }
        if (kind==='status') values.position=Number(values.position);
        const record=data[kind].find(r=>r.id===id);
        if (record) Object.assign(record,values);
        else data[kind].push({id:'local-'+sequence++, role:'board', serverId:null, ...values});
        render(kind);
        persist(kind, record || data[kind][data[kind].length - 1], values);
        if (kind==='supplier') document.dispatchEvent(new Event('kb:suppliers-changed'));
        // Доска, фильтры и пульт читают эти справочники — просим перерисоваться.
        if (kind==='user' || kind==='store' || kind==='status') document.dispatchEvent(new Event('kb:dictionaries-changed'));
        dialog.close();
    }
    dialog.addEventListener('close',()=>{ if(window.KBSelect) window.KBSelect.close(); if(opener?.isConnected) opener.focus(); else $('mgmt-'+editing.kind+'-new').focus(); });
    Object.keys(data).forEach(kind=>{
        render(kind);
        $('mgmt-'+kind+'-new').onclick=e=>open(kind,null,e.currentTarget);
        $('mgmt-'+kind+'-rows').onclick=e=>{const button=e.target.closest('[data-edit]');if(button) open(kind,button.dataset.edit,button);};
    });
    $('mgmt-supplier-search').addEventListener('input',()=>render('supplier'));
})();

/* ============================================================
   Почта, профиль и уведомления — живой CRM-функционал v2
   (фидбек 18.09: без этого старую систему не снять).
   ============================================================ */
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    function toast(text, isError) {
        const t = $('kb-toast');
        if (!t) return;
        t.hidden = false;
        t.textContent = text;
        t.classList.toggle('toast-error', Boolean(isError));
        setTimeout(() => { t.hidden = true; t.classList.remove('toast-error'); }, 4000);
    }

    /* --- Почта: настройки + ручная синхронизация --- */
    function fillStatusOptions() {
        const sel = $('mgmt-email-status');
        if (!sel || sel.options.length) return;
        const names = (window.KBData && window.KBData.statuses || []).map((s) => s.name);
        if (!names.includes('Новый запрос')) names.unshift('Новый запрос');
        sel.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
    }
    async function loadMail() {
        if (!window.V2Api.token()) return;
        try {
            const s = await window.V2Api.api('/email-parser/settings');
            const body = $('mgmt-email-body');
            if (!body) return;
            body.hidden = false;
            fillStatusOptions();
            $('mgmt-email-email').value = s.email || '';
            $('mgmt-email-imap').value = s.imap_server || '';
            $('mgmt-email-status').value = s.target_status || 'Новый запрос';
            $('mgmt-email-last').textContent = s.last_sync
                ? 'Последняя синхронизация: ' + new Date(s.last_sync).toLocaleString('ru-RU')
                : 'Синхронизаций ещё не было';
        } catch (e) { /* настройки недоступны — карточка остаётся свёрнутой */ }
    }
    $('mgmt-email-save').addEventListener('click', async () => {
        const password = $('mgmt-email-pass').value.trim();
        try {
            await window.V2Api.api('/email-parser/settings', {
                method: 'POST',
                body: {
                    email: $('mgmt-email-email').value.trim(),
                    imap_server: $('mgmt-email-imap').value.trim(),
                    password: password || '********',
                    target_status: $('mgmt-email-status').value || 'Новый запрос'
                }
            });
            $('mgmt-email-pass').value = '';
            toast('Настройки почты сохранены');
            loadMail();
        } catch (e) { toast('Не сохранено: ' + (e.detail || e.message || 'ошибка'), true); }
    });
    $('mgmt-email-sync').addEventListener('click', async () => {
        const btn = $('mgmt-email-sync');
        btn.disabled = true;
        try {
            const res = await window.V2Api.api('/email-parser/sync', { method: 'POST' });
            const first = (res.results || [])[0] || {};
            if (first.success) toast('Синхронизация выполнена: новых писем ' + (res.total_count || 0));
            else toast('Синхронизация не прошла: ' + (first.error || 'ошибка'), true);
            loadMail();
        } catch (e) { toast('Синхронизация не прошла: ' + (e.detail || e.message || 'ошибка'), true); }
        finally { btn.disabled = false; }
    });

    /* --- Профиль: смена пароля --- */
    $('mgmt-pass-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const err = $('mgmt-pass-error');
        err.textContent = '';
        const oldPassword = $('mgmt-pass-old').value;
        const newPassword = $('mgmt-pass-new').value;
        if (newPassword !== $('mgmt-pass-new2').value) { err.textContent = 'Новые пароли не совпадают.'; return; }
        if (newPassword.length < 6) { err.textContent = 'Пароль от 6 символов.'; return; }
        try {
            const res = await window.V2Api.api('/auth/me/password', {
                method: 'PUT',
                body: { old_password: oldPassword, new_password: newPassword }
            });
            window.alert((res && res.detail || 'Пароль изменён.') + ' Войдите с новым паролем.');
            await window.V2Api.api('/auth/logout', { method: 'POST' }).catch(() => {});
            window.V2Api.clear();
            location.reload();
        } catch (e) { err.textContent = e.detail || e.message || 'Не удалось сменить пароль.'; }
    });

    /* --- Уведомления: колокольчик --- */
    async function refreshBellCount() {
        if (!window.V2Api.token()) return;
        try {
            const list = await window.V2Api.api('/notifications');
            const badge = $('v2-bell-count');
            if (!badge) return;
            badge.hidden = !list.unread_count;
            badge.textContent = list.unread_count > 99 ? '99+' : String(list.unread_count);
        } catch (e) { /* тихо: колокольчик не критичен */ }
    }
    function renderItem(n) {
        const when = n.created_at ? new Date(n.created_at).toLocaleString('ru-RU') : '';
        return '<div class="kb-check-row" style="display:block"><b' + (n.is_read ? '' : ' style="color:var(--accent)"') + '>' + esc(n.title) + '</b>' +
            (n.details ? '<p style="margin:2px 0 0;white-space:pre-wrap">' + esc(n.details) + '</p>' : '') +
            '<p style="margin:2px 0 0;opacity:.7">' + esc(when) + '</p></div>';
    }
    $('v2-bell').addEventListener('click', async () => {
        const panel = $('v2-notif-panel');
        if (!panel) return;
        if (!panel.hidden) { panel.hidden = true; return; }
        panel.hidden = false;
        panel.innerHTML = '<p class="kb-detail-hint">Загрузка…</p>';
        try {
            const list = await window.V2Api.api('/notifications');
            panel.innerHTML = list.items.length
                ? list.items.map(renderItem).join('<hr style="border:0;border-top:1px solid var(--border);margin:6px 0">')
                : '<p class="kb-detail-hint">Уведомлений нет.</p>';
            if (list.unread_count) {
                await window.V2Api.api('/notifications/read', { method: 'POST', body: { ids: [] } });
                const badge = $('v2-bell-count');
                if (badge) { badge.hidden = true; badge.textContent = ''; }
            }
        } catch (e) { panel.innerHTML = '<p class="kb-detail-hint">Не удалось загрузить уведомления.</p>'; }
    });
    document.addEventListener('click', (event) => {
        const panel = $('v2-notif-panel');
        if (panel && !panel.hidden && !panel.contains(event.target) && event.target.closest('#v2-bell') === null) panel.hidden = true;
    });

    // Первичная загрузка: колокольчик сразу, карточка почты — при входе в Admin.
    refreshBellCount();
    setInterval(refreshBellCount, 60000);
    document.addEventListener('click', (event) => {
        const adminLink = event.target.closest('[data-view="admin"]');
        if (adminLink) setTimeout(loadMail, 300);
    });
    window.addEventListener('hashchange', () => { if (location.hash.includes('admin')) setTimeout(loadMail, 300); });
})();
