// Аудит-матрица: ключевые экраны + интерактивные состояния + ширины.
// 1920 CSS px ≈ физическим 1440 при масштабе браузера 75%.
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
const page = await browser.newPage();
page.setDefaultTimeout(25000);

const shot = async (name) => {
    await page.screenshot({ path: path.join(OUT, name + '.jpg'), type: 'jpeg', quality: 85 });
    console.log('shot:', name);
};

const goNav = async (label) => {
    await page.click(`#app-sidebar .sidebar-nav >> text="${label}"`, { timeout: 6000 });
    await page.waitForTimeout(3000);
};

await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
if (await page.$('#username')) {
    await page.fill('#username', env.CRM_USER);
    await page.fill('#password', env.CRM_PASS);
    await page.click('#login-submit');
}
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3500);

// --- 1440px: ключевые экраны и интерактивные состояния ---
await shot('1440-kanban');

try {
    await page.waitForSelector('.kanban-card', { timeout: 12000 });
    await page.click('.kanban-card');
    await page.waitForTimeout(2000);
    await shot('1440-modal-card');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(700);
} catch (e) { console.log('modal:', e.message.split('\n')[0]); }

try {
    await page.click('.filter-toggle', { timeout: 5000 });
    await page.waitForTimeout(900);
    await page.click('.filter-bar .dropdown-toggle', { timeout: 5000 });
    await page.waitForTimeout(700);
    await shot('1440-filters-dropdown');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
} catch (e) { console.log('filters:', e.message.split('\n')[0]); }

await goNav('Задачи');      await shot('1440-tasks');
await goNav('Финансы');     await shot('1440-payments');
await goNav('Клиенты');     await shot('1440-clients');
await goNav('Поставщики');  await shot('1440-suppliers');
await goNav('Дашборд');     await shot('1440-dashboard');
await goNav('Календарь');   await shot('1440-calendar');
await goNav('Настройки');   await shot('1440-settings');

// тёмная тема
try {
    await page.click('.theme-toggle', { timeout: 4000 });
    await page.waitForTimeout(1200);
    await shot('1440-dark-current');
    await page.click('.theme-toggle', { timeout: 4000 });
    await page.waitForTimeout(900);
} catch (e) { console.log('theme:', e.message.split('\n')[0]); }

// --- матрица ширин: канбан и финансы ---
for (const [tag, w] of [['z75-1920', 1920], ['w1280', 1280], ['w1024', 1024]]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(1200);
    try { await goNav('Канбан-доска'); await shot(tag + '-kanban'); } catch (e) { console.log(tag, 'kanban skip'); }
    try { await goNav('Финансы'); await shot(tag + '-payments'); } catch (e) { console.log(tag, 'payments skip'); }
}

await browser.close();
console.log('DONE, shots in', OUT);
