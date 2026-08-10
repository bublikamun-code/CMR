let modalLoading = false;
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('card-modal');
    const closeBtn = document.querySelector('#card-modal .close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    
    let mouseDownTarget = null;
    modal.addEventListener('mousedown', (e) => { mouseDownTarget = e.target; });
    modal.addEventListener('click', (e) => { 
        if (e.target === modal && mouseDownTarget === modal) closeModal(); 
    });
});

function closeModal() {
    document.getElementById('card-modal').classList.add('hidden');
}

async function openCardModal(cardId) {
    if (modalLoading) return;
    modalLoading = true;

    const modal = document.getElementById('card-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body-left');

    modal.classList.remove('hidden');
    modalBody.innerHTML = '<p>Загрузка данных...</p>';

    try {
        let card;
        try {
            card = await apiFetch(`/kanban/cards/${cardId}`);
        } catch {
            const cards = await apiFetch('/kanban/cards');
            card = cards.find(c => c.id === cardId);
        }
        if (!card) throw new Error("Карточка не найдена");

        const escapedTitle = escapeHtml(card.title);
        modalTitle.innerHTML = `
            <textarea id="edit-card-title" rows="1" class="auto-expand-title" placeholder="Название сделки...">${escapedTitle}</textarea>
        `;
        
        const titleEl = document.getElementById('edit-card-title');
        titleEl.style.height = 'auto';
        titleEl.style.height = titleEl.scrollHeight + 'px';
        titleEl.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        });
        titleEl.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                const newTitle = titleEl.value.trim();
                if (!newTitle) return;
                try {
                    await apiFetch(`/cards/${card.id}`, { 
                        method: 'PATCH', 
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ title: newTitle }) 
                    });
                    if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                    if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                    if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                    showToast('Название сохранено', 'success');
                } catch (err) {
                    showToast('Не удалось сохранить название: ' + err.message, 'error');
                }
            }
        });
        titleEl.addEventListener('blur', async () => {
            const newTitle = titleEl.value.trim();
            if (!newTitle || newTitle === card.title) return;
            try {
                await apiFetch(`/cards/${card.id}`, { 
                    method: 'PATCH', 
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ title: newTitle }) 
                });
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
            } catch (err) {
                showToast('Не удалось сохранить название: ' + err.message, 'error');
            }
        });

        renderModalContent(card, document.getElementById('modal-body-left'), document.getElementById('modal-body-right'));
    } catch (error) {
        modalBody.innerHTML = `<p class="modal-error">Ошибка: ${escapeHtml(error.message)}</p>`;
    } finally {
        modalLoading = false;
    }
}

async function renderModalContent(card, leftContainer, rightContainer) {
    const container = leftContainer; // backward compat
    const now = new Date();
    const dueDate = card.due_date ? new Date(card.due_date + 'T00:00:00') : null;
    const isOverdue = dueDate && dueDate < now && card.status !== 'Закрыто';
    const dueDateStr = card.due_date || '';

    // LEFT COLUMN: Static fields
    leftContainer.innerHTML = `
        <div class="modal-section">
            <label class="modal-label">Итоговая сумма сделки (BYN)</label>
            <div class="modal-input-row">
                <input type="number" id="input-total-amount" value="${escapeHtml(String(card.total_amount || 0))}" placeholder="Сумма">
            </div>
        </div>
        <div class="modal-section">
            <label class="modal-label">Дата окончания</label>
            <div class="modal-input-row">
                <input type="date" id="input-due-date" value="${dueDateStr}" class="${isOverdue ? 'input-overdue' : ''}">
                ${isOverdue ? '<span class="overdue-badge">ПРОСРОЧЕНО</span>' : ''}
            </div>
        </div>
        <div class="modal-section">
            <label class="modal-label">Email отправителя</label>
            <div class="modal-input-row">
                <input type="email" id="input-sender-email" value="${escapeHtml(card.sender_email || '')}" placeholder="email@company.com" readonly>
            </div>
        </div>
        <div class="modal-section">
            <label class="modal-label">Клиент</label>
            <div id="client-mount"></div>
        </div>
        <div class="modal-section">
            <label class="modal-label">Теги</label>
            <div id="tags-mount" class="tags-container"></div>
        </div>
        <div class="modal-section">
            <label class="modal-label">Привязка к магазину</label>
            <div id="store-mount" class="store-dropdown"></div>
        </div>
        <div class="modal-section">
            <h3>Чек-лист к оплате</h3>
            <div id="checklist-container"></div>
            <div id="checklist-summary" class="checklist-summary"></div>
            <div class="modal-input-row checklist-add-row checklist-add-align">
                <div id="new-supplier-mount" class="input-company"></div>
                <input type="number" id="new-amount" placeholder="Сумма к оплате" class="input-amount">
                <button id="btn-add-checklist" class="btn-primary btn-sm">Добавить</button>
            </div>
        </div>
        <div class="modal-section">
            <h3>Счета (Вложения)</h3>
            <div id="file-dropzone" class="dropzone">
                Перетащите файлы сюда или кликните
                <input type="file" id="file-input" multiple>
            </div>
            <div id="attachments-container" class="attachments-container"></div>
        </div>
        <div class="modal-section" id="invoices-section" hidden>
            <h3>Накладные <span id="invoices-badge" class="col-count"></span></h3>
            <div id="invoices-container"></div>
            <div class="invoices-summary" id="invoices-summary"></div>
        </div>
        <div class="modal-actions">
            <button id="btn-to-assembly" class="btn-action btn-assembly">В Сборку (+Реестр)</button>
            <button id="btn-trigger-payment" class="btn-action btn-writeoff-action">В Списание</button>
        </div>
    `;

    // Кнопки этапов: «В Списание» доступна только со «Сборки» или когда счёт оплачен
    applyStageButtons(card);
    // Накладные показываем только когда сделка дошла до списания
    renderCardInvoices(card);
    // Прошлые сделки того же отправителя — предложить связать
    renderRelatedCards(card);

    // RIGHT COLUMN: Timeline
    rightContainer.innerHTML = `
        <div id="related-cards-mount"></div>
        <div class="timeline-sticky-input">
            <label class="modal-label">Комментарий</label>
            <textarea id="input-description" rows="1" class="auto-expand" placeholder="Заметка к сделке...">${escapeHtml(card.description || '')}</textarea>
        </div>
        <div class="timeline" id="activity-container">
            <div class="activity-loading">Загрузка...</div>
        </div>
    `;

    requestAnimationFrame(() => {
        var ta = document.getElementById('input-description');
        if (ta) {
            ta.style.height = 'auto';
            ta.style.height = ta.scrollHeight + 'px';
            ta.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = this.scrollHeight + 'px';
            });
        }
    });

    // Load activity log
    loadCardActivity(card.id);

    // Load tags
    loadCardTags(card);

    // 1. Отрисовка чек-листа
    const checklistContainer = document.getElementById('checklist-container');
    checklistContainer.innerHTML = '';
    renderChecklistSummary(card);
    
    if (card.checklists) {
        card.checklists.forEach(item => {
            const isCompleted = item.is_paid && item.is_secondary_check;
            const div = document.createElement('div');
            div.className = 'checklist-item';
            const invoiceRow = item.invoice_file_path
                ? `<div class="checklist-invoice has-file">
                       <a href="#" class="checklist-invoice-link" data-download="${escapeHtml(item.invoice_file_path.split('/').pop())}" data-nice-name="${escapeHtml(item.invoice_file_name)}" title="${escapeHtml(item.invoice_file_name)}">${ICON_FILE} ${escapeHtml(item.invoice_file_name)}</a>
                       <a href="#" class="checklist-invoice-dl" data-download="${escapeHtml(item.invoice_file_path.split('/').pop())}" data-nice-name="${escapeHtml(item.invoice_file_name)}" title="Скачать">↓</a>
                       <button class="checklist-invoice-del" data-id="${item.id}" title="Открепить счёт">&times;</button>
                   </div>`
                : `<button type="button" class="checklist-invoice-add" data-id="${item.id}">${ICON_CLIP} Прикрепить счёт</button>`;

            div.innerHTML = `
                <div class="checklist-item-header">
                    <label class="checklist-label">
                        <input type="checkbox" class="checklist-cb-main" data-id="${item.id}" ${item.is_paid ? 'checked' : ''}>
                        <input type="checkbox" class="checklist-cb-sec" data-id="${item.id}" ${item.is_secondary_check ? 'checked' : ''}>
                        <span class="checklist-text ${isCompleted ? 'completed' : ''}">
                            <span class="checklist-company">${escapeHtml(item.company_name)}</span>
                            <span class="amount-field">
                                <input type="text" inputmode="decimal" autocomplete="off" class="checklist-amount" data-id="${item.id}" value="${formatMoney(item.amount)}" title="Сумма к оплате — нажмите, чтобы изменить" aria-label="Сумма к оплате">
                                <span class="amount-cur">BYN</span>
                            </span>
                        </span>
                    </label>
                    ${!item.supplier_id
                        ? `<button type="button" class="checklist-link-supplier" data-id="${item.id}" title="Записан текстом. Привязать к поставщику из справочника">не привязан</button>`
                        : ''}
                    <button class="delete-checklist" data-id="${item.id}">&times;</button>
                </div>
                <div class="checklist-supplier-picker" data-id="${item.id}" hidden></div>
                <input type="text" class="checklist-note" data-id="${item.id}" value="${escapeHtml(item.note)}" placeholder="Примечание...">
                ${invoiceRow}
                <input type="file" class="checklist-invoice-input" data-id="${item.id}" hidden>
            `;

            div.addEventListener('dragover', (e) => { e.preventDefault(); div.classList.add('drop-target'); });
            div.addEventListener('dragleave', () => div.classList.remove('drop-target'));
            div.addEventListener('drop', async (e) => {
                e.preventDefault();
                div.classList.remove('drop-target');
                const file = e.dataTransfer.files[0];
                if (!file) return;
                if (file.size > 25 * 1024 * 1024) {
                    showToast('Файл слишком большой (макс. 25 МБ)', 'error');
                    return;
                }
                try {
                    const formData = new FormData();
                    formData.append('file', file);
                    await apiFetch(`/checklists/${item.id}/invoice`, { method: 'POST', body: formData });
                    openCardModal(card.id);
                    showToast('Счёт прикреплён', 'success');
                } catch (err) {
                    showToast('Не удалось загрузить: ' + err.message, 'error');
                }
            });

            checklistContainer.appendChild(div);
        });
    }

    // 2. Отрисовка файлов
    const attachmentsContainer = document.getElementById('attachments-container');
    if (card.attachments) {
        card.attachments.forEach(file => {
            const fileDiv = document.createElement('div');
            fileDiv.className = 'attachment-item';
            fileDiv.innerHTML = `
                <a href="#" class="attachment-link" data-download="${escapeHtml(file.file_path.split('/').pop())}" data-nice-name="${escapeHtml(file.file_name)}">${ICON_FILE} ${escapeHtml(file.file_name)}</a>
                <button class="btn-delete-attachment" data-file-id="${file.id}" data-card-id="${card.id}">&times;</button>
            `;
            attachmentsContainer.appendChild(fileDiv);
        });
    }

    // --- ОБРАБОТЧИКИ СОБЫТИЙ (event delegation) ---

    container.removeEventListener('click', container._modalClickHandler);
    container._modalClickHandler = (e) => {
        if (e.target.classList.contains('btn-delete-attachment')) {
            deleteAttachment(parseInt(e.target.dataset.fileId), parseInt(e.target.dataset.cardId));
        }
    };
    container.addEventListener('click', container._modalClickHandler);

    container.removeEventListener('change', container._modalChangeHandler);
    container._modalChangeHandler = async (e) => {
        if (e.target.id === 'input-total-amount') {
            try {
                await apiFetch(`/cards/${card.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ total_amount: parseFloat(e.target.value) || 0 }) });
                if (window.refreshCardOnBoard) refreshCardOnBoard(card.id); else loadKanbanBoard();
            } catch (err) {
                showToast('Не удалось сохранить сумму: ' + err.message, 'error');
            }
        } else if (e.target.id === 'input-due-date') {
            try {
                const val = e.target.value || null;
                await apiFetch(`/cards/${card.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ due_date: val }) });
                if (window.refreshCardOnBoard) refreshCardOnBoard(card.id); else loadKanbanBoard();
                showToast('Дата сохранена', 'success');
            } catch (err) {
                showToast('Не удалось сохранить дату: ' + err.message, 'error');
            }
        } else if (e.target.id === 'input-description') {
            try {
                await apiFetch(`/cards/${card.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ description: e.target.value }) });
            } catch (err) {
                showToast('Не удалось сохранить комментарий: ' + err.message, 'error');
            }
        }
    };
    container.addEventListener('change', container._modalChangeHandler);

    const STORE_OPTIONS = APP_STORES;
    const storeDropdown = createDropdown({
        options: STORE_OPTIONS,
        value: card.store_location || '',
        onChange: async (val) => {
            await apiFetch(`/cards/${card.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ store_location: val }) });
            if (window.refreshCardOnBoard) refreshCardOnBoard(card.id); else loadKanbanBoard();
            if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
            if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
            if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
        }
    });
    document.getElementById('store-mount').appendChild(storeDropdown);

    let allClientsList = [];
    try {
        allClientsList = await apiFetch('/clients');
    } catch(e) {}
    const clientOptions = [{ value: '', label: '— Не привязан —' }, ...allClientsList.map(c => ({ value: String(c.id), label: c.name + (c.unp ? ' (УНП: ' + c.unp + ')' : '') }))];
    const clientDropdown = createDropdown({
        options: clientOptions,
        value: String(card.client_id || ''),
        searchable: true,
        onChange: async (val) => {
            const cid = val ? parseInt(val) : null;
            try {
                await apiFetch(`/cards/${card.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ client_id: cid }) });
            } catch (err) {
                showToast('Не удалось сохранить клиента: ' + err.message, 'error');
            }
        }
    });
    document.getElementById('client-mount').appendChild(clientDropdown);

    // Поле суммы: Enter — сохранить, Esc — отменить правку
    checklistContainer.addEventListener('keydown', (e) => {
        const el = e.target;
        if (!el.classList || !el.classList.contains('checklist-amount')) return;
        if (e.key === 'Enter') { e.preventDefault(); el.blur(); return; }
        if (e.key === 'Escape') {
            e.preventDefault();
            const prev = (card.checklists || []).find(c => String(c.id) === String(el.dataset.id));
            if (prev) el.value = formatMoney(prev.amount);
            el.blur();
        }
    });

    // в фокусе — сырое число (удобно править), без фокуса — с разделителями
    checklistContainer.addEventListener('focusin', (e) => {
        const el = e.target;
        if (!el.classList || !el.classList.contains('checklist-amount')) return;
        const n = parseMoney(el.value);
        el.value = (n === null) ? '' : String(n);
        setTimeout(() => { try { el.select(); } catch (err) {} }, 0);
    });

    // фильтр ввода: только цифры и один разделитель, не больше 2 знаков после него
    checklistContainer.addEventListener('input', (e) => {
        const el = e.target;
        if (!el.classList || !el.classList.contains('checklist-amount')) return;
        let v = el.value.replace(/[^\d.,]/g, '').replace(/,/g, '.');
        const parts = v.split('.');
        if (parts.length > 2) v = parts[0] + '.' + parts.slice(1).join('');
        v = v.replace(/^(\d*\.\d{0,2}).*$/, '$1');
        if (v !== el.value) {
            const pos = el.selectionStart;
            el.value = v;
            try { el.setSelectionRange(pos - 1 < 0 ? 0 : pos, pos - 1 < 0 ? 0 : pos); } catch (err) {}
        }
    });

    checklistContainer.addEventListener('change', async (e) => {
        if (!e.target.dataset.id) return;
        const id = e.target.dataset.id;

        if (e.target.classList.contains('checklist-invoice-input')) {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            if (file.size > 25 * 1024 * 1024) {
                showToast('Файл слишком большой (макс. 25 МБ)', 'error');
                return;
            }
            try {
                const formData = new FormData();
                formData.append('file', file);
                await apiFetch(`/checklists/${id}/invoice`, { method: 'POST', body: formData });
                openCardModal(card.id);
                showToast('Счёт прикреплён', 'success');
            } catch (err) {
                showToast('Не удалось загрузить счёт: ' + err.message, 'error');
            }
            return;
        }

        const isNote = e.target.classList.contains('checklist-note');
        const isAmount = e.target.classList.contains('checklist-amount');

        let body = {};
        if (e.target.classList.contains('checklist-cb-main')) body = { is_paid: e.target.checked };
        else if (e.target.classList.contains('checklist-cb-sec')) body = { is_secondary_check: e.target.checked };
        else if (isNote) body = { note: e.target.value };
        else if (isAmount) {
            const prev = (card.checklists || []).find(c => String(c.id) === String(id));
            const num = parseMoney(e.target.value);

            if (num === null || num < 0) {
                showToast('Сумма должна быть положительным числом', 'error');
                if (prev) e.target.value = formatMoney(prev.amount);
                e.target.classList.add('amount-invalid');
                setTimeout(() => e.target.classList.remove('amount-invalid'), 900);
                return;
            }

            // без фокуса показываем с разделителями разрядов
            e.target.value = formatMoney(num);

            if (prev && Number(prev.amount) === num) return;   // не изменилось — запрос не нужен
            body = { amount: num };
        }

        try {
            await apiFetch(`/checklists/${id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });

            // Обновляем карточку В ПАМЯТИ: раньше менялась только галочка в DOM,
            // а card.checklists оставался старым — поэтому сводка пересчитывалась
            // лишь при повторном открытии карточки.
            const item = (card.checklists || []).find(c => String(c.id) === String(id));
            if (item) Object.assign(item, body);

            renderChecklistSummary(card);
            updateChecklistItemState(e.target, item);

            if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
        } catch (err) {
            showToast('Не удалось сохранить: ' + err.message, 'error');
            // откатываем галочку, раз сохранить не удалось
            if (e.target.type === 'checkbox') e.target.checked = !e.target.checked;
        }
    });

    container.removeEventListener('click', container._downloadHandler);
    container._downloadHandler = async (e) => {
        const downloadEl = e.target.closest('[data-download]');
        if (downloadEl) {
            // настоящее имя лежит в data-nice-name (или title / тексте ссылки)
            e.preventDefault();
            const nice = downloadEl.dataset.niceName
                || downloadEl.getAttribute('title')
                || (downloadEl.textContent || '').trim();
            downloadFile(downloadEl.dataset.download, nice);
        }
    };
    container.addEventListener('click', container._downloadHandler);

    checklistContainer.addEventListener('click', async (e) => {
        // Старая запись, введённая текстом: раскрываем выбор поставщика.
        // Само название при этом не теряется — оно останется, пока
        // пользователь сам не выберет поставщика из справочника.
        if (e.target.classList.contains('checklist-link-supplier')) {
            const id = e.target.dataset.id;
            const mount = checklistContainer.querySelector(`.checklist-supplier-picker[data-id="${id}"]`);
            if (!mount) return;
            if (!mount.hidden) { mount.hidden = true; return; }
            mount.hidden = false;
            mount.innerHTML = '<div class="supplier-empty">Загрузка...</div>';
            await mountSupplierPicker(mount, null, async (supplierId, supplierName) => {
                if (!supplierId) return;
                try {
                    await apiFetch(`/checklists/${id}`, {
                        method: 'PATCH',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            supplier_id: supplierId,
                            company_name: supplierName
                        })
                    });
                    showToast('Поставщик привязан', 'success');
                    openCardModal(card.id);
                    if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
                } catch (err) {
                    showToast('Не удалось привязать: ' + err.message, 'error');
                }
            });
            return;
        }

        if (e.target.classList.contains('delete-checklist')) {
            if (!await confirmDialog('Удалить пункт чек-листа?')) return;
            try {
                await apiFetch(`/checklists/${e.target.dataset.id}`, { method: 'DELETE' });
                openCardModal(card.id);
                if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
            } catch (err) {
                showToast('Ошибка удаления: ' + err.message, 'error');
            }
            return;
        }
        if (e.target.classList.contains('checklist-invoice-add')) {
            const input = checklistContainer.querySelector(`.checklist-invoice-input[data-id="${e.target.dataset.id}"]`);
            if (input) input.click();
            return;
        }
        if (e.target.classList.contains('checklist-invoice-del')) {
            if (!await confirmDialog('Открепить счёт от пункта?')) return;
            try {
                await apiFetch(`/checklists/${e.target.dataset.id}/invoice`, { method: 'DELETE' });
                openCardModal(card.id);
                showToast('Счёт откреплён', 'success');
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
            return;
        }
    });

    // Поставщик выбирается из справочника, а не вводится руками.
    let newSupplierId = null;
    let newSupplierName = '';
    mountSupplierPicker(document.getElementById('new-supplier-mount'), null, (id, name) => {
        newSupplierId = id;
        newSupplierName = name || '';
    });

    const addChecklistAction = async () => {
        const amountEl = document.getElementById('new-amount');
        const amount = parseFloat(amountEl.value);
        if (!newSupplierId) return showToast('Выберите поставщика', 'error');
        if (!amount || amount <= 0) { amountEl.focus(); return showToast('Укажите сумму', 'error'); }
        try {
            await apiFetch(`/cards/${card.id}/checklists`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                // company_name шлём всегда: это снимок названия, и он же
                // позволяет работать, пока бэкенд ещё не знает supplier_id
                body: JSON.stringify({
                    supplier_id: newSupplierId,
                    company_name: newSupplierName,
                    amount: amount
                })
            });
            openCardModal(card.id);
            if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
        } catch (err) {
            // 422 = сервер ещё не знает поле supplier_id (не перезапущен бэкенд)
            const msg = /422|unprocessable|extra fields|supplier_id/i.test(err.message || '')
                ? 'Сервер не принял поставщика — нужен перезапуск бэкенда'
                : 'Не удалось добавить: ' + err.message;
            showToast(msg, 'error');
        }
    };

    document.getElementById('btn-add-checklist').onclick = addChecklistAction;
    document.getElementById('new-amount').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addChecklistAction();
    });

    const dropzone = document.getElementById('file-dropzone');
    const fileInput = document.getElementById('file-input');
    dropzone.onclick = () => fileInput.click();
    fileInput.onchange = (e) => uploadFiles(card.id, e.target.files);
    dropzone.ondragover = (e) => { e.preventDefault(); dropzone.style.borderColor = 'var(--border-interactive)'; };
    dropzone.ondragleave = () => dropzone.style.borderColor = 'var(--border-color)';
    dropzone.ondrop = (e) => { e.preventDefault(); uploadFiles(card.id, e.dataTransfer.files); };

    document.getElementById('btn-to-assembly').onclick = async () => {
        const store = storeDropdown.dataset.value;
        if (!store) return showToast("Выберите магазин перед отправкой в сборку", 'error');

        try {
            await apiFetch(`/payments/trigger_from_card/${card.id}`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ store_location: store }) });
        } catch (e) { 
            if (!e.message.includes('уже добавлена')) {
                showToast('Ошибка: ' + e.message, 'error');
            }
        }

        try {
            await apiFetch(`/kanban/cards/${card.id}/status`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ status: "Сборка" }) });
        } catch (e) { console.error("Ошибка статуса:", e); }

        closeModal(); loadKanbanBoard();
    };

    document.getElementById('btn-trigger-payment').onclick = async () => {
        const store = storeDropdown.dataset.value;
        if (!store) return showToast("Выберите магазин перед списанием", 'error');

        try {
            await apiFetch(`/payments/trigger_from_card/${card.id}`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ store_location: store }) });
        } catch (e) { 
            if (!e.message.includes('уже добавлена')) {
                showToast('Ошибка: ' + e.message, 'error');
            }
        }

        try {
            await apiFetch(`/kanban/cards/${card.id}/status`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ status: "На списание" }) });
        } catch (e) { console.error("Ошибка статуса:", e); }

        closeModal(); 
        loadKanbanBoard();
        
        if (typeof loadWriteoffsBoard === 'function') {
            loadWriteoffsBoard();
        }
    };
}

// === ТЕГИ ===
async function loadCardTags(card) {
    const tagsMount = document.getElementById('tags-mount');
    if (!tagsMount) return;

    try {
        const allTags = await apiFetch('/tags');
        const cardTagIds = new Set((card.tags || []).map(t => t.id));

        const renderTagPills = () => {
            tagsMount.innerHTML = '';
            const selectedTags = allTags.filter(t => cardTagIds.has(t.id));
            selectedTags.forEach(tag => {
                const pill = document.createElement('span');
                pill.className = 'tag-pill';
                const c = sanitizeColor(tag.color);
                const r = parseInt(c.slice(1,3),16), g = parseInt(c.slice(3,5),16), b = parseInt(c.slice(5,7),16);
                const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
                const blend = isDark ? 0.3 : 0.8;
                const bg = isDark
                    ? `rgba(${r},${g},${b},${blend})`
                    : `rgb(${Math.round(r+(255-r)*blend)},${Math.round(g+(255-g)*blend)},${Math.round(b+(255-b)*blend)})`;
                pill.style.color = c;
                pill.style.background = bg;
                pill.innerHTML = `${escapeHtml(tag.name)} <button class="tag-remove" data-id="${tag.id}" title="Удалить тег">&times;</button>`;
                pill.querySelector('.tag-remove').onclick = async (e) => {
                    e.stopPropagation();
                    try {
                        await apiFetch(`/tags/cards/${card.id}/tags/${tag.id}`, { method: 'DELETE' });
                        cardTagIds.delete(tag.id);
                        renderTagPills();
                        if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
                    } catch (err) {
                        showToast('Ошибка: ' + err.message, 'error');
                    }
                };
                tagsMount.appendChild(pill);
            });

            const addBtn = document.createElement('button');
            addBtn.className = 'tag-add-btn';
            addBtn.textContent = '+';
            addBtn.title = 'Добавить тег';
            addBtn.onclick = (e) => {
                e.stopPropagation();
                const existingMenu = tagsMount.querySelector('.tag-dropdown-menu');
                if (existingMenu) { existingMenu.remove(); return; }

                const menu = document.createElement('div');
                menu.className = 'tag-dropdown-menu';
                allTags.forEach(tag => {
                    if (cardTagIds.has(tag.id)) return;
                    const item = document.createElement('button');
                    item.className = 'tag-dropdown-item';
                    item.innerHTML = `<span class="tag-dot" style="background:${sanitizeColor(tag.color)}"></span>${escapeHtml(tag.name)}`;
                    item.onclick = async (ev) => {
                        ev.stopPropagation();
                        try {
                            await apiFetch(`/tags/cards/${card.id}/tags/${tag.id}`, { method: 'POST' });
                            cardTagIds.add(tag.id);
                            renderTagPills();
                            if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
                        } catch (err) {
                            showToast('Ошибка: ' + err.message, 'error');
                        }
                        menu.remove();
                    };
                    menu.appendChild(item);
                });

                if (allTags.every(t => cardTagIds.has(t.id))) {
                    const empty = document.createElement('div');
                    empty.className = 'tag-dropdown-empty';
                    empty.textContent = 'Все теги добавлены';
                    menu.appendChild(empty);
                }

                const createItem = document.createElement('button');
                createItem.className = 'tag-dropdown-item tag-dropdown-create';
                createItem.textContent = '+ Создать новый тег';
                createItem.onclick = async (ev) => {
                    ev.stopPropagation();
                    menu.remove();
                    showTagCreateDialog(card, allTags, cardTagIds, tagsMount, renderTagPills);
                };
                menu.appendChild(createItem);

                tagsMount.appendChild(menu);
            };
            tagsMount.appendChild(addBtn);
        };

        renderTagPills();
    } catch (err) {
        tagsMount.innerHTML = '<span class="tag-error">Не удалось загрузить теги</span>';
    }
}

// === АКТИВНОСТЬ ===
async function loadCardActivity(cardId) {
    const container = document.getElementById('activity-container');
    if (!container) return;

    try {
        const activities = await apiFetch(`/activity?card_id=${cardId}&limit=10`);
        if (activities.length === 0) {
            container.innerHTML = '<div class="activity-empty">Нет действий</div>';
            return;
        }

        // Отменённые действия зачёркиваем: запись «Выписана накладная ТН123»
        // остаётся в истории, но видно, что она уже неактуальна.
        const digits = (v) => String(v || '').replace(/\D+/g, '');
        const cancelled = new Set();
        activities.forEach(act => {
            if (/отмен|удал/i.test(act.action || '')) {
                const key = digits(act.details);
                if (key) cancelled.add(key);
            }
        });

        container.innerHTML = '';
        activities.forEach(act => {
            const item = document.createElement('div');
            const isCancelAct = /отмен|удал/i.test(act.action || '');
            const key = digits(act.details);
            // зачёркиваем только исходное действие, не саму запись об отмене
            const isStale = !isCancelAct && /накладн/i.test(act.action || '')
                            && key && cancelled.has(key);
            item.className = 'activity-item' + (isStale ? ' activity-stale' : '')
                             + (isCancelAct ? ' activity-cancel' : '');
            const time = new Date(act.created_at).toLocaleString('ru-RU');
            const details = act.details ? `<span class="activity-details">${escapeHtml(act.details)}</span>` : '';
            item.innerHTML = `
                <span class="activity-action">${escapeHtml(act.action)}</span>
                ${details}
                <span class="activity-time">${time}</span>
            `;
            container.appendChild(item);
        });
    } catch (err) {
        container.innerHTML = `<div class="activity-error">Ошибка загрузки: ${escapeHtml(err.message)}</div>`;
    }
}

const TAG_COLORS = [
    '#4f7cf5', '#ef4444', '#f59e0b', '#10b981', '#8b5cf6',
    '#ec4899', '#06b6d4', '#f97316', '#6366f1', '#14b8a6',
    '#e11d48', '#84cc16', '#0ea5e9', '#a855f7', '#64748b'
];

function showTagCreateDialog(card, allTags, cardTagIds, tagsMount, renderTagPills) {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    let selectedColor = TAG_COLORS[0];

    const colorSquares = TAG_COLORS.map((c, i) =>
        `<button type="button" class="tag-color-sq ${i === 0 ? 'selected' : ''}" data-color="${c}" style="background:${c}"></button>`
    ).join('');

    overlay.innerHTML = `
        <div class="confirm-box tag-create-box">
            <p class="confirm-message">Новый тег</p>
            <input type="text" id="tag-create-name" class="tag-create-input" placeholder="Название тега..." maxlength="30">
            <div class="tag-color-palette">${colorSquares}</div>
            <div class="confirm-actions">
                <button type="button" class="confirm-cancel">Отмена</button>
                <button type="button" class="confirm-ok">Создать</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('show'));

    const colorBtns = overlay.querySelectorAll('.tag-color-sq');
    colorBtns.forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            selectedColor = btn.dataset.color;
            colorBtns.forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
        };
    });

    const nameInput = overlay.querySelector('#tag-create-name');
    setTimeout(() => nameInput.focus(), 50);

    const close = async (create) => {
        overlay.classList.remove('show');
        setTimeout(() => overlay.remove(), 200);
        if (!create) return;
        const name = nameInput.value.trim();
        if (!name) { showToast('Введите название тега', 'error'); return; }
        try {
            const newTag = await apiFetch('/tags', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ name, color: selectedColor }) });
            allTags.push(newTag);
            await apiFetch(`/tags/cards/${card.id}/tags/${newTag.id}`, { method: 'POST' });
            cardTagIds.add(newTag.id);
            renderTagPills();
            if (window.refreshCardOnBoard) refreshCardOnBoard(card.id);
            showToast('Тег создан', 'success');
        } catch (err) {
            showToast('Ошибка создания тега: ' + err.message, 'error');
        }
    };

    overlay.querySelector('.confirm-cancel').onclick = () => close(false);
    overlay.querySelector('.confirm-ok').onclick = () => close(true);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') close(true); });
}

async function uploadFiles(cardId, files) {
    if (!files.length) return;
    const MAX_SIZE = 25 * 1024 * 1024;
    for (let file of files) {
        if (file.size > MAX_SIZE) {
            showToast(`Файл "${file.name}" слишком большой (макс. 25 МБ)`, 'error');
            continue;
        }
        const formData = new FormData();
        formData.append('file', file);
        await apiFetch(`/cards/${cardId}/attachments`, { method: 'POST', body: formData });
    }
    openCardModal(cardId);
}

async function deleteAttachment(fileId, cardId) {
    if (await confirmDialog('Удалить файл?')) {
        await apiFetch(`/attachments/${fileId}`, { method: 'DELETE' });
        openCardModal(cardId);
        showToast('Файл удалён', 'success');
    }
}

/**
 * Скачать вложение.
 * @param {string} filename имя файла на диске (хеш_имя)
 * @param {string} niceName исходное имя из базы — его и покажем пользователю
 *
 * Имена на диске у старых писем потеряли расширение: сырой
 * MIME-заголовок =?UTF-8?B?...?= прогонялся через re.sub, и «.pdf»
 * превращался в «_». Браузер без расширения сохранял файл как .txt.
 * Берём расширение из настоящего имени, а если и там нет — определяем
 * по первым байтам содержимого.
 */
async function sniffExt(blob) {
    try {
        const head = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
        const hex = Array.from(head).map(b => b.toString(16).padStart(2, '0')).join('');
        if (hex.startsWith('25504446')) return '.pdf';               // %PDF
        if (hex.startsWith('504b0304')) return '.docx';              // ZIP -> docx/xlsx
        if (hex.startsWith('d0cf11e0')) return '.doc';               // старый MS Office
        if (hex.startsWith('ffd8ff')) return '.jpg';
        if (hex.startsWith('89504e47')) return '.png';
        if (hex.startsWith('52617221')) return '.rar';
    } catch (e) {}
    return '';
}

async function downloadFile(filename, niceName) {
    const token = getToken();
    try {
        const resp = await fetch(API_BASE_URL + '/files/' + encodeURIComponent(filename), {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!resp.ok) throw new Error('Ошибка ' + resp.status);
        const blob = await resp.blob();

        // выбираем имя для сохранения
        let outName = (niceName || '').trim() || filename;
        if (window.CRM_DECODE_MIME) outName = window.CRM_DECODE_MIME(outName) || outName;
        outName = outName.replace(/[\\/:*?"<>|]+/g, '_').replace(/\s+/g, ' ').trim();

        const hasExt = /\.[A-Za-z0-9]{2,5}$/.test(outName);
        if (!hasExt) {
            const fromDisk = (filename.match(/\.[A-Za-z0-9]{2,5}$/) || [''])[0];
            outName += fromDisk || (await sniffExt(blob));
        }

        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = outName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        showToast('Не удалось скачать файл: ' + err.message, 'error');
    }
}


/* ============================================================
   НАКЛАДНЫЕ В КАРТОЧКЕ (добавлено 05.08.2026)
   Показываются только на этапах «Сборка / На списание / Закрыто».
   Каждая накладная = отдельная транзакция с тем же card_id,
   каждая отдельно попадает на доску списания и в Документы.
   ============================================================ */

const INVOICE_STAGES = ['Сборка', 'На списание', 'Закрыто'];

async function renderCardInvoices(card) {
    const section = document.getElementById('invoices-section');
    if (!section) return;

    if (!INVOICE_STAGES.includes(card.status)) {
        section.hidden = true;
        return;
    }
    section.hidden = false;

    const container = document.getElementById('invoices-container');
    const summaryEl = document.getElementById('invoices-summary');
    const badge = document.getElementById('invoices-badge');
    container.innerHTML = '<div class="inv-loading">Загрузка...</div>';

    let data;
    try {
        data = await apiFetch(`/payments/cards/${card.id}/invoices`);
    } catch (e) {
        container.innerHTML = `<div class="inv-empty">Не удалось загрузить: ${escapeHtml(e.message)}</div>`;
        return;
    }

    badge.textContent = data.issued.length || '';
    container.innerHTML = '';

    // --- выписанные накладные: закрытые пункты чек-листа ---
    data.issued.forEach((inv, i) => {
        const row = document.createElement('div');
        row.className = 'checklist-item inv-item is-done';
        const dateStr = inv.invoice_date
            ? new Date(inv.invoice_date + 'T00:00:00').toLocaleDateString('ru-RU')
            : '';
        row.innerHTML = `
            <div class="checklist-item-header">
                <label class="checklist-label">
                    <span class="inv-check" aria-hidden="true">
                        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    </span>
                    <span class="checklist-text inv-num-text">${escapeHtml(inv.invoice_number || '№ не указан')}</span>
                </label>
                <span class="inv-right">
                    <span class="inv-amount-text">${inv.amount.toFixed(2)} BYN</span>
                    <button class="delete-checklist inv-del" data-id="${inv.id}" title="Удалить накладную">&times;</button>
                </span>
            </div>
            <div class="inv-sub">${dateStr ? escapeHtml(dateStr) + ' · ' : ''}${escapeHtml(inv.store_location || '')}${inv.store_location ? ' · ' : ''}${inv.written_off ? 'списана' : 'ждёт списания'}</div>
        `;
        container.appendChild(row);
    });

    // --- активный пункт: незаполненная строка на остаток ---
    if (data.rest > 0.01 && !data.closed) {
        const draft = document.createElement('div');
        draft.className = 'checklist-item inv-item inv-draft';
        draft.innerHTML = `
            <div class="checklist-item-header">
                <label class="checklist-label">
                    <span class="inv-check inv-check-empty" aria-hidden="true"></span>
                    <span class="checklist-text inv-draft-title">Накладная ${data.issued.length + 1}</span>
                </label>
                <span class="inv-right"><span class="inv-rest-hint">остаток ${data.rest.toFixed(2)} BYN</span></span>
            </div>
            <div class="inv-draft-fields">
                <input type="text" id="new-inv-num" class="inv-in inv-in-num" placeholder="№ накладной">
                <input type="date" id="new-inv-date" class="inv-in inv-in-date" value="${new Date().toISOString().slice(0, 10)}">
                <input type="number" step="0.01" id="new-inv-amount" class="inv-in inv-in-amount" value="${data.rest.toFixed(2)}" placeholder="0.00">
            </div>
            <div class="inv-draft-actions">
                <button id="btn-cancel-invoice" class="btn-secondary btn-sm">Отменить</button>
                <button id="btn-save-invoice" class="btn-primary btn-sm">Выписать</button>
            </div>
        `;
        container.appendChild(draft);

        const numEl = draft.querySelector('#new-inv-num');
        const dateEl = draft.querySelector('#new-inv-date');
        const amtEl = draft.querySelector('#new-inv-amount');

        draft.querySelector('#btn-cancel-invoice').onclick = () => {
            numEl.value = '';
            amtEl.value = data.rest.toFixed(2);
            numEl.focus();
        };

        draft.querySelector('#btn-save-invoice').onclick = async () => {
            const number = numEl.value.trim();
            const amount = parseFloat(amtEl.value || 0);
            if (!number) { numEl.focus(); return showToast('Укажите номер накладной', 'error'); }
            if (!amount || amount <= 0) { amtEl.focus(); return showToast('Укажите сумму накладной', 'error'); }
            if (amount > data.rest + 0.01) {
                amtEl.focus();
                return showToast(`Сумма больше остатка (${data.rest.toFixed(2)} BYN)`, 'error');
            }
            const btn = draft.querySelector('#btn-save-invoice');
            btn.disabled = true;
            try {
                const res = await apiFetch(`/payments/cards/${card.id}/issue-invoice`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        invoice_number: number,
                        invoice_date: dateEl.value || null,
                        amount: amount,
                        store_location: data.store_location || card.store_location || null
                    })
                });
                showToast(res.message, 'success');
                if (res.card_closed) card.status = 'Закрыто';
                await renderCardInvoices(card);
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            } finally { btn.disabled = false; }
        };

        [numEl, dateEl, amtEl].forEach(inp => {
            inp.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); draft.querySelector('#btn-save-invoice').click(); }
            });
        });
    } else if (!data.issued.length) {
        container.innerHTML = '<div class="inv-empty">Накладных пока нет</div>';
    }

    // --- удаление выписанной накладной ---
    container.querySelectorAll('.inv-del').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            if (!await confirmDialog('Удалить эту накладную? Сумма вернётся в остаток.')) return;
            try {
                await apiFetch(`/payments/transactions/${e.currentTarget.dataset.id}`, { method: 'DELETE' });
                // статус сделки пересчитывается по факту, а не назначается вслепую
                try {
                    const r = await apiFetch(`/payments/cards/${card.id}/sync-writeoff-status`, { method: 'POST' });
                    card.status = r.status;
                } catch (e2) {}
                await renderCardInvoices(card);
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
                showToast('Накладная удалена, сумма вернулась в остаток', 'success');
            } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
        });
    });

    // --- сводка: одна строка + тонкий прогресс ---
    const pct = data.card_amount > 0
        ? Math.min(100, Math.round(data.issued_amount / data.card_amount * 100))
        : 0;
    const full = data.rest <= 0.01;
    summaryEl.innerHTML = `
        <div class="inv-sum-line">
            <span>Выписано <b>${data.issued.length}</b> ${declOf(data.issued.length, ['накладная', 'накладные', 'накладных'])}</span>
            <span class="inv-sum-money"><b>${data.issued_amount.toFixed(2)}</b> / ${data.card_amount.toFixed(2)} BYN</span>
            ${full
                ? '<span class="inv-badge badge-ok">закрыто полностью</span>'
                : `<span class="inv-badge badge-warn">остаток ${data.rest.toFixed(2)}</span>`}
        </div>
        <div class="inv-progress"><div class="inv-progress-fill${full ? ' fill-done' : ''}" style="width:${pct}%"></div></div>
    `;
}

function declOf(n, forms) {
    const a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1) return forms[0];
    return forms[2];
}


/* ============================================================
   СВЯЗЫВАНИЕ ПИСЕМ С ПРОШЛЫМИ СДЕЛКАМИ (05.08.2026)
   Ищем ТОЛЬКО по адресу отправителя. Тема не используется:
   заявки с сайта всегда с одинаковым заголовком, но это
   разные клиенты — автосклейка по теме недопустима.
   ============================================================ */
async function renderRelatedCards(card) {
    const mount = document.getElementById('related-cards-mount');
    if (!mount || !card.sender_email) return;

    let data;
    try {
        data = await apiFetch(`/email-parser/related/${card.id}`);
    } catch (e) { return; }

    if (!data.related || !data.related.length) return;

    mount.innerHTML = `
        <div class="related-banner">
            <div class="related-head">
                От <b>${escapeHtml(data.sender_email)}</b> уже есть
                ${data.related.length} ${data.related.length === 1 ? 'сделка' : 'сделок'}
            </div>
            <div class="related-list">
                ${data.related.map(r => `
                    <div class="related-item">
                        <a href="#" class="related-open" data-id="${r.id}">#${r.id} ${escapeHtml((r.title || '').slice(0, 46))}</a>
                        <span class="related-status">${escapeHtml(r.status || '')}</span>
                        <button class="btn-link-card" data-id="${r.id}">Присоединить</button>
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    mount.querySelectorAll('.related-open').forEach(a => {
        a.addEventListener('click', (e) => { e.preventDefault(); openCardModal(+e.target.dataset.id); });
    });

    mount.querySelectorAll('.btn-link-card').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const targetId = +e.target.dataset.id;
            if (!await confirmDialog(`Перенести это письмо в сделку #${targetId}? Текущая карточка уйдёт в корзину.`)) return;
            try {
                const res = await apiFetch(`/email-parser/link/${card.id}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target_card_id: targetId })
                });
                showToast(res.message || 'Письмо связано', 'success');
                closeModal();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                setTimeout(() => openCardModal(targetId), 300);
            } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
        });
    });
}


/* ============================================================
   СВОДКА ПО ЗАКУПКЕ У ПОСТАВЩИКОВ (06.08.2026)

   Чек-лист карточки = счета ПОСТАВЩИКОВ, которые нужно оплатить,
   чтобы закупить товар под этот заказ. Это НЕ оплата заказа
   клиентом — суммы не связаны и не сравниваются с суммой сделки.
   Считаем только: сколько поставщикам должны и сколько уже оплатили.
   ============================================================ */
function renderChecklistSummary(card) {
    const el = document.getElementById('checklist-summary');
    if (!el) return;

    const items = card.checklists || [];
    if (!items.length) { el.innerHTML = ''; return; }

    const total = items.reduce((a, i) => a + (parseFloat(i.amount) || 0), 0);
    const paidItems = items.filter(i => i.is_paid);
    const paid = paidItems.reduce((a, i) => a + (parseFloat(i.amount) || 0), 0);
    const camePaid = items.filter(i => i.is_secondary_check).length;
    const rest = +(total - paid).toFixed(2);
    const pct = total > 0 ? Math.min(100, Math.round(paid / total * 100)) : 0;
    const full = rest <= 0.01;

    el.innerHTML = `
        <div class="chk-sum-line">
            <span>Поставщикам <b>${total.toFixed(2)}</b> BYN · заказано ${paidItems.length}/${items.length} · пришло ${camePaid}/${items.length}</span>
            ${full ? '' : `<span class="pay-badge pay-partial">осталось оплатить ${rest.toFixed(2)}</span>`}
        </div>
        <div class="inv-progress"><div class="inv-progress-fill${full ? ' fill-done' : ''}" style="width:${pct}%"></div></div>
    `;
}


/* ============================================================
   КНОПКИ ЭТАПОВ СДЕЛКИ (06.08.2026)
   Порядок работы: сначала «В Сборку», и только потом «В Списание».
   Списывать можно, когда сделка собрана ИЛИ счёт уже оплачен —
   раньше обе кнопки висели всегда и позволяли перепрыгнуть этап.
   ============================================================ */
function applyStageButtons(card) {
    const btnAssembly = document.getElementById('btn-to-assembly');
    const btnWriteoff = document.getElementById('btn-trigger-payment');
    if (!btnAssembly || !btnWriteoff) return;

    const status = card.status || '';

    // «В Сборку» — на ранних этапах. Уже в сборке/списании она не нужна.
    const canAssembly = ['Новый запрос', 'В работе', 'Ждет оплаты'].includes(status);
    // «В Списание» — только из «Сборки». Чек-лист поставщиков на это не влияет:
    // он про закупку товара, а не про оплату заказа клиентом.
    const canWriteoff = status === 'Сборка';

    btnAssembly.hidden = !canAssembly;
    btnWriteoff.hidden = !canWriteoff;

    // Сделка уже на списании или закрыта — этапные кнопки не нужны,
    // работа идёт через блок «Накладные» и доску списания.
    const actions = btnAssembly.closest('.modal-actions');
    if (actions) actions.hidden = !canAssembly && !canWriteoff;
}


/* ============================================================
   ЖИВОЕ ОБНОВЛЕНИЕ ПУНКТА ЧЕК-ЛИСТА (06.08.2026)
   Пункт считается закрытым, когда счёт и оплачен, и товар пришёл.
   Раньше зачёркивание появлялось только после переоткрытия карточки.
   ============================================================ */
function updateChecklistItemState(input, item) {
    if (!item) return;
    const row = input.closest('.checklist-item');
    if (!row) return;
    const text = row.querySelector('.checklist-text');
    if (text) text.classList.toggle('completed', !!(item.is_paid && item.is_secondary_check));
}


/* ============================================================
   ВЫБОР ПОСТАВЩИКА В ЧЕК-ЛИСТЕ (06.08.2026)

   Поставщики ведутся в разделе «Поставщики», и каждый пункт
   закупки закрепляется за конкретным поставщиком — так же,
   как сделка закрепляется за клиентом. Ручной ввод убран:
   иначе одна и та же компания попадала в базу в трёх написаниях.
   ============================================================ */
let _suppliersCache = null;

async function loadSuppliersList(force = false) {
    if (_suppliersCache && !force) return _suppliersCache;
    try {
        _suppliersCache = await apiFetch('/suppliers');
    } catch (e) {
        _suppliersCache = [];
    }
    return _suppliersCache;
}

async function mountSupplierPicker(mount, currentId, onChange) {
    if (!mount) return;
    const suppliers = await loadSuppliersList();

    if (!suppliers.length) {
        mount.innerHTML = '<div class="supplier-empty">Сначала добавьте поставщиков в разделе «Поставщики»</div>';
        return;
    }

    const options = suppliers.map(s => ({ value: String(s.id), label: s.name }));
    mount.innerHTML = '';
    mount.appendChild(createDropdown({
        options,
        value: currentId != null ? String(currentId) : '',
        searchable: suppliers.length > 7,     // длинный список — с поиском
        onChange: (val) => {
            const picked = suppliers.find(x => String(x.id) === String(val));
            onChange(val ? parseInt(val) : null, picked ? picked.name : '');
        }
    }));
}
