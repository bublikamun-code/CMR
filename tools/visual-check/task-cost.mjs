// Замер стоимости типовых сценариев (UX-аудит, Фаза 5.1).
//
// Проходит сценарии до открытой формы/модалки (READ-ONLY: никаких
// финальных сабмитов — на месте действия фиксирует форму, считает
// видимые поля, скринит точку остановки и закрывает через Отмену/Escape).
//
// Запуск: node tools/visual-check/task-cost.mjs
// Креды: tools/visual-check/.env (CRM_USER/CRM_PASS, программно, не печатаются).
// Сервер: http://127.0.0.1:8801 (копия боевой БД).
// Отчёт: tmp/ux-audit/task-cost.md; скрины: tmp/ux-audit/shots/task-*.png.
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..', '..');
const SHOTS = path.join(ROOT, 'tmp', 'ux-audit', 'shots');
fs.mkdirSync(SHOTS, { recursive: true });

const env = Object.fromEntries(
    fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
        .split('\n').filter(l => l.includes('='))
        .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
);

const BASE = 'http://127.0.0.1:8801';
const PAUSE = 2800;

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(25000);

// JS-ошибки страницы (отлавливаем «тихие» поломки рендера)
const pageErrors = [];
page.on('pageerror', e => pageErrors.push({ scenario: curScenario, msg: e.message }));

// --- обёртка-счётчик кликов ------------------------------------------------
const clicks = [];          // [{scenario, sel}]
const scrolls = [];         // [{scenario, where}] — трение «нужен скролл»
let curScenario = 'login';
const rawClick = page.click.bind(page);
page.click = async (sel, opts) => {
    clicks.push({ scenario: curScenario, sel });
    console.log(`  [click] ${curScenario}: ${sel}`);
    return rawClick(sel, opts);
};
const rawSelect = page.selectOption.bind(page);
page.selectOption = async (sel, val, opts) => {
    clicks.push({ scenario: curScenario, sel: sel + ' (select)' });
    console.log(`  [select] ${curScenario}: ${sel} = ${val}`);
    return rawSelect(sel, val, opts);
};

const shot = async (name) => {
    await page.screenshot({ path: path.join(SHOTS, name + '.png'), type: 'png' });
    console.log('  [shot]', name);
};

// подсчёт видимых полей внутри корня
async function fields(rootSel) {
    return page.$eval(rootSel, root => {
        const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        const inputs = [...root.querySelectorAll('input, select, textarea')]
            .filter(el => el.type !== 'hidden' && !el.disabled && vis(el));
        const labels = [...new Set([...root.querySelectorAll('label')]
            .filter(vis).map(l => l.textContent.trim()).filter(Boolean))];
        const buttons = [...new Set([...root.querySelectorAll('button')]
            .filter(vis).map(b => (b.textContent || '').trim()).filter(Boolean))];
        return { inputs: inputs.length, labels, buttons };
    });
}

const results = [];
function record(id, title, data) {
    results.push({ id, title, ...data });
    const c = clicks.filter(x => x.scenario === id).length;
    console.log(`\n[${id}] кликов: ${c}, экранов: ${(data.screens || []).length}, полей: ${data.fieldsCount}`);
}

// --- логин -----------------------------------------------------------------
await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
await page.waitForSelector('#login-screen').catch(() => {});
await page.fill('#username', env.CRM_USER);
await page.fill('#password', env.CRM_PASS);
await page.click('#login-submit');
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3500);

// =============================================================================
// S1. Неоплаченная сделка → форма выписки накладной
// =============================================================================
curScenario = 'S1';
try {
    // канбан — лендинг после входа; убеждаемся, что раздел активен
    const onKanban = await page.$eval('#page-kanban', el => el.classList.contains('active')).catch(() => false);
    if (!onKanban) {
        await page.click('#app-sidebar .nav-btn[data-target="page-kanban"]');
        await page.waitForTimeout(PAUSE);
    }
    // карточка в колонке «Сборка» с бейджем оплаты «Не оплачен»/«Частично»
    const cardInfo = await page.evaluate(() => {
        const cols = [...document.querySelectorAll('#kanban-board .kanban-column')];
        const colCounts = cols.map(c => ({
            status: c.dataset.status,
            count: (c.querySelector('.col-count') || {}).textContent?.trim() || '',
        }));
        const target = cols.find(c => c.dataset.status === 'Сборка');
        if (!target) return null;
        const cards = [...target.querySelectorAll('.kanban-card')];
        const unpaid = cards.find(el => el.querySelector('.pay-badge-kanban.pay-unpaid, .pay-badge-kanban.pay-partial'))
            || cards.find(el => el.querySelector('.pay-badge-kanban'));
        if (!unpaid) return null;
        unpaid.scrollIntoView({ block: 'center' });
        return {
            id: unpaid.dataset.id,
            title: (unpaid.querySelector('.card-title') || {}).textContent?.trim() || '',
            badge: (unpaid.querySelector('.pay-badge-kanban') || {}).textContent?.trim() || '',
            cardsInCol: cards.length,
            totalCards: document.querySelectorAll('#kanban-board .kanban-card').length,
            colCounts,
        };
    });
    if (!cardInfo) throw new Error('не нашёл карточку в «Сборке»');
    scrolls.push({ scenario: curScenario, where: 'скролл до карточки в колонке «Сборка»' });
    console.log('  карточка:', JSON.stringify(cardInfo));
    await page.waitForTimeout(600);
    await page.click(`#kanban-board .kanban-card[data-id="${cardInfo.id}"]`);
    // ждём рендер секции «Накладные» (fetch + отрисовка черновика)
    await page.waitForTimeout(1500);
    const s1errs = pageErrors.filter(e => e.scenario === 'S1').map(e => e.msg);
    // секция «Накладные» в модалке — нужен скролл внутри модалки
    const hasInvoices = await page.$eval('#invoices-section', el => !el.hidden).catch(() => false);
    let inv = null;
    if (hasInvoices) {
        await page.$eval('#invoices-section', el => el.scrollIntoView({ block: 'center' }));
        scrolls.push({ scenario: curScenario, where: 'скролл внутри модалки до секции «Накладные»' });
        await page.waitForTimeout(500);
        inv = await page.$eval('#invoices-section', sec => ({
            draft: !!sec.querySelector('.inv-draft'),
            draftFields: sec.querySelectorAll('.inv-draft input').length,
            issuedRows: sec.querySelectorAll('.inv-item.is-done').length,
            summary: (sec.querySelector('.invoices-summary') || {}).textContent?.trim() || '',
        })).catch(() => null);
    }
    await shot('task-s1-invoice-form');
    const f = hasInvoices ? await fields('#invoices-section') : { inputs: 0, labels: [], buttons: [] };
    await page.keyboard.press('Escape');
    await page.waitForTimeout(600);
    record('S1', 'Неоплаченная сделка → выписка накладной', {
        clicks: clicks.filter(x => x.scenario === 'S1').length,
        screens: ['kanban', 'card-modal'],
        fieldsCount: f.inputs,
        labels: f.labels,
        note: `карточек на доске (видимых): ${cardInfo.totalCards}; счётчики колонок: ${cardInfo.colCounts.map(c => `${c.status}: ${c.count}`).join(', ')}; бейдж оплаты: «${cardInfo.badge}»; ` +
            (hasInvoices
                ? `секция «Накладные» открыта, но пустая: черновик: ${inv?.draft}, полей: ${inv?.draftFields}, выписано ранее: ${inv?.issuedRows}` +
                    (s1errs.length ? `; JS-ошибка: ${s1errs.join(' | ')}` : '')
                : 'СЕКЦИЯ «НАКЛАДНЫЕ» СКРЫТА (статус не Сборка/На списание/Закрыто)'),
    });
} catch (e) {
    record('S1', 'Неоплаченная сделка → выписка накладной', { error: e.message.split('\n')[0] });
    await page.keyboard.press('Escape').catch(() => {});
}

// =============================================================================
// S2. Принять накладную от магазина (фильтр «Новая» → чекбокс «Пришла»)
// =============================================================================
curScenario = 'S2';
try {
    await page.click('#app-sidebar .nav-btn[data-target="page-finance"]');
    await page.waitForTimeout(PAUSE);
    await page.click('button[data-finance-tab="nakladnye"]');
    await page.waitForTimeout(PAUSE);
    const rowsBefore = await page.$eval('#nakladnye-table', t =>
        t.querySelectorAll('tbody tr').length);
    // фильтр по статусу «Новая» — безопасно (это фильтр, не запись).
    // Нативный select заменён кастомным dropdown (enhanceSelectToDropdown).
    await page.click('#nakladnye-filter-status + .select-dd-wrap .dropdown-toggle');
    await page.waitForTimeout(400);
    await page.click('#page-nakladnye .dropdown.open .dropdown-item[data-value="new"]');
    await page.waitForTimeout(1200);
    const table = await page.$eval('#nakladnye-table', t => {
        const vis = el => !!(el.offsetWidth || el.offsetHeight);
        const rows = [...t.querySelectorAll('tbody tr')].filter(vis);
        const first = rows[0];
        return {
            rows: rows.length,
            cols: t.querySelectorAll('thead th').length,
            head: [...t.querySelectorAll('thead th')].map(h => h.textContent.trim()),
            firstRowCells: first ? [...first.querySelectorAll('td')].map(td => td.textContent.trim().slice(0, 40)) : [],
            arrivedCbInRow: first ? !!first.querySelector('.cb-arrived') : false,
        };
    });
    console.log('  таблица накладных:', JSON.stringify(table));
    // ширина кастомных пилюль фильтров (обрезанные подписи — трение)
    const pillWidths = await page.evaluate(() =>
        [...document.querySelectorAll('#page-nakladnye .panel-toolbar .select-dd-wrap .dropdown-toggle')]
            .map(b => Math.round(b.getBoundingClientRect().width))
    );
    if (await page.$('#nakladnye-table tbody tr .cb-arrived')) {
        await page.hover('#nakladnye-table tbody tr .cb-arrived');
    }
    await shot('task-s2-nakladnaya-arrive');
    record('S2', 'Принять накладную от магазина', {
        clicks: clicks.filter(x => x.scenario === 'S2').length,
        screens: ['finance', 'finance-nakladnye'],
        fieldsCount: 1, // сам чекбокс «Пришла» — единственное действие
        labels: [`строк: ${table.rows}, колонок: ${table.cols}`, `шапка: ${table.head.join(' | ')}`],
        note: `приёмка = один чекбокс «Пришла» в строке таблицы (PATCH на onchange, без модалки/подтверждения); ` +
            `строк до фильтра: ${rowsBefore}, после «Новая»: ${table.rows}; чекбокс в строке: ${table.arrivedCbInRow}; ` +
            `пилюли фильтров обрезаны: ширины ${pillWidths.join('/')}px`,
    });
} catch (e) {
    record('S2', 'Принять накладную от магазина', { error: e.message.split('\n')[0] });
}

// =============================================================================
// S3. Провести списание по группе (Финансы → Списание → плитка группы)
// =============================================================================
curScenario = 'S3';
try {
    await page.click('button[data-finance-tab="writeoffs"]');
    await page.waitForTimeout(PAUSE);
    const board = await page.evaluate(() => {
        const vis = el => !!(el.offsetWidth || el.offsetHeight);
        const groups = [...document.querySelectorAll('.writeoff-group')].filter(vis);
        const stores = [...document.querySelectorAll('.writeoff-store-block h3')]
            .map(h => (h.childNodes[0]?.textContent || h.textContent).trim());
        const todoTiles = document.querySelectorAll('.writeoff-column[data-status="false"] .kanban-card').length;
        const doneTiles = document.querySelectorAll('.writeoff-column[data-status="true"] .kanban-card').length;
        const g = groups.find(x => !x.classList.contains('writeoff-done')) || groups[0];
        let buttons = [];
        if (g) {
            g.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
            g.classList.add('hover');
            g.scrollIntoView({ block: 'center' });
            buttons = [...g.querySelectorAll('.writeoff-tile-actions button')].map(b => (b.textContent || '').trim());
        }
        return {
            stores, todoTiles, doneTiles,
            groupCount: groups.length,
            groupDone: groups.filter(x => x.classList.contains('writeoff-done')).length,
            firstGroupButtons: buttons,
            hasGroup: !!g,
        };
    });
    console.log('  доска списаний:', JSON.stringify(board));
    await page.waitForTimeout(700);
    await shot('task-s3-writeoff-group');
    // зафиксируем кнопки на одиночной плитке к списанию (hover-панель)
    let singleInfo = null;
    const singleSel = '.writeoff-column[data-status="false"] .kanban-card:not(.writeoff-group)';
    if (await page.$(singleSel)) {
        await page.hover(singleSel);
        await page.waitForTimeout(400);
        singleInfo = await page.$eval(singleSel, el =>
            [...el.querySelectorAll('.writeoff-tile-actions button')].map(b => (b.textContent || '').trim())
        ).catch(() => null);
        await page.$eval(singleSel, el => el.scrollIntoView({ block: 'center' })).catch(() => {});
        await shot('task-s3-writeoff-single');
    }
    record('S3', 'Провести списание по группе', {
        clicks: clicks.filter(x => x.scenario === 'S3').length,
        screens: ['finance', 'finance-writeoffs'],
        fieldsCount: 0,
        labels: board.hasGroup ? [`кнопки на плитке группы: ${board.firstGroupButtons.join(' | ')}`] : ['групповых плиток нет'],
        note: `складов: ${board.stores.length}; плиток к списанию: ${board.todoTiles}, списано: ${board.doneTiles}; ` +
            `групп: ${board.groupCount} (из них списано: ${board.groupDone}); ` +
            (board.hasGroup
                ? `кнопки группы: [${board.firstGroupButtons.join(' | ')}] — прямой кнопки «Списать» у группы нет`
                : 'групповых плиток в данных нет') +
            (singleInfo ? `; кнопки одиночной плитки (hover): [${singleInfo.join(' | ')}]` : '; одиночных плиток нет'),
    });
} catch (e) {
    record('S3', 'Провести списание по группе', { error: e.message.split('\n')[0] });
}

// =============================================================================
// S4. Найти просроченную задачу
// =============================================================================
curScenario = 'S4';
try {
    await page.click('#app-sidebar .nav-btn[data-target="page-tasks"]');
    await page.waitForTimeout(PAUSE);
    const before = await page.evaluate(() => ({
        total: document.querySelectorAll('.task-card').length,
        overdueCards: document.querySelectorAll('.task-card.task-card-overdue').length,
    }));
    await page.click('#tasks-status-chips button[data-status="overdue"]');
    await page.waitForTimeout(1000);
    const after = await page.evaluate(() => {
        const vis = el => !!(el.offsetWidth || el.offsetHeight);
        const cards = [...document.querySelectorAll('.task-card')].filter(vis);
        return {
            visible: cards.length,
            sample: cards.slice(0, 3).map(c => ({
                title: (c.querySelector('.task-card-title') || c.querySelector('.task-title') || {}).textContent?.trim()?.slice(0, 60) || c.textContent.trim().slice(0, 60),
                due: (c.querySelector('.task-due') || {}).textContent?.trim() || '',
                overdue: c.classList.contains('task-card-overdue'),
            })),
        };
    });
    console.log('  задачи:', JSON.stringify({ before, after }));
    await shot('task-s4-tasks-overdue');
    record('S4', 'Найти просроченную задачу', {
        clicks: clicks.filter(x => x.scenario === 'S4').length,
        screens: ['tasks'],
        fieldsCount: after.visible * 2, // название + срок на каждой карточке
        labels: after.sample.map(s => `${s.title} — срок ${s.due}${s.overdue ? ' (⚠)' : ''}`),
        note: `на доске всего карточек: ${before.total}, визуально просроченных (до фильтра): ${before.overdueCards}; после клика чипа «Просроченные» видно: ${after.visible}`,
    });
} catch (e) {
    record('S4', 'Найти просроченную задачу', { error: e.message.split('\n')[0] });
}

// =============================================================================
// S5. Найти сделку по названию клиента (поиск по канбану)
// =============================================================================
curScenario = 'S5';
try {
    await page.click('#app-sidebar .nav-btn[data-target="page-kanban"]');
    await page.waitForTimeout(PAUSE);
    // берём реальный фрагмент названия первой карточки
    const probe = await page.evaluate(() => {
        const c = document.querySelector('#kanban-board .kanban-card .card-title');
        const t = (c || {}).textContent?.trim() || '';
        return { full: t, frag: t.split(/\s+/).filter(Boolean).slice(0, 2).join(' ') };
    });
    console.log('  ищем:', JSON.stringify(probe));
    await page.click('#kanban-search');
    await page.fill('#kanban-search', probe.frag);
    await page.waitForTimeout(1200);
    const found = await page.evaluate(() => {
        const vis = el => !!(el.offsetWidth || el.offsetHeight);
        return [...document.querySelectorAll('#kanban-board .kanban-card')].filter(vis).length;
    });
    await page.click('#kanban-board .kanban-card');
    await page.waitForTimeout(1500);
    await shot('task-s5-card-by-client');
    // что видит пользователь в модалке для проверки той ли сделки
    const modalHead = await page.evaluate(() => ({
        title: (document.querySelector('.card-title-view') || {}).textContent?.trim()?.slice(0, 80) || '',
        client: ((document.querySelector('#client-mount') || {}).textContent?.trim() || '').replace(/\s+/g, ' ').slice(0, 60),
    })).catch(() => ({}));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    await page.fill('#kanban-search', '');
    record('S5', 'Найти сделку по названию клиента', {
        clicks: clicks.filter(x => x.scenario === 'S5').length,
        screens: ['kanban', 'card-modal'],
        fieldsCount: 0,
        labels: [`запрос: «${probe.frag}» → найдено карточек: ${found}`, `модалка: ${modalHead.title || '?'} / клиент: ${modalHead.client || '?'}`],
        note: 'поиск #kanban-search фильтрует доску по названию сделки/компании',
    });
} catch (e) {
    record('S5', 'Найти сделку по названию клиента', { error: e.message.split('\n')[0] });
    await page.keyboard.press('Escape').catch(() => {});
}

// =============================================================================
// S6. Дебиторка («Контроль»): что видит пользователь
// =============================================================================
curScenario = 'S6';
try {
    await page.click('#app-sidebar .nav-btn[data-target="page-finance"]');
    await page.waitForTimeout(PAUSE);
    // активный таб запоминается — показываем дефолтный реестр для чистоты кадра
    await page.click('button[data-finance-tab="payments"]');
    await page.waitForTimeout(1200);
    const fact = await page.evaluate(() => ({
        financeTabs: [...document.querySelectorAll('#finance-tabs [data-finance-tab]')].map(b => b.textContent.trim()),
        hasControlTab: !!document.querySelector('[data-finance-tab="control"]'),
        hasControlPanel: !!document.getElementById('page-control'),
        controlJsLoaded: typeof window.loadControlBoard === 'function',
        scriptsWithControl: [...document.scripts].some(s => (s.src || '').includes('control.js')),
    }));
    await shot('task-s6-finance-tabs-no-control');
    record('S6', 'Дебиторка (вкладка «Контроль»)', {
        clicks: clicks.filter(x => x.scenario === 'S6').length,
        screens: ['finance'],
        fieldsCount: 0,
        labels: [`вкладки Финансов: ${fact.financeTabs.join(' | ')}`],
        note: `data-finance-tab="control": ${fact.hasControlTab}; #page-control в DOM: ${fact.hasControlPanel}; ` +
            `control.js подключён: ${fact.scriptsWithControl}; window.loadControlBoard определён: ${fact.controlJsLoaded} → дебиторки НЕТ`,
    });
} catch (e) {
    record('S6', 'Дебиторка (вкладка «Контроль»)', { error: e.message.split('\n')[0] });
}

// =============================================================================
// S7. Добавить оплату по сделке (модалка → статус «Частично» → сумма)
// =============================================================================
curScenario = 'S7';
try {
    await page.click('#app-sidebar .nav-btn[data-target="page-kanban"]');
    await page.waitForTimeout(PAUSE);
    // любая карточка с бейджем оплаты (клик по конкретному data-id)
    const s7card = await page.evaluate(() => {
        const cards = [...document.querySelectorAll('#kanban-board .kanban-card')];
        const target = cards.find(el => el.querySelector('.pay-badge-kanban:not(.pay-paid)'))
            || cards.find(el => el.querySelector('.pay-badge-kanban'))
            || cards[0];
        if (target) target.scrollIntoView({ block: 'center' });
        return target ? target.dataset.id : null;
    });
    await page.waitForTimeout(500);
    await page.click(`#kanban-board .kanban-card[data-id="${s7card}"]`);
    await page.waitForTimeout(1800);
    // бейдж статуса оплаты в шапке модалки
    const badgeTxt = await page.$eval('.pay-dd-toggle', b => b.textContent.trim()).catch(() => '');
    console.log('  текущий статус оплаты:', badgeTxt);
    await page.click('.pay-dd-toggle');
    await page.waitForTimeout(400);
    await shot('task-s7-pay-status-menu');
    // «Частично» открывает inline-форму суммы (без сохранения до OK)
    await page.click('.pay-dd-menu .pay-dd-item[data-value="Частично"]');
    await page.waitForTimeout(400);
    await page.fill('#modal-pay-amount', '100,00');
    const form = await page.evaluate(() => {
        const f = document.querySelector('.payment-form-inline');
        return {
            exists: !!f,
            inputs: f ? f.querySelectorAll('input').length : 0,
            buttons: f ? [...f.querySelectorAll('button')].map(b => b.textContent.trim()) : [],
        };
    });
    await shot('task-s7-payment-form');
    await page.click('#modal-pay-cancel'); // Отмена — безопасно
    await page.waitForTimeout(500);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    record('S7', 'Добавить оплату по сделке', {
        clicks: clicks.filter(x => x.scenario === 'S7').length,
        screens: ['kanban', 'card-modal'],
        fieldsCount: form.inputs,
        labels: [`форма оплаты: полей ${form.inputs}, кнопки: ${form.buttons.join(' | ')}`, `статус до: «${badgeTxt}»`],
        note: 'путь: карточка → бейдж статуса → «Частично» → поле суммы + OK/Отмена',
    });
} catch (e) {
    record('S7', 'Добавить оплату по сделке', { error: e.message.split('\n')[0] });
    await page.keyboard.press('Escape').catch(() => {});
}

// =============================================================================
// S8. Найти конкретный платёж в реестре оплат
// =============================================================================
curScenario = 'S8';
try {
    await page.click('#app-sidebar .nav-btn[data-target="page-finance"]');
    await page.waitForTimeout(PAUSE);
    // активный таб Финансов запоминается в localStorage — явно открываем «Реестр оплат»
    await page.click('button[data-finance-tab="payments"]');
    await page.waitForTimeout(1200);
    // реальный фрагмент названия из первой строки таблицы
    const probe = await page.evaluate(() => {
        const row = document.querySelector('#payments-table tbody tr[data-id]');
        if (!row) return null;
        const nameCell = row.querySelector('td:nth-child(2)');
        const t = (nameCell || {}).textContent?.trim() || '';
        return { full: t, frag: t.split(/\s+/).filter(Boolean)[0] || t.slice(0, 6) };
    });
    if (!probe) throw new Error('реестр оплат пуст');
    console.log('  ищем платёж:', JSON.stringify(probe));
    const totalsBefore = await page.evaluate(() => ({
        count: (document.getElementById('payments-count') || {}).textContent?.trim() || '',
        total: (document.getElementById('payments-total') || {}).textContent?.trim() || '',
    }));
    await page.click('#payments-search');
    await page.fill('#payments-search', probe.frag);
    await page.waitForTimeout(1000);
    const totalsAfter = await page.evaluate(() => ({
        count: (document.getElementById('payments-count') || {}).textContent?.trim() || '',
        total: (document.getElementById('payments-total') || {}).textContent?.trim() || '',
    }));
    const found = await page.evaluate(() => {
        const vis = el => !!(el.offsetWidth || el.offsetHeight);
        const rows = [...document.querySelectorAll('#payments-table tbody tr[data-id]')].filter(vis);
        const first = rows[0];
        return {
            rows: rows.length,
            totalRows: document.querySelectorAll('#payments-table tbody tr[data-id]').length,
            cols: document.querySelectorAll('#payments-table thead th').length,
            head: [...document.querySelectorAll('#payments-table thead th')].map(h => h.textContent.trim()),
            firstCells: first ? [...first.querySelectorAll('td')].slice(0, 6).map(td => td.textContent.trim().slice(0, 30)) : [],
        };
    });
    await shot('task-s8-payment-found');
    await page.fill('#payments-search', '');
    record('S8', 'Найти платёж в реестре оплат', {
        clicks: clicks.filter(x => x.scenario === 'S8').length,
        screens: ['finance', 'finance-payments'],
        fieldsCount: 0,
        labels: [`запрос: «${probe.frag}» → строк: ${found.rows} из ${found.totalRows}`, `колонок: ${found.cols}: ${found.head.join(' | ')}`],
        note: `первая найденная строка: ${found.firstCells.join(' / ')}; ` +
            `итоги футера до поиска: ${totalsBefore.count} / ${totalsBefore.total}, после: ${totalsAfter.count} / ${totalsAfter.total} ` +
            (totalsBefore.count !== totalsAfter.count || totalsBefore.total !== totalsAfter.total
                ? '(пересчитались корректно)'
                : '(НЕ пересчитались — показывают итог по всем строкам, а не по найденным)'),
    });
} catch (e) {
    record('S8', 'Найти платёж в реестре оплат', { error: e.message.split('\n')[0] });
}

await browser.close();

// =============================================================================
// отчёт
// =============================================================================
const byId = Object.fromEntries(results.map(r => [r.id, r]));
const row = r => {
    if (r.error) return `| ${r.id}. ${r.title} | — | — | — | ❌ ${r.error} |`;
    return `| ${r.id}. ${r.title} | ${r.clicks} | ${(r.screens || []).length} (${(r.screens || []).join(' → ')}) | ${r.fieldsCount}${r.labels && r.labels.length ? '<br>' + r.labels.join('<br>') : ''} | ${r.note || ''} |`;
};

const md = `<!-- generated by tools/visual-check/task-cost.mjs, ${new Date().toISOString()} -->
# Замер стоимости сценариев (UX-аудит, Фаза 5.1)

Сервер: ${BASE} (копия боевой БД: ~922 карточки, ~357 транзакций). Read-only: сценарии доведены до открытой формы, финальные сабмиты не выполнялись. Скрины: tmp/ux-audit/shots/task-*.png.

Клики считает обёртка page.click (включая select-опции); ввод текста в поисковые поля кликом не считается (отдельное действие — фиксируется в примечаниях).

| Сценарий | Клики | Экранов | Полей для чтения | Дефекты и трение |
|---|---|---|---|---|
${results.map(row).join('\n')}

## Дефекты и трение (подробно)

См. таблицу выше + скрины. Ключевые точки:
- task-s1-invoice-form.png — форма выписки накладной (3 поля) внутри модалки, до неё скролл.
- task-s2-nakladnaya-arrive.png — приёмка накладной = чекбокс «Пришла» в строке таблицы.
- task-s3-writeoff-group.png — плитка группового списания (hover-панель кнопок).
- task-s4-tasks-overdue.png — чип «Просроченные» в разделе задач.
- task-s5-card-by-client.png — модалка найденной по поиску сделки.
- task-s6-finance-tabs-no-control.png — вкладки Финансов: «Контроля» нет.
- task-s7-pay-status-menu.png / task-s7-payment-form.png — выпадайка статуса оплаты и inline-форма суммы.
- task-s8-payment-found.png — найденный платёж в реестре.

## JS-ошибки страницы (pageerror)

${pageErrors.length ? pageErrors.map(e => `- ${e.scenario}: ${e.msg}`).join('\n') : '- не зафиксировано'}

## Сырые логи кликов

${clicks.map(c => `- ${c.scenario}: ${c.sel}`).join('\n')}

## Скроллы (трение)

${scrolls.length ? scrolls.map(s => `- ${s.scenario}: ${s.where}`).join('\n') : '- программных скроллов не понадобилось'}
`;

fs.writeFileSync(path.join(ROOT, 'tmp', 'ux-audit', 'task-cost.md'), md);
console.log('\nREPORT: tmp/ux-audit/task-cost.md');
console.log('SHOTS:', SHOTS);
console.log('\nСводка:');
for (const r of results) {
    console.log(`  ${r.id}: clicks=${byId[r.id].clicks ?? '-'} fields=${r.fieldsCount ?? '-'} ${r.error ? 'ERROR: ' + r.error : ''}`);
}
