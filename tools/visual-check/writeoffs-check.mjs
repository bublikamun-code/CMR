// Разовая проверка страницы Списание после снятия внутренней обрезки колонок.
// Запуск: node writeoffs-check.mjs
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
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(25000);

await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
if (await page.$('#username')) {
    await page.fill('#username', env.CRM_USER);
    await page.fill('#password', env.CRM_PASS);
    await page.click('#login-submit');
}
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(3000);

// Финансы → Списание
await page.click('#app-sidebar .sidebar-nav >> text="Финансы"', { timeout: 6000 });
await page.waitForTimeout(2000);
await page.click('#page-finance >> text="Списание"', { timeout: 6000 }).catch(async () => {
    await page.click('text="Списание"', { timeout: 6000 });
});
await page.waitForTimeout(3000);

// проверка: ни один .writeoff-cards не должен скроллиться
const clipped = await page.$$eval('.writeoff-cards', els =>
    els.filter(e => e.scrollHeight > e.clientHeight + 1).length);
console.log('колонок с внутренней обрезкой:', clipped);

// переработка 2026-09-03: склады из данных, месячные группы, аккордеон
const board = await page.evaluate(() => {
    const stores = Array.from(document.querySelectorAll('.writeoff-store-block')).map(b => b.dataset.store);
    const months = Array.from(document.querySelectorAll('.wo-month-group')).map(g => ({
        store: g.closest('.writeoff-store-block')?.dataset.store,
        month: g.dataset.month,
        collapsed: g.classList.contains('collapsed'),
        sum: g.querySelector('.wo-month-sum')?.textContent?.trim(),
        count: g.querySelector('.wo-month-count')?.textContent?.trim(),
        tiles: g.querySelectorAll('.writeoff-card').length,
        hidden: g.querySelectorAll('.wo-hidden-extra').length,
        showMore: !!g.querySelector('.wo-show-more'),
    }));
    const totals = Array.from(document.querySelectorAll('.writeoff-store-block h3')).map(h => h.textContent.replace(/\s+/g, ' ').trim());
    return { stores, months, totals, monthGroups: months.length };
});
console.log(JSON.stringify(board, null, 1));

// аккордеон: клик по первому заголовку месяца меняет состояние
const firstHeader = await page.$('.wo-month-header');
if (firstHeader) {
    const before = await page.$eval('.wo-month-group', g => g.classList.contains('collapsed'));
    await firstHeader.click();
    await page.waitForTimeout(300);
    const after = await page.$eval('.wo-month-group', g => g.classList.contains('collapsed'));
    console.log('аккордеон: до =', before, 'после =', after);
    await firstHeader.click(); // вернуть как было
}

// drag&drop жив: dragover на колонке выставляет подсветку
const dnd = await page.evaluate(() => {
    const col = document.querySelector('.writeoff-column');
    col.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true }));
    const highlighted = !!col.style.boxShadow;
    col.dispatchEvent(new DragEvent('dragleave', { bubbles: true }));
    document.dispatchEvent(new DragEvent('dragend', { bubbles: true }));
    return { dragoverDelegated: highlighted, afterClear: col.style.boxShadow === '' };
});
console.log('dnd:', JSON.stringify(dnd));

await page.screenshot({ path: path.join(OUT, 'fix-writeoffs-1920-full.jpg'), type: 'jpeg', quality: 85, fullPage: true });
console.log('shot: fix-writeoffs-1920-full (fullPage)');
await page.screenshot({ path: path.join(OUT, 'fix-writeoffs-1920-viewport.jpg'), type: 'jpeg', quality: 85 });
console.log('shot: fix-writeoffs-1920-viewport');

await browser.close();
