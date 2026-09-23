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
    if (isShown()) {
        build();
    } else {
        const observer = new MutationObserver(() => {
            if (!isShown()) return;
            observer.disconnect();
            build();
        });
        observer.observe(root, { attributes: true, attributeFilter: ['hidden', 'class'] });
    }

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

    // DOM строк создаётся один раз: checkbox/details и фокус не теряются.
    // data-card на строке: клик по любому месту строки открывает карточку
    // (клики по галочкам и details карточку не открывают).
    $('#fin-payment-rows').innerHTML = outgoing.map((r) => `<tr data-record="${r.id}"${r.cardId ? ` data-card="${r.cardId}"` : ''}>
        <td><span class="num fin-date-edit" data-record="${r.id}" title="Изменить дату оплаты" class="fin-date-edit-link">${r.date}</span><br><b>${esc(r.client)}</b><small>${esc(r.card)}</small></td>
        <td class="num">${money(r.amount)}</td><td class="num">Оплачено ${money(r.paid)}<small>Долг ${money(r.amount - r.paid)}</small></td>
        <td>${esc(r.store)}<small>${esc(r.estimate)}</small></td><td>${tnText(r)}</td>
        <td>${checkbox(r, 'calculated', 'Просчёт')}</td>
        <td>${checkbox(r, 'posted', 'Списано')}</td>
        <td><details><summary>Реквизиты и примечание</summary><p>Печать: ${esc(r.print)}.</p><p>${esc(r.authority)}.</p><textarea class="fin-note-edit" data-record="${r.id}" rows="3" style="width:100%" placeholder="Примечание (сохраняется автоматически)">${esc(r.note || '')}</textarea></details></td>
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
        <td><b>${esc(r.client)}</b><small>${esc(r.card)}</small></td><td>${tnText(r)}<small>Счёт ${esc(r.tn.bill)}</small></td>
        <td class="num">${money(r.amount)}</td><td>${checkbox(r, 'tnHere', 'ТН у нас')}</td><td>${checkbox(r, 'billHere', 'Счет у нас')}</td><td>${printCell(r)}</td>
    </tr>`).join('');
    $('#fin-journal-rows').innerHTML = issued.map((r) => `<tr data-record="${r.id}">
        <td>${esc(r.tn.number)}</td><td class="num">${r.tn.date}</td>
        <td><details><summary>${esc(r.card)}</summary><p>${esc(r.client)} · ${esc(r.store)} · ${esc(r.estimate)}</p><p>Существующая привязка: ${esc(r.tn.number)} от ${r.tn.date}, вся сумма карточки ${money(r.amount)} BYN.</p><p>Оформление № и даты — в карточке сделки, журнал здесь только для просмотра.</p></details></td>
        <td class="num">${money(r.amount)}</td>
    </tr>`).join('');
    // Фидбек 20.09: статусы входящих — живые галочки, а не текст «Да/Нет»; правка уходит в PATCH /nakladnye.
    // Без видимого текста в label: заголовок колонки уже называет статус,
    // а текст ломал бы живой поиск (updateIncoming ищет по textContent строки).
    const INC_FIELD_PROP = { verified: 'checked', arrived: 'arrived', paid: 'paid' };
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
            filesHtml = '<p class="fin-note" style="margin:4px 0">Файлов нет</p>';
        } else {
            const photoItems = photos.map((p, i) =>
                `<li><button type="button" class="fin-inc-photo" data-action="inc-photo" data-filename="${esc(p)}" data-nak="${esc(r.number)}" title="Открыть фото в новой вкладке">📷 Фото ${i + 1}</button></li>`
            ).join('');
            const excelItem = hasExcel
                ? `<li><button type="button" class="fin-inc-excel" data-action="inc-excel" data-nak-id="${esc(numId)}" title="Скачать Excel для ${esc(r.number)}">📊 Excel</button></li>`
                : '';
            filesHtml = `<ul class="fin-inc-files" style="list-style:none;padding:0;margin:4px 0;display:flex;flex-direction:column;gap:4px">${photoItems}${excelItem}</ul>`;
        }
        return `<tr>
        <td><b>${esc(r.supplier)}</b></td><td>${esc(r.number)}<small>${r.date}</small></td><td>${esc(r.store)}</td>
        <td class="num">${money(r.amount)}</td><td>${incCheck(r, 'verified', 'Проверена', numId)}</td><td>${incCheck(r, 'arrived', 'Пришла', numId)}</td><td>${incCheck(r, 'paid', 'Оплачена', numId)}</td>
        <td><details><summary>НДС и файлы</summary><p>Без НДС: ${money(r.amount - r.vat)} BYN.</p><p>НДС 20%: ${money(r.vat)} BYN, включён в сумму.</p>${filesHtml}</details></td>
    </tr>`;
    }).join('');
    // --- Входящие: загрузка миниатюр фото через авторизованный blob --------
    (function loadIncPhotoThumbs() {
        if (!window.V2Api || !window.V2Api.token()) return;
        document.querySelectorAll('.fin-inc-photo[data-filename]').forEach(async (btn) => {
            const filename = btn.dataset.filename;
            if (!filename) return;
            try {
                const resp = await window.V2Api.download('/nakladnye/photos/' + encodeURIComponent(filename));
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const img = document.createElement('img');
                img.src = url;
                img.alt = btn.dataset.nak || filename;
                img.style.cssText = 'width:80px;height:60px;object-fit:cover;border-radius:4px;border:1px solid var(--border);cursor:pointer;display:block';
                img.dataset.action = 'inc-photo-view';
                img.dataset.blobUrl = url;
                img.title = 'Открыть фото в новой вкладке';
                btn.replaceWith(img);
            } catch (e) {
                btn.textContent = '📷 ' + filename + ' (ошибка)';
                btn.title = 'Не удалось загрузить: ' + (e.message || 'неизвестная ошибка');
            }
        });
    })();
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
            let blobUrl = target.dataset.blobUrl;
            const filename = target.dataset.filename;
            const openPhoto = (bUrl) => {
                const w = window.open(bUrl, '_blank', 'noopener');
                if (!w) {
                    const t = document.getElementById('kb-toast');
                    if (t) { t.hidden = false; t.textContent = 'Разрешите всплывающие окна для просмотра фото'; setTimeout(() => t.hidden = true, 4000); }
                }
            };
            if (blobUrl) {
                openPhoto(blobUrl);
            } else if (filename) {
                window.V2Api.download('/nakladnye/photos/' + encodeURIComponent(filename))
                    .then((resp) => {
                        if (!resp.ok) throw new Error('HTTP ' + resp.status);
                        return resp.blob();
                    })
                    .then((blob) => {
                        blobUrl = URL.createObjectURL(blob);
                        target.dataset.blobUrl = blobUrl;
                        openPhoto(blobUrl);
                    })
                    .catch((e) => {
                        const t = document.getElementById('kb-toast');
                        if (t) { t.hidden = false; t.textContent = 'Ошибка загрузки фото: ' + (e.message || 'неизвестная ошибка'); setTimeout(() => t.hidden = true, 4000); }
                    });
            }
        } else if (action === 'inc-excel') {
            const nakId = target.dataset.nakId;
            if (!nakId) return;
            if (!window.V2Api || !window.V2Api.token()) {
                const t = document.getElementById('kb-toast');
                if (t) { t.hidden = false; t.textContent = 'Excel доступен только после входа в CRM'; setTimeout(() => t.hidden = true, 4000); }
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
    // Kanban documents are session-local too; one row per partial invoice.
    // Подписку на kb:documents держит обёртка в начале скрипта — она должна
    // срабатывать и до первой сборки раздела.
    function applyDocuments(detail) {
        $$('#fin-document-rows tr[data-kanban]').forEach((row) => { byId.delete(row.dataset.record); row.remove(); });
        // Одна вставка вместо N: каждая insertAdjacentHTML — отдельный парсинг
        // и мутация DOM, а реестр ТН перестраивается на каждое kb:documents.
        const kanbanRows = [];
        // Проход собирается заново при каждом kb:documents, поэтому список
        // «уже показанных» документов начинаем со строк реестра.
        const listedDocs = new Set(registryDocTxIds);
        (detail || []).forEach((card) => card.docs.forEach((doc, index) => {
            // Номер и дата в этом проходе уже есть (строка реестра или второй
            // документ той же карточки) — дубль не добавляем. index при этом
            // берётся из исходного массива, поэтому fin:originals продолжает
            // попадать в нужный документ карточки.
            if (doc.txId !== null && doc.txId !== undefined) {
                if (listedDocs.has(doc.txId)) return;
                listedDocs.add(doc.txId);
            }
            const id = 'kb-' + card.id + '-' + index;
            // Запись документа с доски описываем теми же полями, что и строку
            // реестра (client, card, date, store, tn, amount). Без них поиск по
            // документам и фильтр «месяц выписки» работали только по строкам
            // реестра, aria-label галочки читался «undefined», а экспорт CSV
            // не мог назвать ни клиента, ни номер ТН.
            const r = {
                id, cardId: card.id, index, docTxId: doc.txId,
                tnHere: doc.originalsReturned, billHere: false, billRequired: false,
                client: card.client, card: card.id + ' · ' + card.title, date: doc.date,
                store: storeLabel(card.store), estimate: '',
                tn: { number: doc.series + ' ' + doc.number, date: doc.date, bill: '' },
                amount: doc.amount
            };
            byId.set(id, r);
            kanbanRows.push(`<tr data-kanban data-record="${id}"${card.id ? ` data-card="${card.id}"` : ''}>
                <td><b>${esc(card.client)}</b><small>${esc(card.id + ' · ' + card.title)}</small></td>
                <td>${esc(doc.series + ' ' + doc.number)}<small>${esc(doc.date)}</small></td>
                <td class="num">${money(doc.amount)}</td><td>${checkbox(r, 'tnHere', 'ТН у нас')}</td><td>Не оформлен</td><td></td>
            </tr>`);
        }));
        if (kanbanRows.length) $('#fin-document-rows').insertAdjacentHTML('beforeend', kanbanRows.join(''));
        syncDocMonths();
        updateDocuments();
    }
    // Групповые ТН: одна накладная на несколько карточек (аналог групп списаний).
    function applyGroups(detail) {
        $$('#fin-document-rows tr[data-group], #fin-journal-rows tr[data-group]').forEach((row) => { byId.delete(row.dataset.record); row.remove(); });
        // Две таблицы — две сборки и две вставки вместо 2N.
        const groupDocRows = [];
        const groupJournalRows = [];
        (detail || []).forEach((g) => {
            const id = 'kbg-' + g.id;
            const cardsText = g.covers.map((cov) => cov.cardId).join(', ');
            // P0-хотфикс 23.09 (часть D): флаги группы — флаги её записи-документа
            // (boot отдаёт docTxId/tnHere/billHere). Без docTxId галочка «ТН у нас»
            // у групповой ТН не уходила на сервер (doc-flag) и терялась при
            // перечитывании, хотя у одиночных ТН работала штатно.
            const r = { id, card: 'Группа · ' + g.covers.length + ' карточек', client: g.client, date: g.date, store: g.store, estimate: '', tn: { number: g.series + ' ' + g.number, date: g.date, bill: '—' }, amount: g.amount, paid: 0, docTxId: g.docTxId || null, tnHere: Boolean(g.tnHere), billHere: Boolean(g.billHere), billRequired: false };
            byId.set(id, r);
            groupDocRows.push(`<tr data-group data-record="${id}">
                <td><b>${esc(g.client)}</b><small>${esc(r.card)}: ${esc(cardsText)}</small></td>
                <td>${esc(g.series + ' ' + g.number)}<small>${esc(g.date)}</small></td>
                <td class="num">${money(g.amount)}</td><td>${checkbox(r, 'tnHere', 'ТН у нас')}</td><td>Не оформлен</td><td></td>
            </tr>`);
            groupJournalRows.push(`<tr data-group data-record="${id}">
                <td>${esc(g.series + ' ' + g.number)}</td><td class="num">${esc(g.date)}</td>
                <td><details><summary>${esc(r.card)}</summary><p>${esc(g.client)} · ${esc(g.store)}</p><p>Карточки: ${esc(cardsText)}.</p><p>Одна накладная на всю группу; отмена возвращает остаток каждой карточке.</p></details></td>
                <td class="num">${money(g.amount)}</td>
            </tr>`);
        });
        if (groupDocRows.length) $('#fin-document-rows').insertAdjacentHTML('beforeend', groupDocRows.join(''));
        if (groupJournalRows.length) $('#fin-journal-rows').insertAdjacentHTML('beforeend', groupJournalRows.join(''));
        syncDocMonths();
        updateDocuments();
        updateJournal();
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
        const span = event.target.closest('.fin-date-edit');
        if (!span) return;
        const r = byId.get(span.dataset.record);
        if (!r) return;
        const iso = r.date.split('.').reverse().join('-');
        span.outerHTML = `<input type="date" class="fin-date-input" data-record="${r.id}" value="${iso}">`;
        const input = document.querySelector(`.fin-date-input[data-record="${r.id}"]`);
        input.focus();
        const commit = () => {
            const value = input.value;
            const ru = value ? value.split('-').reverse().join('.') : r.date;
            r.date = ru;
            if (window.V2Api && window.V2Api.token() && value) {
                window.V2Api.api('/payments/transactions/' + r.txId, { method: 'PATCH', body: { date: value } })
                    .catch(() => { const t = document.getElementById('kb-toast'); if (t) { t.hidden = false; t.textContent = 'Дату не сохранить: ' + 'ошибка'; setTimeout(() => t.hidden = true, 3000); } });
            }
            const back = document.createElement('span');
            back.className = 'num fin-date-edit';
            back.dataset.record = r.id;
            back.title = 'Изменить дату оплаты';
            back.style.cssText = 'cursor:pointer;text-decoration:underline dotted';
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
    if (docSortSelEl) docSortSelEl.addEventListener('change', () => { docSort = docSortSelEl.value; sortDocRows(); updateDocuments(); });
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
    function sortDocRows() {
        const tbody = document.getElementById('fin-document-rows');
        if (!tbody) return;
        const trs = [...tbody.querySelectorAll('tr')];
        trs.sort((a, b) => {
            const ra = byId.get(a.dataset.record), rb = byId.get(b.dataset.record);
            const ka = docSortKey(ra), kb = docSortKey(rb);
            return docSort === 'desc' ? kb.localeCompare(ka) : ka.localeCompare(kb);
        });
        trs.forEach((tr) => tbody.appendChild(tr));
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
            <td><b>${esc(c.client || 'Без клиента')}</b><small>${esc(c.id + ' · ' + c.title)}</small></td>
            <td>${esc(storeLabel(c.store))}</td><td><span class="fin-status">${esc(statusName(c.stage))}</span></td>
            <td class="num">${money(c.amount || 0)}</td><td class="num">${money(c.paidAmount || 0)}</td>
            <td class="num" style="font-weight:800">${money(debt)}</td>
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
    const controlUnposted = $('#fin-control-show-unposted');
    if (controlUnposted) controlUnposted.addEventListener('click', () => {
        const tab = $('[data-fin-tab="payments"]');
        const filter = $('[data-payment-filter="unposted"]');
        pressed('[data-fin-tab]', tab);
        $$('[data-fin-panel]').forEach((panel) => { panel.hidden = panel.id !== 'fin-payments'; });
        paymentFilter = 'unposted';
        pressed('[data-payment-filter]', filter);
        updatePayments();
        filter.focus();
    });
    $$('[data-fin-tab]').forEach((button) => button.addEventListener('click', () => {
        pressed('[data-fin-tab]', button);
        $$('[data-fin-panel]').forEach((panel) => { panel.hidden = panel.id !== button.getAttribute('aria-controls'); });
        updatePayments();
        updateDocuments();
        if (button.dataset.finTab === 'control') renderControl();
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
    // закрывают и сбрасывают. Паттерн kb-search доски.
    function wireFinSearch(wrapId) {
        const wrap = document.getElementById(wrapId);
        if (!wrap) return;
        const toggle = wrap.querySelector('.fin-search-toggle');
        const field = wrap.querySelector('.fin-search-field');
        const input = field.querySelector('input');
        const setOpen = (open) => {
            wrap.classList.toggle('is-open', open);
            field.inert = !open;
            toggle.setAttribute('aria-expanded', String(open));
            toggle.setAttribute('aria-label', open ? 'Закрыть поиск' : 'Открыть поиск');
            if (open) {
                input.focus();
            } else {
                if (input.value) { input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); }
                toggle.focus();
            }
        };
        toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
        wrap.querySelector('.fin-search-close').addEventListener('click', () => setOpen(false));
        input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); setOpen(false); } });
    }
    ['fin-payment-search-wrap', 'fin-document-search-wrap', 'fin-incoming-search-wrap'].forEach(wireFinSearch);
    // Живой поиск по документам: те же поля, что у matches (клиент, карточка,
    // дата, магазин, просчёт, ТН), плюс номер счёта в строке.
    $('#fin-document-search').addEventListener('input', () => updateDocuments());
    // Входящие: строки строятся один раз и без data-record — фильтруем по
    // тексту строки тем же нормализованием (ё → е, нижний регистр).
    function updateIncoming() {
        const q = normalize($('#fin-incoming-search').value);
        $$('#fin-incoming-rows tr').forEach((tr) => { tr.hidden = !!q && !normalize(tr.textContent).includes(q); });
    }
    $('#fin-incoming-search').addEventListener('input', updateIncoming);
    updatePayments();
    updateDocuments();
    updateJournal();
    // Обёртка вызывает их сама, проигрывая kb:documents/kb:groups, которые
    // board.js успел разослать до первого открытия раздела.
    return { applyDocuments: applyDocuments, applyGroups: applyGroups };
    }
})();
