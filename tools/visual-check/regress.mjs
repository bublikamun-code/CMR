// Пиксельная скриншот-регрессия CRM на детерминированной сид-БД.
//
// Зачем: ловит визуальные поломки (сдвиг вёрстки, пропавший экран, сломанная
// тема) там, где юнит-тесты молчат. Каждый прогон видит одну и ту же базу
// (tools/visual-check/seed_db.py) и замороженные часы (Clock API), поэтому
// скриншоты побайтово-стабильны и честно сравниваются pixelmatch'ем.
//
// Кто запускает: scripts/check.sh (шаг 8, визуальный гейт) и руки
// разработчика после правок вёрстки.
//
// Как запускать (из tools/visual-check/, сервер уже поднят на 8799):
//   TZ=Europe/Minsk node regress.mjs                      — сравнение с эталонами
//   TZ=Europe/Minsk node regress.mjs --update-baselines   — запись эталонов
//   npm run regress / npm run regress-update / npm run seed — то же через npm
//
// TZ=Europe/Minsk обязателен (пиновать в скрипте нельзя — это env процесса):
// дашборд/календарь/даты форматируются локалью браузера, чужой пояс сдвинет
// цифры и часы на скринах.
//
// Exit-коды: 0 — все скрины совпали (или эталоны записаны); 1 — есть
// расхождения/ошибки консоли/нет эталона; 2 — неправильное окружение (TZ).
//
// Каталоги: baselines/ — эталоны, коммитятся в git; shots-diff/ — текущий
// прогон и дифф-картинки, вне git (добавляется в корневой .gitignore).
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE || 'http://127.0.0.1:8799';

// Креды сид-БД зашиты: это не секрет, а часть фикстуры seed_db.py
// (visual_admin / VisualPass123!, роль admin). .env рядом — креды боевого
// сервера, для регрессии они не годятся: там живые данные, скрины флейкят.
const SEED_USER = 'visual_admin';
const SEED_PASS = 'VisualPass123!';

// Даты на скринах форматируются локалью — пояс обязан быть минским
// (сид-данные сентябрь 2026, без DST в сентябре).
if (process.env.TZ !== 'Europe/Minsk') {
    console.error('ОШИБКА: TZ должен быть Europe/Minsk (сейчас: ' +
        JSON.stringify(process.env.TZ) + ').');
    console.error('Запуск: TZ=Europe/Minsk node regress.mjs [--update-baselines]');
    process.exit(2);
}

const UPDATE = process.argv.includes('--update-baselines');

const BASELINES_DIR = path.join(__dirname, 'baselines');   // эталоны (в git)
const SHOTS_DIR = path.join(__dirname, 'shots-diff');      // прогон + диффы
fs.mkdirSync(BASELINES_DIR, { recursive: true });
fs.mkdirSync(SHOTS_DIR, { recursive: true });

// Замороженные часы: «сейчас» = 12.09.2026 10:00 по Минску. Ставится в
// КАЖДОМ новом контексте ДО первого goto — иначе на скринах «обновлено:
// HH:MM» (js/control.js:103) и календарь показывали бы реальное время.
const FROZEN_NOW = new Date('2026-09-12T10:00:00+03:00');

const SECTIONS = ['kanban', 'tasks', 'finance', 'clients', 'suppliers', 'dashboard', 'calendar', 'settings'];
const WIDTHS = [1024, 1280, 1440, 1920];

const errors = [];   // console error / pageerror после логина
const failures = []; // расхождения пикселей / размеры / нет эталона
let shotCount = 0;
const shotNames = []; // имена в порядке снятия — порядок сравнения

// Проверка в конце (базовая для обоих режимов): в режиме записи эталонов
// сравнения нет, но экраны всё равно должны сняться без ошибок консоли.
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
    // --- светлый контекст ------------------------------------------------
    // Тема НЕ задаётся: светлая — дефолт приложения. Для тёмной темы будет
    // отдельный контекст (тема применяется boot.js на загрузке страницы,
    // поэтому без перезагрузки/нового контекста она не включится).
    const lightCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const lightPage = await lightCtx.newPage();
    lightPage.setDefaultTimeout(25000);
    lightPage.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    lightPage.on('pageerror', e => errors.push('pageerror: ' + e.message));

    // 1. Экран входа ДО логина — единственный скрин неавторизованного вида.
    await lightPage.clock.install({ time: FROZEN_NOW });
    await lightPage.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
    await lightPage.waitForSelector('#username', { state: 'visible' }).catch(() => {});
    await lightPage.waitForTimeout(800);
    await shot(lightPage, '01-login');

    // Логин как в nakladnye-check.mjs: 401/404 до входа — штатный шум экрана
    // входа, поэтому ошибки консоли считаем только ПОСЛЕ аутентификации.
    await lightPage.fill('#username', SEED_USER);
    await lightPage.fill('#password', SEED_PASS);
    await lightPage.click('#login-submit');
    await lightPage.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
    await lightPage.waitForTimeout(2500);
    errors.length = 0;

    // 2. Светлая серия: для каждой ширины по очереди все 8 разделов.
    // Имена: light-{ширина}-{раздел}. В finance скриним реестр оплат —
    // дефолтный таб payments кликается всегда (без проверки «уже активен»).
    for (const w of WIDTHS) {
        await lightPage.setViewportSize({ width: w, height: 900 });
        await lightPage.waitForTimeout(1200); // ресайз: перерисовка раскладки
        for (const section of SECTIONS) {
            await lightPage.click(`#app-sidebar .nav-btn[data-target="page-${section}"]`, { timeout: 8000 });
            // Данные грузятся асинхронно (fetch + отрисовка таблиц/графиков);
            // дашборду нужно больше — Chart.js рисует после загрузки данных.
            await lightPage.waitForTimeout(section === 'dashboard' ? 4000 : 2800);
            if (section === 'finance') {
                await lightPage.click('button[data-finance-tab="payments"]', { timeout: 8000 });
                await lightPage.waitForTimeout(2800);
            }
            await shot(lightPage, `light-${w}-${section}`);
        }
    }

    // 3. Модалки и таб накладных — в светлом контексте на 1440 (стабильное
    // место в конце серии, состояние страницы чистое: после обхода разделов
    // последний экран — settings).
    await lightPage.setViewportSize({ width: 1440, height: 900 });
    await lightPage.waitForTimeout(1200);

    // Таб накладных отдельным скрином (серия light-{w}-finance снимает
    // реестр оплат; накладные — отдельный экран Финансов).
    await lightPage.click('#app-sidebar .nav-btn[data-target="page-finance"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('button[data-finance-tab="nakladnye"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await shot(lightPage, 'light-1440-nakladnye-tab');

    // Модалка сделки: клик по первой канбан-карточке, закрытие — Escape.
    await lightPage.click('#app-sidebar .nav-btn[data-target="page-kanban"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('.kanban-card', { timeout: 8000 });
    await lightPage.waitForTimeout(1500);
    await shot(lightPage, '1440-modal-deal');
    await lightPage.keyboard.press('Escape');
    await lightPage.waitForTimeout(600);

    // Модалка накладной: Финансы → таб накладных → «Добавить», закрытие —
    // кнопка «Отмена» (inline-обработчик NakladnyeUI).
    await lightPage.click('#app-sidebar .nav-btn[data-target="page-finance"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('button[data-finance-tab="nakladnye"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('#nakladnye-add-btn', { timeout: 8000 });
    await lightPage.waitForTimeout(800);
    await shot(lightPage, '1440-modal-nakladnaya');
    await lightPage.click('#nakladnaya-modal button:has-text("Отмена")', { timeout: 6000 }).catch(() => {});
    await lightPage.waitForTimeout(600);

    await lightCtx.close();

    // --- тёмный контекст --------------------------------------------------
    // addInitScript на контексте сработает на каждый goto в нём; тема
    // применяется на загрузке страницы (boot.js читает crm_theme), поэтому
    // тёмная серия — отдельный контекст со своим логином и проходом.
    const darkCtx = await browser.newContext({
        viewport: { width: 1440, height: 900 },
    });
    await darkCtx.addInitScript(() => {
        try { localStorage.setItem('crm_theme', 'dark'); } catch {}
    });
    const darkPage = await darkCtx.newPage();
    darkPage.setDefaultTimeout(25000);
    darkPage.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    darkPage.on('pageerror', e => errors.push('pageerror: ' + e.message));

    await darkPage.clock.install({ time: FROZEN_NOW });
    await darkPage.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
    if (await darkPage.$('#username')) {
        await darkPage.fill('#username', SEED_USER);
        await darkPage.fill('#password', SEED_PASS);
        await darkPage.click('#login-submit');
    }
    await darkPage.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
    await darkPage.waitForTimeout(2500);
    // Как в светлом контексте: шум до логина не считаем.
    errors.length = 0;

    // Тёмная серия: 8 разделов на 1440. Имена: dark-1440-{раздел}.
    for (const section of SECTIONS) {
        await darkPage.click(`#app-sidebar .nav-btn[data-target="page-${section}"]`, { timeout: 8000 });
        await darkPage.waitForTimeout(section === 'dashboard' ? 4000 : 2800);
        if (section === 'finance') {
            await darkPage.click('button[data-finance-tab="payments"]', { timeout: 8000 });
            await darkPage.waitForTimeout(2800);
        }
        await shot(darkPage, `dark-1440-${section}`);
    }
    await darkCtx.close();

    // --- сравнение ---------------------------------------------------------
    if (!UPDATE) {
        console.log('\nСравнение с эталонами:');
        for (const name of shotNames) {
            const baseFile = path.join(BASELINES_DIR, name + '.png');
            const curFile = path.join(SHOTS_DIR, name + '.png');
            if (!fs.existsSync(baseFile)) {
                failures.push(`${name}: нет эталона (запустите --update-baselines)`);
                continue;
            }
            const basePng = PNG.sync.read(fs.readFileSync(baseFile));
            const curPng = PNG.sync.read(fs.readFileSync(curFile));
            if (basePng.width !== curPng.width || basePng.height !== curPng.height) {
                failures.push(`${name}: размеры ${basePng.width}x${basePng.height} != ${curPng.width}x${curPng.height}`);
                continue;
            }
            const { width, height } = basePng;
            const diff = new PNG({ width, height });
            // diffColor/alpha — опции pixelmatch: он сам раскрашивает diff-буфер
            // (расхождения — красным с прозрачностью 0.3).
            const diffPixels = pixelmatch(basePng.data, curPng.data, diff.data, width, height,
                { threshold: 0.1, diffColor: [255, 0, 0], alpha: 0.3 });
            // Допуск 0.1% пикселей: антиалиасинг шрифтов осциллирует на
            // единицы пикселей, ноль — недостижимый идеал.
            const maxPixels = Math.ceil(width * height * 0.001);
            if (diffPixels > maxPixels) {
                fs.writeFileSync(path.join(SHOTS_DIR, name + '-diff.png'), PNG.sync.write(diff));
                failures.push(`${name}: ${diffPixels}px (${(100 * diffPixels / (width * height)).toFixed(3)}%)`);
            } else {
                console.log(`OK    ${name} (${diffPixels}px)`);
            }
        }
    }
} finally {
    // Браузер обязан гаситься и на исключении: иначе зависший Chrome
    // держит порт и следующий прогон стартует грязным.
    await browser.close().catch(() => {});
}

// --- итог -------------------------------------------------------------------
if (errors.length) {
    console.log('\nОШИБКИ КОНСОЛИ (после логина):');
    errors.forEach(e => console.log('  ' + e));
}
if (failures.length) {
    console.log('\nРАСХОЖДЕНИЯ:');
    failures.forEach(f => console.log('  ' + f));
}
if (failures.length || errors.length) {
    console.log(`\nИТОГ: FAIL (${failures.length} расхождений, ${errors.length} ошибок консоли)`);
    process.exit(1);
}
if (UPDATE) {
    console.log(`\nИТОГ: OK — ${shotCount} эталонов записаны в ${path.relative(__dirname, BASELINES_DIR)}/`);
} else {
    console.log(`\nИТОГ: OK — ${shotCount} скринов совпали`);
}
process.exit(0);

// --- helpers ----------------------------------------------------------------
// Скриншот: PNG (нужен для pixelmatch — он ест сырые RGBA), один кадр без
// прокрутки (fullPage: false — фиксированный размер = размеры совпадают),
// без анимаций и без мигающего каретка-курсора в полях.
async function shot(page, name) {
    await page.screenshot({
        path: path.join(UPDATE ? BASELINES_DIR : SHOTS_DIR, name + '.png'),
        type: 'png',
        fullPage: false,
        animations: 'disabled',
        caret: 'hide',
    });
    shotNames.push(name);
    shotCount++;
    console.log(`shot: ${name}`);
}
