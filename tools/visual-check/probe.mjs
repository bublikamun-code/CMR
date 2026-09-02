// Интерактивные состояния: фильтры+дропдаун, уведомления, карточки клиентов/поставщиков.
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, 'shots');

const env = Object.fromEntries(
    fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
        .split('\n').filter(l => l.includes('='))
        .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
);

const BASE = 'http://87-232-64-12.nip.io';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(25000);
const shot = async (n) => { await page.screenshot({ path: path.join(OUT, n + '.jpg'), type: 'jpeg', quality: 85 }); console.log('shot:', n); };

await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
if (await page.$('#username')) {
    await page.fill('#username', env.CRM_USER);
    await page.fill('#password', env.CRM_PASS);
    await page.click('#login-submit');
}
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3000);

// фильтры + открытый дропдаун приоритета
try {
    await page.click('.filter-toggle', { timeout: 5000 });
    await page.waitForTimeout(800);
    const toggles = await page.$$('.filter-bar .dropdown-toggle');
    if (toggles.length) { await toggles[toggles.length - 1].click(); }
    await page.waitForTimeout(600);
    await shot('1440-filters-dd-open');
    await page.keyboard.press('Escape');
} catch (e) { console.log('filters:', e.message.split('\n')[0]); }

// колокольчик уведомлений
try {
    await page.click('#notif-bell', { timeout: 4000 });
    await page.waitForTimeout(800);
    await shot('1440-notif-panel');
    await page.click('#notif-bell');
} catch (e) { console.log('notif:', e.message.split('\n')[0]); }

// клиенты: карточка
try {
    await page.click('#app-sidebar .sidebar-nav >> text="Клиенты"', { timeout: 5000 });
    await page.waitForTimeout(3000);
    await page.click('.clickable-company, #clients-table tbody tr', { timeout: 6000 });
    await page.waitForTimeout(1500);
    await shot('1440-client-card');
    await page.keyboard.press('Escape');
} catch (e) { console.log('client card:', e.message.split('\n')[0]); }

// поставщики: карточка
try {
    await page.click('#app-sidebar .sidebar-nav >> text="Поставщики"', { timeout: 5000 });
    await page.waitForTimeout(3000);
    await page.click('.clickable-company, #suppliers-table tbody tr', { timeout: 6000 });
    await page.waitForTimeout(1500);
    await shot('1440-supplier-card');
    await page.keyboard.press('Escape');
} catch (e) { console.log('supplier card:', e.message.split('\n')[0]); }

await browser.close();
console.log('DONE');
