// Общие утилиты: тосты, подтверждения, поиск, экспорт CSV, кастомный дропдаун.
//
// Focus trap, Escape и возврат фокуса для модалок живут в ui-polish.js.
// Здесь их не дублируем: две реализации на одном keydown конкурировали —
// features.js прятал модалку через classList и возвращал фокус сам, а
// ui-polish.js в это же время искал .close-btn и возвращал фокус по своей
// цепочке, из-за чего фокус уезжал не туда.

const SAFE_COLOR_RE = /^#[0-9a-fA-F]{6}$/;
function sanitizeColor(c) { return (c && SAFE_COLOR_RE.test(c)) ? c : '#4f7cf5'; }

function getSkeletonHTML(cols, rowsCount = 5) {
    let html = '';
    for (let i = 0; i < rowsCount; i++) {
        html += '<tr class="skeleton-row">';
        for (let j = 0; j < cols; j++) {
            const widthClass = j === 0 ? 'medium' : (j === cols - 1 ? 'short' : '');
            html += `<td><div class="skeleton-line ${widthClass}"></div></td>`;
        }
        html += '</tr>';
    }
    return html;
}

/**
 * Озвучивание сообщения для скринридера.
 *
 * Держим два постоянных live-региона вместо одного: «вежливый» дожидается
 * паузы в речи, «настойчивый» перебивает — ошибку сохранения пользователь
 * должен услышать сразу, а не после того, как дочитает текущий абзац.
 * Регионы создаются один раз и живут в DOM: если создавать их вместе с
 * сообщением, скринридер не успевает заметить появление узла и молчит.
 *
 * @param {string} message
 * @param {'success'|'error'|'info'} type
 */
function announceToScreenReader(message, type = 'info') {
    const assertive = type === 'error';
    const id = assertive ? 'a11y-live-assertive' : 'a11y-live-polite';
    let region = document.getElementById(id);
    if (!region) {
        region = document.createElement('div');
        region.id = id;
        region.className = 'sr-only';
        region.setAttribute('aria-live', assertive ? 'assertive' : 'polite');
        region.setAttribute('aria-atomic', 'true');
        document.body.appendChild(region);
    }
    // Два одинаковых сообщения подряд не читаются: текст не меняется, и
    // мутации нет. Чистим регион и пишем в следующем кадре.
    region.textContent = '';
    requestAnimationFrame(() => { region.textContent = message; });
}

/**
 * Всплывающее уведомление (тост) вместо alert().
 * @param {string} message
 * @param {'success'|'error'|'info'} type
 */
function showToast(message, type = 'info', timeout = 3200) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        // Сам стек тостов — чисто визуальный. Озвучивание идёт через
        // отдельные live-регионы ниже: если объявить живой областью и
        // контейнер, и вложенный тост, часть скринридеров читает дважды.
        container.setAttribute('aria-hidden', 'true');
        document.body.appendChild(container);
    }
    announceToScreenReader(message, type);
    const icon = type === 'success' ? '✓' : (type === 'error' ? '!' : 'i');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span class="toast-icon">${icon}</span><span class="toast-msg">${escapeHtml(message)}</span><button type="button" class="toast-close" aria-label="Закрыть уведомление">✕</button>`;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));

    // UI Audit (2026-08-09): ручное закрытие через ✕.
    // Тост раньше исчезал только по таймеру (3.2 с), но для важных уведомлений
    // это долго, а для спама — нельзя убрать стек. Добавлена кнопка-крестик.
    let dismissed = false;
    const dismiss = () => {
        if (dismissed) return;
        dismissed = true;
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    };
    toast.querySelector('.toast-close').addEventListener('click', dismiss);
    setTimeout(dismiss, timeout);
}

/**
 * Стилизованное подтверждение вместо confirm(). Возвращает Promise<boolean>.
 */
function confirmDialog(message, { okText = 'Удалить', cancelText = 'Отмена', danger = true } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        overlay.innerHTML = `
            <div class="confirm-box">
                <p class="confirm-message">${escapeHtml(message)}</p>
                <div class="confirm-actions">
                    <button type="button" class="confirm-cancel">${escapeHtml(cancelText)}</button>
                    <button type="button" class="confirm-ok ${danger ? 'danger' : ''}">${escapeHtml(okText)}</button>
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
            if (e.key === 'Escape') close(false);
            if (e.key === 'Enter') close(true);
        }
        overlay.querySelector('.confirm-cancel').onclick = () => close(false);
        overlay.querySelector('.confirm-ok').onclick = () => close(true);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
        document.addEventListener('keydown', onKey);
        overlay.querySelector('.confirm-ok').focus();
    });
}

// Закрываем все открытые дропдауны при клике вне их / прокрутке / ресайзе
function closeAllDropdowns() {
    document.querySelectorAll('.dropdown.open').forEach(d => d.classList.remove('open'));
}

// Клик ВНУТРИ меню (включая полосу прокрутки) закрывать не должен.
// Клик по скроллбару приходит на элемент меню, но по координатам лежит
// за его клиентской областью — обычная проверка contains() его пропускала.
document.addEventListener('click', (e) => {
    const menu = e.target.closest && e.target.closest('.dropdown-menu, .dropdown-toggle');
    if (menu) return;
    closeAllDropdowns();
});

// Прокрутка ВНУТРИ самого меню — это листание списка, а не уход со страницы.
window.addEventListener('scroll', (e) => {
    const t = e.target;
    if (t && t.classList && t.classList.contains('dropdown-menu')) return;
    if (t && t.closest && t.closest('.dropdown-menu')) return;
    closeAllDropdowns();
}, true);

window.addEventListener('resize', closeAllDropdowns);

// Позиционируем меню как fixed относительно кнопки, чтобы его не обрезали
// контейнеры с overflow (например, горизонтально прокручиваемые таблицы).
function positionDropdownMenu(root) {
    const toggle = root.querySelector('.dropdown-toggle');
    const menu = root.querySelector('.dropdown-menu');
    const r = toggle.getBoundingClientRect();
    const menuH = menu.scrollHeight || 160;

    menu.style.position = 'fixed';
    menu.style.right = 'auto';
    // Меню повторяет ширину поля, а не растягивается на минимум 250px.
    menu.style.minWidth = r.width + 'px';
    menu.style.width = r.width + 'px';
    menu.style.maxWidth = 'none';
    // Сдвигаем влево, только если меню реально не помещается по правому краю.
    const overflowRight = r.left + r.width - (window.innerWidth - 8);
    menu.style.left = (overflowRight > 0 ? Math.max(8, r.left - overflowRight) : r.left) + 'px';

    const spaceBelow = window.innerHeight - r.bottom;
    if (spaceBelow < menuH + 16 && r.top > menuH) {
        // Не помещается снизу — открываем вверх
        menu.style.top = 'auto';
        menu.style.bottom = (window.innerHeight - r.top + 6) + 'px';
    } else {
        menu.style.bottom = 'auto';
        menu.style.top = (r.bottom + 6) + 'px';
    }
}

/**
 * Создаёт кастомный выпадающий список (вместо нативного <select>,
 * который на macOS выглядит «системно»).
 *
 * @param {Object} cfg
 * @param {Array<{value:string,label:string}>} cfg.options
 * @param {string} cfg.value - текущее значение
 * @param {(value:string)=>void} cfg.onChange - колбэк при выборе
 * @returns {HTMLElement} корневой элемент (значение читается через root.dataset.value)
 */
function createDropdown({ options, value, onChange, searchable = false }) {
    const root = document.createElement('div');
    root.className = 'dropdown' + (searchable ? ' dropdown-searchable' : '');
    root.dataset.value = value ?? '';

    const current = options.find(o => o.value === value);
    const caret = `<svg class="dropdown-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`;

    root.innerHTML = `
        <button type="button" class="dropdown-toggle">
            <span class="dropdown-value">${escapeHtml(current ? current.label : '— выбрать —')}</span>
            ${caret}
        </button>
        <div class="dropdown-menu" role="listbox">
            ${searchable ? '<input type="text" class="dropdown-search" placeholder="Поиск...">' : ''}
        </div>
    `;

    const menu = root.querySelector('.dropdown-menu');
    const valueLabel = root.querySelector('.dropdown-value');
    const searchInput = root.querySelector('.dropdown-search');

    options.forEach(opt => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'dropdown-item' + (opt.value === value ? ' selected' : '');
        item.textContent = opt.label;
        item.dataset.value = opt.value;
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            root.dataset.value = opt.value;
            valueLabel.textContent = opt.label;
            menu.querySelectorAll('.dropdown-item').forEach(i => i.classList.toggle('selected', i === item));
            root.classList.remove('open');
            if (onChange) onChange(opt.value);
        });
        menu.appendChild(item);
    });

    // Поиск по пунктам
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            const q = e.target.value.toLowerCase();
            menu.querySelectorAll('.dropdown-item').forEach(item => {
                item.style.display = item.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
        });
        searchInput.addEventListener('click', (e) => e.stopPropagation());
        searchInput.addEventListener('keydown', (e) => {
            const items = [...menu.querySelectorAll('.dropdown-item')].filter(i => i.style.display !== 'none');
            const active = menu.querySelector('.dropdown-item:focus');
            const idx = items.indexOf(active);
            if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(idx + 1, items.length - 1)]?.focus(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); items[Math.max(idx - 1, 0)]?.focus(); }
            else if (e.key === 'Enter' && active) { e.preventDefault(); active.click(); }
        });
    }

    root.querySelector('.dropdown-toggle').addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = root.classList.contains('open');
        closeAllDropdowns();
        if (!isOpen) {
            root.classList.add('open');
            positionDropdownMenu(root);
            if (searchInput) {
                searchInput.value = '';
                searchInput.dispatchEvent(new Event('input'));
                setTimeout(() => searchInput.focus(), 50);
            }
        }
    });

    // Клик внутри меню не должен закрывать его до выбора
    menu.addEventListener('click', (e) => e.stopPropagation());
    // Тачпад/колесо внутри списка листают список, а не страницу под ним
    menu.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true });
    // Нажатие на полосу прокрутки не должно доходить до document
    menu.addEventListener('mousedown', (e) => e.stopPropagation());

    return root;
}

// Минималистичные монохромные SVG-иконки (вместо эмодзи)
const ICON_CLIP = '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
const ICON_FILE = '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';

// === СОРТИРОВКА ТАБЛИЦ ===
function sortRows(rows, key, dir) {
    return [...rows].sort((a, b) => {
        let av = a[key], bv = b[key];
        if (key === 'amount') {
            return ((parseFloat(av) || 0) - (parseFloat(bv) || 0)) * dir;
        }
        if (key === 'date') {
            const at = av ? new Date(av).getTime() : 0;
            const bt = bv ? new Date(bv).getTime() : 0;
            return (at - bt) * dir;
        }
        av = (av ?? '').toString().toLowerCase();
        bv = (bv ?? '').toString().toLowerCase();
        return av.localeCompare(bv, 'ru') * dir;
    });
}

// Навешивает сортировку на заголовки таблицы. onChange(key, dir) вызывается при клике.
function setupSorting(tableId, onChange) {
    const ths = document.querySelectorAll(`#${tableId} thead th.sortable`);
    ths.forEach(th => {
        th.addEventListener('click', () => {
            const dir = th.classList.contains('sort-asc') ? -1 : 1;
            ths.forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
            th.classList.add(dir === 1 ? 'sort-asc' : 'sort-desc');
            onChange(th.dataset.sort, dir);
        });
    });
}

// Варианты для статуса печати — используются в нескольких местах
const PRINT_OPTIONS = [
    { value: 'Печать', label: 'Печать' },
    { value: 'Доверенность', label: 'Доверенность' },
    { value: 'БН', label: 'Безнал (БН)' }
];

const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                     'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

// Ключ месяца ('YYYY-MM') из ISO-даты или 'YYYY-MM-DD'. Пусто → 'none'.
function monthKeyOf(dateStr) {
    if (!dateStr) return 'none';
    const m = String(dateStr).match(/^(\d{4})-(\d{2})/);
    return m ? `${m[1]}-${m[2]}` : 'none';
}

function monthLabelOf(key) {
    if (key === 'none') return 'Без даты';
    const [y, mo] = key.split('-');
    return `${MONTH_NAMES[parseInt(mo, 10) - 1]} ${y}`;
}

/**
 * Строит дропдаун-фильтр по месяцам и монтирует его в контейнер mountId.
 * dateGetter(row) возвращает дату, по которой группируем.
 */
function buildMonthFilter(mountId, rows, dateGetter, selected, onChange) {
    const mount = document.getElementById(mountId);
    if (!mount) return;

    const keys = [...new Set(rows.map(r => monthKeyOf(dateGetter(r))))];
    // По убыванию (свежие сверху), 'none' — в конец
    keys.sort((a, b) => (a === 'none') - (b === 'none') || (a < b ? 1 : -1));

    const options = [{ value: 'all', label: 'Все месяцы' },
                     ...keys.map(k => ({ value: k, label: monthLabelOf(k) }))];

    mount.innerHTML = '';
    mount.appendChild(createDropdown({ options, value: selected || 'all', onChange }));
}

/**
 * Простой клиентский поиск: показывает только строки таблицы,
 * текст которых содержит введённую подстроку (без учёта регистра).
 */
function filterTableRows(tableId, query) {
    const q = (query || '').trim().toLowerCase();
    const rows = document.querySelectorAll(`#${tableId} tbody tr`);
    rows.forEach(row => {
        // Берём текст + значения инпутов внутри строки
        let text = row.textContent.toLowerCase();
        row.querySelectorAll('input[type="text"], input[type="search"]').forEach(inp => {
            text += ' ' + inp.value.toLowerCase();
        });
        row.style.display = (!q || text.includes(q)) ? '' : 'none';
    });
}

/**
 * Подписка поля поиска на таблицу с задержкой.
 *
 * Раньше каждая из четырёх таблиц вешала обработчик самостоятельно и без
 * задержки: filterTableRows обходит все строки и читает textContent, то есть
 * на каждое нажатие клавиши браузер делает полный проход по таблице с
 * принудительным пересчётом раскладки. На нескольких сотнях строк ввод
 * начинал заметно отставать от клавиатуры.
 *
 * @param {string} inputId  id поля поиска
 * @param {string} tableId  id таблицы
 * @param {number} delay    задержка в мс
 */
function bindTableSearch(inputId, tableId, delay = 200) {
    const input = document.getElementById(inputId);
    if (!input) return;
    let timer = null;
    input.addEventListener('input', (e) => {
        const value = e.target.value;
        clearTimeout(timer);
        timer = setTimeout(() => filterTableRows(tableId, value), delay);
    });
}

/**
 * Фильтр карточек на канбан-доске по всем полям.
 */
function filterKanbanCards(query) {
    const q = (query || '').trim().toLowerCase();
    document.querySelectorAll('#kanban-board .kanban-card').forEach(card => {
        const title = (card.querySelector('.card-title')?.textContent || '').toLowerCase();
        const tags = Array.from(card.querySelectorAll('.card-tag')).map(t => t.textContent.toLowerCase()).join(' ');
        const manager = (card.querySelector('.card-manager')?.textContent || '').toLowerCase();
        const store = (card.querySelector('.store-badge')?.textContent || '').toLowerCase();
        const amount = (card.querySelector('.card-amount')?.textContent || '').toLowerCase();
        const allText = card.textContent.toLowerCase();
        card.style.display = (!q || title.includes(q) || tags.includes(q) || manager.includes(q)
            || store.includes(q) || amount.includes(q) || allText.includes(q)) ? '' : 'none';
    });
}

/**
 * Превращает массив транзакций в CSV и инициирует скачивание.
 */
function exportTransactionsToCsv(rows, fileName) {
    if (!rows || !rows.length) {
        showToast('Нечего экспортировать — список пуст.', 'info');
        return;
    }

    const headers = ['Дата', 'Название', 'Сумма (BYN)', 'Магазин', '№ накладной',
                     'Дата накладной', 'Печать/доверенность', 'Примечание'];

    const escapeCsv = (val) => {
        const s = (val === null || val === undefined) ? '' : String(val);
        // Экранируем кавычки и оборачиваем поле, если есть спецсимволы
        if (/[";\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
        return s;
    };

    const lines = [headers.join(';')];
    rows.forEach(t => {
        const dateStr = t.date ? new Date(t.date).toLocaleDateString('ru-RU') : '';
        lines.push([
            dateStr, t.company_name, t.amount || 0, t.store_location || '',
            t.invoice_number || '', t.invoice_date || '', t.print_status || '', t.note || ''
        ].map(escapeCsv).join(';'));
    });

    // BOM, чтобы Excel корректно открыл кириллицу в UTF-8
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// Подсчёт суммы для футера таблицы
function sumAmount(rows) {
    return (rows || []).reduce((acc, t) => acc + (parseFloat(t.amount) || 0), 0);
}

function formatMoney(value) {
    return (value || 0).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * Разобрать сумму, введённую человеком.
 * Принимает «1 744,49», «1744.49», «1 744,49 BYN» — возвращает число
 * или null, если распознать не удалось.
 */
function parseMoney(value) {
    if (value === null || value === undefined) return null;
    const raw = String(value)
        .replace(/\u00a0/g, '')      // неразрывный пробел из toLocaleString
        .replace(/\s/g, '')
        .replace(/[^\d.,-]/g, '')    // отбрасываем BYN и прочие буквы
        .replace(',', '.');
    if (raw === '' || raw === '.' || raw === '-') return null;
    const num = parseFloat(raw);
    if (!isFinite(num)) return null;
    return Math.round(num * 100) / 100;
}

// === THEME TOGGLE ===
(function initTheme() {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    toggle.textContent = isDark ? '☾' : '☀';
    toggle.title = isDark ? 'Светлая тема' : 'Тёмная тема';
    toggle.addEventListener('click', () => {
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        if (dark) {
            document.documentElement.removeAttribute('data-theme');
            try { localStorage.setItem('crm_theme', 'light'); } catch(e) {}
            toggle.textContent = '☀';
            toggle.title = 'Тёмная тема';
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
            try { localStorage.setItem('crm_theme', 'dark'); } catch(e) {}
            toggle.textContent = '☾';
            toggle.title = 'Светлая тема';
        }
        // UI Audit (2026-08-09, C.3): после смены темы перерендерим Chart.js
        // с новыми цветами из CSS-переменных. Событие themeChanged
        // ловится в dashboard.js.
        document.dispatchEvent(new CustomEvent('crm:theme-changed', { detail: { dark: !dark } }));
    });
})();

// === COMPACT MODE ===
(function initCompact() {
    const toggle = document.getElementById('compact-toggle');
    if (!toggle) return;
    const isCompact = document.body.classList.contains('compact-mode');
    toggle.classList.toggle('active', isCompact);
    toggle.addEventListener('click', () => {
        document.body.classList.toggle('compact-mode');
        const active = document.body.classList.contains('compact-mode');
        try { localStorage.setItem('crm_compact', active); } catch(e) {}
        toggle.classList.toggle('active', active);
    });
})();

// === COMMAND PALETTE ===
(function initCommandPalette() {
    const overlay = document.getElementById('cmd-palette-overlay');
    const input = document.getElementById('cmd-palette-input');
    const results = document.getElementById('cmd-palette-results');
    if (!overlay || !input || !results) return;

    let allCards = [];
    let allClients = [];
    let loaded = false;

    const PAGES = [
        { id: 'page-kanban', label: 'Канбан-доска', icon: '◫' },
        { id: 'page-payments', label: 'Реестр оплат', icon: '≡' },
        { id: 'page-writeoffs', label: 'Списание', icon: '⬡' },
        { id: 'page-documents', label: 'Документы', icon: '📄' },
        { id: 'page-counterparties', label: 'Контрагенты', icon: '👥' },
        { id: 'page-dashboard', label: 'Дашборд', icon: '📊' },
    ];

    async function ensureData() {
        if (loaded) return;
        try {
            [allCards, allClients] = await Promise.all([
                apiFetch('/kanban/cards').catch(() => []),
                apiFetch('/clients').catch(() => [])
            ]);
        } catch(e) {}
        loaded = true;
    }

    function open() {
        overlay.classList.remove('hidden');
        requestAnimationFrame(() => {
            overlay.classList.add('show');
            input.value = '';
            input.focus();
            renderResults('');
        });
    }

    function close() {
        overlay.classList.remove('show');
        setTimeout(() => overlay.classList.add('hidden'), 150);
    }

    function renderResults(query) {
        const q = query.toLowerCase().trim();
        let html = '';

        const matchedPages = PAGES.filter(p => !q || p.label.toLowerCase().includes(q));
        if (matchedPages.length) {
            html += '<div class="cmd-palette-group-label">Страницы</div>';
            matchedPages.forEach(p => {
                html += `<div class="cmd-palette-item" data-type="page" data-id="${p.id}">
                    <span class="cmd-palette-item-icon">${p.icon}</span>
                    <span class="cmd-palette-item-label">${escapeHtml(p.label)}</span>
                </div>`;
            });
        }

        if (q.length >= 1) {
            const matchedCards = allCards.filter(c => c.title.toLowerCase().includes(q)).slice(0, 8);
            if (matchedCards.length) {
                html += '<div class="cmd-palette-group-label">Сделки</div>';
                matchedCards.forEach(c => {
                    html += `<div class="cmd-palette-item" data-type="card" data-id="${c.id}">
                        <span class="cmd-palette-item-icon">◧</span>
                        <span class="cmd-palette-item-label">${escapeHtml(c.title)}</span>
                        <span class="cmd-palette-item-hint">${c.total_amount || 0} BYN</span>
                    </div>`;
                });
            }

            const matchedClients = allClients.filter(c => c.name.toLowerCase().includes(q) || (c.unp && c.unp.includes(q))).slice(0, 5);
            if (matchedClients.length) {
                html += '<div class="cmd-palette-group-label">Клиенты</div>';
                matchedClients.forEach(c => {
                    html += `<div class="cmd-palette-item" data-type="client" data-id="${c.id}">
                        <span class="cmd-palette-item-icon">👤</span>
                        <span class="cmd-palette-item-label">${escapeHtml(c.name)}</span>
                        <span class="cmd-palette-item-hint">${c.unp || ''}</span>
                    </div>`;
                });
            }
        }

        if (!html) {
            html = '<div class="cmd-palette-empty">Ничего не найдено</div>';
        }

        results.innerHTML = html;

        results.querySelectorAll('.cmd-palette-item').forEach(item => {
            item.addEventListener('click', () => {
                const type = item.dataset.type;
                const id = item.dataset.id;
                close();
                if (type === 'page') {
                    const navBtn = document.querySelector(`[data-target="${id}"]`);
                    if (navBtn) navBtn.click();
                } else if (type === 'card') {
                    openCardModal(parseInt(id));
                } else if (type === 'client') {
                    const navBtn = document.querySelector('[data-target="page-counterparties"]');
                    if (navBtn) navBtn.click();
                }
            });
        });
    }

    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            if (overlay.classList.contains('hidden')) {
                ensureData().then(open);
            } else {
                close();
            }
        }
    });

    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    input.addEventListener('input', (e) => renderResults(e.target.value));
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') close();
        if (e.key === 'Enter') {
            const first = results.querySelector('.cmd-palette-item');
            if (first) first.click();
        }
    });
})();
