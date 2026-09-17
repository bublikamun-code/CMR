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
        user: [['username','Логин','text',true],['full_name','Полное имя'],['role','Роль','select',true]],
        store: [['name','Название','text',true],['address','Адрес'],['phone','Телефон','tel']],
        status: [['name','Название','text',true],['color','Цвет','color'],['position','Позиция','number',true]]
    };
    const titles = {supplier:'Поставщик', user:'Пользователь', store:'Магазин', status:'Статус сделки'};
    let sequence = 10, editing, opener;
    window.KBSuppliers = {list: () => data.supplier.map(s => [s.id, s.name])};
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
        else data[kind].push({id:'local-'+sequence++, role:'board', ...values});
        render(kind);
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
