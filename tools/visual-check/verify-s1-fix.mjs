// Повторный замер S1 после фикса localDateISO (js/card_modal.js:36).
// Read-only: модалка открывается, форма проверяется, сабмитов нет.
// Скрин: tmp/ux-audit/shots/task-s1-fixed.png
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

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(25000);

const pageErrors = [];
const consoleErrors = [];
const notFound = [];
page.on('pageerror', e => pageErrors.push(e.message));
page.on('console', m => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200) + (m.location()?.url ? ' @ ' + m.location().url : ''));
});
page.on('response', r => { if (r.status() === 404) notFound.push(r.url()); });

await page.goto('http://127.0.0.1:8801', { waitUntil: 'networkidle' }).catch(() => {});
await page.fill('#username', env.CRM_USER);
await page.fill('#password', env.CRM_PASS);
await page.click('#login-submit');
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3200);

// карточка в «Сборке» с неоплаченным/частичным бейджем (как в исходном S1)
const card = await page.evaluate(() => {
    const cols = [...document.querySelectorAll('#kanban-board .kanban-column')];
    const target = cols.find(c => c.dataset.status === 'Сборка');
    if (!target) return null;
    const cards = [...target.querySelectorAll('.kanban-card')];
    const el = cards.find(x => x.querySelector('.pay-badge-kanban.pay-unpaid, .pay-badge-kanban.pay-partial'))
        || cards.find(x => x.querySelector('.pay-badge-kanban'));
    if (!el) return null;
    el.scrollIntoView({ block: 'center' });
    return { id: el.dataset.id, title: (el.querySelector('.card-title') || {}).textContent?.trim() || '' };
});
console.log('карточка:', JSON.stringify(card));
await page.waitForTimeout(500);
await page.click(`#kanban-board .kanban-card[data-id="${card.id}"]`);
await page.waitForTimeout(2000);

// --- проверка 1: секция «Накладные» и черновик -------------------------
const inv = await page.evaluate(() => {
    const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const sec = document.getElementById('invoices-section');
    const draft = sec?.querySelector('.inv-draft');
    const num = draft?.querySelector('#new-inv-num');
    const date = draft?.querySelector('#new-inv-date');
    const amt = draft?.querySelector('#new-inv-amount');
    return {
        sectionHidden: sec ? sec.hidden : null,
        draftExists: !!draft,
        fields: {
            num: num ? { visible: vis(num), placeholder: num.placeholder } : null,
            date: date ? { visible: vis(date), value: date.value } : null,
            amount: amt ? { visible: vis(amt), value: amt.value, placeholder: amt.placeholder } : null,
        },
        restHint: (sec?.querySelector('.inv-rest-hint') || {}).textContent?.trim() || '',
        actions: draft ? [...draft.querySelectorAll('button')].map(b => b.textContent.trim()) : [],
        issuedRows: sec ? sec.querySelectorAll('.inv-item.is-done').length : 0,
    };
});
console.log('накладные:', JSON.stringify(inv, null, 2));

// --- проверка 2: дефолтная дата = сегодня (YYYY-MM-DD, локальные компоненты)
const today = new Date();
const pad = n => String(n).padStart(2, '0');
const todayStr = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
console.log('ожидаемая дата:', todayStr, '| фактическая:', inv.fields.date?.value,
    '| совпадает:', inv.fields.date?.value === todayStr);

// --- проверка 3: групповой путь (group-inv-date) ------------------------
const groupInfo = await page.evaluate(() => {
    const sec = document.getElementById('group-writeoff-section');
    if (!sec || sec.hidden) return { visible: false };
    const date = sec.querySelector('#group-inv-date');
    const inputs = [...sec.querySelectorAll('input')].map(i => ({ id: i.id, visible: !!(i.offsetWidth || i.offsetHeight), value: i.value }));
    return { visible: true, dateValue: date ? date.value : null, inputs };
});
console.log('групповое списание:', JSON.stringify(groupInfo));

// скриншот формы (скролл до секции «Накладные»)
await page.$eval('#invoices-section', el => el.scrollIntoView({ block: 'center' })).catch(() => {});
await page.waitForTimeout(400);
await page.screenshot({ path: path.join(SHOTS, 'task-s1-fixed.png'), type: 'png' });
console.log('shot: task-s1-fixed.png');

await page.keyboard.press('Escape');
await page.waitForTimeout(500);
await browser.close();

const relevantConsole = consoleErrors.filter(e => !e.includes('401') && !e.includes('Не авторизован'));
console.log('\nИТОГ:');
console.log('  форма черновика рендерится:', inv.draftExists && inv.fields.num?.visible && inv.fields.date?.visible && inv.fields.amount?.visible);
console.log('  дефолтная дата ок:', inv.fields.date?.value === todayStr, `(${inv.fields.date?.value})`);
console.log('  pageerror:', pageErrors.length ? pageErrors : 'нет');
console.log('  console errors (без шума 401):', relevantConsole.length ? relevantConsole : 'нет');
console.log('  404 URLs:', notFound.length ? notFound : 'нет');
console.log('  групповое списание:', groupInfo.visible ? `секция видна, group-inv-date=${groupInfo.dateValue}` : 'секция скрыта (групп нет)');
