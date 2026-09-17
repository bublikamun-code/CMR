// Проверка стенда site-v2 (Этап 0–1): вход, чтение реальных данных из API,
// сверка сумм доски с JSON API, реестр оплат, клиенты.
// Предпосылки: бекенд на 127.0.0.1:8125 с CRM_DATA_DIR на сид-БД
// (tools/seed_v2_stand.py), статика site-v2 на 127.0.0.1:8123.
// Запуск: node v2-stand-verify.mjs  (логин/пароль — переменные окружения)
import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';

const FRONT = process.env.V2_FRONT || 'http://127.0.0.1:8123/index.html';
const API = process.env.V2_API || 'http://127.0.0.1:8125';
const USER = process.env.V2_USER || 'ManagerY';
const PASSWORD = process.env.V2_PASSWORD || 'stand-pass-1';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
    const p = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    p.setDefaultTimeout(8000);
    const errors = [];
    p.on('pageerror', e => errors.push(e.message));
    await p.addInitScript(base => { try { localStorage.setItem('crm_api_base', base); } catch (e) {} }, API);

    await p.goto(FRONT + '#board', { waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(700);
    assert(!await p.evaluate(() => document.getElementById('login-overlay').hidden), 'должна показаться форма входа');

    await p.fill('#login-username', USER);
    await p.fill('#login-password', 'wrong-pass');
    await p.click('#login-submit');
    await p.waitForTimeout(500);
    assert.match(await p.textContent('#login-error'), /Неверное имя пользователя или пароль/, 'неверный пароль даёт ошибку');

    await p.fill('#login-password', PASSWORD);
    await p.click('#login-submit');
    await p.waitForFunction(() => document.getElementById('login-overlay').hidden);
    await p.waitForTimeout(1200);

    const source = await p.textContent('#shell-source');
    assert.match(source, /CRM API/, 'индикатор источника — CRM API: ' + source);

    // Доска против API: количество и сумма (в копейках)
    const board = await p.evaluate(() => ({
        onBoard: [...document.querySelectorAll('#kb-board .kb-card')].length,
        sumKop: window.KBData.cards
            .filter(c => c.stage !== 'done')
            .reduce((a, c) => a + c.amount, 0),
        queue: document.getElementById('kb-queue-count').textContent
    }));
    const api = await p.evaluate(async () => {
        const r = await fetch((localStorage.getItem('crm_api_base') || '') + '/kanban/cards', {
            headers: { Authorization: 'Bearer ' + localStorage.getItem('crm_token') }
        });
        const cards = await r.json();
        const open = cards.filter(c => c.status !== 'Закрыто');
        return {
            count: open.length,
            sumKop: open.reduce((a, c) => a + Math.round(c.total_amount * 100), 0)
        };
    });
    assert.equal(board.onBoard + Number(board.queue), api.count, 'карточек на доске + в очереди = всего в API');
    assert.equal(board.sumKop, api.sumKop, 'суммы доски (копейки) совпадают с API');

    // Реестр оплат: строки из API-транзакций
    await p.evaluate(() => { location.hash = '#fin'; });
    await p.waitForTimeout(400);
    await p.click('[data-fin-tab="payments"]');
    const finRows = await p.locator('#fin-payment-rows tr').count();
    assert(finRows > 0, 'в реестре есть строки из API');
    const total = await p.textContent('#fin-payment-total');

    // Клиенты
    await p.evaluate(() => { location.hash = '#clients'; });
    await p.waitForTimeout(400);
    const clients = await p.locator('#cl-rows tr').count();
    assert(clients > 0, 'клиенты из API отрисованы');

    // Пульт
    await p.evaluate(() => { location.hash = '#day'; });
    await p.waitForTimeout(400);
    const deals = Number(await p.textContent('#day-kpi-deals'));
    assert(deals > 0, 'KPI пульта считается');

    // Запись 2.1: смена этапа карточки сохраняется в CRM
    await p.evaluate(() => { location.hash = '#board'; });
    await p.waitForTimeout(400);
    const moved = await p.evaluate(() => {
        const card = window.KBData.cards.find(c => c.stage === 'new') ||
                     window.KBData.cards.find(c => c.stage === 'work');
        const from = card.stage;
        document.querySelector(`#kb-board [data-card="${card.id}"]`).click();
        const sel = document.querySelector('[data-deal-field="stage"]');
        const next = [...sel.options].map(o => o.value).find(v => v !== card.stage && v !== 'done');
        sel.value = next;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        return { id: card.id, from, to: next };
    });
    await p.waitForTimeout(800);
    await p.reload({ waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(1300);
    const persisted = await p.evaluate(id => {
        const card = window.KBData.cards.find(c => c.id === id);
        return { stage: card && card.stage };
    }, moved.id);
    assert.equal(persisted.stage, moved.to, 'этап карточки пережил перезагрузку: ' + JSON.stringify(moved));

    // Запись 2.2: создание и отметка задачи
    await p.evaluate(() => { location.hash = '#tasks'; });
    await p.waitForTimeout(400);
    const taskTitle = 'Проверка записи v2 ' + Date.now();
    await p.evaluate(t => document.getElementById('task-new').click(), taskTitle);
    await p.waitForTimeout(200);
    await p.evaluate(t => {
        document.getElementById('task-title').value = t;
        document.getElementById('task-due').value = '2026-09-30';
        document.getElementById('task-form').requestSubmit();
    }, taskTitle);
    await p.waitForTimeout(600);
    const toggle = await p.evaluate(title => {
        const t = window.KBData.tasks.find(x => x.title === title);
        const box = document.querySelector(`#tasks-list [data-task-toggle="${t.id}"]`);
        box.click();
        return { id: t.id, done: t.done };
    }, taskTitle);
    await p.waitForTimeout(600);
    await p.reload({ waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(1300);
    const taskPersisted = await p.evaluate(title => {
        const t = window.KBData.tasks.find(x => x.title === title);
        return t ? { done: t.done } : null;
    }, taskTitle);
    assert(taskPersisted, 'созданная задача есть в CRM после перезагрузки');
    assert.equal(taskPersisted.done, true, 'отметка выполнения пережила перезагрузку');
    assert.notEqual(toggle, null);

    await p.screenshot({ path: '/tmp/v2-stand-verified.png' });
    assert.deepEqual(errors, [], 'нет ошибок JS: ' + errors.join('; '));
    console.log(`PASS v2-stand: вход, доска ${board.onBoard}+${board.queue} карточек, суммы ${board.sumKop} коп. = API, реестр ${finRows} строк, клиентов ${clients}, запись: карточка ${moved.from}→${moved.to} и задача сохранены`);
} finally {
    await browser.close();
}
