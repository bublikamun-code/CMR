// Доказательная скриншот-матрица UX-аудита (фаза 5.1).
//
// Зачем: снять все экраны CRM на копии боевой БД (127.0.0.1:8801) в светлой
// и тёмной темах, плюс модалки, таб накладных и админку — с фиксацией
// ошибок консоли после логина и метрик «сломанности» (горизонтальный
// скролл, однотонный/пустой экран) по каждому скрину.
//
// Как запускать: node audit-shots.mjs (сервер уже поднят на 8801).
// Креды читаются из .env рядом (как walk.mjs); пароль никогда не печатается.
//
// Дисциплина read-only: модалки только открываются и закрываются (Escape /
// «Отмена»), формы с изменением данных не сабмитятся.
//
// Скрины: tmp/ux-audit/shots/ (вне git). Имена:
//   {ширина}-{раздел}.png — 8 разделов × [1280, 1440, 1600, 1920], светлая
//   dark-{раздел}.png     — 8 разделов на 1440, тёмная
//   finance-nakladnye-tab.png, modal-deal.png, modal-nakladnaya.png, admin.png
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PNG } from 'pngjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE || 'http://127.0.0.1:8801';
const OUT = path.join(__dirname, '..', '..', 'tmp', 'ux-audit', 'shots');
fs.mkdirSync(OUT, { recursive: true });

// Креды из .env рядом (в репозиторий не коммитятся). Пароль уходит только
// в page.fill — в stdout/логи он не попадает ни в одной ветке.
const env = Object.fromEntries(
    fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
        .split('\n').filter(l => l.includes('='))
        .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
);

const SECTIONS = ['kanban', 'tasks', 'finance', 'clients', 'suppliers', 'dashboard', 'calendar', 'settings'];
const WIDTHS = [1280, 1440, 1600, 1920];

const errors = [];   // console error / pageerror после логина
const report = [];   // метрики по каждому скрину: hscroll / uniform
const shotNames = [];

// Логин главного приложения: поля #username/#password (index.html).
async function loginMain(page) {
    await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForSelector('#username', { state: 'visible' }).catch(() => {});
    await page.fill('#username', env.CRM_USER);
    await page.fill('#password', env.CRM_PASS);
    await page.click('#login-submit');
    await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
    await page.waitForTimeout(2500);
    // Шум до аутентификации (401/404 экрана входа) не считаем — как в regress.mjs
    errors.length = 0;
}

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
    // --- светлая серия: 8 разделов × 4 ширины --------------------------------
    const lightCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const lightPage = await lightCtx.newPage();
    lightPage.setDefaultTimeout(25000);
    lightPage.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    lightPage.on('pageerror', e => errors.push('pageerror: ' + e.message));

    await loginMain(lightPage);

    for (const w of WIDTHS) {
        await lightPage.setViewportSize({ width: w, height: 900 });
        await lightPage.waitForTimeout(1200); // ресайз: перерисовка раскладки
        for (const section of SECTIONS) {
            await lightPage.click(`#app-sidebar .nav-btn[data-target="page-${section}"]`, { timeout: 8000 });
            await lightPage.waitForTimeout(section === 'dashboard' ? 4000 : 2800);
            await shot(lightPage, `${w}-${section}`);
        }
    }

    // --- таб накладных в финансах (светлая, 1440) ----------------------------
    await lightPage.setViewportSize({ width: 1440, height: 900 });
    await lightPage.waitForTimeout(1200);
    await lightPage.click('#app-sidebar .nav-btn[data-target="page-finance"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('button[data-finance-tab="nakladnye"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await shot(lightPage, 'finance-nakladnye-tab');

    // --- модалка сделки: первая канбан-карточка, закрытие Escape -------------
    await lightPage.click('#app-sidebar .nav-btn[data-target="page-kanban"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('.kanban-card', { timeout: 8000 });
    await lightPage.waitForTimeout(1500);
    await shot(lightPage, 'modal-deal');
    await lightPage.keyboard.press('Escape');
    await lightPage.waitForTimeout(600);

    // --- модалка накладной: finance → nakladnye → «Добавить» -----------------
    await lightPage.click('#app-sidebar .nav-btn[data-target="page-finance"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('button[data-finance-tab="nakladnye"]', { timeout: 8000 });
    await lightPage.waitForTimeout(2800);
    await lightPage.click('#nakladnye-add-btn', { timeout: 8000 });
    await lightPage.waitForTimeout(800);
    await shot(lightPage, 'modal-nakladnaya');
    // Закрытие без сабмита: кнопка «Отмена» (inline-обработчик NakladnyeUI)
    await lightPage.click('#nakladnaya-modal button:has-text("Отмена")', { timeout: 6000 }).catch(() => {});
    await lightPage.waitForTimeout(600);

    await lightCtx.close();

    // --- тёмная серия: 8 разделов на 1440 ------------------------------------
    // Тема применяется boot.js на загрузке страницы — задаём её через
    // addInitScript ДО первого goto в отдельном контексте (как regress.mjs).
    const darkCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await darkCtx.addInitScript(() => {
        try { localStorage.setItem('crm_theme', 'dark'); } catch {}
    });
    const darkPage = await darkCtx.newPage();
    darkPage.setDefaultTimeout(25000);
    darkPage.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    darkPage.on('pageerror', e => errors.push('pageerror: ' + e.message));

    await loginMain(darkPage);

    for (const section of SECTIONS) {
        await darkPage.click(`#app-sidebar .nav-btn[data-target="page-${section}"]`, { timeout: 8000 });
        await darkPage.waitForTimeout(section === 'dashboard' ? 4000 : 2800);
        await shot(darkPage, `dark-${section}`);
    }
    await darkCtx.close();

    // --- админка admin.html (отдельная форма входа) --------------------------
    // Роут /admin отдаёт FileResponse("admin.html"); /admin.html — запасной
    // путь (статика может быть отдана иначе). Форма админки — своя:
    // #login-username / #login-password (сабмит-кнопка тоже #login-submit).
    const adminPage = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    adminPage.setDefaultTimeout(25000);
    adminPage.on('console', m => { if (m.type() === 'error') errors.push('admin console: ' + m.text()); });
    adminPage.on('pageerror', e => errors.push('admin pageerror: ' + e.message));

    await adminPage.goto(BASE + '/admin', { waitUntil: 'networkidle' }).catch(() => {});
    if (!(await adminPage.$('#login-form'))) {
        await adminPage.goto(BASE + '/admin.html', { waitUntil: 'networkidle' }).catch(() => {});
    }
    if (await adminPage.$('#login-username')) {
        await adminPage.fill('#login-username', env.CRM_USER);
        await adminPage.fill('#login-password', env.CRM_PASS);
        await adminPage.click('#login-submit');
        await adminPage.waitForTimeout(2500);
    }
    const adminOk = await adminPage
        .$eval('#admin-app', el => !el.classList.contains('hidden'))
        .catch(() => false);
    console.log('admin login:', adminOk ? 'OK' : 'НЕ УДАЛСЯ — снимаю доступный вид');
    await shot(adminPage, 'admin');
    await adminPage.close();
} finally {
    // Браузер обязан гаситься и на исключении (зависший Chrome держит профиль)
    await browser.close().catch(() => {});
}

// --- итог -------------------------------------------------------------------
console.log(`\nИТОГО СКРИНОВ: ${shotNames.length} → ${path.relative(__dirname, OUT)}/`);

if (errors.length) {
    console.log('\nОШИБКИ КОНСОЛИ (после логина):');
    [...new Set(errors)].forEach(e => console.log('  ' + e));
} else {
    console.log('\nОшибок консоли после логина: нет');
}

const hscroll = report.filter(r => r.hscroll);
const uniform = report.filter(r => r.uniform > 0.995); // >99.5% одного цвета — пустой экран
console.log('\nМЕТРИКИ (гориз. скролл / однотонность):');
report.forEach(r => console.log(
    `  ${r.name}: hscroll=${r.hscroll ? '+' + r.hscrollPx + 'px' : 'нет'} uniform=${(100 * r.uniform).toFixed(2)}%`
));
if (hscroll.length) console.log('\nГОРИЗОНТАЛЬНЫЙ СКРОЛЛ: ' + hscroll.map(r => r.name).join(', '));
if (uniform.length) console.log('ПОДОЗРЕНИЕ НА ПУСТОЙ ЭКРАН: ' + uniform.map(r => r.name).join(', '));

// Отчёт рядом со скринами — приложение к аудиту
fs.writeFileSync(
    path.join(OUT, 'report.json'),
    JSON.stringify({ base: BASE, shots: report, errors: [...new Set(errors)] }, null, 2)
);

// --- helpers ----------------------------------------------------------------
// Скриншот: PNG, один кадр без прокрутки, без анимаций и каретки (как
// regress.mjs). После съёмки считаем метрики: горизонтальный скролл
// (scrollWidth > innerWidth) и долю самого частого цвета (однотонность —
// признак пустого/сломанного экрана; скрины делаются 'animations: disabled',
// так что спиннеры в кадре уже застывшие и не имитируют контент).
async function shot(page, name) {
    const file = path.join(OUT, name + '.png');
    await page.screenshot({
        path: file,
        type: 'png',
        fullPage: false,
        animations: 'disabled',
        caret: 'hide',
    });
    shotNames.push(name);

    const scroll = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
    })).catch(() => null);

    let uniform = 0;
    try {
        const png = PNG.sync.read(fs.readFileSync(file));
        const freq = new Map();
        let n = 0;
        // Шаг 40 байт = каждый 10-й пиксель (RGBA) — быстрый сэмпл
        for (let i = 0; i < png.data.length; i += 40) {
            const key = ((png.data[i] << 16) | (png.data[i + 1] << 8) | png.data[i + 2]).toString(16);
            freq.set(key, (freq.get(key) || 0) + 1);
            n++;
        }
        uniform = Math.max(...freq.values()) / n;
    } catch {}

    report.push({
        name,
        hscroll: scroll ? scroll.sw > scroll.iw + 1 : null,
        hscrollPx: scroll ? scroll.sw - scroll.iw : null,
        uniform: +uniform.toFixed(4),
    });
    console.log(`shot: ${name}`);
}
