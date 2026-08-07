/**
 * Кастомные объекты — UI для создания и управления объектами/полями/записями.
 * Доступен через страницу "Настройки" в основной CRM.
 */
(function() {
    let currentObject = null;
    let currentFields = [];
    let currentRecords = [];

    const FIELD_TYPES = [
        { value: 'text', label: 'Текст' },
        { value: 'number', label: 'Число' },
        { value: 'select', label: 'Выбор из списка' },
        { value: 'date', label: 'Дата' },
        { value: 'currency', label: 'Валюта' },
        { value: 'boolean', label: 'Да/Нет' }
    ];

    function showToast(msg, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, type);
        } else {
            alert(msg);
        }
    }

    async function apiFetch(path, options = {}) {
        let token;
        try { token = localStorage.getItem('crm_token'); } catch(e) { token = null; }
        const headers = { ...options.headers };
        if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const res = await fetch(path, { ...options, headers });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
        return res.json();
    }

    // === ОБЪЕКТЫ ===

    async function loadObjects() {
        const objects = await apiFetch('/custom/objects');
        const list = document.getElementById('custom-objects-list');
        if (!list) return;
        list.innerHTML = '';
        if (objects.length === 0) {
            list.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:20px;">Нет кастомных объектов. Создайте первый!</p>';
            return;
        }
        objects.forEach(obj => {
            const div = document.createElement('div');
            div.className = 'custom-object-item';
            div.innerHTML = `
                <div class="custom-object-info">
                    <strong>${escapeHtml(obj.label)}</strong>
                    <span style="color:var(--text-muted);font-size:12px;">${escapeHtml(obj.name)}</span>
                </div>
                <div class="custom-object-actions">
                    <button class="btn btn-secondary btn-sm" onclick="customObjects.openObject(${obj.id})">Открыть</button>
                    <button class="btn btn-danger btn-sm" onclick="customObjects.deleteObject(${obj.id}, '${escapeHtml(obj.name)}')">Удалить</button>
                </div>
            `;
            list.appendChild(div);
        });
    }

    async function createObject() {
        const name = document.getElementById('new-object-name').value.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
        const label = document.getElementById('new-object-label').value.trim();
        if (!name || !label) { showToast('Заполните все поля', 'error'); return; }
        try {
            await apiFetch('/custom/objects', { method: 'POST', body: JSON.stringify({ name, label }) });
            document.getElementById('new-object-name').value = '';
            document.getElementById('new-object-label').value = '';
            showToast('Объект создан', 'success');
            await loadObjects();
        } catch(e) { showToast(e.message, 'error'); }
    }

    async function deleteObject(id, name) {
        if (!confirm(`Удалить объект "${name}" и все его данные?`)) return;
        try {
            await apiFetch(`/custom/objects/${id}`, { method: 'DELETE' });
            showToast('Удалено', 'success');
            if (currentObject && currentObject.id === id) {
                currentObject = null;
                document.getElementById('object-detail').classList.add('hidden');
                document.getElementById('objects-list').classList.remove('hidden');
            }
            await loadObjects();
        } catch(e) { showToast(e.message, 'error'); }
    }

    // === ОБЪЕКТ (Детали) ===

    async function openObject(id) {
        const objects = await apiFetch('/custom/objects');
        currentObject = objects.find(o => o.id === id);
        if (!currentObject) return;

        document.getElementById('objects-list').classList.add('hidden');
        document.getElementById('object-detail').classList.remove('hidden');
        document.getElementById('object-detail-title').textContent = currentObject.label;

        await loadFields();
        await loadRecords();
    }

    function backToList() {
        currentObject = null;
        document.getElementById('objects-list').classList.remove('hidden');
        document.getElementById('object-detail').classList.add('hidden');
    }

    // === ПОЛЯ ===

    async function loadFields() {
        if (!currentObject) return;
        currentFields = await apiFetch(`/custom/objects/${currentObject.id}/fields`);
        const list = document.getElementById('fields-list');
        list.innerHTML = '';
        currentFields.forEach(f => {
            const typeLabel = FIELD_TYPES.find(t => t.value === f.field_type)?.label || f.field_type;
            const div = document.createElement('div');
            div.className = 'field-item';
            div.innerHTML = `
                <span><b>${escapeHtml(f.label)}</b> <span style="color:var(--text-muted);font-size:12px;">(${escapeHtml(f.name)}, ${typeLabel})</span></span>
                <button class="btn btn-danger btn-sm" onclick="customObjects.deleteField(${f.id})">Удалить</button>
            `;
            list.appendChild(div);
        });
    }

    async function createField() {
        const name = document.getElementById('new-field-name').value.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
        const label = document.getElementById('new-field-label').value.trim();
        const type = document.getElementById('new-field-type').value;
        if (!name || !label) { showToast('Заполните все поля', 'error'); return; }
        try {
            await apiFetch(`/custom/objects/${currentObject.id}/fields`, {
                method: 'POST',
                body: JSON.stringify({ name, label, field_type: type, position: currentFields.length })
            });
            document.getElementById('new-field-name').value = '';
            document.getElementById('new-field-label').value = '';
            showToast('Поле добавлено', 'success');
            await loadFields();
        } catch(e) { showToast(e.message, 'error'); }
    }

    async function deleteField(id) {
        if (!confirm('Удалить поле и все его значения?')) return;
        try {
            await apiFetch(`/custom/fields/${id}`, { method: 'DELETE' });
            showToast('Удалено', 'success');
            await loadFields();
        } catch(e) { showToast(e.message, 'error'); }
    }

    // === ЗАПИСИ ===

    async function loadRecords() {
        if (!currentObject) return;
        currentRecords = await apiFetch(`/custom/objects/${currentObject.id}/records`);
        renderRecordsTable();
    }

    function renderRecordsTable() {
        const container = document.getElementById('records-container');
        if (!container) return;

        if (currentFields.length === 0) {
            container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:20px;">Сначала добавьте поля объекту</p>';
            return;
        }

        if (currentRecords.length === 0) {
            container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:20px;">Нет записей. Создайте первую!</p>';
            return;
        }

        let html = '<table class="data-table"><thead><tr>';
        currentFields.forEach(f => {
            html += `<th>${escapeHtml(f.label)}</th>`;
        });
        html += '<th>Действия</th></tr></thead><tbody>';

        currentRecords.forEach(rec => {
            html += '<tr>';
            currentFields.forEach(f => {
                const val = rec.data[f.name];
                let display = '';
                if (val !== undefined && val !== null) {
                    if (f.field_type === 'boolean') display = val ? '✓' : '✕';
                    else if (f.field_type === 'currency') display = `${Number(val).toLocaleString('ru-RU')} BYN`;
                    else display = escapeHtml(String(val));
                }
                html += `<td>${display}</td>`;
            });
            html += `<td><button class="btn btn-danger btn-sm" onclick="customObjects.deleteRecord(${rec.id})">Удалить</button></td>`;
            html += '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    async function createRecord() {
        if (!currentObject || currentFields.length === 0) return;

        const data = {};
        currentFields.forEach(f => {
            const input = document.getElementById(`record-field-${f.id}`);
            if (!input) return;
            if (f.field_type === 'boolean') {
                data[f.name] = input.checked;
            } else if (f.field_type === 'number' || f.field_type === 'currency') {
                data[f.name] = input.value ? parseFloat(input.value) : null;
            } else {
                data[f.name] = input.value || null;
            }
        });

        try {
            await apiFetch(`/custom/objects/${currentObject.id}/records`, {
                method: 'POST',
                body: JSON.stringify({ data })
            });
            showToast('Запись создана', 'success');
            document.getElementById('new-record-form').classList.add('hidden');
            await loadRecords();
        } catch(e) { showToast(e.message, 'error'); }
    }

    async function deleteRecord(id) {
        if (!confirm('Удалить запись?')) return;
        try {
            await apiFetch(`/custom/records/${id}`, { method: 'DELETE' });
            showToast('Удалено', 'success');
            await loadRecords();
        } catch(e) { showToast(e.message, 'error'); }
    }

    function showNewRecordForm() {
        const form = document.getElementById('new-record-form');
        form.classList.toggle('hidden');
        if (!form.classList.contains('hidden')) {
            form.innerHTML = '';
            currentFields.forEach(f => {
                let input = '';
                if (f.field_type === 'boolean') {
                    input = `<input type="checkbox" id="record-field-${f.id}">`;
                } else if (f.field_type === 'date') {
                    input = `<input type="date" id="record-field-${f.id}">`;
                } else if (f.field_type === 'number' || f.field_type === 'currency') {
                    input = `<input type="number" step="0.01" id="record-field-${f.id}" placeholder="${escapeHtml(f.label)}">`;
                } else {
                    input = `<input type="text" id="record-field-${f.id}" placeholder="${escapeHtml(f.label)}">`;
                }
                form.innerHTML += `<div class="form-group"><label>${escapeHtml(f.label)}${f.is_required ? ' *' : ''}</label>${input}</div>`;
            });
            form.innerHTML += `<button class="btn btn-primary" onclick="customObjects.createRecord()" style="margin-top:12px">Создать</button>`;
        }
    }

    // Экспорт
    window.customObjects = {
        loadObjects, createObject, deleteObject,
        openObject, backToList,
        loadFields, createField, deleteField,
        loadRecords, createRecord, deleteRecord, showNewRecordForm
    };

    // Автозагрузка при открытии страницы
    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('custom-objects-list')) {
            loadObjects();
        }
    });
})();
