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
    // Через защиту: при неперезаписанной ошибке сохранения спросим,
    // что делать с несохранёнными правками (план 0.1).
    guardCloseCardModal();
}

/* ---------- helpers: date wrapper + activity avatar ---------- */
const ICON_UPLOAD = '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
// ICON_PENCIL намеренно не объявляем здесь: он уже определён в features.js
// (загружается раньше). Дублирующий const падал с SyntaxError: Can't create
// duplicate variable и ломал весь card_modal.js → openCardModal не определялась.

function formatDateRU(isoDate) {
    if (!isoDate) return '';
    const d = new Date(isoDate + 'T00:00:00');
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString('ru-RU');
}

function dateInputHTML({ id, value, className = '' }) {
    const display = value ? formatDateRU(value) : 'дд.мм.гггг';
    return `
        <div class="date-input-wrapper ${className} ${!value ? 'date-empty' : ''}" id="${id}-wrapper">
            <input type="date" id="${id}" value="${value || ''}">
            <span class="date-display">${display}</span>
            <span class="date-calendar-icon">${ICON_CALENDAR}</span>
        </div>
    `;
}

function syncDateWrapper(input) {
    const wrapper = input && input.closest('.date-input-wrapper');
    if (!wrapper) return;
    const display = wrapper.querySelector('.date-display');
    if (display) display.textContent = input.value ? formatDateRU(input.value) : 'дд.мм.гггг';
    wrapper.classList.toggle('date-empty', !input.value);
}

/* ============================================================
   ТРЕКЕР СОХРАНЕНИЯ ПОЛЕЙ КАРТОЧКИ (план 0.1)

   Поля (название, сумма, дата, клиент, магазин) сохраняются
   сразу при изменении, но раньше это происходило молча: при
   сбое сети пользователь узнавал о потере по косвенным признакам,
   а при успехе не видел никакого подтверждения. Индикатор в
   шапке модалки показывает «Сохранение… / Сохранено ЧЧ:ММ /
   Ошибка — Повторить», а при закрытии карточки с неперезаписанной
   ошибкой запрашивает явное решение.
   ============================================================ */
const CardSaveTracker = {
    pending: 0,
    failed: null,       // функция повтора последней неудавшейся отправки
    lastSavedAt: null,
    hideTimer: null,

    reset() {
        this.failed = null;
        this.lastSavedAt = null;
        clearTimeout(this.hideTimer);
        this.render();
    },
    begin() {
        this.pending++;
        this.failed = null;
        this.render();
    },
    ok() {
        this.pending = Math.max(0, this.pending - 1);
        this.lastSavedAt = new Date();
        this.render();
    },
    fail(retryFn) {
        this.pending = Math.max(0, this.pending - 1);
        this.failed = retryFn;
        this.render();
    },
    hasUnsaved() {
        return this.pending > 0 || !!this.failed;
    },
    // Повтор последней неудавшейся отправки. Возвращает true, если
    // незагруженных изменений не осталось.
    async retry() {
        if (!this.failed) return true;
        const run = this.failed;
        this.begin();
        try {
            await run();
            this.failed = null;
            this.ok();
            return true;
        } catch (err) {
            this.fail(run);
            showToast('Не удалось сохранить: ' + err.message, 'error');
            return false;
        }
    },
    render() {
        const el = document.getElementById('card-save-indicator');
        if (!el) return;
        el.classList.remove('is-saving', 'is-saved', 'is-error');
        clearTimeout(this.hideTimer);
        if (this.pending > 0) {
            el.classList.add('is-saving');
            el.innerHTML = '<span class="save-spinner" aria-hidden="true"></span>Сохранение…';
            el.hidden = false;
        } else if (this.failed) {
            el.classList.add('is-error');
            el.innerHTML = `<span class="save-ico" aria-hidden="true">${ICON_ALERT}</span>Ошибка сохранения
                <button type="button" class="save-retry-btn">Повторить</button>`;
            el.hidden = false;
            el.querySelector('.save-retry-btn').onclick = () => this.retry();
        } else if (this.lastSavedAt) {
            el.classList.add('is-saved');
            const t = this.lastSavedAt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            el.innerHTML = `<span class="save-ico" aria-hidden="true">${ICON_CHECK}</span>Сохранено ${t}`;
            el.hidden = false;
            this.hideTimer = setTimeout(() => { el.hidden = true; }, 3000);
        } else {
            el.hidden = true;
        }
    }
};

/**
 * Сохранить поле карточки через трекер. onDone вызывается только
 * при успехе — там обновляем доску и зависимые таблицы.
 */
async function saveCardField(card, patch, onDone) {
    const run = async () => {
        const updated = await apiFetch(`/cards/${card.id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch)
        });
        if (updated && typeof updated === 'object' && !Array.isArray(updated)) Object.assign(card, updated);
    };
    CardSaveTracker.begin();
    try {
        await run();
        CardSaveTracker.ok();
        if (typeof onDone === 'function') onDone();
        return true;
    } catch (err) {
        CardSaveTracker.fail(run);
        showToast('Не удалось сохранить: ' + err.message, 'error');
        return false;
    }
}

/* ---------- защита закрытия карточки с несохранёнными правками (план 0.1) ----------

   ui-polish.js закрывает модалки глобальным capture-обработчиком на document.
   card_modal.js загружается раньше него, поэтому наш capture-слушатель,
   объявленный здесь на верхнем уровне, срабатывает первым: пока трекер
   держит незаписанные изменения, закрытие перехватывается и пользователю
   предлагается выбор (Сохранить / Не сохранять / Отмена). */

let _cardOverlayDown = false;
let _guardCloseBusy = false;

function doCloseCardModal() {
    const modal = document.getElementById('card-modal');
    if (!modal) return;
    if (window.closeModalSmooth) window.closeModalSmooth(modal);
    else modal.classList.add('hidden');
}

/**
 * Трёхвариантный диалог вместо confirm(): «Сохранить» повторяет
 * отправку и закрывает только при успехе, «Не сохранять» отбрасывает,
 * «Отмена» остаётся в карточке. Разрешение: 'save' | 'discard' | 'cancel'.
 */
function unsavedChangesDialog() {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        overlay.innerHTML = `
            <div class="confirm-box">
                <p class="confirm-message">Есть несохранённые изменения</p>
                <div class="confirm-actions confirm-actions-3">
                    <button type="button" class="confirm-cancel">Не сохранять</button>
                    <button type="button" class="confirm-neutral">Отмена</button>
                    <button type="button" class="confirm-ok">Сохранить</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('show'));

        const close = (val) => {
            overlay.classList.remove('show');
            setTimeout(() => overlay.remove(), 200);
            document.removeEventListener('keydown', onKey);
            resolve(val);
        };
        function onKey(e) {
            if (e.key === 'Escape') close('cancel');
            if (e.key === 'Enter') close('save');
        }
        overlay.querySelector('.confirm-cancel').onclick = () => close('discard');
        overlay.querySelector('.confirm-neutral').onclick = () => close('cancel');
        overlay.querySelector('.confirm-ok').onclick = () => close('save');
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close('cancel'); });
        document.addEventListener('keydown', onKey);
        overlay.querySelector('.confirm-ok').focus();
    });
}

async function guardCloseCardModal() {
    if (_guardCloseBusy) return;
    _guardCloseBusy = true;
    try {
        // Дождёмся летящих запросов, чтобы не пугать пользователя диалогом,
        // который через секунду сам станет неактуальным.
        const started = Date.now();
        while (CardSaveTracker.pending > 0 && Date.now() - started < 3000) {
            await new Promise(r => setTimeout(r, 100));
        }
        if (CardSaveTracker.failed) {
            const choice = await unsavedChangesDialog();
            if (choice === 'cancel') return;
            if (choice === 'save') {
                const ok = await CardSaveTracker.retry();
                if (!ok) return;             // сохранить не удалось — остаёмся в карточке
            }
            CardSaveTracker.failed = null;   // «Не сохранять» — отпускаем ошибку
            CardSaveTracker.render();
        }
        doCloseCardModal();
    } finally {
        _guardCloseBusy = false;
    }
}

// Перехват крестику и клику по подложке раньше глобального закрытия.
document.addEventListener('mousedown', (e) => {
    _cardOverlayDown = !!(e.target && e.target.id === 'card-modal');
}, true);
document.addEventListener('click', (e) => {
    if (!e.target || !e.target.closest) return;
    const modal = document.getElementById('card-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    const onOverlay = e.target === modal && _cardOverlayDown;
    const onCloseBtn = e.target.classList.contains('close-btn') && modal.contains(e.target);
    if (!onOverlay && !onCloseBtn) return;
    if (!CardSaveTracker.hasUnsaved()) return;   // обычное закрытие — не мешаем
    e.preventDefault();
    e.stopImmediatePropagation();
    guardCloseCardModal();
}, true);

// Esc закрывает карточку — через ту же защиту (план 4.3).
// Если фокус в поле ввода, Esc сначала отрабатывает по полю
// (откат правки — так ведут себя суммы чек-листа), модалку закрываем
// только когда фокус вне полей.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('card-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (document.querySelector('.confirm-overlay')) return;   // свой Esc у диалога
    const tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (modal.querySelector('.dropdown.open')) { closeAllDropdowns(); return; }   // сначала свернуть меню
    e.preventDefault();
    e.stopImmediatePropagation();
    guardCloseCardModal();
}, true);

function escapeAttrLocal(v) {
    return (v == null ? '' : String(v))
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function getActivityAvatar(action, userId, userName) {
    const text = (action || '').toLowerCase();
    const isSystem = userId == null || /импорт|cron|автоматически/.test(text);
    if (isSystem) {
        return { initials: 'CRM', className: 'activity-avatar-system', title: 'Системное действие' };
    }
    // Есть имя автора — инициалы из него (план 1.2: видно, КТО изменил),
    // иначе — как раньше, из текста действия.
    const source = userName || (action || '');
    const words = source.split(/\s+/).filter(Boolean);
    const initials = words.slice(0, 2).map(w => w.charAt(0).toUpperCase()).join('') || 'П';
    return { initials, className: 'activity-avatar-user', title: userName || action };
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

        CardSaveTracker.reset();

        // Заголовок — режим просмотра по умолчанию (план 1.4): название нельзя
        // задеть курсором случайно; правка — явным кликом на карандаш.
        const renderTitleView = () => {
            modalTitle.innerHTML = `
                <span class="card-title-view" title="${escapeAttrLocal(card.title)}">${escapeHtml(card.title)}</span>
                <button type="button" id="btn-edit-title" class="title-edit-btn" title="Переименовать сделку" aria-label="Переименовать сделку">${ICON_PENCIL}</button>
            `;
            document.getElementById('btn-edit-title').onclick = renderTitleEdit;
        };
        const renderTitleEdit = () => {
            modalTitle.innerHTML = `
                <textarea id="edit-card-title" rows="1" class="auto-expand-title" placeholder="Название сделки...">${escapeHtml(card.title)}</textarea>
            `;
            const titleEl = document.getElementById('edit-card-title');
            let finished = false;
            titleEl.style.height = 'auto';
            titleEl.style.height = titleEl.scrollHeight + 'px';
            titleEl.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = this.scrollHeight + 'px';
            });
            const finish = () => {
                if (finished) return;
                finished = true;
                renderTitleView();
            };
            const saveTitle = async () => {
                const newTitle = titleEl.value.trim();
                if (!newTitle || newTitle === card.title) { finish(); return; }
                await saveCardField(card, { title: newTitle }, () => {
                    if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                    if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                    if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                });
                finish();
            };
            titleEl.addEventListener('keydown', async (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    e.stopPropagation();
                    await saveTitle();
                }
                if (e.key === 'Escape') {
                    e.preventDefault();
                    e.stopPropagation();
                    finish();   // отмена правки без сохранения
                }
            });
            titleEl.addEventListener('blur', () => { if (!finished) saveTitle(); });
            titleEl.focus();
        };
        renderTitleView();

        renderModalContent(card, document.getElementById('modal-body-left'), document.getElementById('modal-body-right'));
        renderModalPaymentHeader(card);
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
    // Синхронно с kanban.js: оплаченная сделка не помечается просрочкой;
    // сделка в «Списании»/«Закрыто» фактически выписана — тоже (фидбек 06.09)
    const isPaid = card.payment_status === 'Оплачен';
    const dealDone = card.status === 'На списание' || card.status === 'Закрыто';
    const isOverdue = dueDate && dueDate < now && !dealDone && !isPaid;
    const dueDateStr = card.due_date || '';

    // LEFT COLUMN: Static fields
    leftContainer.innerHTML = `
        <div class="modal-split-scroll">
        <div class="modal-section card-info-section">
            <label class="modal-label">${ICON_WALLET} Итоговая сумма сделки (BYN)</label>
            <div class="modal-input-row">
                <input type="text" inputmode="decimal" autocomplete="off" id="input-total-amount" class="money-input" value="${formatMoney(parseFloat(card.total_amount) || 0)}" placeholder="0,00" title="Например: 5 176,45">
            </div>
        </div>
        <div class="modal-section card-info-section">
            <label class="modal-label">${ICON_CALENDAR} Дата окончания</label>
            <div class="modal-input-row date-input-row">
                ${dateInputHTML({ id: 'input-due-date', value: dueDateStr, className: isOverdue ? 'input-overdue' : '' })}
                ${isOverdue ? `<span class="overdue-badge">${ICON_CLOCK} Просрочено</span>` : ''}
            </div>
        </div>
        <div class="modal-section card-info-section">
            <label class="modal-label">Email отправителя</label>
            <div class="modal-input-row">
                <input type="email" id="input-sender-email" value="${escapeHtml(card.sender_email || '')}" placeholder="email@company.com" readonly>
            </div>
        </div>
        <div class="modal-section card-info-section">
            <label class="modal-label">Клиент</label>
            <div id="client-mount"></div>
        </div>
        <div class="modal-section card-info-section">
            <label class="modal-label">Теги</label>
            <div id="tags-mount" class="tags-container"></div>
        </div>
        <div class="modal-section card-info-section">
            <label class="modal-label">Привязка к магазину</label>
            <div id="store-mount" class="store-dropdown"></div>
        </div>
        <div class="modal-section">
            <h3 class="section-title">${ICON_WALLET} Чек-лист к оплате</h3>
            <div id="checklist-summary" class="checklist-summary"></div>
            <div id="checklist-container"></div>
            <div class="modal-input-row checklist-add-row checklist-add-align">
                <div id="new-supplier-mount" class="input-company"></div>
                <input type="number" id="new-amount" placeholder="Сумма к оплате" class="input-amount">
                <button id="btn-add-checklist" class="btn-primary btn-sm">Добавить</button>
            </div>
        </div>
        <div class="modal-section">
            <h3 class="section-title">${ICON_CLIP} Счета (вложения)</h3>
            <div id="file-dropzone" class="dropzone dropzone-upload">
                <span class="dropzone-icon">${ICON_UPLOAD}</span>
                <span class="dropzone-text">Перетащите файлы сюда или кликните для загрузки</span>
                <input type="file" id="file-input" multiple>
            </div>
            <div id="attachments-container" class="attachments-container"></div>
        </div>
        <div class="modal-section" id="invoices-section" hidden>
            <h3 class="section-title">Накладные <span id="invoices-badge" class="col-count"></span></h3>
            <div id="invoices-container"></div>
            <div class="invoices-summary" id="invoices-summary"></div>
        </div>
        <div class="modal-section" id="group-writeoff-section" hidden>
            <h3 class="section-title">Групповое списание</h3>
            <div id="group-writeoff-container"></div>
        </div>
        </div>
        <div class="modal-actions modal-actions-stacked">
            <button id="btn-to-assembly" class="btn-action btn-assembly" title="Сделка попадёт в реестр оплат">${ICON_BOX} Передать в сборку</button>
            <button id="btn-trigger-payment" class="btn-action btn-writeoff-action">${ICON_TRUCK} В списание</button>
        </div>
    `;

    // Кнопки этапов: «В Списание» доступна только со «Сборки» или когда счёт оплачен
    applyStageButtons(card);
    // Накладные показываем только когда сделка дошла до списания
    renderCardInvoices(card);
    // Групповое списание: две карточки одного клиента под одной накладной
    renderCardGroupBlock(card);
    // Прошлые сделки того же отправителя — предложить связать.
    // (Фикс аудита 10.09: вызов перенесён НИЖЕ создания #related-cards-mount —
    // раньше banner писался в узел, которого ещё нет/уже заменён, и баннер
    // «От этого отправителя есть сделки» никогда не появлялся.)

    // RIGHT COLUMN: Timeline
    rightContainer.innerHTML = `
        <div id="related-cards-mount"></div>
        <div class="timeline-header">
            <h3 class="section-title">${ICON_COMMENT} История</h3>
            <div class="activity-filter" id="activity-filter" role="group" aria-label="Фильтр истории">
                <button type="button" data-af="all">Всё</button>
                <button type="button" data-af="comments">Комментарии</button>
                <button type="button" data-af="events">События</button>
            </div>
        </div>
        <div class="timeline" id="activity-container">
            <div class="activity-loading">Загрузка...</div>
        </div>
        <div class="comment-input-box">
            <textarea id="input-description" rows="2" placeholder="Заметка к сделке..."></textarea>
            <button id="btn-send-comment" class="btn-primary btn-sm" type="button" title="Сохранить заметку (Enter)">${ICON_CHECK}</button>
        </div>
    `;

    // Прошлые сделки того же отправителя — предлагаем связать (контейнер
    // related-cards-mount уже в DOM, см. комментарий выше).
    renderRelatedCards(card);

    // Поле заметки фиксированной высоты: длинный текст скроллится внутри,
    // а не растягивает панель комментариев на полкарточки.

    const sendComment = async () => {
        const ta = document.getElementById('input-description');
        if (!ta) return;
        const note = ta.value.trim();
        if (!note) {
            showToast('Заметка пустая — нечего сохранять', 'error');
            return;
        }
        try {
            await apiFetch(`/cards/${card.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ description: ta.value }) });
            showToast('Заметка сохранена', 'success');
            // заметка ушла в ленту активности — показываем её сразу,
            // без переоткрытия карточки
            loadCardActivity(card.id);
            ta.value = '';
            ta.focus();
        } catch (err) {
            showToast('Не удалось сохранить заметку: ' + err.message, 'error');
        }
    };
    const sendCommentBtn = document.getElementById('btn-send-comment');
    if (sendCommentBtn) sendCommentBtn.onclick = sendComment;
    // Enter отправляет заметку, Shift+Enter — перенос строки (план 4.3).
    // Заметка — разовое сообщение: после отправки поле очищается, как в мессенджерах,
    // текст остаётся в ленте ниже.
    const commentInput = document.getElementById('input-description');
    if (commentInput) {
        commentInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendComment();
            }
        });
    }

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
            div.className = `checklist-item ${isCompleted ? 'is-done' : ''}`;
            const invoiceRow = item.invoice_file_path
                ? `<div class="checklist-invoice has-file">
                       <a href="#" class="checklist-invoice-link" data-checklist-id="${item.id}" data-nice-name="${escapeHtml(item.invoice_file_name)}" title="${escapeHtml(item.invoice_file_name)}">${ICON_FILE} ${escapeHtml(item.invoice_file_name)}</a>
                       <a href="#" class="checklist-invoice-dl" data-checklist-id="${item.id}" data-nice-name="${escapeHtml(item.invoice_file_name)}" title="Скачать">${ICON_DOWNLOAD}</a>
                       <button class="checklist-invoice-del" data-id="${item.id}" title="Открепить счёт">${ICON_TRASH}</button>
                   </div>`
                : `<button type="button" class="checklist-invoice-add" data-id="${item.id}">${ICON_CLIP} Прикрепить счёт</button>`;

            div.innerHTML = `
                <div class="checklist-row">
                    <label class="checklist-cb-label" title="Оплачен поставщику">
                        <input type="checkbox" class="checklist-cb-main" data-id="${item.id}" ${item.is_paid ? 'checked' : ''}>
                        <span class="checklist-cb-text">Опл.</span>
                    </label>
                    <label class="checklist-cb-label" title="Пришло на склад">
                        <input type="checkbox" class="checklist-cb-sec" data-id="${item.id}" ${item.is_secondary_check ? 'checked' : ''}>
                        <span class="checklist-cb-text">Приш.</span>
                    </label>
                    <span class="checklist-company ${isCompleted ? 'completed' : ''}" title="${escapeHtml(item.company_name)}">${escapeHtml(item.company_name)}</span>
                    <span class="amount-field">
                        <input type="text" inputmode="decimal" autocomplete="off" class="checklist-amount" data-id="${item.id}" value="${formatMoney(item.amount)}" title="Сумма к оплате — нажмите, чтобы изменить" aria-label="Сумма к оплате">
                        <span class="amount-cur">BYN</span>
                    </span>
                    ${!item.supplier_id
                        ? `<button type="button" class="checklist-link-supplier" data-id="${item.id}" title="Записан текстом. Привязать к поставщику из справочника">не привязан</button>`
                        : ''}
                    <button class="delete-checklist" data-id="${item.id}" title="Удалить">${ICON_TRASH}</button>
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
            fileDiv.className = 'file-chip attachment-chip';
            fileDiv.innerHTML = `
                <span class="file-chip__icon">${ICON_FILE}</span>
                <span class="file-chip__name" title="${escapeHtml(file.file_name)}">${escapeHtml(file.file_name)}</span>
                <a href="#" class="file-chip__action file-chip__download" data-attachment-id="${file.id}" data-nice-name="${escapeHtml(file.file_name)}" title="Скачать">${ICON_DOWNLOAD}</a>
                <button class="file-chip__action file-chip__delete" data-file-id="${file.id}" data-card-id="${card.id}" title="Удалить вложение">${ICON_TRASH}</button>
            `;
            attachmentsContainer.appendChild(fileDiv);
        });
    }

    // --- ОБРАБОТЧИКИ СОБЫТИЙ (event delegation) ---

    container.removeEventListener('click', container._modalClickHandler);
    container._modalClickHandler = (e) => {
        if (e.target.classList.contains('btn-delete-attachment') || e.target.classList.contains('file-chip__delete')) {
            deleteAttachment(parseInt(e.target.dataset.fileId), parseInt(e.target.dataset.cardId));
        }
    };
    container.addEventListener('click', container._modalClickHandler);

    container.removeEventListener('change', container._modalChangeHandler);
    container._modalChangeHandler = async (e) => {
        // Сумма сделки: только число. Раньше здесь стоял type="number" и
        // молчаливый parseFloat() || 0 — опечатка затирала сумму нулём.
        if (e.target.id === 'input-total-amount') {
            const num = parseMoney(e.target.value);
            const prev = parseFloat(card.total_amount) || 0;
            if (num === null || num < 0) {
                showToast('Введите сумму числом, например 5 176,45', 'error');
                e.target.classList.add('amount-invalid');
                e.target.value = formatMoney(prev);
                setTimeout(() => e.target.classList.remove('amount-invalid'), 1200);
                return;
            }
            e.target.value = formatMoney(num);
            if (Math.abs(num - prev) < 0.005) return;   // не изменилось — запрос не нужен
            await saveCardField(card, { total_amount: num }, () => {
                renderModalPaymentHeader(card);
                if (window.refreshCardOnBoard) refreshCardOnBoard(card.id); else loadKanbanBoard();
            });
        } else if (e.target.id === 'input-due-date') {
            syncDateWrapper(e.target);
            const val = e.target.value || null;
            if ((val || null) === (card.due_date || null)) return;
            await saveCardField(card, { due_date: val }, () => {
                if (window.refreshCardOnBoard) refreshCardOnBoard(card.id); else loadKanbanBoard();
            });
        }
        // description (заметка) сохраняется ТОЛЬКО кнопкой с галочкой:
        // раньше blur тоже отправлял PATCH, и клик по кнопке давал
        // двойной запрос + двойную запись в ленту активности.
    };
    container.addEventListener('change', container._modalChangeHandler);

    // Поле суммы: в фокусе — сырое число, без фокуса — с разделителями;
    // ввод фильтруется так же, как суммы в чек-листе.
    const totalAmountInput = document.getElementById('input-total-amount');
    totalAmountInput.addEventListener('focusin', () => {
        const n = parseMoney(totalAmountInput.value);
        totalAmountInput.value = (n === null) ? '' : String(n);
        setTimeout(() => { try { totalAmountInput.select(); } catch (err) {} }, 0);
    });
    totalAmountInput.addEventListener('input', () => {
        let v = totalAmountInput.value.replace(/[^\d.,]/g, '').replace(/,/g, '.');
        const parts = v.split('.');
        if (parts.length > 2) v = parts[0] + '.' + parts.slice(1).join('');
        v = v.replace(/^(\d*\.\d{0,2}).*$/, '$1');
        if (v !== totalAmountInput.value) {
            const pos = totalAmountInput.selectionStart;
            totalAmountInput.value = v;
            try { totalAmountInput.setSelectionRange(pos - 1 < 0 ? 0 : pos, pos - 1 < 0 ? 0 : pos); } catch (err) {}
        }
    });
    totalAmountInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); totalAmountInput.blur(); }
        if (e.key === 'Escape') {
            e.preventDefault();
            // Esc здесь означает «отменить правку поля», а не «закрыть карточку»:
            // не пускаем событие в глобальный Esc-обработчик модалок (ui-polish).
            e.stopPropagation();
            totalAmountInput.value = formatMoney(parseFloat(card.total_amount) || 0);
            totalAmountInput.blur();
        }
    });

    // Фикс аудита 10.09: как с _modalClickHandler/_modalChangeHandler —
    // контейнер постоянный, анонимный слушатель копился бы на каждой
    // перерисовке модалки (загрузка вложений, пунктов чек-листа и т.д.).
    container.removeEventListener('input', container._modalInputHandler);
    container._modalInputHandler = (e) => {
        if (e.target && e.target.closest('.date-input-wrapper')) syncDateWrapper(e.target);
    };
    container.addEventListener('input', container._modalInputHandler);

    const STORE_OPTIONS = APP_STORES;
    const storeDropdown = createDropdown({
        options: STORE_OPTIONS,
        value: card.store_location || '',
        onChange: async (val) => {
            if ((val || '') === (card.store_location || '')) return;
            await saveCardField(card, { store_location: val }, () => {
                if (window.refreshCardOnBoard) refreshCardOnBoard(card.id); else loadKanbanBoard();
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
            });
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
            if (cid === (card.client_id || null)) return;
            await saveCardField(card, { client_id: cid });
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
            // откат правки пункта не должен заодно закрывать карточку
            e.stopPropagation();
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
        const attEl = e.target.closest('[data-attachment-id]');
        const chkEl = e.target.closest('[data-checklist-id]');
        if (attEl || chkEl) {
            e.preventDefault();
            const el = attEl || chkEl;
            const nice = el.dataset.niceName
                || el.getAttribute('title')
                || (el.textContent || '').trim();
            if (attEl) {
                downloadAttachment(parseInt(attEl.dataset.attachmentId), nice);
            } else {
                downloadChecklistInvoice(parseInt(chkEl.dataset.checklistId), nice);
            }
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

    // Сделка дальше «Нового запроса» без цены и даты не двигается
    // (план 0.2 и 1.7): подсвечиваем поле и возвращаем пользователя к нему.
    const warnField = (el, msg) => {
        showToast(msg, 'error');
        if (el) {
            el.classList.add('amount-invalid');
            setTimeout(() => el.classList.remove('amount-invalid'), 1500);
            el.scrollIntoView({ block: 'center', behavior: 'smooth' });
            el.focus();
        }
    };
    const warnNoAmount = () => warnField(document.getElementById('input-total-amount'), 'Укажите сумму сделки');
    const hasAmount = () => (parseFloat(card.total_amount) || 0) > 0;

    document.getElementById('btn-to-assembly').onclick = async () => {
        if (!hasAmount()) return warnNoAmount();
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
        if (!hasAmount()) return warnNoAmount();
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
                pill.innerHTML = `${escapeHtml(tag.name)} <button class="tag-remove" data-id="${tag.id}" title="Удалить тег">${ICON_TRASH}</button>`;
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
        tagsMount.innerHTML = '';
        tagsMount.appendChild(renderAlert({ type: 'error', title: 'Не удалось загрузить теги', message: err.message }));
    }
}

// === АКТИВНОСТЬ ===

// Inline-редактирование комментария: текст записи заменяется на
// textarea с кнопками «Сохранить»/«Отмена».
function startCommentEdit(container, entryId) {
    const item = container.querySelector(`.activity-comment-edit[data-id="${entryId}"]`)?.closest('.activity-item');
    if (!item || item.querySelector('.activity-edit-box')) return;
    const content = item.querySelector('.activity-content');
    const longEl = content.querySelector('.activity-details-long');
    const shortEl = content.querySelector('.activity-details:not(.activity-details-long)');
    const currentText = longEl ? longEl.textContent : (shortEl ? shortEl.textContent : '');
    // прячем исходный текст и toggle
    [longEl, content.querySelector('.activity-details-toggle'), shortEl].forEach(el => { if (el) el.style.display = 'none'; });

    const box = document.createElement('div');
    box.className = 'activity-edit-box';
    box.innerHTML = `
        <textarea class="activity-edit-text" rows="3"></textarea>
        <div class="activity-edit-actions">
            <button type="button" class="btn btn-primary btn-sm activity-edit-save">Сохранить</button>
            <button type="button" class="btn btn-secondary btn-sm activity-edit-cancel">Отмена</button>
        </div>
    `;
    const ta = box.querySelector('.activity-edit-text');
    ta.value = currentText;
    content.appendChild(box);
    ta.focus();

    const close = () => {
        [longEl, content.querySelector('.activity-details-toggle'), shortEl].forEach(el => { if (el) el.style.display = ''; });
        box.remove();
    };
    box.querySelector('.activity-edit-cancel').onclick = close;
    box.querySelector('.activity-edit-save').onclick = async () => {
        const text = ta.value.trim();
        if (!text) { showToast('Комментарий не может быть пустым', 'error'); return; }
        try {
            await apiFetch(`/activity/${entryId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ details: text })
            });
            showToast('Комментарий обновлён', 'success');
            const cid = container.dataset.cardId ? parseInt(container.dataset.cardId) : null;
            if (cid) loadCardActivity(cid);
        } catch (err) {
            showToast('Не удалось сохранить: ' + err.message, 'error');
        }
    };
}

// Активный фильтр ленты (план 1.2): всё / только комментарии / только события.
// FIX 2026-09-03 (аудит): при заблокированном localStorage файл падал целиком.
let _activityFilter = 'all';
try { _activityFilter = localStorage.getItem('crm_activity_filter') || 'all'; } catch (e) {}

async function loadCardActivity(cardId) {
    const container = document.getElementById('activity-container');
    if (!container) return;
    container.dataset.cardId = cardId;
    // Переключение фильтра перерисовывает кэш ленты, без повторного запроса
    const filterBar = document.getElementById('activity-filter');
    if (filterBar) {
        filterBar.onclick = (e) => {
            const btn = e.target.closest('[data-af]');
            if (!btn) return;
            _activityFilter = btn.dataset.af;
            localStorage.setItem('crm_activity_filter', _activityFilter);
            renderCardActivityList(cardId, container._activities || []);
        };
    }
    try {
        const activities = await apiFetch(`/activity?card_id=${cardId}&limit=50`);
        // Фикс аудита 10.09: пока ответ был в пути, модалку могли закрыть и
        // открыть для другой сделки — контейнер заменён или теперь хранит
        // другой cardId. Чужую/устаревшую историю не отрисовываем.
        const live = document.getElementById('activity-container');
        if (live !== container || container.dataset.cardId !== String(cardId)) return;
        container._activities = activities;
        renderCardActivityList(cardId, activities);
    } catch (err) {
        const live = document.getElementById('activity-container');
        if (live !== container || container.dataset.cardId !== String(cardId)) return;
        container.innerHTML = '';
        container.appendChild(renderAlert({ type: 'error', title: 'Ошибка загрузки активности', message: err.message }));
    }
}

function renderCardActivityList(cardId, activities) {
    const container = document.getElementById('activity-container');
    if (!container) return;

    const isComment = (a) => a.action === 'Комментарий';
    const filtered = activities.filter(a =>
        _activityFilter === 'all' ||
        (_activityFilter === 'comments' && isComment(a)) ||
        (_activityFilter === 'events' && !isComment(a)));

    // Счётчики и активная кнопка фильтра
    const filterBar = document.getElementById('activity-filter');
    if (filterBar) {
        const nComments = activities.filter(isComment).length;
        const nEvents = activities.length - nComments;
        filterBar.querySelector('[data-af="all"]').textContent = `Всё (${activities.length})`;
        filterBar.querySelector('[data-af="comments"]').textContent = `Комментарии (${nComments})`;
        filterBar.querySelector('[data-af="events"]').textContent = `События (${nEvents})`;
        filterBar.querySelectorAll('[data-af]').forEach(b =>
            b.classList.toggle('active', b.dataset.af === _activityFilter));
    }

    if (filtered.length === 0) {
        const emptyMsg = activities.length === 0
            ? 'Нет действий'
            : (_activityFilter === 'comments' ? 'Комментариев пока нет' : 'Событий пока нет');
        container.innerHTML = `<div class="activity-empty">${emptyMsg}</div>`;
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
        // Один делегированный обработчик на всю ленту: раскрытие длинных
        // деталей, редактирование и удаление комментариев.
        container.onclick = async (e) => {
            const t = e.target;
            if (t.closest('.activity-details-toggle')) {
                const btn = t.closest('.activity-details-toggle');
                const wrap = btn.closest('.activity-content');
                const long = wrap && wrap.querySelector('.activity-details-long');
                if (!long) return;
                const clamped = long.classList.toggle('is-clamped');
                btn.textContent = clamped ? 'показать полностью' : 'свернуть';
                return;
            }
            const editBtn = t.closest('.activity-comment-edit');
            if (editBtn) {
                startCommentEdit(container, parseInt(editBtn.dataset.id));
                return;
            }
            const delBtn = t.closest('.activity-comment-del');
            if (delBtn) {
                const id = parseInt(delBtn.dataset.id);
                const ok = await confirmDialog('Удалить комментарий?', 'Действие необратимо.');
                if (!ok) return;
                try {
                    await apiFetch(`/activity/${id}`, { method: 'DELETE' });
                    showToast('Комментарий удалён', 'success');
                    loadCardActivity(cardId);
                } catch (err) {
                    showToast('Не удалось удалить: ' + err.message, 'error');
                }
            }
        };
        filtered.forEach(act => {
            const item = document.createElement('div');
            const isCancelAct = /отмен|удал/i.test(act.action || '');
            const key = digits(act.details);
            // зачёркиваем только исходное действие, не саму запись об отмене
            const isStale = !isCancelAct && /накладн/i.test(act.action || '')
                            && key && cancelled.has(key);
            const isSystem = act.user_id == null;
            const avatar = getActivityAvatar(act.action, act.user_id, act.user_name);
            item.className = 'activity-item' + (isStale ? ' activity-stale' : '')
                             + (isCancelAct ? ' activity-cancel' : '')
                             + (isSystem ? ' activity-system' : '');
            const time = new Date(act.created_at).toLocaleString('ru-RU');
            // Длинные детали (например, текст импортированного письма)
            // сворачиваем до нескольких строк с кнопкой «показать полностью» —
            // лента остаётся компактной, как история коммитов.
            const LONG_DETAILS = 160;
            let details = '';
            if (act.details) {
                const text = act.details;
                if (text.length > LONG_DETAILS || text.includes('\n')) {
                    details = `
                        <div class="activity-details activity-details-long is-clamped" data-expand="details">${escapeHtml(text)}</div>
                        <button type="button" class="activity-details-toggle" data-target="details">показать полностью</button>
                    `;
                } else {
                    details = `<span class="activity-details">${escapeHtml(text)}</span>`;
                }
            }
            // Комментарии можно править и удалять (свои — всегда, чужие — админам;
            // сервер дополнительно проверяет права).
            const canManage = act.action === 'Комментарий' && act.id;
            const manageBtns = canManage ? `
                <span class="activity-manage">
                    <button type="button" class="activity-comment-edit" data-id="${act.id}" title="Редактировать">${ICON_PENCIL}</button>
                    <button type="button" class="activity-comment-del" data-id="${act.id}" title="Удалить">${ICON_TRASH}</button>
                </span>
            ` : '';
            item.innerHTML = `
                <div class="activity-avatar ${avatar.className}" title="${escapeAttrLocal(avatar.title)}">${avatar.initials}</div>
                <div class="activity-content">
                    <div class="activity-line">
                        <span class="activity-action">${escapeHtml(act.action)}</span>
                        ${act.user_name ? `<span class="activity-user">${escapeHtml(act.user_name)}</span>` : ''}
                        <span class="activity-time">${time}</span>
                        ${manageBtns}
                    </div>
                    ${details}
                </div>
            `;
            container.appendChild(item);
        });

        // Кнопка «показать полностью» имеет смысл только там, где текст
        // реально обрезан клампом: короткое многострочное письмо и так видно
        // целиком, и клик по кнопке ничего не меняет — выглядит как «не
        // разворачивается». У нормально обрезанного scrollHeight больше
        // видимой высоты (max-height + overflow:hidden), у помещающегося — равен.
        container.querySelectorAll('.activity-details-long.is-clamped').forEach(el => {
            if (el.scrollHeight <= el.clientHeight + 2) {
                const btn = el.parentElement.querySelector('.activity-details-toggle');
                if (btn) btn.style.display = 'none';
            }
        });
    }

const TAG_COLORS = [
    '#4f46e5', '#dc2626', '#f59e0b', '#10b981', '#8b5cf6',
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
    // FIX 2026-09-03 (аудит): ошибка одного файла раньше роняла весь цикл
    // (необработанный rejection, остальные файлы не загружались).
    for (let file of files) {
        if (file.size > MAX_SIZE) {
            showToast(`Файл "${file.name}" слишком большой (макс. 25 МБ)`, 'error');
            continue;
        }
        const formData = new FormData();
        formData.append('file', file);
        try {
            await apiFetch(`/cards/${cardId}/attachments`, { method: 'POST', body: formData });
            showToast(`Файл "${file.name}" загружен`, 'success');
        } catch (err) {
            showToast(`Ошибка загрузки "${file.name}": ${err.message}`, 'error');
        }
    }
    openCardModal(cardId);
}

async function deleteAttachment(fileId, cardId) {
    if (await confirmDialog('Удалить файл?')) {
        try {
            await apiFetch(`/attachments/${fileId}`, { method: 'DELETE' });
            openCardModal(cardId);
            showToast('Файл удалён', 'success');
        } catch (err) {
            showToast('Ошибка удаления: ' + err.message, 'error');
        }
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

// FIX 2026-09-06 (аудит): downloadFile(filename) удалена — она ходила в
// GET /files/{filename}, который закрыт как IDOR (отдача любого файла из
// uploads без проверки владельца) и с 31.08 не использовалась: скачивание
// идёт через downloadById по id вложения/счёта.

async function downloadById(endpoint, id, niceName) {
    // UI FIX 2026-08-31: id = null → endpoint уже полный путь до /download.
    // Переменная названа downloadUrl: внутри try ниже есть своя const url
    // для blob — прежнее имя давало TDZ-ошибку «Cannot access 'url'…».
    const downloadUrl = (id === null || id === undefined)
        ? `${API_BASE_URL}${endpoint}`
        : `${API_BASE_URL}${endpoint}/${id}/download`;
    // P2-1: токен в httpOnly-куке, getToken() возвращает null. Безусловный
    // заголовок давал «Bearer null»: бэкенд видел непустой Authorization,
    // не переключался на куку и отвечал 401 «Не удалось проверить токен».
    // Кука уходит с запросом сама (same-origin), заголовок — только если
    // токен реально есть.
    const token = getToken();
    try {
        const resp = await fetch(downloadUrl, {
            credentials: 'same-origin',
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if (!resp.ok) {
            let detail = '';
            try { const j = await resp.json(); if (j && j.detail) detail = String(j.detail); } catch (e) {}
            if (resp.status === 404) {
                throw new Error('Файл не найден на сервере' + (detail ? ': ' + detail : ''));
            }
            throw new Error(detail || ('Ошибка ' + resp.status));
        }
        const blob = await resp.blob();
        const disposition = resp.headers.get('content-disposition') || '';
        const match = disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        let serverName = match ? match[1].replace(/['"]/g, '') : '';

        let outName = (niceName || serverName || '').trim();
        if (window.CRM_DECODE_MIME) outName = window.CRM_DECODE_MIME(outName) || outName;
        outName = outName.replace(/[\\/:*?"<>|]+/g, '_').replace(/\s+/g, ' ').trim();

        const hasExt = /\.[A-Za-z0-9]{2,5}$/.test(outName);
        if (!hasExt) {
            outName += (await sniffExt(blob));
        }

        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = outName || 'download';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        showToast('Не удалось скачать файл: ' + err.message, 'error');
    }
}

function downloadAttachment(attachmentId, niceName) {
    return downloadById('/attachments', attachmentId, niceName);
}

function downloadChecklistInvoice(checklistId, niceName) {
    // UI FIX 2026-08-31: было /checklists/{id}/download — маршрута нет,
    // FastAPI отвечал 404 {"detail":"Not Found"}, счёт не скачивался.
    // Правильный путь: /checklists/{id}/invoice/download.
    return downloadById(`/checklists/${checklistId}/invoice/download`, null, niceName);
}


/* ============================================================
   ОПЛАТА В КАРТОЧКЕ
   ============================================================ */

function statusClassForPayment(status) {
    const map = {
        'Не оплачен': 'pay-unpaid',
        'Частично': 'pay-partial',
        'Оплачен': 'pay-paid',
        'Отсрочка': 'pay-deferred'
    };
    return map[status] || 'pay-unpaid';
}

async function saveCardPayment(card, payload) {
    try {
        const updated = await apiFetch(`/cards/${card.id}/payment`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        Object.assign(card, updated);
        renderModalPaymentHeader(card);
        showToast('Оплата сохранена', 'success');
        if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
        if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
        if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
    } catch (err) {
        showToast('Ошибка сохранения: ' + err.message, 'error');
    }
}

function renderModalPaymentHeader(card) {
    const mount = document.getElementById('modal-payment-header');
    if (!mount) return;

    const total = parseFloat(card.total_amount) || 0;
    const paid = parseFloat(card.paid_amount) || 0;
    const status = card.payment_status || 'Не оплачен';
    const due = card.payment_due_date || '';
    const cls = statusClassForPayment(status);
    const dueStr = due ? new Date(due + 'T00:00:00').toLocaleDateString('ru-RU') : '';

    mount.innerHTML = `
        <div class="payment-total">${ICON_WALLET} <span class="tabular-nums">${formatMoneyBYN(total)}</span></div>
        <div class="payment-status-line">
            <span id="modal-pay-select-mount"></span>
            ${paid > 0.01 ? `<span class="pay-amount tabular-nums">${formatMoneyBYN(paid)}</span>` : ''}
            ${dueStr ? `<span class="pay-due">до ${dueStr}</span>` : ''}
        </div>
        <div class="payment-actions" id="modal-pay-actions"></div>
    `;

    const mountSel = mount.querySelector('#modal-pay-select-mount');
    const actions = mount.querySelector('#modal-pay-actions');
    if (!mountSel || !actions) return;

    // Статус оплаты: бейдж-dropdown вместо нативного селекта —
    // без стрелки на бейдже и без системного меню при открытии
    const PAY_DD_STATUSES = ['Не оплачен', 'Оплачен', 'Частично', 'Отсрочка'];
    const dd = document.createElement('div');
    dd.className = 'dropdown pay-status-dd';
    const badge = document.createElement('button');
    badge.type = 'button';
    badge.className = `pay-badge pay-dd-toggle ${cls}`;
    badge.title = 'Изменить статус оплаты';
    badge.innerHTML = `<span class="tabular-nums">${status}</span>`;
    dd.appendChild(badge);

    const menuEl = document.createElement('div');
    menuEl.className = 'dropdown-menu pay-dd-menu';
    menuEl.setAttribute('role', 'listbox');
    PAY_DD_STATUSES.forEach(s => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'pay-dd-item' + (s === status ? ' selected' : '');
        item.dataset.value = s;
        item.innerHTML = `<span class="pay-dd-dot ${statusClassForPayment(s)}"></span>${s}`;
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            dd.classList.remove('open');
            applyPayStatus(s);
        });
        menuEl.appendChild(item);
    });
    dd.appendChild(menuEl);
    badge.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = dd.classList.contains('open');
        if (typeof closeAllDropdowns === 'function') closeAllDropdowns();
        if (!isOpen) dd.classList.add('open');
    });
    mountSel.appendChild(dd);

    const select = { value: status };

    function applyPayStatus(newStatus) {
        select.value = newStatus;
        badge.className = `pay-badge pay-dd-toggle ${PAYMENT_STATUS_CLASSES[newStatus] || 'pay-unpaid'}`;
        badge.querySelector('span').textContent = newStatus;
        menuEl.querySelectorAll('.pay-dd-item').forEach(i =>
            i.classList.toggle('selected', i.dataset.value === newStatus));
        if (newStatus === 'Оплачен') {
            if (total <= 0) {
                showToast('Укажите сумму сделки', 'error');
                renderModalPaymentHeader(card);
                return;
            }
            saveCardPayment(card, { paid_amount: total, payment_status: 'Оплачен' });
        } else if (newStatus === 'Частично') {
            actions.innerHTML = `
                <div class="payment-form-inline">
                    <!-- Фикс аудита 10.09: text+inputmode вместо number — запятая в вводе больше не теряет значение -->
                    <input type="text" inputmode="decimal" id="modal-pay-amount" max="${total}" value="${paid > 0 && paid < total ? paid.toFixed(2) : ''}" placeholder="0,00">
                    <button id="modal-pay-confirm" class="btn-primary btn-sm">OK</button>
                    <button id="modal-pay-cancel" class="btn-secondary btn-sm">Отмена</button>
                </div>
            `;
            const amountInput = actions.querySelector('#modal-pay-amount');
            actions.querySelector('#modal-pay-confirm').onclick = () => {
                // Фикс аудита 10.09: parseMoney понимает запятую и пробелы
                const amount = parseMoney(amountInput.value) ?? 0;
                if (amount <= 0) return showToast('Укажите сумму оплаты', 'error');
                if (amount > total + 0.01) return showToast('Сумма оплаты не может превышать сумму сделки', 'error');
                const newStatus = amount >= total - 0.01 ? 'Оплачен' : 'Частично';
                saveCardPayment(card, { paid_amount: amount, payment_status: newStatus });
            };
            actions.querySelector('#modal-pay-cancel').onclick = () => renderModalPaymentHeader(card);
            amountInput.focus();
        } else if (newStatus === 'Отсрочка') {
            actions.innerHTML = `
                <div class="payment-form-inline">
                    <input type="text" inputmode="decimal" id="modal-pay-amount" max="${total}" value="${paid > 0 ? paid.toFixed(2) : ''}" placeholder="0,00">
                    ${dateInputHTML({ id: 'modal-pay-due', value: due })}
                    <button id="modal-pay-confirm" class="btn-primary btn-sm">OK</button>
                    <button id="modal-pay-cancel" class="btn-secondary btn-sm">Отмена</button>
                </div>
            `;
            const amountInput = actions.querySelector('#modal-pay-amount');
            const dueInput = actions.querySelector('#modal-pay-due');
            if (dueInput) {
                dueInput.addEventListener('input', () => syncDateWrapper(dueInput));
            }
            actions.querySelector('#modal-pay-confirm').onclick = () => {
                const amount = parseMoney(amountInput.value) ?? 0;
                if (!dueInput.value) return showToast('Укажите дату отсрочки', 'error');
                saveCardPayment(card, { paid_amount: amount, payment_status: 'Отсрочка', payment_due_date: dueInput.value });
            };
            actions.querySelector('#modal-pay-cancel').onclick = () => renderModalPaymentHeader(card);
            amountInput.focus();
        } else {
            actions.innerHTML = '';
            if (paid > 0.01 || card.payment_status !== 'Не оплачен') {
                // FIX 2026-09-03 (аудит): выбор «Не оплачен» мгновенно и без
                // подтверждения стирал внесённую оплату — один промах по
                // бейджу. Теперь обнуление только через диалог.
                confirmDialog('Поставить «Не оплачен» и сбросить внесённую сумму оплаты?', { okText: 'Сбросить', danger: true })
                    .then(ok => {
                        if (ok) saveCardPayment(card, { paid_amount: 0, payment_status: 'Не оплачен', payment_due_date: null });
                        else renderModalPaymentHeader(card);
                    });
            }
        }
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
        container.innerHTML = '';
        container.appendChild(renderAlert({ type: 'error', title: 'Не удалось загрузить накладные', message: e.message }));
        return;
    }

    badge.textContent = data.issued.length || '';
    container.innerHTML = '';

    // --- выписанные накладные: закрытые пункты чек-листа ---
    data.issued.forEach((inv, i) => {
        const row = document.createElement('div');
        row.className = 'checklist-item inv-item is-done';
        // дата хранится строкой; в <input type="date"> подставляется только
        // YYYY-MM-DD — записи со старым форматом открываются с пустым полем
        const rawDate = (inv.invoice_date || '').trim();
        const dateIso = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : '';
        // Фикс аудита 10.09: старая дата не ISO-формата давала literal
        // «Invalid Date» в строке — теперь показываем сырую строку.
        let legacyDateStr = '';
        if (!dateIso && rawDate) {
            const d = new Date(rawDate + 'T00:00:00');
            legacyDateStr = isNaN(d) ? rawDate : d.toLocaleDateString('ru-RU');
        }
        row.innerHTML = `
            <div class="checklist-item-header">
                <label class="checklist-label">
                    <span class="inv-check" aria-hidden="true">
                        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    </span>
                    <span class="checklist-text inv-num-text">${escapeHtml(inv.invoice_number || '№ не указан')}</span>
                </label>
                <span class="inv-right">
                    <span class="inv-amount-text tabular-nums">${formatMoneyBYN(inv.amount)}</span>
                    <button class="delete-checklist inv-del" data-id="${inv.id}" title="Удалить накладную">${ICON_TRASH}</button>
                </span>
            </div>
            <div class="inv-sub">
                <input type="date" class="inv-date-edit" data-id="${inv.id}" value="${dateIso}"
                       title="Дата выписки — нажмите, чтобы изменить" aria-label="Дата выписки накладной">
                ${legacyDateStr ? `<span title="Дата из старой записи">${escapeHtml(legacyDateStr)}</span>` : ''}
                <span>${escapeHtml(inv.store_location || '')}${inv.store_location ? ' · ' : ''}${inv.written_off ? 'списана' : 'ждёт списания'}</span>
            </div>
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
                <span class="inv-right"><span class="inv-rest-hint tabular-nums">остаток ${formatMoneyBYN(data.rest)}</span></span>
            </div>
            <div class="inv-draft-fields">
                <input type="text" id="new-inv-num" class="inv-in inv-in-num" placeholder="№ накладной">
                <input type="date" id="new-inv-date" class="inv-in inv-in-date" value="${localDateISO(new Date())}">
                <input type="text" inputmode="decimal" id="new-inv-amount" class="inv-in inv-in-amount" value="${data.rest.toFixed(2)}" placeholder="0,00">
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
            const amount = parseMoney(amtEl.value) ?? 0;
            if (!number) { numEl.focus(); return showToast('Укажите номер накладной', 'error'); }
            if (!amount || amount <= 0) { amtEl.focus(); return showToast('Укажите сумму накладной', 'error'); }
            if (amount > data.rest + 0.01) {
                amtEl.focus();
                return showToast(`Сумма больше остатка (${formatMoneyBYN(data.rest)})`, 'error');
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
        container.innerHTML = '';
        container.appendChild(renderEmptyState({
            icon: ICON_FILE,
            title: 'Накладных пока нет',
            description: 'Выписанные накладные будут отображаться здесь.'
        }));
    }

    // --- правка даты выписки накладной ---
    container.querySelectorAll('.inv-date-edit').forEach(inp => {
        inp.addEventListener('change', async () => {
            try {
                await apiFetch(`/payments/transactions/${inp.dataset.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ invoice_date: inp.value || null })
                });
                showToast('Дата накладной сохранена', 'success');
                // сервер синхронизирует дату и на копии в «Документах» —
                // перезагрузим блок и таблицу, чтобы нигде не осталась старая
                await renderCardInvoices(card);
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
            } catch (err) {
                showToast('Не удалось сохранить дату: ' + err.message, 'error');
                renderCardInvoices(card); // вернуть в поле серверное значение
            }
        });
    });

    // --- удаление выписанной накладной ---
    container.querySelectorAll('.inv-del').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            // id снимаем с кнопки ДО await: после паузы на диалоге событие
            // давно погашено и e.currentTarget обнулён — Safari падал с
            // «null is not an object (evaluating 'e.currentTarget.dataset')»,
            // а накладная не удалялась вовсе.
            const txId = btn.dataset.id;
            if (!await confirmDialog('Удалить эту накладную? Сумма вернётся в остаток.')) return;
            try {
                await apiFetch(`/payments/transactions/${txId}`, { method: 'DELETE' });
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
            <span class="inv-sum-money tabular-nums"><b>${formatMoney(data.issued_amount)}</b> / ${formatMoneyBYN(data.card_amount)}</span>
            ${full
                ? '<span class="inv-badge badge-ok">закрыто полностью</span>'
                : `<span class="inv-badge badge-warn tabular-nums">остаток ${formatMoney(data.rest)}</span>`}
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
   ГРУППОВОЕ СПИСАНИЕ В КАРТОЧКЕ
   ============================================================ */
async function renderCardGroupBlock(card) {
    const section = document.getElementById('group-writeoff-section');
    const container = document.getElementById('group-writeoff-container');
    if (!section || !container) return;
    section.hidden = false;
    container.innerHTML = '<p class="text-muted">Загрузка...</p>';

    try {
        const [cards, groups] = await Promise.all([
            (window.CRM_STORE && CRM_STORE.get('cards')) || apiFetch('/kanban/cards'),
            apiFetch('/writeoffs/groups/')
        ]);

        if (card.writeoff_group_id) {
            const group = groups.find(g => g.id === card.writeoff_group_id);
            if (!group) {
                container.innerHTML = '<p class="text-muted">Группа не найдена</p>';
                return;
            }
            renderExistingGroup(card, group, container);
            return;
        }

        // Карточка не в группе — предлагаем кандидатов
        const canGroup = card.status === 'На списание' && card.client_id && card.store_location;
        if (!canGroup) {
            section.hidden = true;
            return;
        }

        const candidates = cards.filter(c =>
            c.id !== card.id &&
            c.status === 'На списание' &&
            c.client_id === card.client_id &&
            c.store_location === card.store_location &&
            !c.writeoff_group_id
        );

        if (!candidates.length) {
            container.innerHTML = '<p class="text-muted">Нет других сделок этого клиента для группового списания</p>';
            return;
        }

        container.innerHTML = `
            <p class="text-muted">Выберите сделки того же клиента, которые хотите объединить в одну накладную:</p>
            <div class="group-candidates">
                ${candidates.map(c => `
                    <label class="group-candidate">
                        <input type="checkbox" value="${c.id}" data-group-candidate>
                        <span class="gw-cand-title">${escapeHtml(c.title)}</span>
                        <span class="gw-cand-amount tabular-nums">${formatMoneyBYN(parseFloat(c.total_amount) || 0)}</span>
                    </label>
                `).join('')}
            </div>
            <div class="group-actions">
                <input type="text" id="new-group-name" class="group-name-input" placeholder="Название группы (необязательно)">
                <button id="btn-create-group" class="btn-primary btn-sm">Создать группу</button>
            </div>
        `;

        container.querySelector('#btn-create-group').onclick = async () => {
            const selected = Array.from(container.querySelectorAll('[data-group-candidate]:checked')).map(cb => parseInt(cb.value));
            if (selected.length === 0) return showToast('Выберите хотя бы одну сделку', 'error');
            const name = container.querySelector('#new-group-name').value.trim() || null;
            try {
                await apiFetch('/writeoffs/groups/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ card_ids: [card.id, ...selected], name })
                });
                showToast('Группа создана', 'success');
                await refreshAndRerenderGroup(card);
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };
    } catch (e) {
        container.innerHTML = '';
        container.appendChild(renderAlert({ type: 'error', title: 'Не удалось загрузить группы', message: e.message }));
    }
}

function renderExistingGroup(card, group, container) {
    const total = parseFloat(group.total_amount) || 0;
    const cardItems = (group.cards || []).map(c => `
        <div class="group-member">
            <span class="gm-title">${escapeHtml(c.title)}</span>
            <span class="gm-amount tabular-nums">${formatMoneyBYN(parseFloat(c.total_amount) || 0)}</span>
            ${!group.written_off && c.id === card.id ? `<button class="btn-link btn-sm btn-leave-group" data-id="${c.id}">выйти</button>` : ''}
        </div>
    `).join('');

    const closedInfo = group.written_off ? `
        <div class="group-invoice-info">
            <span class="inv-badge badge-ok">закрыто общей накладной</span>
            <div class="gm-invoice">№ ${escapeHtml(group.invoice_number || '—')} · ${formatMoneyBYN(total)}</div>
            ${group.invoice_date ? `<div class="gm-invoice-date">${(() => { const d = new Date(group.invoice_date + 'T00:00:00'); return isNaN(d) ? escapeHtml(group.invoice_date) : d.toLocaleDateString('ru-RU'); })()}</div>` : ''}
        </div>
    ` : '';

    container.innerHTML = `
        <div class="group-card">
            <div class="group-card-header">
                <strong>${escapeHtml(group.name)}</strong>
                <span class="col-count">${group.cards ? group.cards.length : 0}</span>
            </div>
            <div class="group-card-total tabular-nums">${formatMoneyBYN(total)}</div>
            <div class="group-members">${cardItems}</div>
            ${closedInfo}
            ${!group.written_off ? `
                <div class="group-invoice-form" id="group-invoice-form">
                    <input type="text" id="group-inv-num" class="inv-in inv-in-num" placeholder="№ накладной">
                    <input type="date" id="group-inv-date" class="inv-in inv-in-date" value="${localDateISO(new Date())}">
                    <input type="text" inputmode="decimal" id="group-inv-amount" class="inv-in inv-in-amount" value="${total.toFixed(2)}" readonly title="Сумма группы" placeholder="0,00">
                </div>
                <div class="group-actions">
                    <button id="btn-issue-group-invoice" class="btn-primary btn-sm">Выписать общую накладную</button>
                    <button id="btn-disband-group" class="btn-secondary btn-sm btn-danger">Распустить группу</button>
                </div>
            ` : ''}
        </div>
    `;

    if (!group.written_off) {
        const numEl = container.querySelector('#group-inv-num');
        const dateEl = container.querySelector('#group-inv-date');

        container.querySelector('#btn-issue-group-invoice').onclick = async () => {
            const number = numEl.value.trim();
            if (!number) { numEl.focus(); return showToast('Укажите номер накладной', 'error'); }
            try {
                await apiFetch(`/writeoffs/groups/${group.id}/issue-invoice`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ invoice_number: number, invoice_date: dateEl.value || null })
                });
                showToast('Общая накладная выписана', 'success');
                await refreshAndRerenderGroup(card);
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                if (typeof loadPaymentsTable === 'function') loadPaymentsTable();
                if (typeof loadDocumentsTable === 'function') loadDocumentsTable();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };

        container.querySelector('#btn-disband-group').onclick = async () => {
            if (!await confirmDialog('Распустить группу? Сделки останутся на списании по отдельности.', { okText: 'Распустить', danger: true })) return;
            try {
                await apiFetch(`/writeoffs/groups/${group.id}`, { method: 'DELETE' });
                showToast('Группа распущена', 'success');
                card.writeoff_group_id = null;
                await renderCardGroupBlock(card);
                if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
            } catch (err) {
                showToast('Ошибка: ' + err.message, 'error');
            }
        };

        container.querySelectorAll('.btn-leave-group').forEach(btn => {
            btn.onclick = async (e) => {
                e.stopPropagation();
                const cid = parseInt(btn.dataset.id);
                try {
                    await apiFetch(`/writeoffs/groups/${group.id}/cards/${cid}`, { method: 'DELETE' });
                    showToast('Карточка выведена из группы', 'success');
                    if (cid === card.id) {
                        card.writeoff_group_id = null;
                        await renderCardGroupBlock(card);
                    } else {
                        await refreshAndRerenderGroup(card);
                    }
                    if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard();
                    if (typeof loadKanbanBoard === 'function') loadKanbanBoard();
                } catch (err) {
                    showToast('Ошибка: ' + err.message, 'error');
                }
            };
        });
    }
}

async function refreshAndRerenderGroup(card) {
    try {
        const fresh = await apiFetch(`/kanban/cards/${card.id}`);
        Object.assign(card, fresh);
        await renderCardGroupBlock(card);
    } catch (e) {
        console.error(e);
    }
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
        <div class="chk-sum-card">
            <div class="chk-sum-main">
                <div class="chk-sum-row"><span class="chk-sum-label">Заказано</span><b class="tabular-nums">${paidItems.length} из ${items.length}</b></div>
                <div class="chk-sum-row"><span class="chk-sum-label">Пришло</span><b class="tabular-nums">${camePaid} из ${items.length}</b></div>
                ${full ? '' : `<div class="chk-sum-row"><span class="chk-sum-label">Осталось оплатить</span><span class="pay-badge pay-partial tabular-nums">${formatMoneyBYN(rest)}</span></div>`}
            </div>
            <div class="chk-sum-progress" title="Оплачено ${pct}%">
                <div class="chk-sum-progress__fill${full ? ' is-done' : ''}" style="width:${pct}%"></div>
            </div>
        </div>
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
    const completed = !!(item.is_paid && item.is_secondary_check);
    const text = row.querySelector('.checklist-company');
    if (text) text.classList.toggle('completed', completed);
    row.classList.toggle('is-done', completed);
}


/* ============================================================
   ВЫБОР ПОСТАВЩИКА В ЧЕК-ЛИСТЕ (06.08.2026)

   Поставщики ведутся в разделе «Поставщики», и каждый пункт
   закупки закрепляется за конкретным поставщиком — так же,
   как сделка закрепляется за клиентом. Ручной ввод убран:
   иначе одна и та же компания попадала в базу в трёх написаниях.
   ============================================================ */
let _suppliersCache = null;

// Фикс аудита 10.09: кэш не инвалидался — поставщик, созданный в разделе
// «Поставщики», в чек-листе карточки не появлялся до перезагрузки страницы.
window.invalidateSuppliersCache = function() { _suppliersCache = null; };

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
