(function () {
    'use strict';
    const root = document.getElementById('view-fin');
    // ==== Отложенная сборка раздела (пункт 3 плана, «Стоимость отрисовки») ====
    // Содержимое #view-fin строится при ПЕРВОМ входе в «Финансы», а не на
    // старте оболочки: замер на стенде с копией прод-БД дал 14 912 узлов из
    // 21 097, и все они лежали в скрытом разделе. Статический каркас (вкладки,
    // фильтры, пустые таблицы) остаётся в разметке — это 265 узлов.
    // Подписку на kb:documents/kb:groups держит обёртка, а не сборщик: board.js
    // рассылает их и до первого открытия финансов (стартовая отрисовка доски),
    // поэтому последние данные копятся и применяются сразу после сборки —
    // порядок строк тот же, что и сегодня при «событие после сборки».
    let fin = null;              // null — раздел ещё не собран
    let pendingDocuments = null; // detail последнего kb:documents до сборки
    let pendingGroups = null;    // detail последнего kb:groups до сборки
    function build() {
        if (fin) return;
        fin = buildFin();
        if (pendingDocuments !== null) {
            const detail = pendingDocuments;
            pendingDocuments = null;
            fin.applyDocuments(detail);
        }
        if (pendingGroups !== null) {
            const detail = pendingGroups;
            pendingGroups = null;
            fin.applyGroups(detail);
        }
    }
    document.addEventListener('kb:documents', (event) => {
        if (!fin) { pendingDocuments = event.detail; return; }
        fin.applyDocuments(event.detail);
    });
    document.addEventListener('kb:groups', (event) => {
        if (!fin) { pendingGroups = event.detail; return; }
        fin.applyGroups(event.detail);
    });
    // kb:reloaded приходит перед синхронной пересборкой KB_FIN_SOURCE в фазе 2.
    // Микрозадача даёт загрузчику сначала обновить источник, затем один раз
    // синхронизировать финансовые реестры с новыми массивами на месте.
    document.addEventListener('kb:reloaded', () => {
        if (!fin) return;
        Promise.resolve().then(() => fin.applyDocuments((window.KBData && window.KBData.cards) || []));
    });
    // Раздел показывает navigation.js — он снимает hidden с #view-fin и вешает
    // .active (клик по меню, hash, back/forward, глубокая ссылка #fin).
    // Наблюдатель закрывает все эти пути одним местом и не требует правок в
    // навигации. Колбэк — микрозадача после смены атрибутов, поэтому сборка
    // проходит до первой отрисовки раздела и пустого кадра не видно.
    // isShown() смотрит на ОБА атрибута: `.view.active { display: flex }` из
    // авторских стилей перебивает дефолтное `[hidden] { display: none }`, так
    // что раздел показан, если снят hidden ИЛИ навешан .active.
    // Имя — isShown, а не visible: внутри сборщика уже есть свой `visible`.
    const isShown = () => !root.hidden || root.classList.contains('active');
    if (isShown()) build();
    const observer = new MutationObserver(() => {
        if (isShown()) {
            if (!fin) build();
            fin.refreshIncPhotoObservation();
        } else if (fin) {
            fin.invalidateIncPhotoRequests();
        }
    });
    observer.observe(root, { attributes: true, attributeFilter: ['hidden', 'class'] });

    // Тело сборщика сохраняет исходные отступы: переформатирование утопило бы
    // правку в 500 строках пробелов и её нельзя было бы отревьюить по diff'у.
    function buildFin() {
    const $ = (selector) => root.querySelector(selector);
    const $$ = (selector) => Array.from(root.querySelectorAll(selector));
    // Единый источник исходящих: одна полная ТН на карточку, суммы в копейках.
    // Только память страницы: ни сети, ни хранилища, ни каскадов статусов.
    // Источник данных финансов: в прототипе — демо-массивы ниже; в site-v2
    // загрузчик (boot.js) кладёт сюда данные из CRM API до подключения скрипта.
    // На боевой базе резерв не срабатывает никогда: KB_FIN_SOURCE существует
    // всегда (пустые массивы — тоже объект), а сбой загрузки показывает экран
    // ошибки, а не раздел на выдуманных сделках. Демо-массивы живут ровно в
    // предпросмотре мокапов и в режиме ?demo=1.
    const finSource = window.KB_FIN_SOURCE || {
        outgoing: [
        { id: 'c104', cardId: 'K-104', card: 'К-104 · Освещение офиса', date: '16.09.2026', client: 'ООО «РемонтСити»', amount: 440000, paid: 440000, store: 'БН', estimate: 'ПР-104', tn: null, calculated: false, posted: false, tnHere: false, billHere: false, print: 'Печать организации при получении', authority: 'Доверенность ещё не передана', note: 'ТН не оформлена. № и дата оформляются в карточке; здесь только просмотр.' },
        { id: 'c103', cardId: 'K-103', card: 'К-103 · Светильники для дома', date: '14.09.2026', client: 'ЗАО «СветлогорскДом»', amount: 890000, paid: 0, store: 'Матусевича', estimate: 'ПР-103', tn: { number: 'ТН-0914', date: '14.09.2026', bill: 'СЧ-103' }, calculated: true, posted: true, tnHere: true, billHere: true, print: 'Печать организации на оригинале', authority: 'Доверенность № 48 от 14.09.2026', note: 'Отсрочка истекла. Оригиналы у нас, долг остаётся.' },
        { id: 'c102', cardId: 'K-102', card: 'К-102 · Подсветка кухни', date: '10.09.2026', client: 'ООО «РемонтСити»', amount: 310000, paid: 50000, store: 'Богдановича', estimate: 'ПР-102', tn: { number: 'ТН-0910', date: '10.09.2026', bill: 'СЧ-102' }, posted: false, tnHere: true, billHere: false, print: 'Без печати, по доверенности', authority: 'Доверенность № 36 от 10.09.2026', note: 'Оплата частичная, отгрузка полная. Ждём оригинал счёта.' },
        { id: 'c101', cardId: 'K-101', card: 'К-101 · Лампы для склада', date: '05.09.2026', client: 'ООО «Веснаторг»', amount: 150000, paid: 150000, store: 'Матусевича', estimate: 'ПР-101', tn: { number: 'ТН-0905', date: '05.09.2026', bill: 'СЧ-101' }, posted: false, tnHere: false, billHere: false, print: 'Печать организации при возврате', authority: 'Получатель — директор, без доверенности', note: 'Полностью оплачено. Оба оригинала ещё у клиента.' }
        ],
        incoming: [
        { id: 'n1', supplier: 'ООО «СветОпт»', number: 'ВХ-501', date: '16.09.2026', store: 'Матусевича', amount: 240000, vat: 40000, checked: true, arrived: false, paid: true, file: 'svetopt-501.pdf', photoPaths: [], excelPath: null },
        { id: 'n2', supplier: 'ООО «ЭлектроСнаб»', number: 'ВХ-498', date: '15.09.2026', store: 'Богдановича', amount: 180000, vat: 30000, checked: false, arrived: true, paid: false, file: 'electro-498.pdf', photoPaths: [], excelPath: null },
        { id: 'n3', supplier: 'ООО «Люмен»', number: 'ВХ-490', date: '12.09.2026', store: 'БН', amount: 96000, vat: 16000, checked: true, arrived: true, paid: false, file: 'lumen-490.pdf', photoPaths: [], excelPath: null }
        ]
    };
    const outgoing = finSource.outgoing;
    const incoming = finSource.incoming;
    const money = (cents) => (cents / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const normalize = (value) => String(value).toLocaleLowerCase('ru-RU').replace(/ё/g, 'е').trim();
    const matches = (r, query) => normalize([r.client, r.card, r.date, r.store, r.estimate, r.tn?.number || '', r.tn?.date || ''].join(' ')).includes(normalize(query));
    const tnText = (r) => r.tn ? `${esc(r.tn.number)}<small>${esc(r.tn.date)}</small>` : 'Не оформлена';
    const checkbox = (r, field, title) => `<label class="fin-check" for="fin-${field}-${r.id}"><input type="checkbox" id="fin-${field}-${r.id}" data-record="${r.id}" data-field="${field}" aria-label="${esc(title + ' — ' + r.card + ', ' + r.client)}" ${r[field] ? 'checked' : ''}>${title}</label>`;
    const PRINT_OPTIONS = [['', '—'], ['Печать', 'Печать'], ['Доверенность', 'Доверенность'], ['БН', 'Безнал (БН)']];
    const printCell = (r) => `<select class="fprint-select" data-record="${r.id}" aria-label="Печать — ${esc(r.card)}">${PRINT_OPTIONS.map(([v, l]) => `<option value="${v}"${(r.print || '') === v ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
    // Пустое значение печати раньше давало строку «Печать: .» — лишняя точка
    // без смысла. Теперь пустое значение читается словами.
    const printLine = (r) => r.print ? `Печать: ${esc(r.print)}.` : 'Печать не указана.';
    const authorityLine = (r) => r.authority ? `${esc(r.authority)}.` : 'Доверенность не указана.';
    // Фото до загрузки — маленький SVG-значок, а не эмодзи: «📷» рисуется
    // по-разному в разных системах и заметно шире самой кнопки.
    const INC_PHOTO_ICON = '<svg class="fin-inc-photo-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M6.4 2h3.2l.9 1.4H13A1.6 1.6 0 0 1 14.6 5v7A1.6 1.6 0 0 1 13 13.6H3A1.6 1.6 0 0 1 1.4 12V5A1.6 1.6 0 0 1 3 3.4h2.5L6.4 2Zm1.6 3.6a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0 1.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z"/></svg>';
    const incPhotoButton = (filename, index, number) => `<button type="button" class="fin-inc-photo" data-action="inc-photo" data-filename="${esc(filename)}" data-photo-label="Фото ${index + 1}" data-nak="${esc(number)}" title="Открыть фото в новой вкладке">${INC_PHOTO_ICON}<span class="fin-inc-photo-label">Фото ${index + 1}</span></button>`;
    const dateEditButton = (r) => {
        const label = `Изменить дату оплаты — ${r.card}`;
        return `<button type="button" class="num fin-date-edit fin-date-edit-link" data-record="${r.id}" title="${esc(label)}" aria-label="${esc(label)}">${esc(r.date)}</button>`;
    };
    const cardOpen = (cardId, text, label) => cardId
        ? `<button type="button" class="fin-row-open" data-card-open="${esc(cardId)}" aria-label="Открыть карточку ${esc(label)}">${esc(text)}</button>` : '';

    // DOM строк создаётся один раз: checkbox/details и фокус не теряются.
    // data-card на строке: клик по любому месту строки открывает карточку
    // (клики по галочкам и details карточку не открывают).
    $('#fin-payment-rows').innerHTML = outgoing.map((r) => `<tr data-record="${r.id}"${r.cardId ? ` data-card="${r.cardId}"` : ''}>
        <td>${dateEditButton(r)}<br>${cardOpen(r.cardId, r.client, r.card)}<small>${esc(r.card)}</small></td>
        <td class="num">${money(r.amount)}</td><td class="num">Оплачено ${money(r.paid)}<small>Долг ${money(r.amount - r.paid)}</small></td>
        <td>${esc(r.store)}<small>${esc(r.estimate)}</small></td><td>${tnText(r)}</td>
        <td>${checkbox(r, 'calculated', 'Просчёт')}</td>
        <td>${checkbox(r, 'posted', 'Списано')}</td>
        <td><details><summary>Реквизиты и примечание</summary><p>${printLine(r)}</p><p>${authorityLine(r)}</p><textarea class="fin-note-edit" data-record="${r.id}" rows="3" placeholder="Примечание (сохраняется автоматически)">${esc(r.note || '')}</textarea></details></td>
    </tr>`).join('');
    // Одна ТН — одна строка. В «Документы» она приходит из двух мест: строкой
    // реестра (последняя ТН сделки в KB_FIN_SOURCE — с магазином, счётом,
    // печатью и живым «ТН у нас») и строкой с доски (card.docs — только номер,
    // дата и сумма). Строка реестра полнее, поэтому документ, уже показанный
    // ею, второй раз не добавляем: на копии прод-БД из 395 строк 194 были
    // дублями, то есть счётчик «Видимых документов» оказывался завышен вдвое.
    // Дубли бывают и внутри самого реестра: у сделки с двумя не-документными
    // записями обе строки ссылаются на одну и ту же последнюю ТН.
    const registryDocTxIds = new Set();
    const issued = outgoing.filter((r) => {
        if (!r.tn) return false;
        // Строки без ссылки на документ (демо-каркас прототипа) сопоставить
        // не с чем — они проходят как есть.
        if (r.docTxId === null || r.docTxId === undefined) return true;
        if (registryDocTxIds.has(r.docTxId)) return false;
        registryDocTxIds.add(r.docTxId);
        return true;
    });
    // Месяцы выписки ТН для фильтра документов собирает syncDocMonths() —
    // после того, как к строкам реестра добавятся ТН с доски и групповые.
    $('#fin-document-rows').innerHTML = issued.map((r) => `<tr data-record="${r.id}"${r.cardId ? ` data-card="${r.cardId}"` : ''}>
        <td>${cardOpen(r.cardId, r.client, r.card)}<small>${esc(r.card)}</small></td><td>${tnText(r)}<small>Счёт ${esc(r.tn.bill)}</small></td>
        <td class="num">${money(r.amount)}</td><td>${checkbox(r, 'tnHere', 'ТН у нас')}</td><td>${checkbox(r, 'billHere', 'Счет у нас')}</td><td>${printCell(r)}</td>
    </tr>`).join('');
    $('#fin-journal-rows').innerHTML = issued.map((r) => `<tr data-record="${r.id}">
        <td>${esc(r.tn.number)}</td><td class="num">${r.tn.date}</td>
        <td><details><summary>${esc(r.card)}</summary><p>${esc(r.client)} · ${esc(r.store)} · ${esc(r.estimate)}</p><p>Существующая привязка: ${esc(r.tn.number)} от ${r.tn.date}, вся сумма карточки ${money(r.amount)} BYN.</p><p>Оформление № и даты — в карточке сделки, журнал здесь только для просмотра.</p></details></td>
        <td class="num">${money(r.amount)}</td>
    </tr>`).join('');
    // Фидбек 20.09: статусы входящих — живые галочки, а не текст «Да/Нет»; правка уходит в PATCH /nakladnye.
    // Без видимого текста в label: заголовок колонки уже называет статус.
    // Поиск получает снимок только видимых полей строки, без скрытого содержимого details.
    const INC_FIELD_PROP = { verified: 'checked', arrived: 'arrived', paid: 'paid' };
    let activeFinTab = 'payments';
    const incPanel = $('#fin-incoming');
    const incCheck = (r, field, title, numId) => `<label class="fin-check" for="fin-inc-${field}-${r.id}"><input type="checkbox" id="fin-inc-${field}-${r.id}" data-inc-id="${numId}" data-inc-field="${field}" aria-label="${esc(title + ' — ' + r.supplier + ', ' + r.number)}" title="${title}"${r[INC_FIELD_PROP[field]] ? ' checked' : ''}></label>`;
    const incRows = $('#fin-incoming-rows');
    if (incRows && !incoming.length) incRows.closest('table').insertAdjacentHTML('afterend', '<p class="fin-note">Входящих накладных от поставщиков нет.</p>');
    $('#fin-incoming-rows').innerHTML = incoming.map((r) => {
        const photos = Array.isArray(r.photoPaths) ? r.photoPaths : [];
        const hasExcel = Boolean(r.excelPath);
        const hasFiles = photos.length > 0 || hasExcel;
        const numId = String(r.id || '').replace(/\D/g, '');
        let filesHtml;
        if (!hasFiles) {
            filesHtml = '<p class="fin-note fin-note-compact">Файлов нет</p>';
        } else {
            const photoItems = photos.map((p, i) =>
                `<li data-photo-filename="${esc(p)}">${incPhotoButton(p, i, r.number)}</li>`
            ).join('');
            const excelItem = hasExcel
                ? `<li data-excel><button type="button" class="fin-inc-excel" data-action="inc-excel" data-nak-id="${numId}" title="Скачать Excel для ${esc(r.number)}">📊 Excel</button></li>`
                : '';
            filesHtml = `<ul class="fin-inc-files">${photoItems}${excelItem}</ul>`;
        }
        const searchText = [r.supplier, r.number, r.date, r.store, money(r.amount)].join(' ');
        return `<tr data-inc-key="${esc(r.id)}" data-search="${esc(searchText)}">
        <td><b>${esc(r.supplier)}</b></td><td>${esc(r.number)}<small>${r.date}</small></td><td>${esc(r.store)}</td>
        <td class="num">${money(r.amount)}</td><td>${incCheck(r, 'verified', 'Проверена', numId)}</td><td>${incCheck(r, 'arrived', 'Пришла', numId)}</td><td>${incCheck(r, 'paid', 'Оплачена', numId)}</td>
        <td><details><summary>НДС и файлы</summary><p>Без НДС: ${money(r.amount - r.vat)} BYN.</p><p>НДС 20%: ${money(r.vat)} BYN, включён в сумму.</p>${filesHtml}</details></td>
    </tr>`;
    }).join('');
    // --- Входящие: ленивые фото через общий FIFO-планировщик --------------
    // Сеть начинается только для открытого details у видимой вкладки и только
    // после приближения кнопки к viewport. Один файл не загружается дважды.
    const incPhotoEntries = new Map();
    const incPhotoPriorityQueue = [];
    const incPhotoQueue = [];
    let incPhotoRunning = 0;
    let incPhotoGeneration = 0;
    const INC_PHOTO_LIMIT = 3;
    const incPanelVisible = () => activeFinTab === 'incoming' && incPanel && !incPanel.hidden &&
        (!root.hidden || root.classList.contains('active'));
    const incPhotoApiAvailable = () => {
        try {
            return Boolean(window.V2Api && typeof window.V2Api.download === 'function' &&
                typeof window.V2Api.token === 'function' && window.V2Api.token());
        } catch (error) {
            return false;
        }
    };
    const incPhotoAbortError = (message) => typeof DOMException === 'function'
        ? new DOMException(message, 'AbortError')
        : Object.assign(new Error(message), { name: 'AbortError' });
    function rejectIncPhotoWaiters(waiters, error) {
        waiters.splice(0).forEach((waiter) => waiter.reject(error));
    }
    const photoDetails = (button) => button.closest('details');
    const photoNearViewport = (button) => {
        if (!button || !button.isConnected) return false;
        const details = photoDetails(button);
        if (!details || !details.open || !incPanelVisible()) return false;
        const rects = button.getClientRects();
        if (!rects.length) return false;
        const rect = button.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        return rect.bottom >= -240 && rect.top <= (window.innerHeight || document.documentElement.clientHeight) + 240;
    };
    function observeIncPhotoButton(button) {
        const details = photoDetails(button);
        if (incPhotoObserver && details && details.open && incPanelVisible() && button.isConnected) {
            incPhotoObserver.observe(button);
        }
    }
    function setIncPhotoLabel(button, text) {
        const label = button.querySelector('.fin-inc-photo-label');
        if (label) label.textContent = text;
        else button.textContent = text;
    }
    function setIncPhotoButtonState(button, state, message) {
        if (!button || !button.isConnected) return;
        button.dataset.photoState = state;
        const name = button.dataset.photoLabel || 'Фото';
        if (state === 'loading') setIncPhotoLabel(button, 'Фото загружается…');
        else if (state === 'error') {
            setIncPhotoLabel(button, `${name} (ошибка)`);
            button.title = message || 'Не удалось загрузить. Нажмите, чтобы повторить.';
        } else {
            setIncPhotoLabel(button, name);
            button.title = 'Открыть фото в новой вкладке';
        }
    }
    function showIncPhotoImage(button, filename, url) {
        const details = photoDetails(button);
        if (!button.isConnected || !details || !details.open || !incPanelVisible()) return false;
        // Миниатюра 44×44 вместо полноразмерного снимка: строка реестра не
        // разъезжается, полное фото открывается по клику в новой вкладке.
        const name = button.dataset.photoLabel || 'Фото';
        const thumb = document.createElement('button');
        thumb.type = 'button';
        thumb.className = 'fin-inc-photo fin-inc-photo-thumb';
        thumb.dataset.action = 'inc-photo-view';
        thumb.dataset.filename = filename;
        thumb.dataset.blobUrl = url;
        thumb.title = 'Открыть фото в новой вкладке';
        thumb.setAttribute('aria-label', `${name} — открыть в новой вкладке`);
        const img = document.createElement('img');
        img.src = url;
        img.alt = '';
        img.decoding = 'async';
        img.className = 'fin-inc-photo-image';
        img.dataset.filename = filename;
        img.dataset.blobUrl = url;
        img.title = 'Открыть фото в новой вкладке';
        thumb.appendChild(img);
        button.replaceWith(thumb);
        return true;
    }
    function releaseIncPhotoUrl(blobUrl) {
        if (!blobUrl) return;
        const used = Array.from(document.querySelectorAll('img.fin-inc-photo-image[data-blob-url]'))
            .some((img) => img.dataset.blobUrl === blobUrl);
        if (used) return;
        URL.revokeObjectURL(blobUrl);
        incPhotoEntries.forEach((entry, filename) => {
            if (entry.url === blobUrl) {
                entry.url = null;
                entry.state = 'idle';
                entry.buttons.clear();
                incPhotoEntries.delete(filename);
            }
        });
    }
    function canStartIncPhoto(entry) {
        return Boolean(entry && Array.from(entry.buttons).some(photoNearViewport));
    }
    function failQueuedIncPhoto(task, error, state) {
        const entry = incPhotoEntries.get(task.filename);
        if (entry && entry.queuedTask === task) {
            entry.queuedTask = null;
            entry.waiters = [];
            entry.state = state;
            entry.buttons.forEach((button) => setIncPhotoButtonState(button, state, error.message));
        }
        rejectIncPhotoWaiters(task.waiters, error);
    }
    function pumpIncPhotos() {
        while (incPhotoRunning < INC_PHOTO_LIMIT && (incPhotoPriorityQueue.length || incPhotoQueue.length)) {
            const task = incPhotoPriorityQueue.length ? incPhotoPriorityQueue.shift() : incPhotoQueue.shift();
            const entry = incPhotoEntries.get(task.filename);
            if (!entry || task.generation !== incPhotoGeneration) {
                failQueuedIncPhoto(task, incPhotoAbortError('Загрузка фото отменена'), 'idle');
                continue;
            }
            if (!canStartIncPhoto(entry)) {
                failQueuedIncPhoto(task, incPhotoAbortError('Фото больше не отображается'), 'idle');
                if (entry) entry.buttons.forEach((button) => observeIncPhotoButton(button));
                else observeIncPhotoButton(task.button);
                continue;
            }
            if (!incPhotoApiAvailable()) {
                failQueuedIncPhoto(task, new Error('Фото доступно только после входа в CRM'), 'error');
                continue;
            }
            entry.queuedTask = null;
            entry.state = 'loading';
            const request = {
                generation: task.generation,
                controller: new AbortController(),
                waiters: task.waiters
            };
            entry.request = request;
            entry.waiters = request.waiters;
            incPhotoRunning++;
            entry.buttons.forEach((button) => setIncPhotoButtonState(button, 'loading'));
            const isCurrentRequest = () => incPhotoEntries.get(task.filename) === entry &&
                entry.request === request && request.generation === incPhotoGeneration;
            Promise.resolve()
                .then(() => {
                    if (!isCurrentRequest()) throw incPhotoAbortError('Загрузка фото отменена');
                    if (!incPhotoApiAvailable()) throw new Error('Фото доступно только после входа в CRM');
                    return window.V2Api.download('/nakladnye/photos/' + encodeURIComponent(task.filename), { signal: request.controller.signal });
                })
                .then((resp) => {
                    if (!isCurrentRequest()) throw incPhotoAbortError('Загрузка фото отменена');
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    return resp.blob();
                })
                .then((blob) => {
                    if (!isCurrentRequest()) throw incPhotoAbortError('Загрузка фото отменена');
                    if (!canStartIncPhoto(entry)) {
                        entry.state = 'idle';
                        entry.buttons.forEach((button) => setIncPhotoButtonState(button, 'idle'));
                        rejectIncPhotoWaiters(request.waiters, incPhotoAbortError('Фото больше не отображается'));
                        entry.buttons.forEach((button) => observeIncPhotoButton(button));
                        return;
                    }
                    const url = URL.createObjectURL(blob);
                    let inserted = false;
                    entry.buttons.forEach((button) => {
                        if (showIncPhotoImage(button, task.filename, url)) inserted = true;
                    });
                    if (!inserted) {
                        URL.revokeObjectURL(url);
                        entry.state = 'idle';
                        entry.buttons.forEach((button) => setIncPhotoButtonState(button, 'idle'));
                        rejectIncPhotoWaiters(request.waiters, incPhotoAbortError('Фото больше не отображается'));
                        entry.buttons.forEach((button) => observeIncPhotoButton(button));
                        return;
                    }
                    entry.url = url;
                    entry.state = 'loaded';
                    entry.buttons.forEach((button) => button.remove());
                    entry.buttons.clear();
                    request.waiters.splice(0).forEach((waiter) => waiter.resolve(url));
                })
                .catch((error) => {
                    if (!isCurrentRequest()) return;
                    entry.state = 'error';
                    entry.buttons.forEach((button) => setIncPhotoButtonState(button, 'error', 'Не удалось загрузить: ' + (error.message || 'неизвестная ошибка')));
                    rejectIncPhotoWaiters(request.waiters, error);
                    entry.waiters = [];
                })
                .finally(() => {
                    incPhotoRunning--;
                    if (isCurrentRequest()) {
                        entry.request = null;
                        entry.waiters = [];
                    }
                    pumpIncPhotos();
                });
        }
    }
    function enqueueIncPhoto(button, priority) {
        const filename = button.dataset.filename;
        if (!filename) return Promise.reject(new Error('Не указано имя файла'));
        if (!incPhotoApiAvailable()) return Promise.reject(new Error('Фото доступно только после входа в CRM'));
        let entry = incPhotoEntries.get(filename);
        if (!entry) {
            entry = { state: 'idle', url: null, request: null, buttons: new Set(), queuedTask: null, waiters: [] };
            incPhotoEntries.set(filename, entry);
        }
        entry.buttons.add(button);
        if (entry.state === 'loaded' && entry.url) {
            showIncPhotoImage(button, filename, entry.url);
            return Promise.resolve(entry.url);
        }
        if (entry.state === 'loading' && entry.request && entry.request.generation === incPhotoGeneration) {
            const waiters = entry.request.waiters;
            return new Promise((resolve, reject) => waiters.push({ resolve, reject }));
        }
        if (entry.queuedTask && entry.queuedTask.generation === incPhotoGeneration) {
            const task = entry.queuedTask;
            const waiters = task.waiters;
            return new Promise((resolve, reject) => {
                waiters.push({ resolve, reject });
                if (priority) {
                    const normalIndex = incPhotoQueue.indexOf(task);
                    if (normalIndex !== -1) {
                        incPhotoQueue.splice(normalIndex, 1);
                        incPhotoPriorityQueue.push(task);
                    }
                }
            });
        }
        entry.state = 'idle';
        setIncPhotoButtonState(button, 'idle');
        const task = { filename, button, generation: incPhotoGeneration, waiters: [] };
        entry.queuedTask = task;
        entry.waiters = task.waiters;
        if (priority) incPhotoPriorityQueue.push(task);
        else incPhotoQueue.push(task);
        const waiter = new Promise((resolve, reject) => task.waiters.push({ resolve, reject }));
        pumpIncPhotos();
        return waiter;
    }
    function invalidateIncPhotoRequests() {
        const previousGeneration = incPhotoGeneration;
        incPhotoGeneration++;
        const queuedTasks = incPhotoPriorityQueue.splice(0).concat(incPhotoQueue.splice(0));
        const queuedTaskSet = new Set(queuedTasks);
        const abortError = incPhotoAbortError('Загрузка фото отменена');
        incPhotoEntries.forEach((entry) => {
            const queuedTask = entry.queuedTask;
            if (queuedTask && queuedTaskSet.has(queuedTask)) {
                entry.queuedTask = null;
                entry.waiters = [];
            }
            const request = entry.request;
            if (request && request.generation === previousGeneration) {
                request.controller.abort();
                rejectIncPhotoWaiters(request.waiters, abortError);
                entry.waiters = [];
            }
            if (entry.state === 'loading') {
                entry.state = 'idle';
                entry.buttons.forEach((button) => setIncPhotoButtonState(button, 'idle'));
            }
        });
        queuedTasks.forEach((task) => rejectIncPhotoWaiters(task.waiters, abortError));
    }
    function refreshIncPhotoObservation() {
        if (!incPhotoObserver || !incPanelVisible()) return;
        // Миниатюра уже загружена — повторно наблюдать её незачем, иначе
        //IntersectionObserver заставит пересоздать картинку при каждом
        // переключении вкладки.
        $$('.fin-inc-photo[data-filename]:not(.fin-inc-photo-thumb)').forEach((button) => observeIncPhotoButton(button));
    }
    const incPhotoObserver = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            const button = entry.target;
            if (!entry.isIntersecting || !incPanelVisible() || !incPhotoApiAvailable()) return;
            const details = photoDetails(button);
            if (!details || !details.open) return;
            incPhotoObserver.unobserve(button);
            enqueueIncPhoto(button, false).catch(() => {});
        });
    }, { rootMargin: '240px 0px', threshold: 0.01 }) : null;
    incRows.addEventListener('toggle', (event) => {
        const details = event.target;
        if (!details.matches('details')) return;
        if (details.open) refreshIncPhotoObservation();
    }, true);
    // --- Входящие: делегированные обработчики (фото / Excel) ---------------
    document.addEventListener('click', (event) => {
        const target = event.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        if (action === 'inc-photo' || action === 'inc-photo-view') {
            if (!window.V2Api || !window.V2Api.token()) {
                const t = document.getElementById('kb-toast');
                if (t) { t.hidden = false; t.textContent = 'Фото доступно только после входа в CRM'; setTimeout(() => t.hidden = true, 4000); }
                return;
            }
            const openPhoto = (bUrl) => {
                const w = window.open(bUrl, '_blank', 'noopener');
                if (!w) {
                    const t = document.getElementById('kb-toast');
                    if (t) { t.hidden = false; t.textContent = 'Разрешите всплывающие окна для просмотра фото'; setTimeout(() => t.hidden = true, 4000); }
                }
            };
            if (target.dataset.blobUrl) {
                openPhoto(target.dataset.blobUrl);
            } else if (target.dataset.filename) {
                const pending = window.open('', '_blank');
                if (!pending) {
                    const t = document.getElementById('kb-toast');
                    if (t) { t.hidden = false; t.textContent = 'Разрешите всплывающие окна для просмотра фото'; setTimeout(() => t.hidden = true, 4000); }
                    return;
                }
                pending.opener = null;
                enqueueIncPhoto(target, true)
                    .then((bUrl) => {
                        if (pending.closed) return;
                        try {
                            pending.location.replace(bUrl);
                        } catch (error) {
                            pending.location.href = bUrl;
                        }
                    })
                    .catch((error) => {
                        pending.close();
                        const t = document.getElementById('kb-toast');
                        if (t) { t.hidden = false; t.textContent = 'Ошибка загрузки фото: ' + (error.message || 'неизвестная ошибка'); setTimeout(() => t.hidden = true, 4000); }
                    });
            }
        } else if (action === 'inc-excel') {
            const nakId = target.dataset.nakId;
            if (!nakId) return;
            if (!window.V2Api || !window.V2Api.token()) {
                const t = document.getElementById('kb-toast');
                if (t) { t.hidden = false; t.textContent = 'Excel доступно только после входа в CRM'; setTimeout(() => t.hidden = true, 4000); }
                return;
            }
            window.V2Api.download('/nakladnye/' + nakId + '/excel')
                .then(async (resp) => {
                    if (!resp.ok) {
                        let detail = 'HTTP ' + resp.status;
                        try { const d = await resp.json(); detail = d.detail || detail; } catch (x) { /* без тела */ }
                        throw new Error(detail);
                    }
                    const blob = await resp.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'nakladnaya_' + nakId + '.xlsx';
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                })
                .catch((e) => {
                    const t = document.getElementById('kb-toast');
                    if (t) { t.hidden = false; t.textContent = 'Ошибка выгрузки Excel: ' + (e.message || 'неизвестная ошибка'); setTimeout(() => t.hidden = true, 4000); }
                });
        }
    });

    let paymentFilter = 'all';
    let finMonth = 'all';
    let documentFilter = 'all';
    // Фидбек 18.09: документы — фильтр по месяцу выписки ТН и сортировка
    // по дате выписки.
    let docMonth = 'all';
    let docSort = 'desc';
    const byId = new Map(outgoing.map((r) => [r.id, r]));
    // Порядок документа в карточке: на доске его уже нет (строки документов с
    // доски уходят под строку реестра), а fin:originals ищет запись именно по
    // индексу. Строка реестра индекса не знает — находим его по docTxId.
    // -1, если документ в карточке не ищется: board.js такой индекс игнорирует.
    function docIndexOf(r) {
        if (r.index !== null && r.index !== undefined) return r.index;
        const card = ((window.KBData && window.KBData.cards) || []).filter((c) => String(c.id) === String(r.cardId))[0];
        const docs = (card && card.docs) || [];
        if (r.docTxId === null || r.docTxId === undefined) return -1;
        return docs.findIndex((d) => d.txId === r.docTxId);
    }
    // Стабильная сигнатура не зависит от порядка ключей объекта. События без
    // изменения данных завершаются до любых строковых и DOM-операций.
    function stableSignature(value) {
        if (Array.isArray(value)) return '[' + value.map(stableSignature).join(',') + ']';
        if (value && Object.prototype.toString.call(value) === '[object Object]') {
            return '{' + Object.keys(value).sort().map((key) => key + ':' + stableSignature(value[key])).join(',') + '}';
        }
        return JSON.stringify(value === undefined ? null : value);
    }
    const outgoingSignature = () => stableSignature(outgoing);
    const incomingSignature = () => stableSignature(incoming);
    function kanbanDocumentsSignature(detail) {
        return stableSignature((detail || []).map((card) => ({
            id: card.id, client: card.client, title: card.title, store: card.store,
            docs: (card.docs || []).map((doc) => ({
                txId: doc.txId, originalsReturned: doc.originalsReturned,
                date: doc.date, series: doc.series, number: doc.number, amount: doc.amount
            }))
        })));
    }
    let lastOutgoingSourceSignature = outgoingSignature();
    let lastIncomingSourceSignature = incomingSignature();
    let lastDocumentsSignature = null;
    let lastKanbanDocumentsSignature = null;
    let lastGroupsSignature = null;
    let registryIds = new Set(outgoing.map((r) => r.id));

    function currentIssuedRows() {
        const seen = new Set();
        return outgoing.filter((r) => {
            if (!r.tn) return false;
            if (r.docTxId === null || r.docTxId === undefined) return true;
            if (seen.has(r.docTxId)) return false;
            seen.add(r.docTxId);
            return true;
        });
    }
    function makeRow(html) {
        const template = document.createElement('template');
        template.innerHTML = html.trim();
        return template.content.firstElementChild;
    }
    function patchCheckbox(row, selector, r, field, label) {
        const input = row.querySelector(selector);
        if (!input) return;
        input.id = 'fin-' + field + '-' + r.id;
        input.dataset.record = r.id;
        input.checked = Boolean(r[field]);
        input.setAttribute('aria-label', label + ' — ' + r.card + ', ' + r.client);
    }
    function patchPaymentRow(row, r) {
        row.dataset.card = r.cardId || '';
        const dateInput = row.querySelector('.fin-date-input');
        if (dateInput) dateInput.finRecord = r;
        const dateButton = row.querySelector('.fin-date-edit');
        if (dateButton) dateButton.textContent = r.date;
        const open = row.querySelector('[data-card-open]');
        if (open) open.textContent = r.client;
        const cardSmall = row.cells[0].querySelector('small');
        if (cardSmall) cardSmall.textContent = r.card;
        row.cells[1].textContent = money(r.amount);
        row.cells[2].textContent = 'Оплачено ' + money(r.paid);
        const debt = document.createElement('small');
        debt.textContent = 'Долг ' + money(r.amount - r.paid);
        row.cells[2].appendChild(debt);
        row.cells[3].textContent = r.store;
        const estimate = document.createElement('small');
        estimate.textContent = r.estimate;
        row.cells[3].appendChild(estimate);
        row.cells[4].textContent = '';
        if (r.tn) {
            row.cells[4].textContent = r.tn.number;
            const date = document.createElement('small');
            date.textContent = r.tn.date;
            row.cells[4].appendChild(date);
        } else row.cells[4].textContent = 'Не оформлена';
        patchCheckbox(row, 'input[data-field="calculated"]', r, 'calculated', 'Просчёт');
        patchCheckbox(row, 'input[data-field="posted"]', r, 'posted', 'Списано');
        const details = row.cells[7].querySelector('details');
        if (details) {
            const paragraphs = details.querySelectorAll('p');
            if (paragraphs[0]) paragraphs[0].textContent = r.print ? 'Печать: ' + r.print + '.' : 'Печать не указана.';
            if (paragraphs[1]) paragraphs[1].textContent = r.authority ? r.authority + '.' : 'Доверенность не указана.';
            const area = details.querySelector('textarea');
            if (area && document.activeElement !== area) area.value = r.note || '';
        }
    }
    function paymentRowHtml(r) {
        return `<tr data-record="${r.id}"${r.cardId ? ` data-card="${r.cardId}"` : ''}>
            <td>${dateEditButton(r)}<br>${cardOpen(r.cardId, r.client, r.card)}<small>${esc(r.card)}</small></td>
            <td class="num">${money(r.amount)}</td><td class="num">Оплачено ${money(r.paid)}<small>Долг ${money(r.amount - r.paid)}</small></td>
            <td>${esc(r.store)}<small>${esc(r.estimate)}</small></td><td>${tnText(r)}</td>
            <td>${checkbox(r, 'calculated', 'Просчёт')}</td><td>${checkbox(r, 'posted', 'Списано')}</td>
            <td><details><summary>Реквизиты и примечание</summary><p>${printLine(r)}</p><p>${authorityLine(r)}</p><textarea class="fin-note-edit" data-record="${r.id}" rows="3" placeholder="Примечание (сохраняется автоматически)">${esc(r.note || '')}</textarea></details></td>
        </tr>`;
    }
    function documentRowHtml(r, kanban) {
        if (kanban) return `<tr data-kanban data-record="${r.id}"${r.cardId ? ` data-card="${r.cardId}"` : ''}>
            <td>${cardOpen(r.cardId, r.client, r.card)}<small>${esc(r.card)}</small></td><td>${esc(r.tn.number)}<small>${esc(r.tn.date)}</small></td>
            <td class="num">${money(r.amount)}</td><td>${checkbox(r, 'tnHere', 'ТН у нас')}</td><td>Не оформлен</td><td></td></tr>`;
        return `<tr data-record="${r.id}"${r.cardId ? ` data-card="${r.cardId}"` : ''}>
            <td>${cardOpen(r.cardId, r.client, r.card)}<small>${esc(r.card)}</small></td><td>${tnText(r)}<small>Счёт ${esc(r.tn.bill)}</small></td>
            <td class="num">${money(r.amount)}</td><td>${checkbox(r, 'tnHere', 'ТН у нас')}</td><td>${checkbox(r, 'billHere', 'Счет у нас')}</td><td>${printCell(r)}</td></tr>`;
    }
    function patchDocumentRow(row, r, kanban) {
        if (r.cardId) row.dataset.card = r.cardId;
        const open = row.querySelector('[data-card-open]');
        if (open) open.textContent = r.client;
        const firstSmall = row.cells[0].querySelector('small');
        if (firstSmall) firstSmall.textContent = row.hasAttribute('data-group') ? r.card + ': ' + r.cardsText : r.card;
        row.cells[1].textContent = r.tn.number;
        const tnDate = document.createElement('small');
        tnDate.textContent = r.tn.date;
        row.cells[1].appendChild(tnDate);
        if (!kanban) {
            const bill = document.createElement('small');
            bill.textContent = 'Счёт ' + r.tn.bill;
            row.cells[1].appendChild(bill);
        }
        row.cells[2].textContent = money(r.amount);
        patchCheckbox(row, 'input[data-field="tnHere"]', r, 'tnHere', 'ТН у нас');
        if (!kanban) {
            patchCheckbox(row, 'input[data-field="billHere"]', r, 'billHere', 'Счет у нас');
            const print = row.querySelector('select.fprint-select');
            if (print) print.value = r.print || '';
        }
    }
    function journalRowHtml(r) {
        return `<tr data-record="${r.id}"><td>${esc(r.tn.number)}</td><td class="num">${r.tn.date}</td>
            <td><details><summary>${esc(r.card)}</summary><p>${esc(r.client)} · ${esc(r.store)} · ${esc(r.estimate)}</p><p>Существующая привязка: ${esc(r.tn.number)} от ${r.tn.date}, вся сумма карточки ${money(r.amount)} BYN.</p><p>Оформление № и даты — в карточке сделки, журнал здесь только для просмотра.</p></details></td>
            <td class="num">${money(r.amount)}</td></tr>`;
    }
    function patchJournalRow(row, r) {
        row.cells[0].textContent = r.tn.number;
        row.cells[1].textContent = r.tn.date;
        const details = row.cells[2].querySelector('details');
        if (details) {
            const summary = details.querySelector('summary');
            if (summary) summary.textContent = r.card;
            const paragraphs = details.querySelectorAll('p');
            if (paragraphs[0]) paragraphs[0].textContent = r.client + ' · ' + r.store + ' · ' + r.estimate;
            if (paragraphs[1]) paragraphs[1].textContent = 'Существующая привязка: ' + r.tn.number + ' от ' + r.tn.date + ', вся сумма карточки ' + money(r.amount) + ' BYN.';
        }
        row.cells[3].textContent = money(r.amount);
    }
    function patchIncomingFiles(details, r) {
        const photos = Array.isArray(r.photoPaths) ? r.photoPaths : [];
        let list = details.querySelector('.fin-inc-files');
        const note = details.querySelector(':scope > .fin-note-compact');
        if (!photos.length && !r.excelPath) {
            if (list) list.remove();
            if (!note) {
                const empty = document.createElement('p');
                empty.className = 'fin-note fin-note-compact';
                empty.textContent = 'Файлов нет';
                details.appendChild(empty);
            }
            return;
        }
        if (note) note.remove();
        if (!list) {
            list = document.createElement('ul');
            list.className = 'fin-inc-files';
            details.appendChild(list);
        }
        const wanted = new Set(photos.map(String));
        Array.from(list.querySelectorAll('li[data-photo-filename]')).forEach((item) => {
            if (!wanted.has(item.dataset.photoFilename)) {
                const image = item.querySelector('img[data-blob-url]');
                const blobUrl = image && image.dataset.blobUrl;
                item.remove();
                releaseIncPhotoUrl(blobUrl);
            }
        });
        const excelItem = list.querySelector('li[data-excel]');
        if (!r.excelPath && excelItem) excelItem.remove();
        const photoItems = Array.from(list.querySelectorAll('li[data-photo-filename]'));
        photos.forEach((filename, index) => {
            let item = photoItems.find((node) => node.dataset.photoFilename === String(filename));
            if (!item) {
                item = document.createElement('li');
                item.dataset.photoFilename = String(filename);
                item.appendChild(makeRow(incPhotoButton(String(filename), index, r.number)));
            }
            const atIndex = list.children[index];
            if (atIndex !== item) list.insertBefore(item, atIndex || null);
        });
        if (r.excelPath) {
            if (!excelItem) {
                excelItem = makeRow(`<li data-excel><button type="button" class="fin-inc-excel" data-action="inc-excel" data-nak-id="${esc(String(r.id || '').replace(/\D/g, ''))}" title="Скачать Excel для ${esc(r.number)}">📊 Excel</button></li>`);
            }
            const button = excelItem.querySelector('button');
            if (button) {
                button.dataset.nakId = String(r.id || '').replace(/\D/g, '');
                button.title = 'Скачать Excel для ' + r.number;
            }
            const atIndex = list.children[photos.length];
            if (atIndex !== excelItem) list.insertBefore(excelItem, atIndex || null);
        }
    }
    function patchIncomingRow(row, r) {
        row.dataset.search = [r.supplier, r.number, r.date, r.store, money(r.amount)].join(' ');
        row.cells[0].textContent = r.supplier;
        row.cells[1].textContent = r.number;
        const date = document.createElement('small');
        date.textContent = r.date;
        row.cells[1].appendChild(date);
        row.cells[2].textContent = r.store;
        row.cells[3].textContent = money(r.amount);
        const numId = String(r.id || '').replace(/\D/g, '');
        ['verified', 'arrived', 'paid'].forEach((field) => {
            const input = row.querySelector('input[data-inc-field="' + field + '"]');
            if (!input) return;
            input.id = 'fin-inc-' + field + '-' + r.id;
            input.dataset.incId = numId;
            input.checked = Boolean(r[INC_FIELD_PROP[field]]);
            const title = { verified: 'Проверена', arrived: 'Пришла', paid: 'Оплачена' }[field];
            input.setAttribute('aria-label', title + ' — ' + r.supplier + ', ' + r.number);
        });
        const details = row.cells[7].querySelector('details');
        if (details) {
            const paragraphs = details.querySelectorAll(':scope > p');
            if (paragraphs[0]) paragraphs[0].textContent = 'Без НДС: ' + money(r.amount - r.vat) + ' BYN.';
            if (paragraphs[1]) paragraphs[1].textContent = 'НДС 20%: ' + money(r.vat) + ' BYN, включён в сумму.';
            patchIncomingFiles(details, r);
        }
    }
    function incomingRowHtml(r) {
        const numId = String(r.id || '').replace(/\D/g, '');
        const photos = Array.isArray(r.photoPaths) ? r.photoPaths : [];
        const photoItems = photos.map((filename, index) => `<li data-photo-filename="${esc(filename)}">${incPhotoButton(filename, index, r.number)}</li>`).join('');
        const excelItem = r.excelPath ? `<li data-excel><button type="button" class="fin-inc-excel" data-action="inc-excel" data-nak-id="${numId}" title="Скачать Excel для ${esc(r.number)}">📊 Excel</button></li>` : '';
        const filesHtml = photos.length || r.excelPath ? `<ul class="fin-inc-files">${photoItems}${excelItem}</ul>` : '<p class="fin-note fin-note-compact">Файлов нет</p>';
        return `<tr data-inc-key="${esc(r.id)}" data-search="${esc([r.supplier, r.number, r.date, r.store, money(r.amount)].join(' '))}">
            <td><b>${esc(r.supplier)}</b></td><td>${esc(r.number)}<small>${r.date}</small></td><td>${esc(r.store)}</td><td class="num">${money(r.amount)}</td>
            <td>${incCheck(r, 'verified', 'Проверена', numId)}</td><td>${incCheck(r, 'arrived', 'Пришла', numId)}</td><td>${incCheck(r, 'paid', 'Оплачена', numId)}</td>
            <td><details><summary>НДС и файлы</summary><p>Без НДС: ${money(r.amount - r.vat)} BYN.</p><p>НДС 20%: ${money(r.vat)} BYN, включён в сумму.</p>${filesHtml}</details></td></tr>`;
    }
    function reconcileOutgoingSource() {
        const signature = outgoingSignature();
        if (signature === lastOutgoingSourceSignature) return false;
        lastOutgoingSourceSignature = signature;
        const issued = currentIssuedRows();
        registryDocTxIds.clear();
        const nextRegistryIds = new Set();
        outgoing.forEach((r) => { byId.set(r.id, r); nextRegistryIds.add(String(r.id)); });
        issued.forEach((r) => { byId.set(r.id, r); nextRegistryIds.add(String(r.id)); });
        registryIds.forEach((id) => { if (!nextRegistryIds.has(id)) byId.delete(id); });
        registryIds = nextRegistryIds;
        registryDocTxIds.forEach((id) => { if (!issued.some((r) => String(r.docTxId) === String(id))) registryDocTxIds.delete(id); });
        issued.forEach((r) => { if (r.docTxId !== null && r.docTxId !== undefined) registryDocTxIds.add(r.docTxId); });
        const payments = $('#fin-payment-rows');
        const paymentFragment = document.createDocumentFragment();
        outgoing.forEach((r) => {
            let row = payments.querySelector('tr[data-record="' + r.id + '"]');
            const isNew = !row;
            if (isNew) row = makeRow(paymentRowHtml(r));
            patchPaymentRow(row, r);
            if (isNew) paymentFragment.appendChild(row);
        });
        Array.from(payments.querySelectorAll('tr')).forEach((row) => { if (!nextRegistryIds.has(row.dataset.record)) row.remove(); });
        if (paymentFragment.childNodes.length) payments.appendChild(paymentFragment);
        const documentBody = $('#fin-document-rows');
        const issuedIds = new Set(issued.map((r) => String(r.id)));
        Array.from(documentBody.querySelectorAll('tr:not([data-kanban]):not([data-group])')).forEach((row) => { if (!issuedIds.has(row.dataset.record)) row.remove(); });
        const docFragment = document.createDocumentFragment();
        issued.forEach((r) => {
            let row = documentBody.querySelector('tr[data-record="' + r.id + '"]:not([data-kanban]):not([data-group])');
            const isNew = !row;
            if (isNew) row = makeRow(documentRowHtml(r, false));
            patchDocumentRow(row, r, false);
            if (isNew) docFragment.appendChild(row);
        });
        if (docFragment.childNodes.length) documentBody.appendChild(docFragment);
        const journalBody = $('#fin-journal-rows');
        Array.from(journalBody.querySelectorAll('tr:not([data-group])')).forEach((row) => { if (!issuedIds.has(row.dataset.record)) row.remove(); });
        const journalFragment = document.createDocumentFragment();
        issued.forEach((r) => {
            let row = journalBody.querySelector('tr[data-record="' + r.id + '"]:not([data-group])');
            const isNew = !row;
            if (isNew) row = makeRow(journalRowHtml(r));
            patchJournalRow(row, r);
            if (isNew) journalFragment.appendChild(row);
        });
        if (journalFragment.childNodes.length) journalBody.appendChild(journalFragment);
        return true;
    }
    function reconcileIncomingSource() {
        const signature = incomingSignature();
        if (signature === lastIncomingSourceSignature) return false;
        lastIncomingSourceSignature = signature;
        invalidateIncPhotoRequests();
        const body = incRows;
        const existing = new Map(Array.from(body.querySelectorAll('tr[data-inc-key]')).map((row) => [row.dataset.incKey, row]));
        const wanted = new Set(incoming.map((r) => String(r.id)));
        const fragment = document.createDocumentFragment();
        incoming.forEach((r) => {
            let row = existing.get(String(r.id));
            const isNew = !row;
            if (row) patchIncomingRow(row, r);
            else row = makeRow(incomingRowHtml(r));
            if (isNew) fragment.appendChild(row);
            existing.delete(String(r.id));
        });
        existing.forEach((row) => {
            const blobUrls = Array.from(row.querySelectorAll('img[data-blob-url]'))
                .map((image) => image.dataset.blobUrl)
                .filter(Boolean);
            row.remove();
            blobUrls.forEach((blobUrl) => releaseIncPhotoUrl(blobUrl));
        });
        if (fragment.childNodes.length) body.appendChild(fragment);
        refreshIncPhotoObservation();
        return true;
    }
    // Kanban documents are session-local too; one row per partial invoice.
    // Подписку на kb:documents держит обёртка в начале скрипта — она должна
    // срабатывать и до первой сборки раздела.
    function reconcileKanbanDocuments(detail) {
        const records = [];
        const listedDocs = new Set(registryDocTxIds);
        (detail || []).forEach((card) => (card.docs || []).forEach((doc, index) => {
            if (doc.txId !== null && doc.txId !== undefined) {
                if (listedDocs.has(doc.txId)) return;
                listedDocs.add(doc.txId);
            }
            const id = 'kb-' + card.id + '-' + index;
            const r = {
                id, cardId: card.id, index, docTxId: doc.txId,
                tnHere: doc.originalsReturned, billHere: false, billRequired: false,
                client: card.client, card: card.id + ' · ' + card.title, date: doc.date,
                store: storeLabel(card.store), estimate: '',
                tn: { number: doc.series + ' ' + doc.number, date: doc.date, bill: '' }, amount: doc.amount
            };
            records.push(r);
            byId.set(id, r);
        }));
        const body = $('#fin-document-rows');
        const wanted = new Set(records.map((r) => r.id));
        Array.from(body.querySelectorAll('tr[data-kanban]')).forEach((row) => {
            if (!wanted.has(row.dataset.record)) {
                byId.delete(row.dataset.record);
                row.remove();
            }
        });
        const fragment = document.createDocumentFragment();
        records.forEach((r) => {
            let row = body.querySelector('tr[data-kanban][data-record="' + r.id + '"]');
            const isNew = !row;
            if (isNew) row = makeRow(documentRowHtml(r, true));
            patchDocumentRow(row, r, true);
            if (isNew) fragment.appendChild(row);
        });
        if (fragment.childNodes.length) body.appendChild(fragment);
    }
    function applyDocuments(detail) {
        const signature = stableSignature(detail || []) + '|' + outgoingSignature() + '|' + incomingSignature();
        if (signature === lastDocumentsSignature) return;
        lastDocumentsSignature = signature;
        reconcileOutgoingSource();
        reconcileIncomingSource();
        const kanbanSignature = kanbanDocumentsSignature(detail);
        if (kanbanSignature !== lastKanbanDocumentsSignature) {
            lastKanbanDocumentsSignature = kanbanSignature;
            reconcileKanbanDocuments(detail);
        }
        syncDocMonths();
        updateActiveFinTab();
        updateJournal();
    }
    function patchGroupJournalRow(row, r) {
        row.cells[0].textContent = r.tn.number;
        row.cells[1].textContent = r.tn.date;
        const details = row.cells[2].querySelector('details');
        if (details) {
            const summary = details.querySelector('summary');
            if (summary) summary.textContent = r.card;
            const paragraphs = details.querySelectorAll('p');
            if (paragraphs[0]) paragraphs[0].textContent = r.client + ' · ' + r.store;
            if (paragraphs[1]) paragraphs[1].textContent = 'Карточки: ' + r.cardsText + '.';
            if (paragraphs[2]) paragraphs[2].textContent = 'Одна накладная на всю группу; отмена возвращает остаток каждой карточке.';
        }
        row.cells[3].textContent = money(r.amount);
    }
    // Групповые ТН: одна накладная на несколько карточек (аналог групп списаний).
    function applyGroups(detail) {
        const signature = stableSignature(detail || []);
        if (signature === lastGroupsSignature) return;
        lastGroupsSignature = signature;
        const records = [];
        const body = $('#fin-document-rows');
        const journalBody = $('#fin-journal-rows');
        (detail || []).forEach((g) => {
            const id = 'kbg-' + g.id;
            const cardsText = g.covers.map((cov) => cov.cardId).join(', ');
            const r = { id, card: 'Группа · ' + g.covers.length + ' карточек', client: g.client, date: g.date, store: g.store, estimate: '', tn: { number: g.series + ' ' + g.number, date: g.date, bill: '—' }, amount: g.amount, paid: 0, docTxId: g.docTxId || null, tnHere: Boolean(g.tnHere), billHere: Boolean(g.billHere), billRequired: false, cardsText };
            byId.set(id, r);
            records.push(r);
        });
        const wanted = new Set(records.map((r) => r.id));
        Array.from(body.querySelectorAll('tr[data-group]')).forEach((row) => {
            if (!wanted.has(row.dataset.record)) { byId.delete(row.dataset.record); row.remove(); }
        });
        Array.from(journalBody.querySelectorAll('tr[data-group]')).forEach((row) => {
            if (!wanted.has(row.dataset.record)) row.remove();
        });
        const docFragment = document.createDocumentFragment();
        const journalFragment = document.createDocumentFragment();
        records.forEach((r) => {
            let docRow = body.querySelector('tr[data-group][data-record="' + r.id + '"]');
            if (docRow) patchDocumentRow(docRow, r, true);
            else {
                docRow = makeRow(documentRowHtml(r, true));
                docRow.removeAttribute('data-kanban');
                docRow.dataset.group = '';
                docRow.dataset.record = r.id;
                const groupOpen = docRow.querySelector('[data-card-open]');
                if (groupOpen) groupOpen.remove();
                const groupLabel = document.createElement('b');
                groupLabel.textContent = r.client;
                const groupCards = document.createElement('small');
                groupCards.textContent = r.card + ': ' + r.cardsText;
                docRow.cells[0].append(groupLabel, groupCards);
                docRow.cells[1].textContent = r.tn.number;
                const date = document.createElement('small');
                date.textContent = r.tn.date;
                docRow.cells[1].appendChild(date);
                docRow.cells[3].replaceWith(checkboxElement(r, 'tnHere', 'ТН у нас'));
            }
            if (!docRow.isConnected) docFragment.appendChild(docRow);
            let journalRow = journalBody.querySelector('tr[data-group][data-record="' + r.id + '"]');
            if (journalRow) patchGroupJournalRow(journalRow, r);
            else {
                journalRow = makeRow(journalRowHtml(r));
                journalRow.dataset.group = '';
                patchGroupJournalRow(journalRow, r);
                journalFragment.appendChild(journalRow);
            }
        });
        if (docFragment.childNodes.length) body.appendChild(docFragment);
        if (journalFragment.childNodes.length) journalBody.appendChild(journalFragment);
        syncDocMonths();
        updateActiveFinTab();
        updateJournal();
    }
    function checkboxElement(r, field, title) {
        return makeRow(`<td>${checkbox(r, field, title)}</td>`);
    }
    function visibleRows(selector, predicate, keepFocused) {
        const visible = [];
        $$(selector + ' tr').forEach((row) => {
            const r = byId.get(row.dataset.record);
            // Не скрываем элемент под клавиатурным фокусом после отметки.
            row.hidden = !(predicate(r) || (keepFocused && row.contains(document.activeElement)));
            if (!row.hidden) visible.push(r);
        });
        return visible;
    }
    function total(rows) {
        const sum = rows.reduce((acc, r) => ({ amount: acc.amount + r.amount, paid: acc.paid + r.paid }), { amount: 0, paid: 0 });
        return `Итого видимых: ${rows.length} · Сумма ${money(sum.amount)} BYN · Оплачено ${money(sum.paid)} BYN · Долг ${money(sum.amount - sum.paid)} BYN`;
    }
    let lastVisibleRows = [];
    function updatePayments(keepFocused = false) {
        const rows = visibleRows('#fin-payment-rows', (r) => matches(r, $('#fin-payment-search').value) &&
            (paymentFilter === 'all' || (paymentFilter === 'debt' ? r.paid < r.amount : !r.posted)) &&
            (finMonth === 'all' || r.date.slice(6, 10) + '-' + r.date.slice(3, 5) === finMonth), keepFocused);
        lastVisibleRows = rows;
        $('#fin-payment-total').textContent = total(rows);
        $('#fin-payment-empty').hidden = rows.length !== 0;
    }
    // Правка даты оплаты и примечания — те же поля записи, что в рабочей
    // версии (PATCH /payments/transactions/{id}); дата меняется только
    // днем, время записи сохраняет бэкенд.
    document.addEventListener('click', (event) => {
        const button = event.target.closest('.fin-date-edit');
        if (!button) return;
        const r = byId.get(button.dataset.record);
        if (!r) return;
        const iso = r.date.split('.').reverse().join('-');
        button.outerHTML = `<input type="date" class="fin-date-input" data-record="${r.id}" value="${iso}">`;
        const input = document.querySelector(`.fin-date-input[data-record="${r.id}"]`);
        input.finRecord = r;
        input.focus();
        const commit = () => {
            const currentRecord = input.finRecord || r;
            const value = input.value;
            const ru = value ? value.split('-').reverse().join('.') : currentRecord.date;
            currentRecord.date = ru;
            if (window.V2Api && window.V2Api.token() && value) {
                window.V2Api.api('/payments/transactions/' + currentRecord.txId, { method: 'PATCH', body: { date: value } })
                    .catch(() => { const t = document.getElementById('kb-toast'); if (t) { t.hidden = false; t.textContent = 'Дату не сохранить: ' + 'ошибка'; setTimeout(() => t.hidden = true, 3000); } });
            }
            const label = `Изменить дату оплаты — ${currentRecord.card}`;
            const back = document.createElement('button');
            back.type = 'button';
            back.className = 'num fin-date-edit fin-date-edit-link';
            back.dataset.record = r.id;
            back.title = label;
            back.setAttribute('aria-label', label);
            back.textContent = ru;
            input.replaceWith(back);
        };
        input.addEventListener('change', commit);
        input.addEventListener('blur', () => setTimeout(() => { if (document.querySelector(`.fin-date-input[data-record="${r.id}"]`)) commit(); }, 0));
    });
    document.addEventListener('change', (event) => {
        const area = event.target.closest('.fin-note-edit');
        if (!area) return;
        const r = byId.get(area.dataset.record);
        if (!r) return;
        r.note = area.value;
        if (window.V2Api && window.V2Api.token()) {
            window.V2Api.api('/payments/transactions/' + r.txId, { method: 'PATCH', body: { note: area.value } })
                .catch(() => { const t = document.getElementById('kb-toast'); if (t) { t.hidden = false; t.textContent = 'Примечание не сохранено'; setTimeout(() => t.hidden = true, 3000); } });
        }
    });

    // Экспорт CSV ровно видимого набора, как в рабочей версии (';' + BOM).
    const docMonthSelEl = document.getElementById('fin-doc-month');
    if (docMonthSelEl) docMonthSelEl.addEventListener('change', () => { docMonth = docMonthSelEl.value; updateDocuments(); });
    const docSortSelEl = document.getElementById('fin-doc-sort');
    if (docSortSelEl) docSortSelEl.addEventListener('change', () => { docSort = docSortSelEl.value; updateDocuments(); });
    $('#fin-payment-export').addEventListener('click', () => {
        const rows = lastVisibleRows;
        if (!rows.length) { alert('Нечего экспортировать — список пуст.'); return; }
        const headers = ['Дата', 'Клиент', 'Карточка', 'Сумма (BYN)', 'Оплачено (BYN)', 'Долг (BYN)', 'Магазин', '№ ТН', 'Дата ТН', 'Просчёт', 'Списано', 'Печать/доверенность', 'Примечание'];
        const escCsv = (v) => { const s = (v === null || v === undefined) ? '' : String(v); return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
        const lines = [headers.join(';')].concat(rows.map((r) => [
            r.date, r.client, r.card, money(r.amount), money(r.paid), money(r.amount - r.paid),
            r.store, r.tn ? r.tn.number : '', r.tn ? r.tn.date : '',
            r.calculated ? 'да' : 'нет', r.posted ? 'да' : 'нет', r.print || '', r.note || ''
        ].map(escCsv).join(';')));
        const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'reestr_oplat.csv';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
    });
    // Экспорт документов — тем же способом, что реестр оплат: ';' + BOM и
    // ровно видимый набор (поиск, фильтр возврата, месяц выписки).
    // Одна и та же ТН попадает в таблицу дважды: строкой реестра (из
    // KB_FIN_SOURCE, с печатью и счётом) и строкой с доски (card.docs) —
    // на проде 194 из 197 документов. В выгрузке дубли бухгалтерии не нужны,
    // поэтому на документ (docTxId) остаётся одна запись, более полная.
    // С 23.09 дубли убраны и на уровне строк таблицы, так что здесь эта
    // страховка молчит — оставлена намеренно: экспорт не должен рассыпаться,
    // если источник снова начнёт приносить одну ТН дважды.
    $('#fin-document-export').addEventListener('click', () => {
        const byDoc = new Map();
        lastVisibleDocs.forEach((r) => {
            const key = (r.docTxId === null || r.docTxId === undefined) ? 'id' + r.id : 'tx' + r.docTxId;
            const held = byDoc.get(key);
            if (!held || (!held.txId && r.txId)) byDoc.set(key, r);
        });
        const rows = [...byDoc.values()];
        if (!rows.length) { alert('Нечего экспортировать — список пуст.'); return; }
        const headers = ['Клиент', 'Карточка', 'Магазин', 'Сумма (BYN)', '№ ТН', 'Дата ТН', '№ счёта', 'ТН у нас', 'Счёт у нас', 'Печать/доверенность'];
        const escCsv = (v) => { const s = (v === null || v === undefined) ? '' : String(v); return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
        const lines = [headers.join(';')].concat(rows.map((r) => [
            r.client, r.card, r.store, money(r.amount),
            // Номер на доске склеен как series + ' ' + number, а серия не
            // хранится (boot кладёт '') — на выходе висел бы ведущий пробел.
            r.tn ? String(r.tn.number).trim() : '', r.tn ? r.tn.date : '', r.tn ? String(r.tn.bill).trim() : '',
            r.tnHere ? 'да' : 'нет',
            r.billHere ? 'да' : (r.billRequired === false ? 'не оформлен' : 'нет'),
            r.print || ''
        ].map(escCsv).join(';')));
        const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'dokumenty.csv';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
    });
    function docSortKey(r) {
        const d = r.tn ? r.tn.date : '';
        return /^\d{2}\.\d{2}\.\d{4}$/.test(d) ? d.slice(6, 10) + '-' + d.slice(3, 5) + '-' + d.slice(0, 2) : '0000-00-00';
    }
    let lastDocDataSignature = '';
    let lastDocOrderSignature = '';
    function sortDocRows() {
        const tbody = document.getElementById('fin-document-rows');
        if (!tbody) return;
        const trs = [...tbody.querySelectorAll('tr')];
        const dataSignature = docSort + '|' + trs.map((tr) => tr.dataset.record + ':' + docSortKey(byId.get(tr.dataset.record))).join('|');
        if (dataSignature === lastDocDataSignature) return;
        trs.sort((a, b) => {
            const ra = byId.get(a.dataset.record), rb = byId.get(b.dataset.record);
            const ka = docSortKey(ra), kb = docSortKey(rb);
            return docSort === 'desc' ? kb.localeCompare(ka) : ka.localeCompare(kb);
        });
        const orderSignature = docSort + '|' + trs.map((tr) => tr.dataset.record).join('|');
        lastDocDataSignature = dataSignature;
        if (orderSignature === lastDocOrderSignature) return;
        const fragment = document.createDocumentFragment();
        trs.forEach((tr) => fragment.appendChild(tr));
        tbody.appendChild(fragment);
        lastDocOrderSignature = orderSignature;
    }
    // Видимый набор документов — тот же смысл, что lastVisibleRows для реестра:
    // экспорт отдаёт ровно то, что сейчас показывает фильтр.
    let lastVisibleDocs = [];
    function updateDocuments(keepFocused = false) {
        sortDocRows();
        const rows = visibleRows('#fin-document-rows', (r) => (documentFilter === 'all' ||
            (documentFilter === 'returned' ? r.tnHere && (r.billHere || r.billRequired === false) : !r.tnHere || (!r.billHere && r.billRequired !== false))) &&
            matches(r, $('#fin-document-search').value) &&
            (docMonth === 'all' || (r.tn && /^\d{2}\.\d{2}\.\d{4}$/.test(r.tn.date) && r.tn.date.slice(6, 10) + '-' + r.tn.date.slice(3, 5) === docMonth)), keepFocused);
        lastVisibleDocs = rows;
        $('#fin-document-count').textContent = `Видимых документов: ${rows.length} · Все оформленные оригиналы у нас: ${rows.filter((r) => r.tnHere && (r.billHere || r.billRequired === false)).length}`;
        $('#fin-document-empty').hidden = rows.length !== 0;
    }
    function updateJournal() {
        const rows = visibleRows('#fin-journal-rows', (r) => matches(r, $('#fin-journal-search').value), false);
        $('#fin-journal-total').textContent = `Исходящих ТН: ${rows.length} · Сумма ${money(rows.reduce((sum, r) => sum + r.amount, 0))} BYN`;
        $('#fin-journal-empty').hidden = rows.length !== 0;
    }
    // Месяцы выписки — по всем строкам документов, а не только по строкам
    // реестра: месяц групповой накладной или второй ТН карточки иначе просто
    // не появляется в фильтре. Выбранный месяц сохраняем, если он остался.
    function syncDocMonths() {
        const sel = document.getElementById('fin-doc-month');
        if (!sel) return;
        const months = [...new Set([...byId.values()]
            .filter((r) => r.tn && /^\d{2}\.\d{2}\.\d{4}$/.test(r.tn.date))
            .map((r) => r.tn.date.slice(6, 10) + '-' + r.tn.date.slice(3, 5)))].sort().reverse();
        const names = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
        sel.innerHTML = '<option value="all">Все месяцы</option>' + months.map((ym) => {
            const parts = ym.split('-');
            return `<option value="${ym}">${names[Number(parts[1]) - 1]} ${parts[0]}</option>`;
        }).join('');
        if (docMonth !== 'all' && !months.includes(docMonth)) docMonth = 'all';
        sel.value = docMonth;
    }
    function pressed(selector, selected) {
        $$(selector).forEach((button) => button.setAttribute('aria-pressed', String(button === selected)));
    }
    // ==== Вкладка «Контроль» (паритет с legacy site/js/control.js) ====
    // Строки строим при открытии вкладки, а не на сборке раздела: раздел ленив,
    // и дебиторка меняется переносом карточки между колонками и оплатой —
    // держать её строки в DOM с первой сборки смысла нет.
    // ДАННЫЕ: только то, что уже лежит в window.KBData.cards (сумма и оплачено
    // считает бэкенд). Новых запросов вкладка не делает.
    // «Дней» — возраст сделки с created_at, как в legacy site/js/control.js.
    // Дату переносит в KBData загрузчик (v2-boot-template.js); если её нет
    // (демо-набор прототипа, карточка без даты), колонка откатывается к
    // прежнему смыслу — просрочке по сроку сделки (cards.deadline).
    const DEBT_STAGES = ['pay', 'assembly'];   // id этапов, сравнение не по названию
    const DEBT_LIMIT = 60;                     // потолок отрисовки: порядок по остатку долга
    const ruDate = (value) => {
        const m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(String(value || ''));
        return m ? new Date(+m[3], +m[2] - 1, +m[1]) : null;
    };
    const statusName = (stageId) => {
        const st = ((window.KBData && window.KBData.statuses) || []).filter((s) => s.id === stageId)[0];
        return st ? st.name : String(stageId || '');
    };
    const storeLabel = (id) => (window.KBData && window.KBData.storeName ? window.KBData.storeName(id) : String(id || ''));
    // Долг в копейках; «> 1» — то же правило, что в legacy (долг больше копейки).
    // Суммы берём через `|| 0`: пропущенное значение не должно превращать
    // строку в NaN и молча выбрасывать сделку из списка.
    const debtOf = (c) => (c.amount || 0) - (c.paidAmount || 0);
    // Возраст сделки в днях (legacy) — от created_at; срок сделки (deadline)
    // остаётся фолбэком для карточек без даты создания.
    const ageOf = (c, today) => {
        const from = ruDate(c.createdAt) || ruDate(c.deadline);
        return from ? Math.max(0, Math.floor((today - from) / 86400000)) : null;
    };
    // Пороги цвета — как в legacy (30 и 60 дней); до 30 — обычное число.
    const ageCell = (days) => (days === null ? '—'
        : days >= 30 ? `<span class="pill ${days >= 60 ? 'danger' : 'warn'}">${days}</span>`
        : String(days));
    function debtList() {
        const D = window.KBData;
        if (!D || !Array.isArray(D.cards)) return null;   // данных нет вовсе — не путать с «долгов нет»
        const today = D.today instanceof Date ? D.today : new Date();
        return D.cards
            .filter((c) => DEBT_STAGES.indexOf(c.stage) >= 0 && debtOf(c) > 1)
            .map((c) => ({ c, debt: debtOf(c), days: ageOf(c, today) }))
            .sort((a, b) => b.debt - a.debt);
    }
    function renderControl() {
        const body = $('#fin-control-rows');
        if (!body) return;
        const empty = $('#fin-control-empty');
        const limit = $('#fin-control-limit');
        const rows = debtList();
        const shown = rows ? rows.slice(0, DEBT_LIMIT) : [];
        const sum = (list) => money(list.reduce((acc, x) => acc + x.debt, 0));
        body.innerHTML = shown.map(({ c, debt, days }) => `<tr${c.id ? ` data-card="${esc(c.id)}"` : ''}>
            <td>${cardOpen(c.id, c.client || 'Без клиента', c.id + ' · ' + c.title)}<small>${esc(c.id + ' · ' + c.title)}</small></td>
            <td>${esc(storeLabel(c.store))}</td><td><span class="fin-status">${esc(statusName(c.stage))}</span></td>
            <td class="num">${money(c.amount || 0)}</td><td class="num">${money(c.paidAmount || 0)}</td>
            <td class="num fin-debt-strong">${money(debt)}</td>
            <td class="num">${ageCell(days)}</td>
        </tr>`).join('');
        // «Нет данных» (сбой) и «долгов нет» (норма) — разные состояния.
        $('#fin-control-debt-summary').textContent = rows ? (rows.length ? rows.length + ' · ' + sum(rows) : 'нет') : '—';
        $('#fin-control-total').textContent = rows && rows.length ? `Долг по всем строкам: ${rows.length} сделок · ${sum(rows)} BYN` : '';
        empty.hidden = !rows ? false : rows.length !== 0;
        // Названия этапов подставляем из справочника по id — на сервере канон
        // пишется «Ждет оплаты» без «ё», и хардкод разошёлся бы с колонкой.
        empty.textContent = !rows ? 'Данные сделок не загружены — обновите страницу.'
            : (!rows.length ? `Долгов нет: в статусах «${statusName('assembly')}» и «${statusName('pay')}» всё оплачено.` : '');
        limit.hidden = !rows || rows.length <= shown.length;
        limit.textContent = `Показаны ${shown.length} из ${rows ? rows.length : 0} — порядок по остатку долга.`;
        renderControlQueue();
    }
    // Вторая строка контроля — то же количество, что у фильтра «Не списано»
    // в реестре (ручная отметка о списании в учётной программе).
    function renderControlQueue() {
        const el = $('#fin-control-queue');
        if (!el) return;
        const unposted = outgoing.filter((r) => !r.posted);
        el.textContent = unposted.length
            ? `Записей без отметки «Списано» — ${unposted.length} на ${money(unposted.reduce((sum, r) => sum + r.amount, 0))} BYN. Это ровно выборка фильтра «Не списано» в реестре оплат.`
            : 'Все записи реестра отмечены как списанные.';
    }
    function updateActiveFinTab() {
        if (activeFinTab === 'payments') updatePayments();
        else if (activeFinTab === 'documents') updateDocuments();
        else if (activeFinTab === 'incoming') updateIncoming();
        else if (activeFinTab === 'control') renderControl();
    }
    function activateFinTab(tab, shouldUpdate) {
        const previousTab = activeFinTab;
        activeFinTab = tab;
        const button = $('[data-fin-tab="' + tab + '"]');
        pressed('[data-fin-tab]', button);
        $$('[data-fin-panel]').forEach((panel) => { panel.hidden = panel.id !== button.getAttribute('aria-controls'); });
        if (previousTab === 'incoming' && tab !== 'incoming') invalidateIncPhotoRequests();
        if (tab === 'incoming') refreshIncPhotoObservation();
        if (shouldUpdate !== false) updateActiveFinTab();
    }
    const controlUnposted = $('#fin-control-show-unposted');
    if (controlUnposted) controlUnposted.addEventListener('click', () => {
        const filter = $('[data-payment-filter="unposted"]');
        paymentFilter = 'unposted';
        pressed('[data-payment-filter]', filter);
        activateFinTab('payments', true);
        filter.focus();
    });
    $$('[data-fin-tab]').forEach((button) => button.addEventListener('click', () => {
        activateFinTab(button.dataset.finTab, true);
    }));
    $$('[data-payment-filter]').forEach((button) => button.addEventListener('click', () => {
        paymentFilter = button.dataset.paymentFilter;
        pressed('[data-payment-filter]', button);
        updatePayments();
    }));
    $$('[data-document-filter]').forEach((button) => button.addEventListener('click', () => {
        documentFilter = button.dataset.documentFilter;
        pressed('[data-document-filter]', button);
        updateDocuments();
    }));
    $('#fin-payment-search').addEventListener('input', () => updatePayments());
    $('#fin-journal-search').addEventListener('input', updateJournal);
    root.addEventListener('change', (event) => {
        const target = event.target;
        if (target.matches('select.fprint-select')) {
            const r = byId.get(target.dataset.record);
            if (!r) return;
            r.print = target.value; // Печать/Доверенность/БН — поле документа
            if (r.docTxId && window.V2Api && window.V2Api.token()) {
                window.V2Api.api('/payments/transactions/' + r.docTxId, { method: 'PATCH', body: { print_status: target.value || null } })
                    .catch(() => { const t = document.getElementById('kb-toast'); if (t) { t.hidden = false; t.textContent = 'Печать не сохранена'; setTimeout(() => t.hidden = true, 3000); } });
            }
        }
        // Входящие накладные: галочка статуса сохраняется в CRM (PATCH /nakladnye).
        // При ошибке — откат галочки и локального поля + тост (в демо-режиме без
        // токена остаётся локальное состояние, как у галочек реестра).
        const incBox = event.target;
        if (incBox.matches('input[type="checkbox"][data-inc-field]')) {
            const nakId = incBox.dataset.incId;
            const rec = incoming.find((x) => String(x.id) === 'n' + nakId);
            const incProp = INC_FIELD_PROP[incBox.dataset.incField];
            if (!rec || !incProp) return;
            const prev = rec[incProp];
            rec[incProp] = incBox.checked;
            if (window.V2Api && window.V2Api.token()) {
                window.V2Api.api('/nakladnye/' + nakId, { method: 'PATCH', body: { ['is_' + incBox.dataset.incField]: incBox.checked } })
                    .catch((err) => {
                        incBox.checked = prev;
                        rec[incProp] = prev;
                        const t = document.getElementById('kb-toast');
                        if (t) { t.hidden = false; t.textContent = 'Не сохранено: ' + (err.detail || err.message || 'ошибка'); setTimeout(() => t.hidden = true, 3000); }
                    });
            }
            return;
        }
        const input = event.target;
        if (!input.matches('input[type="checkbox"][data-field]')) return;
        const r = byId.get(input.dataset.record);
        if (!r || !['calculated', 'posted', 'tnHere', 'billHere'].includes(input.dataset.field)) return;
        r[input.dataset.field] = input.checked; // Меняется ровно одно поле.
        // Фидбек 18.09: галочки реестра и документов v2 — те же ручные поля,
        // что в основных «Реестре оплат»/«Документах», и сохраняются в CRM
        // (в демо-режиме KBData.mutate нет — остаётся локальное состояние).
        if (window.KBData && typeof window.KBData.mutate === 'function') {
            if (input.dataset.field === 'calculated' || input.dataset.field === 'posted') {
                window.KBData.mutate('fin-flag', { field: input.dataset.field, cardId: r.cardId, partIds: r.partIds, checked: input.checked });
            } else if ((input.dataset.field === 'tnHere' || input.dataset.field === 'billHere') && r.docTxId) {
                window.KBData.mutate('doc-flag', { field: input.dataset.field, txId: r.docTxId, checked: input.checked });
            }
        }
        if (r.cardId && input.dataset.field === 'tnHere') document.dispatchEvent(new CustomEvent('fin:originals', {
            detail: { cardId: r.cardId, index: docIndexOf(r), field: 'originalsReturned', checked: input.checked }
        }));
        if (input.dataset.field === 'posted') updatePayments(true);
        else updateDocuments(true);
    });
    // Пересчитываем фильтр после перехода фокуса, а не во время change.
    ['#fin-payment-rows', '#fin-document-rows'].forEach((selector) => {
        $(selector).addEventListener('focusout', () => {
            setTimeout(() => selector === '#fin-payment-rows' ? updatePayments(true) : updateDocuments(true), 0);
        });
    });
    // Документы: месяцы выписки и сортировка (селекты заполняются здесь —
    // после объявления docMonth/docSort, чтобы не ловить TDZ)
    const docMonthSel = document.getElementById('fin-doc-month');
    syncDocMonths();
    const docSortSel = document.getElementById('fin-doc-sort');
    if (docSortSel) docSortSel.value = docSort;
    // Месячный фильтр — как месячный фильтр рабочей версии
    const finMonthSel = document.getElementById('fin-month');
    if (finMonthSel) {
        const months = [...new Set(outgoing.filter((r) => /^\d{2}\.\d{2}\.\d{4}$/.test(r.date)).map((r) => r.date.slice(6, 10) + '-' + r.date.slice(3, 5)))].sort().reverse();
        const names = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
        finMonthSel.innerHTML = '<option value="all">Все месяцы</option>' + months.map((ym) => {
            const parts = ym.split('-');
            return `<option value="${ym}">${names[Number(parts[1]) - 1]} ${parts[0]}</option>`;
        }).join('');
        finMonthSel.addEventListener('change', () => { finMonth = finMonthSel.value; updatePayments(); });
    }
    // Системные фильтры (месяц, сортировка) и «Печать» в документах — через
    // канонический kb-select, как селекты доски. Строки строятся один раз,
    // поэтому enhancement в конце IIFE достаточен; повторный вызов безопасен
    // (WeakMap + sync подхватывает новые option).
    if (window.KBSelect) {
        [docMonthSel, docSortSel, finMonthSel].forEach((sel) => { if (sel) window.KBSelect.enhance(sel); });
        window.KBSelect.enhance($('#fin-document-rows'));
    }
    // Раскрывающийся поиск на каждой странице финансов: лупа раскрывает поле
    // (инертное в закрытом состоянии), фильтрация живая на input, Esc и «×»
    // закрывают и сбрасывают. Плоский контрол с единственным крестиком.
    function wireFinSearch(wrapId) {
        const wrap = document.getElementById(wrapId);
        if (!wrap) return;
        const toggle = wrap.querySelector('.fin-search-toggle');
        const input = wrap.querySelector('input');
        const clear = wrap.querySelector('.fin-search-clear');
        if (!toggle || !input || !clear) return;
        const setOpen = (open, pointer = false) => {
            wrap.classList.toggle('is-open', open);
            input.classList.toggle('is-pointer-focus', open && pointer);
            input.inert = !open;
            toggle.setAttribute('aria-expanded', String(open));
            toggle.setAttribute('aria-label', open ? 'Закрыть поиск' : 'Открыть поиск');
            if (open) {
                input.focus();
            } else {
                if (input.value) { input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); }
                toggle.focus();
            }
        };
        toggle.addEventListener('click', (event) => {
            setOpen(toggle.getAttribute('aria-expanded') !== 'true', event.detail !== 0);
        });
        clear.addEventListener('click', () => {
            if (input.value) {
                input.value = '';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.focus();
                return;
            }
            setOpen(false);
        });
        const closeOnEscape = (e) => {
            if (e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            setOpen(false);
        };
        input.addEventListener('keydown', closeOnEscape);
        input.addEventListener('focusout', () => input.classList.remove('is-pointer-focus'));
        clear.addEventListener('keydown', closeOnEscape);
    }
    ['fin-payment-search-wrap', 'fin-document-search-wrap', 'fin-incoming-search-wrap'].forEach(wireFinSearch);
    // Живой поиск по документам: те же поля, что у matches (клиент, карточка,
    // дата, магазин, просчёт, ТН), плюс номер счёта в строке.
    $('#fin-document-search').addEventListener('input', () => updateDocuments());
    // Входящие: строки строятся один раз и без data-record — фильтруем по
    // data-search из видимых полей, не читая скрытое содержимое details.
    function updateIncoming() {
        const q = normalize($('#fin-incoming-search').value);
        let visibleCount = 0;
        $$('#fin-incoming-rows tr').forEach((tr) => {
            tr.hidden = !!q && !normalize(tr.dataset.search || '').includes(q);
            if (!tr.hidden) visibleCount += 1;
        });
        $('#fin-incoming-empty').hidden = !q || visibleCount !== 0;
        refreshIncPhotoObservation();
    }
    $('#fin-incoming-search').addEventListener('input', updateIncoming);
    updateActiveFinTab();
    updateJournal();
    // Обёртка вызывает их сама, проигрывая kb:documents/kb:groups, которые
    // board.js успел разослать до первого открытия раздела.
    return { applyDocuments: applyDocuments, applyGroups: applyGroups, invalidateIncPhotoRequests: invalidateIncPhotoRequests, refreshIncPhotoObservation: refreshIncPhotoObservation };
    }
})();
