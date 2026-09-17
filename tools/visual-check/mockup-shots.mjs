// Скриншоты HTML-макетов из tools/mockups — локальный инструмент, в прод не деплоится.
//
// Зачем: макеты обсуждаются глазами, а не текстом. Скрипт снимает все
// состояния макета (виды, тёмную тему, ⌘K-палитру, мобильную ширину) и
// складывает PNG в shots-mockup/ рядом. Playwright берётся из node_modules
// этого же каталога (см. package.json → playwright-core).
//
// Запуск:  node mockup-shots.mjs [файл-макета.html]
//          (по умолчанию shell-v2-prototype.html)
// Результат: tools/visual-check/shots-mockup/{макет}-{состояние}.png
//
// Соглашение для макетов (чтобы скрипт работал без правок):
//   .viewtab[data-view="..."]  — переключатели видов
//   #view-<view>               — секции видов
//   #theme-btn                 — переключение светлой/тёмной темы
//   #open-palette              — открытие палитры
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const file = process.argv[2] || 'shell-v2-prototype.html';
const mockPath = path.resolve(__dirname, '..', 'mockups', file);
if (!fs.existsSync(mockPath)) {
    console.error('Нет такого макета:', mockPath);
    process.exit(1);
}
const base = path.basename(file, '.html');
const OUT = path.join(__dirname, 'shots-mockup');
fs.mkdirSync(OUT, { recursive: true });
const URL_MOCK = 'file://' + mockPath;

const errors = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));

    await page.goto(URL_MOCK, { waitUntil: 'load' });
    await page.waitForTimeout(900);

    // все объявленные в макете виды — в светлой теме
    const views = await page.$$eval('.viewtab', tabs => tabs.map(t => t.dataset.view));
    for (const v of views) {
        await page.click(`.viewtab[data-view="${v}"]`);
        await page.waitForTimeout(250);
        await page.screenshot({ path: path.join(OUT, `${base}-light-${v}.png`) });
    }

    // тот же макет в тёмной теме — на первом виде
    if (views.length) {
        await page.click(`.viewtab[data-view="${views[0]}"]`);
        if (await page.$('#theme-btn')) {
            await page.click('#theme-btn');
            await page.waitForTimeout(250);
            await page.screenshot({ path: path.join(OUT, `${base}-dark-${views[0]}.png`) });
            await page.click('#theme-btn');   // вернуть светлую
            await page.waitForTimeout(150);
        }
    }

    // ⌘K-палитра, если она есть в макете
    if (await page.$('#open-palette')) {
        await page.click('#open-palette');
        await page.waitForTimeout(250);
        await page.screenshot({ path: path.join(OUT, `${base}-palette.png`) });
        await page.keyboard.press('Escape');
    }
    await ctx.close();

    // мобильная ширина
    const ctx2 = await browser.newContext({ viewport: { width: 430, height: 900 }, deviceScaleFactor: 2 });
    const p2 = await ctx2.newPage();
    await p2.goto(URL_MOCK, { waitUntil: 'load' });
    await p2.waitForTimeout(600);
    await p2.screenshot({ path: path.join(OUT, `${base}-mobile-430.png`) });
    await ctx2.close();
} finally {
    await browser.close();
}

console.log('Скрины:', fs.readdirSync(OUT).filter(f => f.startsWith(base)).sort().join('\n        '));
console.log(errors.length ? 'ОШИБКИ:\n' + errors.join('\n') : 'Консоль: чисто');