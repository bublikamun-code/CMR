/* Local preview only: no credentials, network or production permissions. */

/* Ролевая видимость (пункт 9 плана). V2Api и V2Role появляются только в
   собранном site-v2: в предпросмотре мокапа их нет — там раздел показан
   целиком, как и до правки. */
const MGMT_ROLE = (function () {
    function isAdmin() { return window.V2Api ? window.V2Api.isAdmin() : true; }
    function apply(root) { return window.V2Role ? window.V2Role.apply(root) : isAdmin(); }
    return { isAdmin: isAdmin, apply: apply };
})();

/* Один тост на весь модуль управления: раньше текст ошибки показывали три
   копии с таймером (в persist, в почтовом блоке и в новом пункте 15), и
   первое же из них гасило второй тост раньше времени. */
const MGMT_TOAST = (function () {
    let timer = 0;
    return function toast(text, isError) {
        const el = document.getElementById('kb-toast');
        if (!el) return;
        el.hidden = false;
        el.textContent = text;
        el.classList.toggle('toast-error', Boolean(isError));
        clearTimeout(timer);
        timer = setTimeout(() => {
            el.hidden = true;
            el.classList.remove('toast-error');
        }, 4000);
    };
})();

/* ── Ленивые загрузки админских экранов (пункт 15 плана) ────────────────
   Раздел «Управление» отрисован в разметке целиком, но сетевые списки —
   пользователи с сервера, вебхуки, кастомные объекты, автоматизации —
   нужны, только когда раздел действительно открыли: на старте страницы
   не должно уходить ни одного нового запроса. Отслеживается сам атрибут
   hidden на #view-admin — его снимает navigation.js и при клике по рейке,
   и при заходе по адресу #admin, и при «назад» в истории; класс active
   смотрим вместе с ним, как это уже делает fin-блок прототипа.
   Под не-админом загрузчики не зовутся вовсе: карточки скрыты, а запрос
   мелькал бы в сети и в 403. */
const MGMT_LAZY = (function () {
    const subs = [];
    let timer = 0;
    function host() { return document.getElementById('view-admin'); }
    function isOpen() {
        const el = host();
        return !!el && !el.hidden && el.classList.contains('active');
    }
    // «В сети» = есть токен CRM и роль администратора. В предпросмотре
    // мокапов и под ?demo=1 токена нет — экраны честно об этом пишут.
    function online() {
        return !!(window.V2Api && window.V2Api.token() && window.V2Api.isAdmin());
    }
    function run() {
        timer = 0;
        if (!isOpen()) return;
        if (window.V2Api && window.V2Api.token() && !window.V2Api.isAdmin()) return;
        subs.forEach(fn => {
            try { fn(); } catch (e) { console.warn('[mgmt] загрузчик не отработал:', e && e.message || e); }
        });
    }
    // Один таймер на все перерисовки: hidden и class меняются в одном
    // проходе render(), а перечитывать список дважды не нужно.
    function arm() { clearTimeout(timer); timer = setTimeout(run, 0); }
    const el = host();
    if (el && typeof MutationObserver === 'function') {
        new MutationObserver(arm).observe(el, { attributes: true, attributeFilter: ['hidden', 'class'] });
    }
    return { onOpen: fn => { subs.push(fn); }, sync: arm, online: online, isOpen: isOpen };
})();

const MGMT_DICT = (() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    /* ── Статусы сделок: полный список из API (пункт 12, Р3/Р5) ──────────
       Раздел грузит статусы ПРЯМЫМ GET /dictionaries/statuses: роут
       возвращает ВСЕ строки, включая is_active=false, — а boot кладёт в
       общий KBData.statuses только активных (v2-boot-template.js,
       buildKbData), и выключенный статус иначе исчезал бы из админки без
       возможности включить его обратно. Загрузка ленивая — при открытии
       раздела (MGMT_LAZY.onOpen ниже), перечитывание — после каждого
       сохранения (saveStatus → loadStatuses). */

    // Канонические статусы сделок — schemas.CARD_STATUSES (schemas.py:243),
    // тот же список, что CANON_ID в v2-boot-template.js. Сделки хранят статус
    // именем, а сервер принимает только канонические имена (422) — поэтому
    // имя канонического статуса закрыто для правки (Р3): переименование
    // оставило бы карточки без колонки.
    const CANON_NAMES = ['Новый запрос', 'В работе', 'Ждет оплаты', 'Сборка', 'На списание', 'Закрыто'];
    // id канонических статусов — CANON_ID из v2-boot-template.js; «Закрыто» —
    // 'closed' (Р2), чтобы не спорить с псевдо-этапом полной выписки 'done'.
    const CANON_IDS = {
        'Новый запрос': 'new', 'В работе': 'work', 'Ждет оплаты': 'pay',
        'Сборка': 'assembly', 'На списание': 'writeoff', 'Закрыто': 'closed'
    };
    const isCanonName = name => CANON_NAMES.indexOf(name) >= 0;
    // Роли на сервере нет (DealStatusResponse отдаёт только id/name/position/
    // color/is_active) — выводим из имени тем же правилом, что boot (Р1).
    function statusRole(name) {
        return name === 'На списание' ? 'writeoff' : name === 'Закрыто' ? 'archive' : 'board';
    }
    // Тот же slug, что в v2-boot-template.js (функция slug), — id
    // неканонического статуса должен совпасть с тем, что boot построит при
    // следующей загрузке страницы.
    function statusSlug(name) {
        return 'st-' + String(name).toLowerCase().replace(/[^a-zа-я0-9]+/gi, '-').replace(/^-|-$/g, '');
    }
    // Запись раздела из строки API — совместима с persist(): serverId —
    // числовой id сервера, is_active — видимость на доске, canonical —
    // блокировка имени в форме (Р3).
    function statusRecordFromApi(row) {
        return {
            id: 'st-' + row.id,
            serverId: row.id,
            name: row.name,
            color: row.color || '',
            position: row.position || 0,
            is_active: row.is_active !== false,
            canonical: isCanonName(row.name)
        };
    }
    // Режим строки состояния списка статусов: '' | 'loading' | 'preview' |
    // 'error'. Объявлен ДО стартового render('status') (инициализация ниже
    // зовёт render по всем видам) — иначе let-переменные попали бы в TDZ.
    let statusesLoading = false;
    let statusStateMode = '';
    let statusStateReason = '';
    // Состояние раздела до первой ленивой загрузки — снимок KBData.statuses,
    // чтобы предпросмотр макета и ?demo=1 не пустовали. serverId нет только
    // у демо/фолбэк-статусов — числовой id сервера не придумываем: правка
    // такой записи уйдёт в POST, как и раньше.
    function statusRecordFromKb(s) {
        return {
            id: s.serverId != null ? 'st-' + s.serverId : 'local-' + s.id,
            serverId: s.serverId != null ? s.serverId : null,
            name: s.name,
            color: s.color || '',
            position: s.position || 0,
            is_active: true,
            canonical: isCanonName(s.name)
        };
    }
    // Остальные справочники — те же, что читают доска и пульт
    // (shell-v2-data.js): правки меняют доску, фильтры и «Клиент 360» через
    // событие kb:dictionaries-changed. Превью: до перезагрузки, без сети.
    // Статусы — НЕ ссылка на KBData.statuses (см. блок выше): у раздела свой
    // массив, до первой ленивой загрузки — снимок общего справочника; в
    // общий справочник свежий список переносит syncKbStatuses().
    const data = {
        supplier: KBData.suppliers,
        user: KBData.users,
        store: KBData.stores,
        status: (KBData.statuses || []).map(statusRecordFromKb)
    };
    const roles = {manager:'Менеджер', admin:'Администратор', superadmin:'Владелец (полный доступ)', warehouse:'Склад', documents:'Документы'};
    // Роль отображается из имени (statusRole, Р1) — на сервере поля role нет.
    const roleLabels = {board: 'Колонка доски', writeoff: 'Очередь списания', archive: 'Архив'};
    const fields = {
        supplier: [['name','Название','text',true],['unp','УНП'],['contact_person','Контактное лицо'],['phone','Телефон','tel'],['email','Email','email'],['address','Адрес'],['note','Примечание','textarea']],
        user: [['username','Логин','text',true],['password','Пароль (минимум 8 символов; обязателен при создании)','password'],['role','Роль','select',true]],
        store: [['name','Название','text',true],['address','Адрес'],['phone','Телефон','tel']],
        // is_active (Р5) — переключатель видимости: снятый флаг убирает
        // колонку с доски; для нового статуса включён по умолчанию.
        status: [['name','Название','text',true],['color','Цвет','color'],['position','Позиция','number',true],['is_active','Активен','checkbox']]
    };
    const titles = {supplier:'Поставщик', user:'Пользователь', store:'Магазин', status:'Статус сделки'};
    let sequence = 10, editing, opener;
    // Состояние блока «Доступ пользователя» (низ этого модуля). Объявлено
    // здесь: render() вызывается при загрузке модуля, а render → isSelfUser
    // читает selfId, и let-биндинг ниже ещё не был бы инициализирован.
    let selfId = null;
    let usersLoading = false;
    const userBusy = new Set();
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
    // чтобы последующие правки попадали в ту же строку сервера. Цепочка
    // возвращается наружу: saveStatus дожидается её, чтобы перечитать
    // список из API уже после записи на сервер.
    function persist(kind, record, values) {
        const prefix = {supplier:'sup', user:'us', store:'store', status:'st'}[kind];
        const numeric = numericId(record && record.id);
        const payload = { id: kind === 'status' ? (record && record.serverId) || numeric : numeric, fields: values };
        return apiMutate(kind + '-save', payload).then(function (saved) {
            if (saved && saved.id != null && record && String(record.id).startsWith('local-')) {
                record.id = prefix + '-' + saved.id;
                if (saved.slug) record.id = saved.slug;
                if (kind === 'status') record.serverId = saved.id;
                render(kind);
                document.dispatchEvent(new Event(kind === 'supplier' ? 'kb:suppliers-changed' : 'kb:dictionaries-changed'));
            }
        }).catch(function (err) {
            // B1 fix: не глотаем ошибку сохранения — показываем тост.
            MGMT_TOAST('Не сохранено: ' + ((err && (err.detail || err.message)) || 'ошибка сохранения'), true);
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
        // Роль и признак «Отключён» для статусов рисует statusStateCell:
        // колонке «Назначение» нужен живой HTML (пилюля), а не esc-строка.
        if (kind === 'status') return [record.name, record.color, record.position];
        return [];
    }
    function render(kind) {
        const query = kind === 'supplier' ? $('mgmt-supplier-search').value.trim().toLocaleLowerCase('ru') : '';
        // V10 (аудит 19.09): us-none («Не назначен») — служебный
        // псевдопользователь для отображения ответственного на доске,
        // в списке пользователей админки ему не место.
        const rows = data[kind].filter(r => r.id !== 'us-none')
            .filter(r => Object.values(r).join(' ').toLocaleLowerCase('ru').includes(query));
        // «Погашение» строки отключённой записи — общее для пользователей и
        // статусов (is_active === false), css: [data-mgmt] tr.mgmt-off td.
        $('mgmt-'+kind+'-rows').innerHTML = rows.map(r => '<tr data-id="'+esc(r.id)+'"'+(isUserOff(r) ? ' class="mgmt-off"' : '')+'>'+cells(kind,r).map(v=>'<td>'+esc(v ?? '—')+'</td>').join('')+
            (kind === 'user' ? userAccessCell(r) : '')+
            (kind === 'status' ? statusStateCell(r) : '')+
            // «Изменить» — действие администратора в справочниках из ADMIN_MARKS
            // ниже (на словари и пользователей сервер ставит require_admin).
            // Поставщики — исключение: POST/PATCH /suppliers открыт и менеджеру
            // (это его операционная сущность, закупка), там кнопка видна всем.
            '<td'+(kind === 'supplier' ? '' : ' data-admin-only')+'>'+rowActions(kind,r)+'</td></tr>').join('');
        // Строки пересозданы вместе с метками — скрытие для них пересчитываем
        // сразу, иначе под manager оставались бы живые кнопки «Изменить».
        MGMT_ROLE.apply($('mgmt-'+kind+'-rows'));
        if (kind === 'supplier') {
            $('mgmt-supplier-count').textContent = rows.length;
            $('mgmt-supplier-empty').hidden = rows.length > 0;
        }
        if (kind === 'status') appendStatusStateRow();
    }

    function open(kind, id, button) {
        const record = data[kind].find(r=>r.id===id) || {role:'manager', color:'#626962', position:data.status.length};
        // Р3: имя канонического статуса — только чтение (см. CANON_NAMES выше);
        // цвет и позиция правятся свободно.
        const lockedName = kind === 'status' && isCanonName(record.name);
        editing = {kind, id}; opener = button;
        dialog.innerHTML = '<form id="mgmt-form"><h2 id="mgmt-title">'+titles[kind]+'</h2><div class="mgmt-fields">'+fields[kind].map(([key,label,type='text',required])=>{
            const attrs = ' id="mgmt-'+key+'" name="'+key+'"'+(required?' required':'');
            let control;
            if (type==='select') control='<select'+attrs+'>'+Object.entries(roles).map(([value,text])=>'<option value="'+value+'"'+(record[key]===value?' selected':'')+'>'+text+'</option>').join('')+'</select>';
            else if (type==='textarea') control='<textarea'+attrs+' maxlength="2000" rows="3">'+esc(record[key])+'</textarea>';
            else if (type==='checkbox') {
                // Р5: тот же вид переключателя, что в формах вебхуков
                // (.mgmt-check); для новой записи флаг включён по умолчанию.
                return '<label class="mgmt-check" for="mgmt-'+key+'"><input'+attrs+' type="checkbox"'+(record[key]!==false?' checked':'')+'><span>'+label+'</span></label>';
            }
            else control='<input'+attrs+' type="'+type+'" value="'+esc(record[key])+'"'+(type==='number'?' min="0" max="999" step="1"':' maxlength="300"')+(lockedName && key==='name'?' readonly':'')+'>';
            // Подсказка «почему нельзя» — под полем имени канонического статуса.
            const hint = lockedName && key==='name'
                ? '<span class="mgmt-note">Имя изменить нельзя: сделки хранят статус именем, сервер принимает только канонические имена (422), а переименование оставило бы карточки без колонки. Цвет и позиция правятся свободно.</span>'
                : '';
            return '<label for="mgmt-'+key+'"><span>'+label+'</span>'+control+hint+'</label>';
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
        // Статусы — отдельный путь (пункт 12): тело для сервера собирает
        // saveStatus (имя канона не отправляется, is_active отправляется),
        // после записи список перечитывается из API. catch — чтобы отказ
        // не превращался в необработанный rejection.
        if (kind==='status') { saveStatus(id, values).catch(e => console.warn('[mgmt] сохранение статуса не удалось:', e)); return; }
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
        $('mgmt-'+kind+'-rows').onclick=e=>{
            const button=e.target.closest('[data-edit], [data-user-toggle], [data-user-del]');
            if (!button) return;
            if (button.hasAttribute('data-user-toggle')) toggleUserAccess(button);
            else if (button.hasAttribute('data-user-del')) removeUser(button);
            else open(kind, button.dataset.edit, button);
        };
    });

    /* ── Раздел «Статусы сделок»: ленивая загрузка и сохранение (пункт 12) ─
       Источник раздела — прямой GET /dictionaries/statuses, он возвращает и
       неактивные строки (см. блок у data.status). Своего элемента состояния
       у карточки нет (в отличие от #mgmt-user-state), поэтому «Загрузка…»,
       подсказка превью и ошибка с «Повторить» рисуются строкой таблицы;
       сами режимы (statusesLoading/statusStateMode/statusStateReason)
       объявлены вверху модуля — до стартового render('status'). */
    function statusState(mode, reason) {
        statusStateMode = mode;
        statusStateReason = reason || '';
    }
    function appendStatusStateRow() {
        const host = $('mgmt-status-rows');
        if (!host) return;
        const old = document.getElementById('mgmt-status-state-row');
        if (old) old.remove();
        if (!statusStateMode) return;
        const tr = document.createElement('tr');
        tr.id = 'mgmt-status-state-row';
        const td = document.createElement('td');
        td.colSpan = 5;
        if (statusStateMode === 'loading') {
            td.className = 'mgmt-note';
            td.textContent = 'Загрузка статусов из CRM…';
        } else if (statusStateMode === 'preview') {
            td.className = 'mgmt-note';
            td.textContent = 'В предпросмотре макета сети нет: показан локальный снимок справочника. Полный список статусов, включая отключённые, работает только в собранной CRM.';
        } else {
            // Те же классы состояния, что у строки ошибки пользователей:
            // красный текст + «Повторить» (делегирование клика — в bind()
            // модуля MGMT_ADMIN ниже, ключ data-mgmt-retry="status").
            td.className = 'mgmt-hint mgmt-hint-error';
            td.innerHTML = '<span>Не удалось загрузить статусы из CRM: ' + esc(statusStateReason || 'ошибка сети') + '.</span>' +
                ' <button type="button" class="btn btn-ghost btn-sm" data-mgmt-retry="status">Повторить</button>';
        }
        tr.append(td);
        host.append(tr);
    }
    // Колонка «Назначение»: роль выводится из имени тем же правилом, что в
    // boot (Р1), неактивный статус помечается пилюлей (Р5).
    function statusStateCell(r) {
        const role = esc(roleLabels[statusRole(r.name)] || 'Колонка доски');
        return '<td>' + (r.is_active === false ? role + ' <span class="pill warn">Отключён</span>' : role) + '</td>';
    }
    // Новая (или включённая заново) запись общего справочника — тот же вид,
    // что у boot при сборке (v2-boot-template.js, buildKbData): id по
    // CANON_ID/statusSlug, роль из имени (Р1), назначать сделки можно только
    // каноническим — на неканоническое имя сервер отвечает 422.
    function kbStatusFromApi(row) {
        const canon = CANON_IDS[row.name] || null;
        return {
            id: canon || statusSlug(row.name),
            name: row.name,
            color: row.color || 'faint',
            position: row.position || 0,
            role: statusRole(row.name),
            serverId: row.id,
            canAssign: Boolean(canon)
        };
    }
    /* Перенос полного списка из API в общий KBData.statuses «на месте»: доска,
       фильтры и «Пульт дня» держат ссылку на этот массив (подменять его
       нельзя — см. комментарий у mergeUsers про applyInPlace), а событие
       kb:dictionaries-changed после сохранения просит их перерисоваться уже
       по свежим данным. У существующих записей id сохраняется, чтобы колонка
       переименованного (неканонического) статуса не «прыгала» на новый slug
       до перезагрузки страницы. */
    function syncKbStatuses(rows) {
        const kb = window.KBData && window.KBData.statuses;
        // Пустой список не трогаем: boot тогда сам собирает статусы из
        // хардкода STATUS_MAP (ветка пустого словаря) — стирать их нечего.
        if (!Array.isArray(kb) || !rows.length) return;
        const byServerId = {};
        kb.forEach(s => { if (s && s.serverId != null) byServerId[String(s.serverId)] = s; });
        const fresh = [];
        rows.forEach(row => {
            if (!row || row.id == null || row.is_active === false) return;
            const existing = byServerId[String(row.id)];
            if (existing) {
                existing.name = row.name;
                existing.color = row.color || 'faint';
                existing.position = row.position || 0;
                fresh.push(existing);
            } else {
                fresh.push(kbStatusFromApi(row));
            }
        });
        // Тот же порядок, что задаёт boot: position, затем id сервера.
        fresh.sort((a, b) => (a.position || 0) - (b.position || 0) || (a.serverId || 0) - (b.serverId || 0));
        kb.length = 0;
        fresh.forEach(s => kb.push(s));
    }
    // Полный список → данные раздела. Массив чистится «на месте»: render и
    // open читают data.status по ссылке.
    function applyStatusList(list) {
        const rows = Array.isArray(list) ? list : [];
        data.status.length = 0;
        rows.forEach(row => {
            if (row && row.id != null) data.status.push(statusRecordFromApi(row));
        });
        syncKbStatuses(rows);
    }
    async function fetchStatuses() {
        // Прямой GET: роут отдаёт ВСЕ строки, включая is_active=false
        // (dictionaries_router.list_statuses, сортировка по position, id).
        const list = await window.V2Api.api('/dictionaries/statuses');
        applyStatusList(list);
    }
    async function loadStatuses(force) {
        const host = $('mgmt-status-rows');
        if (!host) return;
        if (!MGMT_LAZY.online()) {
            // Предпросмотр макета и ?demo=1: сети нет — остаётся локальный
            // снимок KBData (см. data.status), раздел честно пишет об этом.
            statusState('preview');
            render('status');
            return;
        }
        // Повторный вызов при открытом запросе отбивается; saveStatus идёт
        // с force — перечитать после записи нужно обязательно.
        if (statusesLoading && !force) return;
        statusesLoading = true;
        statusState('loading');
        render('status');
        try {
            await fetchStatuses();
            statusState('');
        } catch (e) {
            statusState('error', userErrorText(e));
        } finally {
            statusesLoading = false;
            render('status');
        }
    }
    /* ── Сохранение статуса (пункт 12) ──────────────────────────────────
       Отличия от общего пути save(): имя канонического статуса в fields не
       отправляется (PATCH без ключа сервер трактует как «не менять», Р3),
       is_active отправляется всегда (Р5); оптимистичная правка касается
       только списка раздела, а общий KBData.statuses синхронизирует
       syncKbStatuses() по свежему ответу API — доска, фильтры и «Пульт
       дня» видят согласованный список (с учётом включения/выключения),
       а не промежуточное состояние. */
    async function saveStatus(id, values) {
        const record = data.status.find(r => r.id === id) || null;
        const creating = !record;
        const canonical = Boolean(record && record.canonical);
        const fields = {
            color: values.color || null,
            position: Number(values.position) || 0,
            // Чекбокс попадает в FormData только в отмеченном виде ('on').
            is_active: values.is_active != null
        };
        if (!canonical) fields.name = values.name;
        let local = record;
        if (local) {
            Object.assign(local, {
                name: canonical ? local.name : values.name,
                color: fields.color,
                position: fields.position,
                is_active: fields.is_active
            });
        } else {
            local = { id: 'local-' + sequence++, serverId: null, name: values.name, color: fields.color, position: fields.position, is_active: fields.is_active, canonical: false };
            data.status.push(local);
        }
        render('status');
        dialog.close();
        try {
            await persist('status', local, fields);
            // POST /dictionaries/statuses создаёт статус активным: в
            // DealStatusCreate (schemas.py:863) поля is_active нет, модель
            // ставит is_active=True. Снятый при создании флаг доводим
            // отдельным PATCH, иначе перечитывание показало бы «Активен»
            // вопреки форме.
            if (creating && fields.is_active === false && local.serverId != null) {
                await apiMutate('status-save', { id: local.serverId, fields: { is_active: false } });
            }
        } finally {
            // Перечитываем в любом случае: после успешной записи приходят
            // свежие данные, после ошибки — серверная правда вместо
            // оптимистичной правки.
            await loadStatuses(true);
            // Доска, фильтры и «Пульт дня» читают общий справочник — просим
            // перерисоваться тем же событием, что и раньше.
            document.dispatchEvent(new Event('kb:dictionaries-changed'));
        }
    }
    // Раздел открыли — тянем полный список с сервера: тот же ленивый пропуск,
    // что у пользователей (MGMT_LAZY зовёт загрузчик при показе #view-admin).
    MGMT_LAZY.onOpen(loadStatuses);

    /* ── Ролевая модель раздела (пункт 9 плана) ──────────────────────────
       Скрытое помечается одним атрибутом data-admin-only, а скрывает его одна
       функция V2Role.apply (v2-boot-template.js) — вместо «if (role)» у каждой
       кнопки. Список сверен с бэком, require_admin()/require_role
       ("admin","superadmin") закрывают: пользователей (auth_router), словари
       магазинов и статусов (dictionaries_router) и почту на запись
       (email_parser_router — POST /settings и POST /sync). Чтение справочников
       остаётся всем ролям, поэтому магазины и статусы скрыты только в части
       правки, а целиком уходят почта и «Пользователи» (список для не-админа =
       он сам). Поставщики не скрываются вовсе: POST/PATCH /suppliers менеджеру
       открыт, это его операционная сущность.
       Пункт 15 плана добавляет в этот же список экраны интеграции: у
       webhooks_router (routers/webhooks_router.py:88) и workflows_router
       (routers/workflows_router.py:21) require_admin() стоит зависимостью на
       всём роутере, то есть и чтение тоже админское; в custom_objects_router
       (:53) под админом создание/удаление типов и полей, а чтение — общее,
       поэтому карточка скрывается целиком по решению плана
       («права — только админ»). */
    const ADMIN_MARKS = [
        ['mgmt-mail-card', 'card'],         // вся карточка почты: синхронизация + настройка ящика
        ['mgmt-user-card', 'card'],         // карточка «Пользователи» целиком (список для не-админа = он сам)
        ['mgmt-wh-card', 'card'],           // вебхуки: весь роутер закрыт require_admin()
        ['mgmt-co-card', 'card'],           // кастомные объекты: типы и поля — админские
        ['mgmt-wf-card', 'card'],           // автоматизации: весь роутер закрыт require_admin()
        ['mgmt-store-new', 'self'],         // «+ Новый магазин»
        ['mgmt-status-new', 'self'],        // «+ Новый статус»
        ['mgmt-user-table', 'action'],      // колонка «Действия» — чтобы не висела пустой
        ['mgmt-store-table', 'action'],
        ['mgmt-status-table', 'action']
    ];
    function markAdminOnly(el) {
        if (el) el.setAttribute('data-admin-only', '');
    }
    ADMIN_MARKS.forEach(([id, what]) => {
        const anchor = $(id);
        if (!anchor) return;
        if (what === 'card') markAdminOnly(anchor.closest('.card'));
        else if (what === 'self') markAdminOnly(anchor);
        else {
            const head = anchor.querySelector('thead tr');
            markAdminOnly(head ? head.lastElementChild : null);
        }
    });
    // Подсказка вместо скрытых действий — раздел не должен выглядеть сломанным
    // или «недоделанным»: создаём один раз и только под не-админом.
    function ensureRoleNote(id, text, host) {
        let note = $(id);
        if (!note) {
            if (!host || MGMT_ROLE.isAdmin()) return;
            note = document.createElement('p');
            note.id = id;
            note.className = 'mgmt-role-note';
            note.textContent = text;
            host.insertBefore(note, host.firstChild);
        }
        note.hidden = MGMT_ROLE.isAdmin();
    }
    function refreshRoleUi() {
        ensureRoleNote('mgmt-role-notice',
            'Править справочники, пользователей, настройки почты и интеграции может только администратор. Здесь можно сменить свой пароль.',
            $('view-admin'));
        MGMT_ROLE.apply($('view-admin'));
    }
    // Вход под другим пользователем без F5 (bootApi() повторяется, модули уже
    // исполнены): справочники перечитываются из обновлённого KBData, скрытие
    // пересчитывается — иначе оно залипает в состоянии прошлого сеанса.
    document.addEventListener('v2:role', () => {
        Object.keys(data).forEach(kind => render(kind));
        refreshRoleUi();
        // Раздел мог быть открыт и до смены роли: админские списки
        // перечитываются тем же проходом, что и при обычном открытии.
        MGMT_LAZY.sync();
    });
    refreshRoleUi();
    MGMT_LAZY.onOpen(loadUsers);

    // Debounce: rebuild таблицы поставщиков не должен бежать на каждый
    // символ — ждём паузу в вводе 150 мс.
    let supplierSearchTimer = 0;
    $('mgmt-supplier-search').addEventListener('input',()=>{
        clearTimeout(supplierSearchTimer);
        supplierSearchTimer = setTimeout(()=>render('supplier'),150);
    });

    /* ── Доступ пользователя: деактивация и удаление (пункт 15 плана) ────
       Контракт сверен с routers/auth_router.py: GET /auth/users (:72) и
       GET /auth/me (:101) отдают is_active (schemas.py:22-29); переключение —
       PATCH /auth/users/{id} (:156) телом {"is_active": bool} под
       require_admin, «Нельзя отключить самого себя» — 400 (:171); удаление —
       DELETE /auth/users/{id} (:129), 400 на суперадмине и на себе, 404 на
       несуществующем. is_active загрузчик переносит в KBData.users сам
       (v2-boot-template.js), поэтому список при открытии раздела перечитывается
       с сервера только ради свежих данных — mergeUsers сливает их в ТОТ ЖЕ
       массив: доска, фильтры и «Клиент 360» держат ссылку на него (applyInPlace
       в boot.js), подменять его нельзя.
       «Свой» id берётся из V2Api.userId() — boot подтвердил его /auth/me при
       старте, второго запроса на каждое открытие раздела не нужно.
       В PATCH уходит одно поле is_active: UserUpdate трактует отсутствие
       поля как «не менять», так что переключатель не затирает роль и пароль.
       selfId/usersLoading/userBusy объявлены в начале модуля (TDZ). */
    function isUserOff(record) { return Boolean(record) && record.is_active === false; }
    // selfId === null (предпросмотр мокапа, ?demo=1, сбой /auth/me) — «это я»
    // не различить, поэтому защита двойная: кнопка не рисуется только когда id
    // известен, а отказ в обработчике плюс серверный 400 держат себя в целости
    // в любом случае.
    function isSelfUser(record) { return selfId != null && numericId(record.id) === selfId; }
    // Сервер не даёт обычному админу править учётку суперадмина (403 в
    // update_user и 400 в delete_user) — не показываем заведомо мёртвые кнопки.
    function canEditOwner() {
        return Boolean(window.V2Api && window.V2Api.currentRole && window.V2Api.currentRole() === 'superadmin');
    }

    function userErrorText(e) {
        if (e && e.unauthorized) return 'срок сеанса истёк — войдите заново';
        // Тексты auth_router уже русские («Нельзя отключить самого себя»,
        // «Нельзя изменить суперадмина») — показываем их причиной без перевода.
        if (e && typeof e.detail === 'string' && e.detail) return e.detail;
        if (e && e.status === 403) return 'недостаточно прав: пользователей правит только администратор';
        if (e && e.status === 404) return 'пользователь не найден — обновите список';
        // message у адаптера — «HTTP 500», пользователю это ничего не говорит:
        // код отдаём в развёрнутом виде, а message — только для сетевых сбоев
        // без статуса (fetch упал, DOM-ошибка и т. п.).
        if (e && e.status) return 'ошибка сервера (код ' + e.status + ')';
        if (e && e.message) return e.message;
        return 'ошибка сети';
    }

    // Строка состояния карточки: загрузка / предпросмотр / ошибка. В «ок»
    // прячется, чтобы не спорить с таблицей; вечной «Загрузки…» не остается.
    function usersState(mode, reason) {
        const el = $('mgmt-user-state');
        if (!el) return;
        el.classList.toggle('mgmt-hint-error', mode === 'error');
        if (mode === 'ok') { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        if (mode === 'loading') { el.textContent = 'Загрузка списка пользователей из CRM…'; return; }
        if (mode === 'preview') {
            el.textContent = 'В предпросмотре макета сети нет: список и переключатели доступа работают только в собранной CRM.';
            return;
        }
        el.innerHTML = 'Не удалось получить список пользователей: ' + esc(reason || 'ошибка сети') + '.' +
            ' <button type="button" class="btn btn-ghost btn-sm" data-mgmt-retry="user">Повторить</button>';
    }

    function mergeUsers(list) {
        const rows = Array.isArray(list) ? list : [];
        let added = 0;
        rows.forEach(su => {
            if (!su || su.id == null) return;
            const id = 'us-' + su.id;
            let record = data.user.find(r => r.id === id);
            if (!record) {
                // Учётку могли создать в другой сессии, когда KBData уже
                // собран: добавляем в справочник, иначе доска и фильтры
                // не знают имени ответственного.
                record = {
                    id: id,
                    username: su.username || String(su.id),
                    full_name: su.username || '',
                    initials: String(su.username || '··').slice(0, 2).toUpperCase(),
                    role: su.role || 'manager'
                };
                data.user.push(record);
                added += 1;
            }
            if (su.username) record.username = su.username;
            if (su.role) record.role = su.role;
            record.is_active = su.is_active !== false;
        });
        return added;
    }

    async function loadUsers() {
        if (!$('mgmt-user-rows')) return;
        if (!MGMT_LAZY.online()) { usersState('preview'); return; }
        if (usersLoading) return;
        usersLoading = true;
        usersState('loading');
        try {
            // «Это я» отличаем по id, который boot подтвердил /auth/me при старте
            // (V2Api.userId): сервер на переключение своей учётки отвечает 400,
            // так что без него лучше не показывать кнопку вовсе. Отдельного
            // /auth/me на каждое открытие раздела больше нет.
            selfId = (window.V2Api && window.V2Api.userId) ? window.V2Api.userId() : null;
            const list = await window.V2Api.api('/auth/users');
            const added = mergeUsers(list);
            usersState('ok');
            render('user');
            if (added) document.dispatchEvent(new Event('kb:dictionaries-changed'));
        } catch (e) {
            usersState('error', userErrorText(e));
            render('user');   // остаёмся на локальном снимке справочника
        } finally {
            usersLoading = false;
        }
    }

    function userAccessCell(record) {
        const off = isUserOff(record);
        return '<td><span class="pill ' + (off ? 'warn' : 'ok') + '">' + (off ? 'Отключён' : 'Активен') + '</span>' +
            (isSelfUser(record) ? ' <span class="mgmt-note">это вы</span>' : '') + '</td>';
    }

    function editButton(record, name) {
        return '<button type="button" class="btn btn-ghost btn-sm" data-edit="' + esc(record.id) +
            '" aria-label="Редактировать ' + name + '">Изменить</button>';
    }

    function rowActions(kind, record) {
        if (kind !== 'user') return editButton(record, esc(record.name || record.username));
        const name = esc(record.username);
        if (record.role === 'superadmin' && !canEditOwner()) {
            return '<span class="mgmt-note">Учётка владельца — правит только суперадмин</span>';
        }
        const buttons = [editButton(record, 'пользователя ' + name)];
        // Себя сервер не даст ни отключить (400 «Нельзя отключить самого
        // себя»), ни удалить (400 «Нельзя удалить самого себя») — кнопки нет.
        if (!isSelfUser(record)) {
            const off = isUserOff(record);
            buttons.push('<button type="button" class="btn btn-ghost btn-sm" data-user-toggle="' + esc(record.id) +
                '" aria-label="' + (off ? 'Включить' : 'Отключить') + ' пользователя ' + name + '">' +
                (off ? 'Включить' : 'Отключить') + '</button>');
            buttons.push('<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-user-del="' + esc(record.id) +
                '" aria-label="Удалить пользователя ' + name + '">Удалить</button>');
        }
        return '<div class="mgmt-acts">' + buttons.join('') + '</div>';
    }

    function needNetwork(what) {
        if (window.V2Api && window.V2Api.token()) return true;
        MGMT_TOAST(what + ' недоступно в предпросмотре макета: нужен вход в CRM.', true);
        return false;
    }
    // Подтверждение опасного действия — тот же диалог, что у доски
    // (KBConfirm.ask, shell-v2-board.js): форма-диалог, не confirm() и не
    // prompt(); фокус после закрытия возвращается в строку таблицы.
    function askConfirm(opts) {
        if (!window.KBConfirm || !window.KBConfirm.ask) {
            MGMT_TOAST('Диалог подтверждения не открылся — обновите страницу.', true);
            return Promise.resolve(false);
        }
        return window.KBConfirm.ask(opts);
    }

    async function toggleUserAccess(button) {
        const id = button.dataset.userToggle;
        const record = data.user.find(r => r.id === id);
        if (!record || userBusy.has(id) || !needNetwork('Смена доступа')) return;
        // Кнопки своей строки не рисуем, но проверка здесь — чтобы защита не
        // зависела от того, успел ли отработать рендер (selfId подтверждён
        // /auth/me на старте и может оказаться null — тогда себя различить
        // нечем, и остаётся этот отказ плюс серверный 400).
        if (isSelfUser(record)) { MGMT_TOAST('Нельзя отключить самого себя: в CRM должен остаться администратор.', true); return; }
        const numeric = numericId(id);
        if (numeric == null) { MGMT_TOAST('Пользователь ещё не сохранён в CRM — сначала создайте его.', true); return; }
        userBusy.add(id);
        button.disabled = true;
        const turnOff = !isUserOff(record);
        try {
            const ok = await askConfirm({
                title: turnOff ? 'Отключить пользователя?' : 'Включить пользователя?',
                message: turnOff
                    ? 'Учётка «' + record.username + '» перестанет входить в CRM. Карточки, задачи и история останутся на месте; включить можно этим же переключателем.'
                    : 'Учётка «' + record.username + '» снова сможет входить в CRM.',
                confirmText: turnOff ? 'Отключить' : 'Включить',
                cancelText: 'Отмена'
            });
            if (!ok) { button.disabled = false; return; }
            const res = await window.V2Api.api('/auth/users/' + numeric, { method: 'PATCH', body: { is_active: !turnOff } });
            record.is_active = (res && typeof res.is_active === 'boolean') ? res.is_active : !turnOff;
            render('user');
            // Флаг поменялся в общем справочнике KBData.users — тем же объектом
            // пользуются доска и «Пульт дня», где отключённый ответственный
            // приглушён: перерисовываем их уже привычным событием (его шлют
            // создание и удаление учётки), без своего канала.
            document.dispatchEvent(new Event('kb:dictionaries-changed'));
            MGMT_TOAST(turnOff
                ? 'Пользователь «' + record.username + '» отключён: вход закрыт, данные остались.'
                : 'Пользователь «' + record.username + '» снова активен.');
        } catch (e) {
            button.disabled = false;
            MGMT_TOAST('Не переключено: ' + userErrorText(e), true);
        } finally {
            userBusy.delete(id);
        }
    }

    async function removeUser(button) {
        const id = button.dataset.userDel;
        const record = data.user.find(r => r.id === id);
        if (!record || userBusy.has(id) || !needNetwork('Удаление пользователя')) return;
        if (isSelfUser(record)) { MGMT_TOAST('Нельзя удалить самого себя — удаление учётки необратимо.', true); return; }
        const numeric = numericId(id);
        if (numeric == null) { MGMT_TOAST('Пользователь ещё не сохранён в CRM.', true); return; }
        userBusy.add(id);
        button.disabled = true;
        try {
            const ok = await askConfirm({
                title: 'Удалить пользователя?',
                message: 'Учётка «' + record.username + '» будет удалена безвозвратно: её снимут с карточек, задач и ленты событий. Если нужно только закрыть доступ — выберите «Отмена» и отключите пользователя.',
                confirmText: 'Удалить',
                cancelText: 'Отмена'
            });
            if (!ok) { button.disabled = false; return; }
            await window.V2Api.api('/auth/users/' + numeric, { method: 'DELETE' });
            const index = data.user.indexOf(record);
            if (index >= 0) data.user.splice(index, 1);
            render('user');
            document.dispatchEvent(new Event('kb:dictionaries-changed'));
            MGMT_TOAST('Пользователь «' + record.username + '» удалён.');
        } catch (e) {
            button.disabled = false;
            MGMT_TOAST('Не удалено: ' + userErrorText(e), true);
        } finally {
            userBusy.delete(id);
        }
    }

    return {
        data: data,
        render: render,
        loadUsers: loadUsers,
        loadStatuses: loadStatuses,
        mergeUsers: mergeUsers,
        userErrorText: userErrorText,
        userAccessCell: userAccessCell,
        rowActions: rowActions,
        isUserOff: isUserOff,
        askConfirm: askConfirm,
        needNetwork: needNetwork,
        toggleUserAccess: toggleUserAccess,
        removeUser: removeUser
    };
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
        MGMT_TOAST(text, isError);
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
        if (!window.V2Api || !window.V2Api.token()) return;
        // Карточка почты — админская и скрыта (метки data-admin-only в первом
        // модуле файла): под manager запрос настроек не нужен и не должен
        // мелькать в сети и в ошибках.
        if (!window.V2Api.isAdmin()) return;
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
                const hint = body.querySelector('[data-mail-error]') || (() => { const p = document.createElement('p'); p.className = 'mgmt-mail-error'; p.setAttribute('data-mail-error', ''); body.prepend(p); return p; })();
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

    // Первичная загрузка: колокольчик сразу, карточка почты — при входе в Admin
    // (тот же ленивый пропуск, что и для экранов пункта 15: см. MGMT_LAZY).
    refreshBellCount();
    setInterval(refreshBellCount, 60000);
    MGMT_LAZY.onOpen(loadMail);
})();

/* ============================================================
   Пункт 15 плана: экраны интеграции — вебхуки, кастомные объекты,
   автоматизации. Все три админские и ленивые: загрузчики регистрирует
   MGMT_LAZY, карточки помечены data-admin-only через ADMIN_MARKS.
   Сеть — напрямую через window.V2Api.api(): новых видов мутаций в
   boot-шаблон не заводим (файл занят другой задачей).
   Все правки уходят PATCH'ом с минимальным телом: сервер гейтит намерение по
   присутствию ключа (model_fields_set) — «нет ключа = не менять», «null =
   очистить там, где колонка nullable». Отправлять все поля подряд нельзя:
   это молча стирало бы иконку, описание и подпись.

   Контракты сверены с бэком:
   • routers/webhooks_router.py — GET /webhooks/events · GET/POST /webhooks/
     · PATCH/DELETE /webhooks/{id} · POST /webhooks/{id}/test; require_admin()
     стоит зависимостью на всём роутере (:88).
   • routers/custom_objects_router.py — GET/POST /custom/objects ·
     PATCH/DELETE /custom/objects/{id} · GET/POST /custom/objects/{id}/fields ·
     PATCH/DELETE /custom/fields/{id}; под админом создание, правка и
     удаление (:182).
     Записи (/custom/objects/{id}/records, /custom/records/{id}) на экране
     не показаны: это рабочие данные, а не конфигурация.
   • routers/workflows_router.py — GET/POST /workflows/ · PATCH/DELETE
     /workflows/{id} · GET /workflows/{id}/triggers|steps|runs ·
     DELETE /workflows/triggers/{id} · DELETE /workflows/steps/{id}.
     Роутера с запуском сценария на сервере нет (есть только журнал запусков),
     поэтому «запустить» в UI тоже нет.
   ============================================================ */
const MGMT_ADMIN = (function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const api = (path, opts) => window.V2Api.api(path, opts);
    const online = () => MGMT_LAZY.online();
    const askConfirm = (opts) => MGMT_DICT.askConfirm(opts);

    /* ── Общая обвязка состояний, ошибок и форм ──────────────────────── */

    // mode: 'loading' | 'empty' | 'preview' | 'error' | 'ok'. В 'ok' строка
    // прячется, в остальных случаях текст обязателен — «Загрузка…» навсегда
    // не остаётся, потому что каждый загрузчик обязан вызвать stateLine и
    // в ветке успеха, и в catch.
    function stateLine(id, mode, texts) {
        const el = $(id);
        if (!el) return;
        const t = texts || {};
        el.classList.toggle('mgmt-hint-error', mode === 'error');
        if (mode === 'ok') { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        if (mode === 'loading' || mode === 'empty' || mode === 'preview') {
            el.textContent = t[mode] || 'Данных нет.';
            return;
        }
        el.innerHTML = '<span>' + esc(t.error || 'Запрос не прошёл.') + '</span>' +
            (t.retry ? ' <button type="button" class="btn btn-ghost btn-sm" data-mgmt-retry="' + esc(t.retry) + '">Повторить</button>' : '');
    }

    // Серверные тексты интеграционных роутов частично английские (валидация
    // SSRF в webhooks_router) — подписываем их по-русски, остальное отдаем как
    // есть: там, где деталь есть, она честнее любого перевода.
    const RU_DETAIL = [
        [/must use http or https/i, 'адрес должен быть на http или https'],
        [/hostname is not allowed/i, 'такой хост запрещён: внутренние и служебные адреса CRM вызывать не должны'],
        [/private\/reserved IP/i, 'адрес указывает во внутренний или зарезервированный IP — такой вебхук сервер не примет'],
        [/could not be resolved/i, 'имя хоста не распознано — проверьте адрес'],
        [/не найден/i, null]
    ];
    function reasonText(e) {
        if (e && e.unauthorized) return 'срок сеанса истёк — войдите заново';
        const detail = (e && typeof e.detail === 'string') ? e.detail : '';
        for (let i = 0; i < RU_DETAIL.length; i += 1) {
            if (detail && RU_DETAIL[i][0].test(detail)) {
                const mapped = RU_DETAIL[i][1];
                if (mapped) return mapped + ' (' + detail + ')';
                break;
            }
        }
        if (detail) return detail;
        if (e && e.status === 403) return 'недостаточно прав: экран только для администратора';
        if (e && e.status === 404) return 'запись не найдена — обновите список';
        // message у адаптера — «HTTP 500»: код отдаём развёрнуто, а message
        // оставляем для сбоев без статуса (упал fetch, ошибка сети).
        if (e && e.status) return 'ошибка сервера (код ' + e.status + ')';
        if (e && e.message) return e.message;
        return 'ошибка сети';
    }

    // Русская деталь сервера («…или сначала очистите записи.») часто приходит
    // с точкой на конце — вторая от обёртки не нужна.
    const sentence = (text) => (/[.!?…]$/.test(text) ? text : text + '.');

    const busy = new Set();
    // failPrefix — окончание фразы («Не сохранено», «Не удалено»), поэтому для
    // офлайн-ветки он не годится: там своё, законченное сообщение.
    function run(key, button, failPrefix, action) {
        if (!window.V2Api || !window.V2Api.token()) {
            MGMT_TOAST('Действие недоступно без входа в CRM: предпросмотр макета работает без сети.', true);
            return Promise.resolve();
        }
        if (busy.has(key)) return Promise.resolve();   // защита от двойного клика
        busy.add(key);
        if (button) button.disabled = true;
        return Promise.resolve()
            .then(action)
            .catch((e) => {
                if (button) button.disabled = false;
                MGMT_TOAST(failPrefix + ': ' + reasonText(e), true);
            })
            .then(() => { busy.delete(key); });
    }

    let extDialog = null;
    let extResolve = null;
    let extOpener = null;
    function extDialogEl() {
        if (extDialog) return extDialog;
        extDialog = document.createElement('dialog');
        extDialog.id = 'mgmt-ext-dialog';
        extDialog.className = 'v2-form-dialog';
        extDialog.setAttribute('aria-labelledby', 'mgmt-ext-title');
        extDialog.addEventListener('close', () => {
            const settle = extResolve;
            extResolve = null;
            if (settle) settle(null);         // закрыли без сохранения: Esc или «Отмена»
            if (extOpener && extOpener.isConnected) extOpener.focus();
            extOpener = null;
            // Тот же B4-паттерн, что у #mgmt-dialog: форму чистим, иначе
            // скрытые required-поля блокируют следующий сабмит.
            extDialog.innerHTML = '';
            if (window.KBSelect) window.KBSelect.close();
        });
        document.body.append(extDialog);
        return extDialog;
    }

    function controlHtml(f, value) {
        const id = 'mgmt-ext-' + f.key;
        const required = f.required ? ' required' : '';
        if (f.type === 'select') {
            return '<select id="' + id + '" name="' + f.key + '"' + required + '>' +
                f.options.map((pair) => '<option value="' + esc(pair[0]) + '"' +
                    (String(pair[0]) === String(value ?? '') ? ' selected' : '') + '>' + esc(pair[1]) + '</option>').join('') +
                '</select>';
        }
        if (f.type === 'textarea') {
            return '<textarea id="' + id + '" name="' + f.key + '" rows="3" maxlength="2000">' + esc(value ?? '') + '</textarea>';
        }
        if (f.type === 'checks') {
            const picked = Array.isArray(value) ? value : [];
            return '<div class="mgmt-checks">' + f.options.map((pair) =>
                '<label class="mgmt-check"><input type="checkbox" name="' + f.key + '" value="' + esc(pair[0]) + '"' +
                (picked.indexOf(pair[0]) >= 0 ? ' checked' : '') + '><span>' + esc(pair[1]) + '</span></label>').join('') + '</div>';
        }
        if (f.type === 'flag') {
            return '<label class="mgmt-check"><input type="checkbox" id="' + id + '" name="' + f.key + '"' +
                (value ? ' checked' : '') + '><span>' + esc(f.label) + '</span></label>';
        }
        return '<input id="' + id + '" name="' + f.key + '" type="' + (f.type || 'text') + '"' + required +
            ' value="' + esc(value ?? '') + '" ' +
            // maxlength у number-полей невалиден (браузер его игнорирует), а
            // порядок поля — целое неотрицательное: те же границы, что у поля
            // «Позиция» в справочнике статусов выше.
            (f.type === 'number' ? 'min="0" step="1"' : 'maxlength="' + (f.maxlength || 300) + '"') +
            (f.placeholder ? ' placeholder="' + esc(f.placeholder) + '"' : '') +
            (f.autocomplete ? ' autocomplete="' + esc(f.autocomplete) + '"' : '') + '>';
    }

    function fieldHtml(f, value) {
        const control = controlHtml(f, value);
        if (f.type === 'flag') return control;
        if (f.type === 'checks') {
            // Группу чекбоксов в <label for> не оборачиваем: общей цели у
            // такой подписи нет, а у каждого пункта она уже внутри.
            return '<div class="mgmt-span"><span class="mgmt-span-label">' + esc(f.label) + '</span>' + control + '</div>';
        }
        return '<label for="mgmt-ext-' + esc(f.key) + '"><span>' + esc(f.label) + '</span>' + control + '</label>';
    }

    function readForm(form, fields) {
        const out = {};
        fields.forEach((f) => {
            if (f.type === 'checks') {
                out[f.key] = [].slice.call(form.querySelectorAll('input[name="' + f.key + '"]:checked')).map((cb) => cb.value);
                return;
            }
            const el = form.elements[f.key];
            if (!el) { out[f.key] = f.type === 'flag' ? false : ''; return; }
            if (f.type === 'flag') { out[f.key] = el.checked === true; return; }
            out[f.key] = String(el.value).trim();
        });
        return out;
    }

    // Форма-диалог вместо цепочки prompt(): промис с заполненными значениями
    // или null, если закрыли без сохранения.
    // opts.submit — асинхронная отправка, нужна правкам, где часть проверок
    // живёт только на сервере (смена типа поля, чужой tenant, занятость имени).
    // Тогда диалог НЕ закрывается, пока запрос в пути, а 400 показывается в
    // самой форме: человек правит поле, а не заполняет форму заново. Без opts.submit
    // поведение прежнее — закрытие по сабмиту и ошибка в общем тосте.
    function formDialog(opts) {
        if (!MGMT_DICT.needNetwork(opts.what || 'Форма')) return Promise.resolve(null);
        const dlg = extDialogEl();
        extOpener = document.activeElement;
        const values = opts.values || {};
        let settle;
        const result = new Promise((resolve) => { settle = resolve; });
        extResolve = settle;
        dlg.innerHTML = '<form id="mgmt-ext-form"><h2 id="mgmt-ext-title">' + esc(opts.title) + '</h2>' +
            '<div class="mgmt-fields">' + opts.fields.map((f) => fieldHtml(f, values[f.key])).join('') + '</div>' +
            (opts.hint ? '<p class="mgmt-form-hint">' + esc(opts.hint) + '</p>' : '') +
            '<p class="kb-form-error" id="mgmt-ext-error" role="alert"></p>' +
            '<div class="mgmt-actions">' +
            '<button class="btn btn-ghost" type="button" id="mgmt-ext-cancel">Отмена</button>' +
            '<button class="btn btn-primary" type="submit" id="mgmt-ext-submit">' + esc(opts.submitText || 'Сохранить') + '</button>' +
            '</div></form>';
        dlg.showModal();
        if (window.KBSelect) window.KBSelect.enhance(dlg);
        const form = $('mgmt-ext-form');
        $('mgmt-ext-cancel').onclick = () => dlg.close();
        form.onsubmit = (event) => {
            event.preventDefault();
            const read = readForm(form, opts.fields);
            const wrong = opts.validate ? opts.validate(read) : '';
            if (wrong) { $('mgmt-ext-error').textContent = wrong; return; }
            if (opts.submit) {
                const send = $('mgmt-ext-submit');
                send.disabled = true;                 // Enter в поле не продублирует запрос
                $('mgmt-ext-error').textContent = '';
                Promise.resolve().then(() => opts.submit(read)).then((outcome) => {
                    const done = extResolve;
                    extResolve = null;
                    dlg.close();
                    if (outcome === 'unchanged') MGMT_TOAST('Ничего не изменилось — запрос не отправлен.');
                    if (done) done(read);
                }).catch((e) => {
                    send.disabled = false;
                    $('mgmt-ext-error').textContent = 'Не сохранено: ' + sentence(reasonText(e));
                });
                return;
            }
            const done = extResolve;
            extResolve = null;
            dlg.close();
            if (done) done(read);
        };
        return result;
    }

    // Общий шаг правки: тело уже собрано из изменённых ключей. Пустое тело —
    // «правка нулевая»: запрос не уходит вовсе (PATCH с {} ничего не меняет,
    // но крутил бы сеть и показывал бы «сохранено» без изменения).
    function submitPatch(body, path, done) {
        if (!Object.keys(body).length) return Promise.resolve('unchanged');
        return api(path, { method: 'PATCH', body: body }).then((res) => {
            done(res || {}, body);
            return 'saved';
        });
    }

    function when(iso) {
        if (!iso) return '';
        const date = new Date(iso);
        return isNaN(date.getTime()) ? String(iso) : date.toLocaleString('ru-RU');
    }
    const NAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/;

    /* ── Вебхуки ──────────────────────────────────────────────────────── */

    const EVENT_RU = {
        'card.created': 'Сделка создана', 'card.updated': 'Сделка изменена', 'card.deleted': 'Сделка удалена',
        'client.created': 'Клиент создан', 'client.updated': 'Клиент изменён', 'client.deleted': 'Клиент удалён',
        'supplier.created': 'Поставщик создан', 'supplier.updated': 'Поставщик изменён', 'supplier.deleted': 'Поставщик удалён',
        'payment.created': 'Оплата проведена', 'payment.updated': 'Оплата изменена',
        'document.created': 'Документ выписан', 'document.updated': 'Документ изменён'
    };
    const eventLabel = (name) => EVENT_RU[name] || name;
    const WH = { items: [], events: [], loading: false };

    // Тело запроса: secret отправляем только заполненным — на сервере
    // «нет поля = не менять» (webhooks_router.py:149-152), пустая строка
    // стёрла бы существующую подпись.
    function webhookBody(values) {
        const body = { url: values.url, events: values.events };
        if (values.secret) body.secret = values.secret;
        return body;
    }
    function validateWebhook(values) {
        if (!/^https?:\/\/\S+$/i.test(values.url)) return 'Укажите полный адрес, например https://example.com/crm-hook';
        if (!values.events.length) return 'Выберите хотя бы одно событие.';
        return '';
    }
    // Тело PATCH существующего вебхука — только изменённые ключи (webhooks_router.py
    // переведён на model_fields_set: нет ключа = не трогать). url и events на
    // сервере NOT NULL, поэтому null в них не шлём никогда; secret наоборот —
    // единственный способ снять подпись это явный null, пустая строка даёт 400
    // («чтобы снять подпись, пришлите null»), а незаполненное поле просто
    // не попадает в тело (см. webhookBody выше).
    function webhookPatchBody(h, values) {
        const body = {};
        const url = values.url.trim();
        const secret = values.secret.trim();
        if (url && url !== h.url) body.url = url;
        const events = Array.isArray(values.events) ? values.events : [];
        const current = Array.isArray(h.events) ? h.events : [];
        if (events.join(',') !== current.join(',')) body.events = events;
        if (values.clear_secret) body.secret = null;
        else if (secret) body.secret = secret;
        return body;
    }
    // Правка: пустой список событий сервер разрешает («ни на что не подписан»,
    // webhooks_router.py:179-185) — в отличие от создания, где такой вебхук бессмыслен.
    function validateWebhookEdit(values) {
        if (!/^https?:\/\/\S+$/i.test(values.url)) return 'Укажите полный адрес, например https://example.com/crm-hook';
        if (values.clear_secret && values.secret.trim()) return 'Выберите одно: новый секрет или снятие подписи.';
        return '';
    }
    // Ответ PATCH — «{"message": "Обновлено"}», без данных вебхука, поэтому
    // строку списка правим на месте по отправленному телу (тот же приём, что у
    // выключателя вебхука ниже), а не перечитываем раздел запросом.
    function applyWebhookPatch(h, body) {
        if ('url' in body) h.url = body.url;
        if ('events' in body) h.events = body.events;
        if ('secret' in body) h.secret = body.secret;
        renderWebhooks();
    }
    function webhookRow(h) {
        const id = esc(h.id);
        const url = esc(h.url);
        const events = (Array.isArray(h.events) ? h.events : []).map(eventLabel);
        const on = h.is_active !== false;
        return '<tr data-id="' + id + '">' +
            '<td>' + url + '</td>' +
            '<td>' + (events.length ? esc(events.join(', ')) : '—') + '</td>' +
            '<td><span class="pill ' + (on ? 'ok' : 'warn') + '">' + (on ? 'Активен' : 'Выключен') + '</span></td>' +
            '<td data-admin-only><div class="mgmt-acts">' +
            '<button type="button" class="btn btn-ghost btn-sm" data-wh-edit="' + id + '" aria-label="Изменить вебхук ' + url + '">Изменить</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-wh-test="' + id + '" aria-label="Проверить вебхук ' + url + '">Тест</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-wh-toggle="' + id + '" aria-label="' + (on ? 'Выключить' : 'Включить') + ' вебхук ' + url + '">' + (on ? 'Выключить' : 'Включить') + '</button>' +
            '<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-wh-del="' + id + '" aria-label="Удалить вебхук ' + url + '">Удалить</button>' +
            '</div></td></tr>';
    }
    function renderWebhooks() {
        const rows = $('mgmt-wh-rows');
        if (!rows) return;
        rows.innerHTML = WH.items.map(webhookRow).join('');
        // Строки пересозданы вместе с метками data-admin-only — скрытие
        // пересчитываем тем же проходом, что и справочники выше.
        MGMT_ROLE.apply(rows);
        stateLine('mgmt-wh-state', WH.items.length ? 'ok' : 'empty', {
            empty: 'Вебхуков пока нет. Нажмите «+ Новый вебхук» — и CRM начнёт сообщать внешней системе о сделках, клиентах, оплатах и документах.'
        });
    }
    function loadWebhooks() {
        const rows = $('mgmt-wh-rows');
        if (!rows) return Promise.resolve();
        if (!online()) {
            rows.innerHTML = '';
            stateLine('mgmt-wh-state', 'preview', { preview: 'В предпросмотре макета сети нет: список и настройки вебхуков работают только в собранной CRM.' });
            return Promise.resolve();
        }
        if (WH.loading) return Promise.resolve();
        WH.loading = true;
        stateLine('mgmt-wh-state', 'loading', { loading: 'Загрузка вебхуков…' });
        return Promise.all([api('/webhooks/'), api('/webhooks/events').catch(() => [])])
            .then((res) => {
                WH.items = Array.isArray(res[0]) ? res[0] : [];
                WH.events = Array.isArray(res[1]) ? res[1] : [];
                renderWebhooks();
            })
            .catch((e) => {
                WH.items = [];
                rows.innerHTML = '';
                stateLine('mgmt-wh-state', 'error', { error: 'Не удалось загрузить вебхуки: ' + reasonText(e) + '.', retry: 'wh' });
            })
            .then(() => { WH.loading = false; });
    }
    function openWebhookForm(h, button) {
        if (!WH.events.length) {
            MGMT_TOAST('Список событий не получен — обновите раздел и попробуйте снова.', true);
            loadWebhooks();
            return Promise.resolve();
        }
        const fields = [
            { key: 'url', label: 'URL получателя', type: 'url', required: true, maxlength: 500, placeholder: 'https://example.com/crm-hook' },
            { key: 'secret', label: 'Секрет подписи' + (h ? ' (оставьте пустым, чтобы не менять)' : ' (не обязательно)'), type: 'password', autocomplete: 'new-password', maxlength: 200, placeholder: 'Заголовок X-Webhook-Signature' },
            { key: 'events', label: 'События, по которым CRM шлёт запрос', type: 'checks', options: WH.events.map((name) => [name, eventLabel(name)]) }
        ];
        // Явный жест снятия подписи: GET /webhooks/ секрет не отдаёт
        // (webhooks_router.py:114-121), поэтому «пустое поле секрета» ничего не
        // значит и снять подпись можно только отметкой — она шлёт secret: null.
        if (h) fields.push({ key: 'clear_secret', label: 'Снять подпись: секрет больше не используется', type: 'flag' });
        return formDialog({
            title: h ? 'Вебхук: изменение' : 'Новый вебхук',
            what: 'Сохранение вебхука',
            submitText: 'Сохранить',
            values: h ? { url: h.url, secret: '', events: Array.isArray(h.events) ? h.events : [], clear_secret: false } : {},
            validate: h ? validateWebhookEdit : validateWebhook,
            hint: h ? 'CRM не показывает текущий секрет — он хранится, чтобы подписывать запросы заголовком X-Webhook-Signature. Оставьте поле пустым, чтобы оставить секрет как есть; чтобы отказаться от подписи совсем, отметьте «Снять подпись». Снять события можно все до нуля: такой вебхук ни на что не реагирует, но остаётся настроенным.' : '',
            fields: fields,
            submit: h ? (values) => submitPatch(webhookPatchBody(h, values), '/webhooks/' + h.id, (res, body) => {
                applyWebhookPatch(h, body);
                MGMT_TOAST('secret' in body && body.secret === null
                    ? 'Подпись снята: CRM больше не добавляет к запросам X-Webhook-Signature.'
                    : 'Вебхук сохранён.');
            }) : null
        }).then((values) => {
            if (!values || h) return null;      // правка уже применена в submit
            return run('wh-save:' + values.url, button, 'Не сохранено', () =>
                api('/webhooks/', { method: 'POST', body: webhookBody(values) }).then(loadWebhooks));
        });
    }
    function toggleWebhook(h, button) {
        const next = h.is_active === false;
        return run('wh-toggle:' + h.id, button, 'Не переключено', () =>
            api('/webhooks/' + h.id, { method: 'PATCH', body: { is_active: next } })
                .then(() => {
                    h.is_active = next;
                    renderWebhooks();
                    MGMT_TOAST(next ? 'Вебхук включён.' : 'Вебхук выключен: запросы не отправляются, настройки сохранены.');
                }));
    }
    function testWebhook(h, button) {
        // Подпись кнопки не меняем: сервер ждёт ответа до 10 секунд, а при
        // неудаче строка не перерисовывается — осталась бы «Отправляем…».
        // Достаточно того, что кнопка заблокирована на время запроса (run()),
        // а о начале говорит отдельный тост.
        MGMT_TOAST('Отправляем тестовый запрос на ' + h.url + '…');
        return run('wh-test:' + h.id, button, 'Проверка не прошла', () =>
            api('/webhooks/' + h.id + '/test', { method: 'POST' }).then((res) => {
                const result = res || {};
                const code = result.status ? ' (код ' + result.status + ')' : '';
                if (result.success) MGMT_TOAST('Вебхук ответил: получатель принял тестовый запрос' + code + '.');
                else MGMT_TOAST('Вебхук не ответил' + code + ': ' + (result.error ? reasonText({ detail: String(result.error) }) : 'получатель вернул ошибку'), true);
            }));
    }
    function removeWebhook(h, button) {
        return askConfirm({
            title: 'Удалить вебхук?',
            message: 'CRM перестанет слать запросы на ' + h.url + '. Настройки восстановить нельзя — если интеграция нужна позже, проще выключить вебхук.',
            confirmText: 'Удалить',
            cancelText: 'Отмена'
        }).then((ok) => {
            if (!ok) return null;
            return run('wh-del:' + h.id, button, 'Не удалено', () =>
                api('/webhooks/' + h.id, { method: 'DELETE' }).then(() => {
                    WH.items = WH.items.filter((x) => String(x.id) !== String(h.id));
                    renderWebhooks();
                    MGMT_TOAST('Вебхук удалён.');
                }));
        });
    }
    const findWebhook = (id) => WH.items.filter((x) => String(x.id) === String(id))[0];

    /* ── Кастомные объекты: типы и их поля ───────────────────────────── */

    const FIELD_TYPE_RU = { text: 'Текст', number: 'Число', currency: 'Сумма', date: 'Дата', boolean: 'Да / нет', select: 'Список', relation: 'Связь', file: 'Файл' };
    const CO = { types: [], fields: [], selected: null, loading: false, loadingId: null, fieldsLoading: false };
    const fieldTypeName = (type) => FIELD_TYPE_RU[type] || type || '—';
    const currentType = () => (CO.selected == null ? null : CO.types.filter((t) => String(t.id) === String(CO.selected))[0] || null);
    // Набор и порядок — как FIELD_TYPES на сервере (custom_objects_router.py:69).
    // В форму СОЗДАНИЯ поля «Связь» и «Файл» не попали (для них нужен отдельный
    // настройщик цели связи), а в форме правки обязаны быть: иначе уже
    // существующее поле relation показалось бы в селекте текстовым.
    const FIELD_TYPE_OPTIONS = ['text', 'number', 'currency', 'date', 'boolean', 'select', 'relation', 'file']
        .map((type) => [type, fieldTypeName(type)]);
    // Тип вне FIELD_TYPES (запись, созданная не из v2) обязан остаться в
    // селекте текущим: иначе предзаполнение показало бы «Текст», а сохранение
    // одной только метки ушло на сервер как смена типа.
    function fieldTypeOptions(current) {
        return FIELD_TYPE_OPTIONS.some((pair) => pair[0] === current)
            ? FIELD_TYPE_OPTIONS
            : FIELD_TYPE_OPTIONS.concat([[current, fieldTypeName(current)]]);
    }

    // Варианты списка форма держит простой строкой через запятую, сервер ждёт
    // массив [{label, value}] — тот же вид, что отправляет создание поля.
    function splitOptions(text) {
        return String(text || '').split(',').map((s) => s.trim()).filter(Boolean).map((s) => ({ label: s, value: s }));
    }
    // list_fields отдаёт то, что лежит в JSON-колонке: v2 пишет объекты, а
    // более ранние записи могли остаться голыми строками. Нормализация нужна,
    // чтобы сравнение «меняли ли варианты» не давало ложную правку на каждый
    // второй запрос (и чтобы предзаполнение не показывало [object Object]).
    function normOptions(raw) {
        if (!Array.isArray(raw)) return [];
        return raw.map((o) => (o && typeof o === 'object')
            ? { label: String(o.label ?? o.value ?? ''), value: String(o.value ?? o.label ?? '') }
            : { label: String(o), value: String(o) });
    }
    const optionsText = (raw) => normOptions(raw).map((o) => o.label).join(', ');
    const optionsKey = (list) => normOptions(list).map((o) => o.label + '\u0000' + o.value).join('\u0001');

    function customTypeRow(t) {
        const id = esc(t.id);
        const label = esc(t.label || t.name);
        return '<tr data-id="' + id + '">' +
            '<td>' + label + '</td>' +
            '<td>' + esc(t.name) + '</td>' +
            '<td data-admin-only><div class="mgmt-acts">' +
            '<button type="button" class="btn btn-ghost btn-sm" data-co-select="' + id + '" aria-label="Показать поля объекта ' + label + '">Поля</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-co-edit="' + id + '" aria-label="Изменить объект ' + label + '">Изменить</button>' +
            '<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-co-del="' + id + '" aria-label="Удалить объект ' + label + '">Удалить</button>' +
            '</div></td></tr>';
    }
    function customFieldRow(f) {
        const id = esc(f.id);
        const label = esc(f.label || f.name);
        return '<tr data-id="' + id + '">' +
            '<td>' + label + '</td>' +
            '<td>' + esc(f.name) + '</td>' +
            '<td>' + esc(fieldTypeName(f.field_type)) + '</td>' +
            '<td>' + (f.is_required ? '<span class="pill accent">Обязательное</span>' : '<span class="mgmt-note">по желанию</span>') + '</td>' +
            '<td data-admin-only><div class="mgmt-acts">' +
            '<button type="button" class="btn btn-ghost btn-sm" data-co-field-edit="' + id + '" aria-label="Изменить поле ' + label + '">Изменить</button>' +
            '<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-co-field-del="' + id + '" aria-label="Удалить поле ' + label + '">Удалить</button>' +
            '</div></td></tr>';
    }
    function renderCustomTypes() {
        const rows = $('mgmt-co-rows');
        if (!rows) return;
        rows.innerHTML = CO.types.map(customTypeRow).join('');
        MGMT_ROLE.apply(rows);
        stateLine('mgmt-co-state', CO.types.length ? 'ok' : 'empty', {
            empty: 'Своих типов объектов пока нет. Создайте тип — например «Оборудование», — и добавьте ему поля.'
        });
    }
    function loadCustomTypes() {
        if (!$('mgmt-co-rows')) return Promise.resolve();
        if (!online()) {
            $('mgmt-co-rows').innerHTML = '';
            stateLine('mgmt-co-state', 'preview', { preview: 'В предпросмотре макета сети нет: типы объектов и их поля работают только в собранной CRM.' });
            return Promise.resolve();
        }
        if (CO.loading) return Promise.resolve();
        CO.loading = true;
        stateLine('mgmt-co-state', 'loading', { loading: 'Загрузка типов объектов…' });
        return api('/custom/objects')
            .then((list) => {
                CO.types = Array.isArray(list) ? list : [];
                renderCustomTypes();
                // Выбранный тип мог быть удалён в другой сессии.
                if (CO.selected != null && !currentType()) { CO.selected = null; CO.fields = []; renderCustomFields(); }
                else if (CO.selected != null) { loadCustomFields(); }
            })
            .catch((e) => {
                CO.types = [];
                $('mgmt-co-rows').innerHTML = '';
                stateLine('mgmt-co-state', 'error', { error: 'Не удалось загрузить типы объектов: ' + reasonText(e) + '.', retry: 'co' });
            })
            .then(() => { CO.loading = false; });
    }
    function renderCustomFields() {
        const box = $('mgmt-co-fields');
        const rows = $('mgmt-co-field-rows');
        if (!box || !rows) return;
        const type = currentType();
        box.hidden = !type;
        if (!type) { rows.innerHTML = ''; return; }
        const title = $('mgmt-co-fields-title');
        if (title) title.textContent = type.label || type.name;
        rows.innerHTML = CO.fields.map(customFieldRow).join('');
        MGMT_ROLE.apply(rows);
        stateLine('mgmt-co-fields-state', CO.fields.length ? 'ok' : 'empty', {
            empty: 'У этого типа ещё нет полей. Добавьте первое — оно появится в карточках записей этого объекта.'
        });
    }
    function loadCustomFields() {
        const type = currentType();
        if (!type || !online()) return Promise.resolve();
        const id = type.id;
        // Тот же порядок, что у деталей сценария: повторный клик по живому
        // запросу отбивается, а переключение на другой тип даёт новый запрос —
        // и опоздавший ответ прежнего типа отбрасывается по id.
        if (CO.fieldsLoading && String(CO.loadingId) === String(id)) return Promise.resolve();
        CO.fieldsLoading = true;
        CO.loadingId = id;
        stateLine('mgmt-co-fields-state', 'loading', { loading: 'Загрузка полей…' });
        return api('/custom/objects/' + id + '/fields')
            .then((list) => {
                if (String(CO.selected) !== String(id)) return;
                CO.fields = Array.isArray(list) ? list : [];
                renderCustomFields();
            })
            .catch((e) => {
                if (String(CO.selected) !== String(id)) return;
                CO.fields = [];
                renderCustomFields();
                stateLine('mgmt-co-fields-state', 'error', {
                    error: 'Не удалось загрузить поля: ' + reasonText(e) + '.',
                    retry: 'co-fields'
                });
            })
            .then(() => { CO.fieldsLoading = false; CO.loadingId = null; });
    }
    function selectCustomType(id) {
        CO.selected = id;
        CO.fields = [];
        renderCustomFields();
        return loadCustomFields();
    }
    function createCustomType(button) {
        return formDialog({
            title: 'Новый тип объекта',
            what: 'Создание типа',
            submitText: 'Создать',
            validate: (v) => (!v.label.trim() ? 'Заполните название — его видит пользователь.'
                : (!NAME_RE.test(v.name) ? 'Системное имя: латиницей, начинается с буквы, без пробелов — например equipment.' : '')),
            fields: [
                { key: 'label', label: 'Название для пользователя', type: 'text', required: true, maxlength: 200, placeholder: 'Оборудование' },
                { key: 'name', label: 'Системное имя', type: 'text', required: true, maxlength: 100, placeholder: 'equipment' }
            ]
        }).then((values) => {
            if (!values) return null;
            return run('co-new:' + values.name, button, 'Не создано', () =>
                api('/custom/objects', { method: 'POST', body: { name: values.name, label: values.label } })
                    .then(() => { MGMT_TOAST('Тип объекта создан.'); return loadCustomTypes(); }));
        });
    }
    // Тело PATCH типа: только изменённые ключи. Иконка nullable — стёртое поле
    // уходит как null (сервер читает null и "" одинаково, _text с blank_clears,
    // custom_objects_router.py:233); name и label на сервере NOT NULL, поэтому
    // пустые значения в тело не попадают вовсе — их отсекает validate.
    function customTypePatchBody(t, values) {
        const body = {};
        const name = values.name.trim();
        const label = values.label.trim();
        const icon = values.icon.trim();
        if (name && name !== t.name) body.name = name;
        if (label && label !== (t.label || '')) body.label = label;
        if (icon !== (t.icon || '')) body.icon = icon || null;
        return body;
    }
    function validateCustomTypeEdit(t) {
        return (values) => {
            const name = values.name.trim();
            const label = values.label.trim();
            if (!label) return 'Заполните название — его видит пользователь.';
            if (!NAME_RE.test(name)) return 'Системное имя: латиницей, начинается с буквы, без пробелов — например equipment.';
            if (CO.types.some((x) => String(x.id) !== String(t.id) && x.name === name)) return 'Тип с таким системным именем уже есть.';
            return '';
        };
    }
    function openCustomTypeForm(t) {
        return formDialog({
            title: 'Тип объекта: изменение',
            what: 'Изменение типа объекта',
            submitText: 'Сохранить',
            values: { label: t.label || '', name: t.name || '', icon: t.icon || '' },
            validate: validateCustomTypeEdit(t),
            hint: 'Системное имя — не подпись, а ключ: по нему CRM раскладывает значения записей этого объекта и на него ссылаются поля-связи. При переименовании сервер переставляет ссылки сам, но внешние интеграции могут остаться на старом имени.',
            fields: [
                { key: 'label', label: 'Название для пользователя', type: 'text', required: true, maxlength: 200 },
                { key: 'name', label: 'Системное имя', type: 'text', required: true, maxlength: 100 },
                { key: 'icon', label: 'Иконка (эмодзи или короткое имя)', type: 'text', maxlength: 50, placeholder: 'например, wrench' }
            ],
            submit: (values) => submitPatch(customTypePatchBody(t, values), '/custom/objects/' + t.id, (res, body) => {
                // PATCH отдаёт полный словарь типа (_object_dict) — обновляем
                // строку на месте, раздел не перезагружаем.
                Object.assign(t, res);
                renderCustomTypes();
                renderCustomFields();       // заголовок блока полей показывает название
                // Название и системное имя читают другие разделы (записи,
                // поля-связи) — просим их перерисоваться тем же событием,
                // что и справочники админки.
                if ('name' in body || 'label' in body) document.dispatchEvent(new Event('kb:dictionaries-changed'));
                MGMT_TOAST('Тип объекта сохранён.');
            })
        });
    }
    function removeCustomType(t) {
        return askConfirm({
            title: 'Удалить тип объекта?',
            message: 'Вместе с типом удалятся его поля и все записи этого объекта. Отменить нельзя.',
            confirmText: 'Удалить',
            cancelText: 'Отмена'
        }).then((ok) => {
            if (!ok) return null;
            return run('co-del:' + t.id, null, 'Не удалено', () =>
                api('/custom/objects/' + t.id, { method: 'DELETE' }).then(() => {
                    if (String(CO.selected) === String(t.id)) { CO.selected = null; CO.fields = []; }
                    CO.types = CO.types.filter((x) => String(x.id) !== String(t.id));
                    renderCustomTypes();
                    renderCustomFields();
                    MGMT_TOAST('Тип объекта удалён.');
                }));
        });
    }
    function createCustomField(button) {
        const type = currentType();
        if (!type) return Promise.resolve();
        return formDialog({
            title: 'Новое поле: ' + (type.label || type.name),
            what: 'Создание поля',
            submitText: 'Создать',
            validate: (v) => (!v.label.trim() ? 'Заполните метку — её видит пользователь.'
                : (!NAME_RE.test(v.name) ? 'Системное имя: латиницей, начинается с буквы, без пробелов — например power.'
                    : (v.field_type === 'select' && !v.options.trim() ? 'Для списка укажите варианты через запятую.' : ''))),
            fields: [
                { key: 'label', label: 'Метка поля', type: 'text', required: true, maxlength: 200 },
                { key: 'name', label: 'Системное имя', type: 'text', required: true, maxlength: 100 },
                { key: 'field_type', label: 'Тип значения', type: 'select', required: true, options: [['text', 'Текст'], ['number', 'Число'], ['currency', 'Сумма, BYN'], ['date', 'Дата'], ['boolean', 'Да / нет'], ['select', 'Выпадающий список']] },
                { key: 'options', label: 'Варианты списка (через запятую)', type: 'text', maxlength: 500, placeholder: 'новое, в ремонте, списано' },
                { key: 'is_required', label: 'Обязательное поле', type: 'flag' }
            ]
        }).then((values) => {
            if (!values) return null;
            const body = {
                name: values.name,
                label: values.label,
                field_type: values.field_type,
                is_required: values.is_required,
                position: CO.fields.length
            };
            if (values.field_type === 'select') {
                body.options = splitOptions(values.options);
            }
            return run('co-field-new:' + type.id + ':' + values.name, button, 'Не создано', () =>
                api('/custom/objects/' + type.id + '/fields', { method: 'POST', body: body })
                    .then(() => { MGMT_TOAST('Поле добавлено.'); return loadCustomFields(); }));
        });
    }
    // Тело PATCH поля — только изменённые ключи. Особенности контракта
    // (custom_objects_router.py:310-361): отсутствие ключа = «не трогать»,
    // options: null очищает варианты списка, is_required: null даёт 400 (флаг
    // поэтому всегда едет true/false), а position: null сервер сам сводит к 0 —
    // «очистить» порядок нельзя, пустое поле отправляем как 0.
    function customFieldPatchBody(f, values) {
        const body = {};
        const name = values.name.trim();
        const label = values.label.trim();
        if (name && name !== f.name) body.name = name;
        if (label && label !== (f.label || '')) body.label = label;
        if (values.field_type !== f.field_type) body.field_type = values.field_type;
        const options = splitOptions(values.options);
        if (optionsKey(options) !== optionsKey(f.options)) body.options = options.length ? options : null;
        const required = values.is_required === true;
        if (required !== Boolean(f.is_required)) body.is_required = required;
        const raw = Number(values.position);
        const position = Number.isFinite(raw) ? Math.trunc(raw) : 0;
        if (position !== Number(f.position || 0)) body.position = position;
        return body;
    }
    function validateFieldEdit(f) {
        return (values) => {
            const name = values.name.trim();
            const label = values.label.trim();
            if (!label) return 'Заполните метку — её видит пользователь.';
            if (!NAME_RE.test(name)) return 'Системное имя: латиницей, начинается с буквы, без пробелов — например power.';
            if (CO.fields.some((x) => String(x.id) !== String(f.id) && x.name === name)) return 'Поле с таким именем уже есть в этом объекте.';
            const body = customFieldPatchBody(f, values);
            const type = ('field_type' in body) ? body.field_type : f.field_type;
            // Тот же инвариант, что на сервере (:346): «список» обязан иметь
            // варианты, но проверяется только если запрос реально трогает тип
            // или варианты — менять метку у select с пустыми вариантами сервер
            // не запрещает, и клиенту нельзя.
            if (type === 'select' && ('field_type' in body || 'options' in body)) {
                const list = ('options' in body) ? body.options : normOptions(f.options);
                if (!list || !list.length) return 'Для типа «список» укажите варианты через запятую — без них сервер поле не сохранит.';
            }
            return '';
        };
    }
    function openCustomFieldForm(f) {
        return formDialog({
            title: 'Поле: изменение — ' + (f.label || f.name),
            what: 'Изменение поля',
            submitText: 'Сохранить',
            values: {
                label: f.label || '', name: f.name || '', field_type: f.field_type,
                options: optionsText(f.options), is_required: f.is_required === true,
                position: String(f.position || 0)
            },
            validate: validateFieldEdit(f),
            hint: 'Тип значения можно менять, только пока у поля нет ни одного значения в записях: иначе сервер ответит «нельзя» и назовёт число строк — тогда проще удалить поле и создать заново. Варианты списка хранятся как «метка = значение» и правятся через запятую.',
            fields: [
                { key: 'label', label: 'Метка поля', type: 'text', required: true, maxlength: 200 },
                { key: 'name', label: 'Системное имя', type: 'text', required: true, maxlength: 100 },
                { key: 'field_type', label: 'Тип значения', type: 'select', required: true, options: fieldTypeOptions(f.field_type) },
                { key: 'options', label: 'Варианты списка (через запятую)', type: 'text', maxlength: 500, placeholder: 'новое, в ремонте, списано' },
                { key: 'position', label: 'Порядок в списке полей', type: 'number' },
                { key: 'is_required', label: 'Обязательное поле', type: 'flag' }
            ],
            submit: (values) => submitPatch(customFieldPatchBody(f, values), '/custom/fields/' + f.id, (res, body) => {
                // Ответ PATCH — полный словарь поля (_field_dict): обновляем
                // строку таблицы на месте, без перезагрузки полей типа.
                Object.assign(f, res);
                MGMT_TOAST('Поле сохранено.');
                if ('name' in body || 'label' in body) document.dispatchEvent(new Event('kb:dictionaries-changed'));
                // Кроме самого поля меняется ещё и порядок списка (list_fields
                // сортирует по position): на месте его честно не переставить,
                // поэтому при правке порядка перечитываем поля существующим
                // загрузчиком. В остальных случаях строка обновлена без сети.
                if ('position' in body) return loadCustomFields();
                renderCustomFields();
            })
        });
    }
    function removeCustomField(fieldId) {
        const type = currentType();
        if (!type) return Promise.resolve();
        const field = CO.fields.filter((f) => String(f.id) === String(fieldId))[0];
        return askConfirm({
            title: 'Удалить поле?',
            message: 'Поле «' + ((field && field.label) || fieldId) + '» и его значения в записях объекта будут удалены.',
            confirmText: 'Удалить',
            cancelText: 'Отмена'
        }).then((ok) => {
            if (!ok) return null;
            return run('co-field-del:' + fieldId, null, 'Не удалено', () =>
                api('/custom/fields/' + fieldId, { method: 'DELETE' }).then(() => {
                    CO.fields = CO.fields.filter((f) => String(f.id) !== String(fieldId));
                    renderCustomFields();
                    MGMT_TOAST('Поле удалено.');
                    return loadCustomFields();
                }));
        });
    }

    /* ── Автоматизации (Workflows) ───────────────────────────────────── */

    const TRIGGER_RU = { record_event: 'Изменение записи', schedule: 'По расписанию', webhook: 'Внешний вызов', manual: 'Вручную' };
    const STEP_RU = { action: 'Действие', condition: 'Условие', delay: 'Задержка' };
    const ACTION_RU = { create_record: 'Создать запись', update_record: 'Изменить запись', send_email: 'Отправить письмо', http_request: 'HTTP-запрос', code: 'Код' };
    const RUN_RU = { success: 'Успешно', error: 'С ошибкой', running: 'Выполняется', pending: 'В очереди' };
    const WF = { items: [], selected: null, loading: false, detailLoading: false, loadingId: null, triggers: [], steps: [], runs: [] };
    const currentWorkflow = () => (WF.selected == null ? null : WF.items.filter((w) => String(w.id) === String(WF.selected))[0] || null);
    const configNote = (config) => {
        if (!config || (typeof config === 'object' && !Object.keys(config).length)) return '';
        const text = typeof config === 'string' ? config : JSON.stringify(config);
        return text.length > 160 ? text.slice(0, 160) + '…' : text;
    };

    function workflowRow(w) {
        const id = esc(w.id);
        const name = esc(w.name);
        const on = w.is_active === true;
        return '<tr data-id="' + id + '">' +
            '<td>' + name + '</td>' +
            '<td>' + (w.description ? esc(w.description) : '—') + '</td>' +
            '<td><span class="pill ' + (on ? 'ok' : 'warn') + '">' + (on ? 'Включён' : 'Выключен') + '</span></td>' +
            '<td data-admin-only><div class="mgmt-acts">' +
            '<button type="button" class="btn btn-ghost btn-sm" data-wf-open="' + id + '" aria-label="Показать триггеры и шаги сценария ' + name + '">Сценарий</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-wf-edit="' + id + '" aria-label="Изменить сценарий ' + name + '">Изменить</button>' +
            '<button type="button" class="btn btn-ghost btn-sm" data-wf-toggle="' + id + '" aria-label="' + (on ? 'Выключить' : 'Включить') + ' сценарий ' + name + '">' + (on ? 'Выключить' : 'Включить') + '</button>' +
            '<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-wf-del="' + id + '" aria-label="Удалить сценарий ' + name + '">Удалить</button>' +
            '</div></td></tr>';
    }
    function renderWorkflows() {
        const rows = $('mgmt-wf-rows');
        if (!rows) return;
        rows.innerHTML = WF.items.map(workflowRow).join('');
        MGMT_ROLE.apply(rows);
        stateLine('mgmt-wf-state', WF.items.length ? 'ok' : 'empty', {
            empty: 'Автоматизаций пока нет. Создайте сценарий, затем добавьте ему триггер и шаги и включите его.'
        });
        const wf = currentWorkflow();
        if (!wf) { WF.selected = null; renderWorkflowDetail(); }
    }
    function loadWorkflows() {
        if (!$('mgmt-wf-rows')) return Promise.resolve();
        if (!online()) {
            $('mgmt-wf-rows').innerHTML = '';
            stateLine('mgmt-wf-state', 'preview', { preview: 'В предпросмотре макета сети нет: автоматизации работают только в собранной CRM.' });
            return Promise.resolve();
        }
        if (WF.loading) return Promise.resolve();
        WF.loading = true;
        stateLine('mgmt-wf-state', 'loading', { loading: 'Загрузка автоматизаций…' });
        return api('/workflows/')
            .then((list) => {
                WF.items = Array.isArray(list) ? list : [];
                renderWorkflows();
                // Блок деталей открыт и сценарий жив — перечитаем его, чтобы
                // триггеры/шаги/журнал не оставались прошлым снимком.
                if (WF.selected != null && currentWorkflow()) openWorkflowDetail(WF.selected);
            })
            .catch((e) => {
                WF.items = [];
                $('mgmt-wf-rows').innerHTML = '';
                stateLine('mgmt-wf-state', 'error', { error: 'Не удалось загрузить автоматизации: ' + reasonText(e) + '.', retry: 'wf' });
            })
            .then(() => { WF.loading = false; });
    }
    function li(mainHtml, noteHtml, buttonHtml) {
        return '<li>' + mainHtml + (buttonHtml || '') + (noteHtml || '') + '</li>';
    }
    function renderWorkflowDetail() {
        const box = $('mgmt-wf-detail');
        if (!box) return;
        const wf = currentWorkflow();
        box.hidden = !wf;
        if (!wf) return;
        const title = $('mgmt-wf-detail-title');
        if (title) title.textContent = wf.name;
        $('mgmt-wf-triggers').innerHTML = WF.triggers.length
            ? WF.triggers.map((t) => li('<span class="mgmt-li-main">' + esc(TRIGGER_RU[t.trigger_type] || t.trigger_type) + '</span>',
                configNote(t.config) ? '<span class="mgmt-li-note">' + esc(configNote(t.config)) + '</span>' : '',
                '<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-wf-trigger-del="' + esc(t.id) + '" aria-label="Удалить триггер">Удалить</button>')).join('')
            : '<li><span class="mgmt-li-note">Триггеров нет — сценарий ни на что не реагирует.</span></li>';
        $('mgmt-wf-steps').innerHTML = WF.steps.length
            ? WF.steps.map((s) => li('<span class="mgmt-li-main">' + esc(STEP_RU[s.step_type] || s.step_type) +
                (s.action_type ? ' · ' + esc(ACTION_RU[s.action_type] || s.action_type) : '') + '</span>',
                configNote(s.config) ? '<span class="mgmt-li-note">' + esc(configNote(s.config)) + '</span>' : '',
                '<button type="button" class="btn btn-ghost btn-sm mgmt-danger" data-wf-step-del="' + esc(s.id) + '" aria-label="Удалить шаг">Удалить</button>')).join('')
            : '<li><span class="mgmt-li-note">Шагов нет — сценарию нечего выполнять.</span></li>';
        $('mgmt-wf-runs').innerHTML = WF.runs.length
            ? WF.runs.map((r) => li('<span class="mgmt-li-main">' + esc(RUN_RU[r.status] || r.status || '—') + '</span>',
                '<span class="mgmt-li-note">' + esc(when(r.started_at) || 'без времени запуска') +
                (r.completed_at ? ' → ' + esc(when(r.completed_at)) : '') + (r.error ? ' · ' + esc(r.error) : '') + '</span>')).join('')
            : '<li><span class="mgmt-li-note">Запусков ещё не было.</span></li>';
    }
    function openWorkflowDetail(id) {
        WF.selected = id;
        WF.triggers = []; WF.steps = []; WF.runs = [];
        renderWorkflowDetail();
        const box = $('mgmt-wf-detail');
        if (box) box.scrollIntoView({ block: 'nearest' });
        // Повторный клик по тому же сценарию, пока ответ в пути, — лишний
        // запрос; переключение на ДРУГОЙ должен дать новый запрос, а
        // опоздавший ответ первого отсекает проверка WF.selected ниже.
        if (WF.detailLoading && String(WF.loadingId) === String(id)) return Promise.resolve();
        WF.detailLoading = true;
        WF.loadingId = id;
        stateLine('mgmt-wf-detail-state', 'loading', { loading: 'Загрузка сценария…' });
        return Promise.all([
            api('/workflows/' + id + '/triggers'),
            api('/workflows/' + id + '/steps'),
            api('/workflows/' + id + '/runs')
        ]).then((res) => {
            if (String(WF.selected) !== String(id)) return;   // переключились — ответ чужой
            WF.triggers = Array.isArray(res[0]) ? res[0] : [];
            WF.steps = Array.isArray(res[1]) ? res[1] : [];
            WF.runs = Array.isArray(res[2]) ? res[2] : [];
            renderWorkflowDetail();
            stateLine('mgmt-wf-detail-state', 'ok');
        }).catch((e) => {
            if (String(WF.selected) !== String(id)) return;
            stateLine('mgmt-wf-detail-state', 'error', { error: 'Не удалось загрузить сценарий: ' + reasonText(e) + '.', retry: 'wf-detail' });
        }).then(() => {
            WF.detailLoading = false;
            WF.loadingId = null;
        });
    }
    function workflowFields(w) {
        return [
            { key: 'name', label: 'Название', type: 'text', required: true, maxlength: 200 },
            { key: 'description', label: 'Описание', type: 'textarea', maxlength: 2000 }
        ];
    }
    // Тело PATCH сценария — только изменённые ключи (workflows_router.py:80-90):
    // name NOT NULL и не бывает null в теле, description nullable — стёртое
    // описание уходит явным null, иначе «очистить» его нельзя было бы (пустая
    // строка сервером тоже читается как «описания нет», но null честнее: он
    // говорит о намерении, а не о незаполненном поле формы).
    function workflowPatchBody(w, values) {
        const body = {};
        const name = values.name.trim();
        if (name && name !== (w.name || '')) body.name = name;
        const description = values.description.trim();
        if (description !== (w.description || '')) body.description = description || null;
        return body;
    }
    // Ответ PATCH /workflows/ — «{"message": "Обновлено"}», данных сценария в
    // нём нет: правим снимок строки по отправленному телу и перерисовываем
    // список с деталями, не дёргая загрузчик раздела.
    function applyWorkflowPatch(w, body) {
        if ('name' in body) w.name = body.name;
        if ('description' in body) w.description = body.description;
        renderWorkflows();
        renderWorkflowDetail();
    }
    function saveWorkflow(w, button) {
        return formDialog({
            title: w ? 'Сценарий: изменение' : 'Новый сценарий',
            what: 'Сохранение сценария',
            submitText: 'Сохранить',
            values: w || {},
            validate: (v) => (v.name ? '' : 'Заполните название сценария.'),
            hint: w ? 'Название очистить нельзя — оно обязательно. Описание можно убрать совсем: для этого достаточно стереть текст.' : '',
            fields: workflowFields(w),
            submit: w ? (values) => submitPatch(workflowPatchBody(w, values), '/workflows/' + w.id, (res, body) => {
                applyWorkflowPatch(w, body);
                MGMT_TOAST('Сценарий сохранён.');
            }) : null
        }).then((values) => {
            if (!values || w) return null;        // правка уже применена в submit
            return run('wf-save:' + values.name, button, 'Не сохранено', () =>
                api('/workflows/', { method: 'POST', body: { name: values.name, description: values.description } })
                    .then(() => {
                        // Новую автоматизацию сервер создаёт выключенной
                        // (models.py:322), иначе она начала бы реагировать молча.
                        MGMT_TOAST('Сценарий создан и выключен — включите его, когда настроите триггер и шаги.');
                        return loadWorkflows();
                    }));
        });
    }
    function toggleWorkflow(w, button) {
        const next = w.is_active !== true;
        return run('wf-toggle:' + w.id, button, 'Не переключено', () =>
            api('/workflows/' + w.id, { method: 'PATCH', body: { is_active: next } })
                .then(() => {
                    w.is_active = next;
                    renderWorkflows();
                    MGMT_TOAST(next ? 'Автоматизация включена.' : 'Автоматизация выключена: триггеры больше не срабатывают.');
                }));
    }
    function removeWorkflow(w, button) {
        return askConfirm({
            title: 'Удалить автоматизацию?',
            message: 'Сценарий «' + w.name + '», его триггеры, шаги и журнал запусков будут удалены.',
            confirmText: 'Удалить',
            cancelText: 'Отмена'
        }).then((ok) => {
            if (!ok) return null;
            return run('wf-del:' + w.id, button, 'Не удалено', () =>
                api('/workflows/' + w.id, { method: 'DELETE' }).then(() => {
                    if (String(WF.selected) === String(w.id)) WF.selected = null;
                    WF.items = WF.items.filter((x) => String(x.id) !== String(w.id));
                    renderWorkflows();
                    renderWorkflowDetail();
                    MGMT_TOAST('Автоматизация удалена.');
                }));
        });
    }
    function removeWorkflowPart(kind, id, button) {
        // kind: 'triggers' | 'steps' — DELETE /workflows/triggers/{id} и
        // DELETE /workflows/steps/{id}; после удаления список перечитывается
        // целиком, чтобы порядок шагов и вложенность пришли с сервера.
        const wf = currentWorkflow();
        if (!wf) return Promise.resolve();
        const title = kind === 'triggers' ? 'Удалить триггер?' : 'Удалить шаг?';
        const label = kind === 'triggers' ? 'Триггер' : 'Шаг';
        return askConfirm({
            title: title,
            message: label + ' будет удалён из сценария «' + wf.name + '». Остальные шаги останутся на месте.',
            confirmText: 'Удалить',
            cancelText: 'Отмена'
        }).then((ok) => {
            if (!ok) return null;
            return run('wf-part:' + kind + ':' + id, button, 'Не удалено', () =>
                api('/workflows/' + (kind === 'triggers' ? 'triggers/' : 'steps/') + id, { method: 'DELETE' })
                    .then(() => { MGMT_TOAST(label + ' удалён.'); return openWorkflowDetail(wf.id); }));
        });
    }

    /* ── Развязка кликов: карточки скрыты у не-админа, но слушатель один ─ */

    function bind() {
        const view = $('view-admin');
        if (!view) return;
        const NEW_BUTTONS = [
            ['mgmt-wh-new', (button) => openWebhookForm(null, button)],
            ['mgmt-co-new', (button) => createCustomType(button)],
            ['mgmt-co-field-new', (button) => createCustomField(button)],
            ['mgmt-wf-new', (button) => saveWorkflow(null, button)]
        ];
        NEW_BUTTONS.forEach((pair) => {
            const button = $(pair[0]);
            if (button) button.onclick = (event) => { pair[1](event.currentTarget); };
        });
        view.addEventListener('click', (event) => {
            const target = event.target;
            if (!target || !target.closest) return;
            const button = target.closest([
                '[data-mgmt-retry]',
                '[data-wh-edit]', '[data-wh-test]', '[data-wh-toggle]', '[data-wh-del]',
                '[data-co-select]', '[data-co-edit]', '[data-co-del]',
                '[data-co-field-edit]', '[data-co-field-del]',
                '[data-wf-open]', '[data-wf-edit]', '[data-wf-toggle]', '[data-wf-del]',
                '[data-wf-trigger-del]', '[data-wf-step-del]'
            ].join(', '));
            if (!button) return;
            if (button.hasAttribute('data-mgmt-retry')) {
                const key = button.dataset.mgmtRetry;
                if (key === 'user') MGMT_DICT.loadUsers();
                else if (key === 'status') MGMT_DICT.loadStatuses();
                else if (key === 'wh') loadWebhooks();
                else if (key === 'co') loadCustomTypes();
                else if (key === 'co-fields') loadCustomFields();
                else if (key === 'wf') loadWorkflows();
                else if (key === 'wf-detail' && WF.selected != null) openWorkflowDetail(WF.selected);
                return;
            }
            const wh = button.dataset.whEdit || button.dataset.whTest || button.dataset.whToggle || button.dataset.whDel;
            if (wh) {
                const hook = findWebhook(wh);
                if (!hook) return;
                if (button.hasAttribute('data-wh-edit')) openWebhookForm(hook, button);
                else if (button.hasAttribute('data-wh-test')) testWebhook(hook, button);
                else if (button.hasAttribute('data-wh-toggle')) toggleWebhook(hook, button);
                else removeWebhook(hook, button);
                return;
            }
            if (button.hasAttribute('data-co-select')) { selectCustomType(button.dataset.coSelect); return; }
            if (button.hasAttribute('data-co-edit')) {
                const type = CO.types.filter((t) => String(t.id) === String(button.dataset.coEdit))[0];
                if (type) openCustomTypeForm(type);
                return;
            }
            if (button.hasAttribute('data-co-del')) {
                const type = CO.types.filter((t) => String(t.id) === String(button.dataset.coDel))[0];
                if (type) removeCustomType(type);
                return;
            }
            if (button.hasAttribute('data-co-field-edit')) {
                const field = CO.fields.filter((f) => String(f.id) === String(button.dataset.coFieldEdit))[0];
                if (field) openCustomFieldForm(field);
                return;
            }
            if (button.hasAttribute('data-co-field-del')) { removeCustomField(button.dataset.coFieldDel); return; }
            const wfId = button.dataset.wfOpen || button.dataset.wfEdit || button.dataset.wfToggle || button.dataset.wfDel;
            if (wfId) {
                const wf = WF.items.filter((w) => String(w.id) === String(wfId))[0];
                if (!wf) return;
                if (button.hasAttribute('data-wf-open')) openWorkflowDetail(wf.id);
                else if (button.hasAttribute('data-wf-edit')) saveWorkflow(wf, button);
                else if (button.hasAttribute('data-wf-toggle')) toggleWorkflow(wf, button);
                else removeWorkflow(wf, button);
                return;
            }
            if (button.hasAttribute('data-wf-trigger-del')) removeWorkflowPart('triggers', button.dataset.wfTriggerDel, button);
            else if (button.hasAttribute('data-wf-step-del')) removeWorkflowPart('steps', button.dataset.wfStepDel, button);
        });
    }

    MGMT_LAZY.onOpen(loadWebhooks);
    MGMT_LAZY.onOpen(loadCustomTypes);
    MGMT_LAZY.onOpen(loadWorkflows);
    bind();

    return {
        stateLine: stateLine,
        reasonText: reasonText,
        formDialog: formDialog,
        submitPatch: submitPatch,
        webhookBody: webhookBody,
        webhookPatchBody: webhookPatchBody,
        validateWebhook: validateWebhook,
        validateWebhookEdit: validateWebhookEdit,
        webhookRow: webhookRow,
        renderWebhooks: renderWebhooks,
        loadWebhooks: loadWebhooks,
        openWebhookForm: openWebhookForm,
        toggleWebhook: toggleWebhook,
        testWebhook: testWebhook,
        customTypeRow: customTypeRow,
        customFieldRow: customFieldRow,
        renderCustomTypes: renderCustomTypes,
        renderCustomFields: renderCustomFields,
        customTypePatchBody: customTypePatchBody,
        validateCustomTypeEdit: validateCustomTypeEdit,
        openCustomTypeForm: openCustomTypeForm,
        customFieldPatchBody: customFieldPatchBody,
        validateFieldEdit: validateFieldEdit,
        openCustomFieldForm: openCustomFieldForm,
        splitOptions: splitOptions,
        normOptions: normOptions,
        optionsText: optionsText,
        optionsKey: optionsKey,
        loadCustomTypes: loadCustomTypes,
        loadCustomFields: loadCustomFields,
        selectCustomType: selectCustomType,
        workflowRow: workflowRow,
        renderWorkflows: renderWorkflows,
        workflowPatchBody: workflowPatchBody,
        applyWorkflowPatch: applyWorkflowPatch,
        saveWorkflow: saveWorkflow,
        loadWorkflows: loadWorkflows,
        openWorkflowDetail: openWorkflowDetail,
        toggleWorkflow: toggleWorkflow,
        FIELD_TYPE_OPTIONS: FIELD_TYPE_OPTIONS,
        WH: WH,
        CO: CO,
        WF: WF
    };
})();
