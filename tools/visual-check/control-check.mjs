// Проверка вкладки «Контроль» (Финансы) — Фаза 5, D3, и подтверждения
// каскадных чекбоксов накладных — D2.
// Запуск: BASE=http://127.0.0.1:8799 node control-check.mjs (сид-стенд,
// поднимается как в check.sh шаг 8). Креды — сид (visual_admin).
// Выход 1, если были ошибки консоли/pageerror или сломан ключевой сценарий.
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });

const BASE = process.env.BASE || 'http://127.0.0.1:8799';
const SEED_USER = 'visual_admin';
const SEED_PASS = 'VisualPass123!';
const errors = [];
const failures = [];
const check = (ok, label) => { console.log((ok ? 'OK  ' : 'FAIL') + '  ' + label); if (!ok) failures.push(label); };

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1756, height: 900 } });
page.setDefaultTimeout(25000);
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
page.on('pageerror', e => errors.push('pageerror: ' + e.message));
// D2: следим за PATCH по накладным — после «Отмены» их быть не должно
const patchUrls = [];
page.on('request', r => { if (r.method() === 'PATCH' && r.url().includes('/nakladnye/')) patchUrls.push(r.url()); });

await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
if (await page.$('#username')) {
    await page.fill('#username', SEED_USER);
    await page.fill('#password', SEED_PASS);
    await page.click('#login-submit');
}
await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
await page.waitForTimeout(2500);
// 401/404 до логина — штатный шум экрана входа (см. nakladnye-check.mjs)
errors.length = 0;
patchUrls.length = 0;

// === 1. Вкладка «Контроль»: открытие и загрузка ===
await page.click('#app-sidebar .nav-btn[data-target="page-finance"]', { timeout: 8000 });
await page.click('button[data-finance-tab="control"]', { timeout: 8000 });
await page.waitForSelector('#page-control.active', { state: 'attached', timeout: 8000 }).catch(() => {});
await page.waitForFunction(() => (document.getElementById('control-updated') || {}).textContent, null, { timeout: 15000 })
    .catch(() => {});
const panelVisible = await page.$eval('#page-control', e => e.classList.contains('active')).catch(() => false);
check(panelVisible, 'вкладка «Контроль» открывается и активна');
const updated = await page.$eval('#control-updated', e => e.textContent).catch(() => null);
check(!!updated && /обновлено/.test(updated), `метка обновления проставлена ("${updated}")`);

// === 2. Bento-плитки заполнены (значение и подпись не пустые) ===
for (const [id, label] of [
    ['control-tile-debt', 'Дебиторка'],
    ['control-tile-overdue', 'Долг 30+'],
    ['control-tile-queue', 'Очередь'],
]) {
    const val = await page.$eval(`#${id}`, e => e.textContent.trim()).catch(() => null);
    const sub = await page.$eval(`#${id}-sub`, e => e.textContent.trim()).catch(() => null);
    // «—» — корректное значение метрики при пустом множестве (см. подпись)
    check(val !== null && val !== '' && (sub === null || sub !== ''),
        `плитка «${label}»: значение "${val}", подпись "${sub}"`);
}
await page.screenshot({ path: path.join(OUT, 'control-01-board.jpg'), type: 'jpeg', quality: 85 });

// === 3. Таблицы: строки данных либо корректное пустое состояние ===
const debtRows = await page.$$eval('#control-debt-table tbody tr', r => r.length).catch(() => -1);
check(debtRows > 0, `таблица дебиторки отрисовалась (строк: ${debtRows})`);
const queueRows = await page.$$eval('#control-queue-table tbody tr', r => r.length).catch(() => -1);
check(queueRows > 0, `таблица очереди списания отрисовалась (строк: ${queueRows})`);

// === 4. Клик по строке долга открывает карточку сделки ===
const debtRow = await page.$('#control-debt-table tbody tr[title="Открыть карточку сделки"]');
if (debtRow) {
    await debtRow.click();
    await page.waitForTimeout(1500);
    const cardOpen = await page.$eval('#card-modal, .card-modal-overlay, [id*="modal"]', e => getComputedStyle(e).display !== 'none')
        .catch(() => false);
    check(cardOpen, 'клик по строке долга открывает карточку сделки');
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(600);
} else {
    console.log('SKIP  клик по строке долга — в сиде нет должников');
}

// === 5. D2: каскадный чекбокс требует подтверждения, «Отмена» не шлёт PATCH ===
await page.click('button[data-finance-tab="nakladnye"]', { timeout: 8000 });
await page.waitForSelector('#nakladnye-table', { state: 'visible', timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2500);
const cascadeCb = await page.$('#nakladnye-table tbody tr .cb-paid:not(:checked)')
    || await page.$('#nakladnye-table tbody tr .cb-arrived:not(:checked)')
    || await page.$('#nakladnye-table tbody tr .cb-verified:not(:checked)');
check(!!cascadeCb, 'есть непринятая накладная для проверки каскада');
if (cascadeCb) {
    patchUrls.length = 0;
    await cascadeCb.click();
    await page.waitForSelector('.confirm-overlay', { state: 'visible', timeout: 5000 }).catch(() => {});
    const dlgText = await page.$eval('.confirm-message', e => e.textContent).catch(() => null);
    check(!!dlgText && /Одно нажатие меняет сразу несколько полей/.test(dlgText),
        `диалог подтверждения перечисляет последствия ("${(dlgText || '').slice(0, 60)}…")`);
    await page.screenshot({ path: path.join(OUT, 'control-02-cascade-confirm.jpg'), type: 'jpeg', quality: 85 });
    await page.click('.confirm-overlay .confirm-cancel', { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(800);
    const reverted = await cascadeCb.evaluate(e => !e.checked).catch(() => null);
    check(reverted === true, 'после «Отмены» чекбокс вернулся в исходное состояние');
    check(patchUrls.length === 0, `PATCH после отмены не уходил (ушло: ${patchUrls.length})`);

    // Простой флаг без каскада (отметить «Проверена») — по-прежнему один клик
    patchUrls.length = 0;
    const simpleCb = await page.$('#nakladnye-table tbody tr .cb-verified:not(:checked)');
    if (simpleCb) {
        await simpleCb.click();
        await page.waitForTimeout(1200);
        const noDialog = await page.$('.confirm-overlay');
        check(!noDialog, 'простое изменение (без каскада) без подтверждения');
        check(patchUrls.length === 1, `PATCH простого изменения ушёл сразу (ушло: ${patchUrls.length})`);
    } else {
        console.log('SKIP  простое изменение — все «Проверена» уже отмечены');
    }
}

await browser.close();

const realErrors = errors.filter(e => !/favicon|401|404|Failed to load resource/.test(e));
if (realErrors.length) {
    console.log('\nОшибки консоли/pageerror после логина:');
    realErrors.forEach(e => console.log('  ' + e));
}
if (failures.length || realErrors.length) {
    console.log(`\nИТОГ: FAIL — ${failures.length} проверок не прошло, ошибок: ${realErrors.length}`);
    process.exit(1);
}
console.log('\nИТОГ: OK — вкладка «Контроль» и D2 работают');
