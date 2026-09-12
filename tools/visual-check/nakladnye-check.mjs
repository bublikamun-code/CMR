// Проверка Vite-пилота: страница накладных на собранном js/nakladnye.bundle.js.
// Запуск: node nakladnye-check.mjs  (BASE переопределяется env: BASE=http://127.0.0.1:8765 node nakladnye-check.mjs)
// Креды — в .env рядом (в репозиторий не коммитятся).
// Выход 1, если были ошибки консоли/pageerror или сломан ключевой сценарий.
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });

const env = Object.fromEntries(
    fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
        .split('\n').filter(l => l.includes('='))
        .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
);

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const CRM_USER = process.env.CRM_USER || env.CRM_USER;
const CRM_PASS = process.env.CRM_PASS || env.CRM_PASS;
const errors = [];
const failures = [];
const check = (ok, label) => { console.log((ok ? 'OK  ' : 'FAIL') + '  ' + label); if (!ok) failures.push(label); };

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1756, height: 900 } });
page.setDefaultTimeout(25000);
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
page.on('pageerror', e => errors.push('pageerror: ' + e.message));

await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
if (await page.$('#username')) {
    await page.fill('#username', CRM_USER);
    await page.fill('#password', CRM_PASS);
    await page.click('#login-submit');
}
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(2500);
// 401/404 до логина — штатный шум загрузки экрана входа (скрипты дёргают
// apiFetch без токена), к пилоту отношения не имеет. Дальше считаем ошибки
// только после успешной аутентификации.
errors.length = 0;

// 1. Раздел «Накладные»: Финансы (сайдбар) → таб «Накладные»
await page.click('#app-sidebar .nav-btn[data-target="page-finance"]', { timeout: 8000 });
await page.click('button[data-finance-tab="nakladnye"]', { timeout: 8000 });
await page.waitForSelector('#nakladnye-table', { state: 'visible', timeout: 15000 }).catch(() => {});
await page.waitForTimeout(3000);
const rowCount = await page.$$eval('#nakladnye-table tbody tr[data-id]', r => r.length).catch(() => -1);
check(rowCount > 0, `таблица накладных загружена (строк: ${rowCount})`);
const totals = await page.$eval('#nakladnye-count', e => e.textContent).catch(() => null);
check(!!totals && /шт\./.test(totals), `итоги посчитаны ("${totals}")`);
await page.screenshot({ path: path.join(OUT, 'vite-pilot-01-table.jpg'), type: 'jpeg', quality: 85 });

// 2. Модалка: открытие кнопкой и закрытие inline-обработчиком (window.NakladnyeUI)
await page.click('#nakladnye-add-btn', { timeout: 8000 });
await page.waitForTimeout(800);
const modalVisible = await page.$eval('#nakladnaya-modal', e => getComputedStyle(e).display !== 'none').catch(() => false);
check(modalVisible, 'модалка открывается (#nakladnye-add-btn)');
await page.screenshot({ path: path.join(OUT, 'vite-pilot-02-modal.jpg'), type: 'jpeg', quality: 85 });
await page.click('#nakladnaya-modal button:has-text("Отмена")', { timeout: 6000 }).catch(() => {});
await page.waitForTimeout(800);
const modalHidden = await page.$eval('#nakladnaya-modal', e => getComputedStyle(e).display === 'none').catch(() => false);
check(modalHidden, 'модалка закрывается inline-обработчиком (NakladnyeUI.closeModal)');

// 3. Поиск по таблице (хелпер bindTableSearch из features.js)
await page.fill('#nakladnye-search', 'zzz-ничего-не-найдётся').catch(() => {});
await page.waitForTimeout(600);
const filtered = await page.$$eval('#nakladnye-table tbody tr[data-id]:not([style*="none"])', r => r.length).catch(() => -1);
check(filtered === 0, `поиск фильтрует (видимых после заведомо пустого запроса: ${filtered})`);
await page.fill('#nakladnye-search', '').catch(() => {});

// 4. Экспорт CSV (download событие)
const [ download ] = await Promise.all([
    page.waitForEvent('download', { timeout: 8000 }).catch(() => null),
    page.click('#nakladnye-export').catch(() => {}),
]);
check(!!download, `экспорт CSV отдаёт файл (${download ? download.suggestedFilename() : 'нет'})`);

// Итог
const bundleLoaded = await page.evaluate(() => typeof window.NakladnyeUI === 'object' && typeof window.loadNakladnyeTable === 'function');
check(bundleLoaded, 'window.NakladnyeUI и window.loadNakladnyeTable на месте (экспорты бандла)');
if (errors.length) { console.log('\nОШИБКИ КОНСОЛИ:'); errors.forEach(e => console.log('  ' + e)); }
await browser.close();
if (failures.length || errors.length) { console.log(`\nИТОГ: FAIL (${failures.length} проверок, ${errors.length} ошибок консоли)`); process.exit(1); }
console.log('\nИТОГ: OK — пилот работает');
