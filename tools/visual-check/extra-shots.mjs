// D8-срез 2: дополнительные кадры для контроля снятия !important —
// страницы/модалки, которых нет в базовой регрессии (regress.mjs).
//   node extra-shots.mjs capture   — снять эталон в tmp/extra-baseline/
//   node extra-shots.mjs compare   — сравнить с эталоном (exit 1 при расхождении)
// Стенд: BASE=http://127.0.0.1:8799 (сид) + /admin.
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, '..', '..', 'tmp', 'extra-baseline');
fs.mkdirSync(OUT, { recursive: true });
const MODE = process.argv[2] || 'compare';
const BASE = process.env.BASE || 'http://127.0.0.1:8799';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const shots = [];

async function shot(page, name) {
    const file = path.join(OUT, name + '.png');
    if (MODE === 'capture') {
        await page.screenshot({ path: file });
        shots.push(name + ' (эталон)');
        return;
    }
    const tmp = path.join(OUT, name + '.new.png');
    await page.screenshot({ path: tmp });
    const a = PNG.sync.read(fs.readFileSync(file));
    const b = PNG.sync.read(fs.readFileSync(tmp));
    if (a.width !== b.width || a.height !== b.height) {
        shots.push(`FAIL ${name}: размер ${a.width}x${a.height} -> ${b.width}x${b.height}`);
        return;
    }
    const diff = pixelmatch(a.data, b.data, null, a.width, a.height, { threshold: 0.1 });
    const total = a.width * a.height;
    const pct = (diff / total * 100).toFixed(3);
    shots.push(`${diff === 0 ? 'OK  ' : 'FAIL'} ${name} (${diff}px, ${pct}%)`);
    if (diff > total * 0.001) fs.copyFileSync(tmp, path.join(OUT, name + '.fail.png'));
    fs.rmSync(tmp, { force: true });
}

const login = async (page) => {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {});
    if (await page.$('#username')) {
        await page.fill('#username', 'visual_admin');
        await page.fill('#password', 'VisualPass123!');
        await page.click('#login-submit');
    }
    await page.waitForSelector('#app-sidebar', { state: 'visible', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2500);
};

// 1. Доска списаний (вкладка Финансов, не покрыта regress)
const p1 = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await login(p1);
await p1.click('.nav-btn[data-target="page-finance"]', { timeout: 10000 }).catch(() => {});
await p1.click('button[data-finance-tab="writeoffs"]', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(2500);
await shot(p1, 'x-writeoffs-board');
// 2. Клиенты: модалка нового клиента
await p1.click('.nav-btn[data-target="page-clients"]', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(1500);
await p1.click('#btn-add-client', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(800);
await shot(p1, 'x-modal-client');
await p1.keyboard.press('Escape').catch(() => {});
await p1.waitForTimeout(400);
// 3. Поставщики: модалка нового поставщика
await p1.click('.nav-btn[data-target="page-suppliers"]', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(1500);
await p1.click('#btn-add-supplier', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(800);
await shot(p1, 'x-modal-supplier');
await p1.keyboard.press('Escape').catch(() => {});
// 4. Задачи: модалка новой задачи
await p1.click('.nav-btn[data-target="page-tasks"]', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(1500);
await p1.click('#btn-add-task', { timeout: 8000 }).catch(() => {});
await p1.waitForTimeout(800);
await shot(p1, 'x-modal-task');
await p1.keyboard.press('Escape').catch(() => {});
await p1.close();

// 5. admin.html
const p2 = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await p2.goto(BASE.replace(/\/$/, '') + '/admin', { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {});
if (await p2.$('#username')) {
    await p2.fill('#username', 'visual_admin');
    await p2.fill('#password', 'VisualPass123!');
    await p2.click('#login-submit').catch(() => {});
}
await p2.waitForTimeout(2500);
await shot(p2, 'x-admin');
await p2.close();

await browser.close();
console.log(shots.join('\n'));
if (MODE === 'compare' && shots.some(s => s.startsWith('FAIL'))) process.exit(1);
