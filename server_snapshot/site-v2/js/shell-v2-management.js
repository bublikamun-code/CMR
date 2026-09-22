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
    const roles = {manager:'Менеджер', admin:'Администратор', superadmin:'Владелец (полный доступ)', warehouse:'Склад', documents:'Документы'};
    const roleLabels = {board: 'Колонка доски', writeoff: 'Очередь списания'};
    const fields = {
        supplier: [['name','Название','text',true],['unp','УНП'],['contact_person','Контактное лицо'],['phone','Телефон','tel'],['email','Email','email'],['address','Адрес'],['note','Примечание','textarea']],
        user: [['username','Логин','text',true],['password','Пароль (минимум 8 символов; обязателен при создании)','password'],['role','Роль','select',true]],
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
        }).catch(function (err) {
            // B1 fix: не глотаем ошибку сохранения — показываем тост.
            var msg = (err && (err.detail || err.message)) || 'ошибка сохранения';
            var t = document.getElementById('kb-toast');
            if (t) { t.hidden = false; t.textContent = 'Не сохранено: ' + msg; t.classList.add('toast-error'); setTimeout(function () { t.hidden = true; t.classList.remove('toast-error'); }, 4000); }
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
        // V10 (аудит 19.09): us-none («Не назначен») — служебный
        // псевдопользователь для отображения ответственного на доске,
        // в списке пользователей админки ему не место.
        const rows = data[kind].filter(r => r.id !== 'us-none')
            .filter(r => Object.values(r).join(' ').toLocaleLowerCase('ru').includes(query));
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
        // P0-2 (аудит 19.09): пароль обязателен только при СОЗДАНИИ
        // пользователя (сервер: POST /auth/users требует ≥8 символов,
        // PATCH принимает пустой пароль = «не менять»).
        if (kind==='user' && !id && values.password && values.password.length < 8) {
            $('mgmt-error').textContent='Пароль должен содержать минимум 8 символов.'; return;
        }
        if (data[kind].some(r=>r.id!==id && r[key].toLocaleLowerCase('ru')===values[key].toLocaleLowerCase('ru'))) {
            $('mgmt-error').textContent='Такая запись уже есть.'; return;
        }
        if (kind==='status') values.position=Number(values.position);
        // full_name в модели/schemas сервера нет — не выдумываем поле.
        delete values.full_name;
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
    dialog.addEventListener('close',()=>{
        // B4 fix: очищаем форму при закрытии, чтобы скрытые required-поля
        // не блокировали сабмит других форм (invalid form control is not focusable).
        dialog.innerHTML = '';
        if(window.KBSelect) window.KBSelect.close();
        if(opener?.isConnected) opener.focus();
        else if(editing && $('mgmt-'+editing.kind+'-new')) $('mgmt-'+editing.kind+'-new').focus();
    });
    Object.keys(data).forEach(kind=>{
        render(kind);
        $('mgmt-'+kind+'-new').onclick=e=>open(kind,null,e.currentTarget);
        $('mgmt-'+kind+'-rows').onclick=e=>{const button=e.target.closest('[data-edit]');if(button) open(kind,button.dataset.edit,button);};
    });
    // Debounce: rebuild таблицы поставщиков не должен бежать на каждый
    // символ — ждём паузу в вводе 150 мс.
    let supplierSearchTimer = 0;
    $('mgmt-supplier-search').addEventListener('input',()=>{
        clearTimeout(supplierSearchTimer);
        supplierSearchTimer = setTimeout(()=>render('supplier'),150);
    });
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
        } catch (e) {
            // B1 fix: показываем состояние ошибки в карточке почты, а не просто сворачиваем.
            const body = $('mgmt-email-body');
            if (body) {
                body.hidden = false;
                const hint = body.querySelector('[data-mail-error]') || (() => { const p = document.createElement('p'); p.setAttribute('data-mail-error', ''); p.style.cssText = 'color:var(--danger);font-size:var(--font-ui);padding:8px 0'; body.prepend(p); return p; })();
                hint.textContent = 'Не удалось загрузить настройки' + (e && e.message ? ': ' + e.message : '') + '. Попробуйте обновить страницу.';
            }
        }
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
            // V6 (аудит 19.09): сервер отдаёт {success, count, cards} напрямую
            // (без обёртки results/total_count) — читаем фактический контракт.
            const res = await window.V2Api.api('/email-parser/sync', { method: 'POST' });
            if (res && res.success) toast('Синхронизация выполнена: новых писем ' + (res.count || 0));
            else toast('Синхронизация не прошла: ' + ((res && res.error) || 'ошибка'), true);
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
            // Осознанно пустой catch: после смены пароля пытаемся отозвать сессию,
            // но если logout не прошёл — всё равно чистим локальный токен и перезагружаем.
            await window.V2Api.api('/auth/logout', { method: 'POST' }).catch(() => {});
            window.V2Api.clear();
            location.reload();
        } catch (e) { err.textContent = e.detail || e.message || 'Не удалось сменить пароль.'; }
    });

    /* --- Уведомления: колокольчик --- */
    // Пункт 10 плана (аудит F9): раскрытие колокольчика БОЛЬШЕ НЕ меняет
    // статусы — авто-POST /notifications/read {ids:[]} на каждое открытие
    // убран. Прочтение теперь только явное: «Прочитать все» либо переход
    // по конкретному уведомлению.
    const NOTIF_TRASH = '<svg class="i-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M9 6V4h6v2M6 6l1 14h10l1-14"/></svg>';
    let notifItems = [];    // снимок списка: локальные правки без повторного GET
    let notifUnread = 0;    // непрочитанных по данным сервера

    function setBellBadge(count) {
        const badge = $('v2-bell-count');
        if (!badge) return;
        badge.hidden = !count;
        badge.textContent = count > 99 ? '99+' : String(count);
    }
    async function refreshBellCount() {
        if (!window.V2Api.token()) return;
        try {
            const list = await window.V2Api.api('/notifications');
            notifUnread = list.unread_count || 0;
            setBellBadge(notifUnread);
        } catch (e) {
            // B1 fix: не глотаем молча — хотя колокольчик не критичен,
            // хотя бы console.warn + снимаем бейдж, чтобы не врать пользователю.
            console.warn('[bell] не удалось загрузить уведомления:', e && e.message || e);
            setBellBadge(0);
        }
    }

    // Куда ведёт уведомление. Своего роутера не изобретаем: сделка открывается
    // тем же мостом KBBoard, которым пользуются «Пульт дня» и «Клиент 360»,
    // клиент — кликом строки его таблицы, задача — переходом в раздел «Задачи»
    // (отдельной карточки задачи в v2 нет, как нет её и в legacy:
    // site/js/notifications.js:99-102). Без entity_id уведомление остаётся
    // некликабельным — мёртвых элементов в панели не держим.
    function notifTarget(n) {
        if (!n || !n.entity_id) return null;
        // Ид в клиентских данных с префиксом: v2-boot-template.js собирает
        // cards как String(id), tasks как 'task-'+id, clients как 'cl-'+id.
        // KBBoard.open сравнивает СТРОГО (card.id === id), поэтому String().
        if (n.entity_type === 'card') return { view: 'board', card: String(n.entity_id), label: 'Открыть сделку' };
        if (n.entity_type === 'task') return { view: 'tasks', task: 'task-' + n.entity_id, label: 'Открыть задачу' };
        if (n.entity_type === 'client') return { view: 'clients', client: 'cl-' + n.entity_id, label: 'Открыть карточку клиента' };
        return null;
    }
    function gotoTarget(target) {
        if (!target) return;
        // Раздел переключаем штатным кликом по рейке: свой обработчик
        // navigation.js отрабатывает синхронно (pushState + render), поэтому
        // объект открываем сразу и hashchange в очереди не остаётся. Прямая
        // запись location.hash дала бы отложенный hashchange, и render()
        // оболочки закрыл бы только что открытую карточку сделки.
        const link = document.querySelector('.rail [data-view="' + target.view + '"]');
        if (link) link.click();
        else if (location.hash !== '#' + target.view) location.hash = '#' + target.view;

        if (target.card) {
            if (window.KBBoard && window.KBBoard.open) window.KBBoard.open(target.card);
            const dialog = $('kb-dialog');
            if (!dialog || !dialog.open) toast('Сделка ' + target.card + ' не найдена в загруженных данных', true);
            return;
        }
        // Строки задач и клиентов живут в DOM постоянно (разделы не
        // пересоздаются), поэтому ищем их сразу. Нет строки — остаёмся в
        // разделе: задачу могли удалить, клиент мог попасть под поиск.
        const row = target.task
            ? document.querySelector('#tasks-list [data-task-toggle="' + target.task + '"]')
            : document.querySelector('#cl-rows [data-cl-id="' + target.client + '"]');
        if (!row) return;
        if (target.client) row.click();   // выбор клиента = существующий клик строки
        // render() оболочки следующим кадром утаскивает фокус на #shell-content
        // и скроллит окно в ноль — ставим фокус после него, тем же порядком rAF.
        requestAnimationFrame(() => {
            row.scrollIntoView({ block: 'center' });
            row.focus({ preventScroll: true });
        });
    }

    function headHtml(readAll) {
        // «Прочитать все» показываем только когда есть что помечать.
        return '<div class="v2-notif-head"><span id="v2-notif-title">Уведомления</span>' +
            (readAll ? '<button type="button" class="btn btn-ghost btn-sm" data-notif-read-all title="Отметить все уведомления прочитанными">Прочитать все</button>' : '') +
            '</div>';
    }
    function renderItem(n) {
        const when = n.created_at ? new Date(n.created_at).toLocaleString('ru-RU') : '';
        const target = notifTarget(n);
        // Точка «не прочитано» декоративна — состояние дублируем текстом для скринридера.
        const body = '<span class="v2-notif-item-title">' + (n.is_read ? '' : '<span class="v2-notif-sr">Не прочитано. </span>') + esc(n.title) + (n.is_read ? '' : '<span class="v2-notif-dot" aria-hidden="true"></span>') + '</span>' +
            (n.details ? '<span class="v2-notif-item-details">' + esc(n.details) + '</span>' : '') +
            '<span class="v2-notif-item-time">' + esc(when) + '</span>';
        const main = target
            ? '<button type="button" class="v2-notif-main" data-notif-open="' + esc(n.id) + '" title="' + esc(target.label) + '">' + body + '</button>'
            : '<div class="v2-notif-body">' + body + '</div>';
        return '<div class="v2-notif-item' + (n.is_read ? '' : ' unread') + '"><div class="v2-notif-row">' + main +
            '<button type="button" class="icon-action danger v2-notif-del" data-notif-del="' + esc(n.id) + '" title="Удалить уведомление" aria-label="Удалить уведомление: ' + esc(n.title) + '">' + NOTIF_TRASH + '</button>' +
            '</div></div>';
    }
    function renderPanel() {
        const panel = $('v2-notif-panel');
        if (!panel) return;
        panel.innerHTML = headHtml(notifUnread > 0) + (notifItems.length
            ? notifItems.map(renderItem).join('')
            : '<div class="v2-notif-item"><p class="kb-detail-hint">Уведомлений нет.</p></div>');
    }
    function closePanel(restoreFocus) {
        const panel = $('v2-notif-panel');
        const bell = $('v2-bell');
        if (!panel || panel.hidden) return;
        panel.hidden = true;
        if (!bell) return;
        bell.setAttribute('aria-expanded', 'false');
        // Фокус возвращаем только при закрытии с клавиатуры/колокольчика: после
        // клика по разделу утаскивать фокус в шапку нельзя.
        if (restoreFocus && (panel.contains(document.activeElement) || document.activeElement === bell)) bell.focus();
    }
    async function openPanel() {
        const panel = $('v2-notif-panel');
        if (!panel) return;
        const bell = $('v2-bell');
        panel.hidden = false;
        if (bell) bell.setAttribute('aria-expanded', 'true');
        panel.innerHTML = headHtml(false) + '<div class="v2-notif-item"><p class="kb-detail-hint">Загрузка…</p></div>';
        try {
            const list = await window.V2Api.api('/notifications');
            notifItems = list.items || [];
            notifUnread = list.unread_count || 0;
            setBellBadge(notifUnread);
            if (!panel.hidden) renderPanel();   // панель могли закрыть, пока шёл запрос
        } catch (e) {
            if (!panel.hidden) panel.innerHTML = headHtml(false) + '<div class="v2-notif-item"><p class="kb-detail-hint">Не удалось загрузить уведомления.</p></div>';
        }
    }
    async function readAll(button) {
        const panel = $('v2-notif-panel');
        button.disabled = true;
        try {
            // ids: [] — сервер помечает ВСЕ непрочитанные текущего пользователя.
            await window.V2Api.api('/notifications/read', { method: 'POST', body: { ids: [] } });
            const list = await window.V2Api.api('/notifications');
            notifItems = list.items || [];
            notifUnread = list.unread_count || 0;
            setBellBadge(notifUnread);
            if (panel && !panel.hidden) {
                renderPanel();
                // Кнопка «Прочитать все» после пометки исчезает (нечего
                // помечать — мёртвых кнопок не держим), поэтому уводим фокус
                // на первое оставшееся действие панели, а не в body.
                const first = panel.querySelector('button') || $('v2-bell');
                if (first) first.focus();
            }
        } catch (e) {
            button.disabled = false;
            toast('Не отмечено: ' + (e.detail || e.message || 'ошибка'), true);
        }
    }
    async function openNotification(id) {
        const n = notifItems.filter((x) => String(x.id) === String(id))[0];
        if (!n) return;
        const target = notifTarget(n);
        closePanel(false);
        gotoTarget(target);
        if (n.is_read) return;
        try {
            // Одно уведомление, а не «всё»: ids: [id].
            await window.V2Api.api('/notifications/read', { method: 'POST', body: { ids: [n.id] } });
            n.is_read = true;
            notifUnread = Math.max(0, notifUnread - 1);
            setBellBadge(notifUnread);
        } catch (e) { toast('Не отмечено: ' + (e.detail || e.message || 'ошибка'), true); }
    }
    async function deleteNotification(button) {
        const panel = $('v2-notif-panel');
        const id = button.dataset.notifDel;
        const index = notifItems.findIndex((x) => String(x.id) === String(id));
        button.disabled = true;
        try {
            await window.V2Api.api('/notifications/' + encodeURIComponent(id), { method: 'DELETE' });
            const removed = index >= 0 ? notifItems[index] : null;
            notifItems = notifItems.filter((x) => String(x.id) !== String(id));
            if (removed && !removed.is_read) notifUnread = Math.max(0, notifUnread - 1);
            setBellBadge(notifUnread);
            if (panel && !panel.hidden) {
                renderPanel();
                // Фокус не должен падать в body: переводим на удаление соседнего
                // уведомления, а когда список опустел — обратно на колокольчик.
                const rest = panel.querySelectorAll('[data-notif-del]');
                const next = rest.length ? rest[Math.max(0, Math.min(index, rest.length - 1))] : $('v2-bell');
                if (next) next.focus();
            }
        } catch (e) {
            button.disabled = false;
            toast('Не удалено: ' + (e.detail || e.message || 'ошибка'), true);
        }
    }

    const bell = $('v2-bell');
    const notifPanel = $('v2-notif-panel');
    // В прототипе (tools/mockups) колокольчика нет — его инжектит сборщик,
    // поэтому без guards весь модуль падал бы на file://.
    if (bell) {
        bell.setAttribute('aria-controls', 'v2-notif-panel');
        bell.setAttribute('aria-expanded', 'false');
        bell.addEventListener('click', () => {
            if (notifPanel && !notifPanel.hidden) closePanel(true);
            else openPanel();
        });
    }
    if (notifPanel) {
        // Заголовок панелей рисуется вместе с содержимым, поэтому до первого
        // открытия aria-labelledby висел бы в пустоту — кладём шапку сразу.
        if (!notifPanel.innerHTML.trim()) notifPanel.innerHTML = headHtml(false);
        notifPanel.setAttribute('aria-labelledby', 'v2-notif-title');
        notifPanel.addEventListener('click', (event) => {
            const action = event.target.closest('[data-notif-read-all], [data-notif-del], [data-notif-open]');
            if (!action) return;
            if (action.hasAttribute('data-notif-read-all')) readAll(action);
            else if (action.hasAttribute('data-notif-del')) deleteNotification(action);
            else openNotification(action.dataset.notifOpen);
        });
    }
    document.addEventListener('click', (event) => {
        const panel = $('v2-notif-panel');
        if (panel && !panel.hidden && !panel.contains(event.target) && event.target.closest('#v2-bell') === null) closePanel(false);
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        const panel = $('v2-notif-panel');
        // Только когда фокус в панели или на колокольчике: Escape у открытых
        // диалогов перехватывать нельзя.
        if (panel && !panel.hidden && (panel.contains(document.activeElement) || document.activeElement === bell)) closePanel(true);
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
