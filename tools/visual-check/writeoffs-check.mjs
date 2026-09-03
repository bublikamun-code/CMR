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

await page.screenshot({ path: path.join(OUT, 'fix-writeoffs-1920-full.jpg'), type: 'jpeg', quality: 85, fullPage: true });
console.log('shot: fix-writeoffs-1920-full (fullPage)');
await page.screenshot({ path: path.join(OUT, 'fix-writeoffs-1920-viewport.jpg'), type: 'jpeg', quality: 85 });
console.log('shot: fix-writeoffs-1920-viewport');

await browser.close();
