import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';

const url = new URL('../mockups/shell-v2-prototype.html', import.meta.url).href;
const routes = { board: 'Доска сделок', day: 'Пульт дня', tasks: 'Задачи', fin: 'Финансы', clients: 'Клиенты', suppliers: 'Поставщики', admin: 'Админ-панель' };
// Календарь — часть экрана «Задачи»; отдельного пункта меню больше нет.
const legacy = { calendar: 'tasks' };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
    for (const width of [1440, 375, 430]) {
        const p = await browser.newPage({ viewport: { width, height: 900 } });
        p.setDefaultTimeout(5000);
        const errors = [];
        p.on('pageerror', e => errors.push(e.message));
        await p.goto(url, { waitUntil: 'domcontentloaded' });
        async function check(route) {
            await p.waitForFunction(r => location.hash === '#' + r && document.querySelector(`[data-view="${r}"]`).getAttribute('aria-current') === 'page', route);
            assert.equal(await p.title(), routes[route] + ' — Свет в доме · Предпросмотр');
            assert.equal(await p.locator('#shell-title').textContent(), routes[route]);
            assert.equal(await p.locator('.rail [aria-current="page"]').count(), 1);
            assert.equal(await p.locator('.view:not([hidden])').count(), 1);
            assert(await p.locator('#view-' + (route === 'calendar' ? 'tasks' : route)).isVisible());
            assert.equal(await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0, `${width}/${route}: page overflow`);
        }
        await check('board');
        assert.equal(await p.locator('[data-view]').count(), 7);
        assert.equal(await p.locator('.viewbar, #view-kit, #view-mob, #open-palette').count(), 0);
        assert(await p.getByText('Предпросмотр · данные не подключены', { exact: true }).isVisible());
        for (const theme of ['dark', 'light']) {
            await p.click('#theme-btn');
            assert.equal(await p.getAttribute('html', 'data-theme'), theme);
            assert.equal(await p.getAttribute('#theme-btn', 'aria-pressed'), String(theme === 'dark'));
            for (const route of Object.keys(routes)) {
                assert.equal(await p.locator(`[data-view="${route}"]`).count(), 1);
                await p.click(`[data-view="${route}"]`);
                await check(route);
                assert.equal(await p.getAttribute('html', 'data-theme'), theme);
            }
        }
        await p.click('[data-view="board"]');
        await p.click('[data-view="fin"]');
        await p.click('[data-view="tasks"]');
        await p.goBack(); await check('fin');
        await p.goBack(); await check('board');
        await p.goForward(); await check('fin');
        await p.reload({ waitUntil: 'domcontentloaded' }); await check('fin');
        for (const bad of ['not-a-route', 'kit', 'mob', '__proto__', '%E0%A4%A']) {
            await p.evaluate(hash => { location.hash = hash; }, bad);
            await check('board');
        }
        for (const route of Object.keys(routes)) {
            await p.goto(url + '#' + route, { waitUntil: 'domcontentloaded' });
            await check(route);
        }
        for (const [oldHash, target] of Object.entries(legacy)) {
            await p.goto(url + '#' + oldHash, { waitUntil: 'domcontentloaded' });
            await check(target);
        }
        await p.click('[data-view="board"]');
        for (const item of await p.locator('.rail [aria-disabled="true"]').all()) {
            assert(await item.isDisabled());
            await item.scrollIntoViewIfNeeded();
            await item.click({ force: true }); await check('board');
            assert.match(await item.textContent(), /Недоступно в превью/);
        }
        await p.locator('[data-view="tasks"]').focus();
        await p.keyboard.press('Enter'); await check('tasks');
        await p.click('[data-view="board"]');
        // The strip is in layout, not overlaid over the board; bottom contract stays 888.
        const board = await p.locator('#kb-board').boundingBox();
        const rail = await p.locator('.rail').boundingBox();
        assert(Math.abs(board.y + board.height - 888) <= 1, JSON.stringify({ width, board }));
        if (width < 721) assert(board.y >= rail.y + rail.height);
        await p.screenshot({ path: `/tmp/navigation-${width}.png` });
        assert.deepEqual(errors, []);
        console.log(`PASS navigation: ${width}, all routes/themes, history, deep links, invalid hashes, keyboard, board bounds`);
        await p.close();
    }
} finally {
    await browser.close();
}
