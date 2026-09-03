// Разовая проверка канбан-карточек: бейджи склада/менеджера/оплаты — единая строка,
// ничего не обрезано вертикально, одинаковая высота пилюль.
// Запуск: node kanban-check.mjs
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
const page = await browser.newPage({ viewport: { width: 1756, height: 900 } });
page.setDefaultTimeout(25000);
await page.addInitScript(() => localStorage.setItem('crm_theme', 'light'));
await page.emulateMedia({ colorScheme: 'light' });

await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
if (await page.$('#username')) {
    await page.fill('#username', env.CRM_USER);
    await page.fill('#password', env.CRM_PASS);
    await page.click('#login-submit');
}
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3000);

// Канбан-доска
await page.click('#app-sidebar .sidebar-nav >> text="Канбан"', { timeout: 6000 }).catch(() => {});
await page.waitForSelector('.kanban-card', { state: 'visible', timeout: 15000 }).catch(() => {});
await page.waitForTimeout(3000);

// проверка: все бейджи внутри .card-meta, одинаковая высота, ничего не переполнено
const report = await page.$$eval('.kanban-card', cards => cards.slice(0, 20).map(c => {
    const meta = c.querySelector('.card-meta');
    if (!meta) return { title: c.querySelector('.card-title')?.textContent?.trim(), noMeta: true };
    const badges = [...meta.children].map(b => ({
        cls: b.className,
        h: Math.round(b.getBoundingClientRect().height),
        top: Math.round(b.getBoundingClientRect().top),
        clipped: b.scrollHeight > b.clientHeight + 1 || b.scrollWidth > b.clientWidth + 1,
        text: b.textContent.trim().slice(0, 40),
    }));
    const tops = new Set(badges.map(b => b.top));
    const hs = new Set(badges.map(b => b.h));
    return {
        title: c.querySelector('.card-title')?.textContent?.trim(),
        badges,
        sameRow: tops.size <= 1,
        sameHeight: hs.size <= 1,
    };
}));
console.log(JSON.stringify(report, null, 1));

// заголовки колонок: сумма не должна обрезаться
const heads = await page.$$eval('.kanban-column', cols => cols.map(col => {
    const h = col.querySelector('h3') || col.querySelector('.kanban-column-header') || col.firstElementChild;
    return {
        text: h?.textContent?.trim().slice(0, 60),
        clipped: h ? h.scrollWidth > h.clientWidth + 1 : null,
    };
}));
console.log('headers:', JSON.stringify(heads, null, 1));

await page.screenshot({ path: path.join(OUT, 'fix-kanban-badges-1920.jpg'), type: 'jpeg', quality: 85 });
console.log('shot: fix-kanban-badges-1920');

await browser.close();
