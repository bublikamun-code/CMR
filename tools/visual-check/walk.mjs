// Скриншот-обход прода для визуального аудита.
// Запуск: node walk.mjs (креды в .env рядом, в репозиторий не коммитятся).
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

const BASE = 'http://87-232-64-12.nip.io';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(20000);

const shot = async (name) => {
    await page.screenshot({ path: path.join(OUT, name + '.jpg'), type: 'jpeg', quality: 85 });
    console.log('shot:', name);
};

// 1. Экран входа
await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
await page.waitForSelector('#login-screen').catch(() => {});
await shot('01-login');

// 2. Вход
await page.fill('#username', env.CRM_USER);
await page.fill('#password', env.CRM_PASS);
await page.click('#login-submit');
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3500);
await shot('02-home');

// 3. Все разделы сайдбара
const labels = await page.$$eval(
    '#app-sidebar .sidebar-nav a, #app-sidebar .sidebar-nav button',
    els => [...new Set(els.map(e => (e.textContent || '').trim()).filter(Boolean))]
);
console.log('NAV:', JSON.stringify(labels));

for (const label of labels) {
    try {
        await page.click(`#app-sidebar .sidebar-nav >> text="${label}"`, { timeout: 4000 });
        await page.waitForTimeout(2800);
        const safe = label.replace(/[^\wа-яА-ЯёЁ-]+/g, '_').slice(0, 24);
        await shot('nav-' + safe);
    } catch {
        console.log('skip:', label);
    }
}

// 4. Модалка сделки на канбане
try {
    await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForTimeout(2500);
    await page.click('.kanban-card', { timeout: 6000 });
    await page.waitForTimeout(1500);
    await shot('modal-card');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
} catch (e) {
    console.log('kanban modal:', e.message.split('\n')[0]);
}

// 5. Открытые фильтры + дропдаун
try {
    await page.click('.filter-toggle', { timeout: 3000 });
    await page.waitForTimeout(800);
    await page.click('.dropdown-toggle', { timeout: 3000 });
    await page.waitForTimeout(600);
    await shot('dropdown-open');
} catch (e) {
    console.log('dropdown:', e.message.split('\n')[0]);
}

// 6. Тёмная тема
try {
    await page.click('.theme-toggle', { timeout: 3000 });
    await page.waitForTimeout(1200);
    await shot('theme-dark');
} catch (e) {
    console.log('theme:', e.message.split('\n')[0]);
}

await browser.close();
console.log('DONE, shots in', OUT);
