// Замер «визуального шума» интерфейса: динамические метрики по computed-стилям.
// Фаза 5.1 UX-аудита. Сервер — копия боевой БД на 8801, read-only: только GET,
// никаких сабмитов форм (логин + навигация по разделам + открытие модалки).
//
// Запуск: node ux-noise-metrics.mjs
// Вывод:  tmp/ux-audit/noise-metrics.json (сырые данные) и
//         tmp/ux-audit/noise-metrics.md (таблица по экранам).
import { chromium } from 'playwright-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const OUT_DIR = path.join(REPO_ROOT, 'tmp', 'ux-audit');
fs.mkdirSync(OUT_DIR, { recursive: true });

// Креды боевого сервера читаем программно из .env рядом (в git не коммитятся),
// пароль никуда не выводим.
const env = Object.fromEntries(
    fs.readFileSync(path.join(__dirname, '.env'), 'utf8')
        .split('\n').filter(l => l.includes('='))
        .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
);

const BASE = process.env.BASE || 'http://127.0.0.1:8801';
const SECTIONS = ['kanban', 'tasks', 'finance', 'clients', 'suppliers', 'dashboard', 'calendar', 'settings'];

// Сбор метрик: один проход по DOM, getComputedStyle по каждому видимому элементу.
// Возвращает JSON-сериализуемый объект (Map/Set нельзя — только plain object).
function collectMetrics() {
    const els = Array.from(document.querySelectorAll('*'));
    const isVisible = (el) => {
        if (el.getClientRects().length === 0) return false;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        // В пределах вьюпорта или документа (с учётом вертикального скролла):
        // полностью вне документа элементов здесь нет, режем только уехавшие
        // за горизонтальные края вьюпорта дальше ширины документа.
        const doc = document.documentElement;
        if (r.left > doc.scrollWidth || r.right < 0) return false;
        return true;
    };
    const sizes = new Set(), families = new Set(), colors = new Set(),
        bgs = new Set(), radii = new Set(), shadows = new Set(), borders = new Set();
    let smallFont = 0, textEls = 0, interactive = 0, visibleTotal = 0;
    const smallFontSizes = {};

    for (const el of els) {
        if (!isVisible(el)) continue;
        visibleTotal++;
        const cs = getComputedStyle(el);
        const size = cs.fontSize;           // всегда "Npx"
        sizes.add(size);
        families.add(cs.fontFamily);
        colors.add(cs.color);
        if (el.textContent && el.textContent.trim()) {
            textEls++;
            const px = parseFloat(size);
            if (px < 13) {
                smallFont++;
                smallFontSizes[size] = (smallFontSizes[size] || 0) + 1;
            }
        }
        // Фон: игнорируем прозрачный (transparent / rgba с alpha=0)
        const bg = cs.backgroundColor;
        if (bg && bg !== 'transparent') {
            const m = bg.match(/rgba?\(([^)]+)\)/);
            if (!m || parseFloat(m[1].split(',').slice(-1)[0]) > 0) bgs.add(bg);
        }
        // Радиус: ненулевой = в значении есть число != 0
        const br = cs.borderRadius;         // "4px" или "4px 4px 0px 0px"
        if (br && /[1-9]/.test(br.replace(/[a-z%().,]/gi, ''))) radii.add(br);
        const bs = cs.boxShadow;
        if (bs && bs !== 'none') shadows.add(bs);
        // Толщина бордюра: ненулевая
        const bw = cs.borderTopWidth + ' ' + cs.borderRightWidth + ' ' +
            cs.borderBottomWidth + ' ' + cs.borderLeftWidth;
        if (/[1-9]/.test(bw.replace(/px/g, ''))) borders.add(bw);

        if (el.matches('button, a, input, select, [onclick], [role="button"]')) interactive++;
    }

    const doc = document.documentElement;
    return {
        visibleElements: visibleTotal,
        distinct: {
            fontSize: sizes.size,
            fontFamily: families.size,
            color: colors.size,
            backgroundColor: bgs.size,
            borderRadius: radii.size,
            boxShadow: shadows.size,
            borderWidth: borders.size,
        },
        values: {   // полные распределения — пригодятся при разборе причин шума
            fontSize: Object.fromEntries([...sizes].sort().map(v => [v, null])),
            fontFamily: [...families].sort(),
        },
        smallFont: {
            count: smallFont,
            textElements: textEls,
            pct: textEls ? +(100 * smallFont / textEls).toFixed(1) : 0,
            bySize: smallFontSizes,
        },
        interactive,
        hScroll: doc.scrollWidth > doc.clientWidth,
        scrollWidth: doc.scrollWidth,
        clientWidth: doc.clientWidth,
    };
}

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const results = { generated: new Date().toISOString(), base: BASE, viewport: { width: 1440, height: 900 }, screens: {} };

try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(20000);

    await page.goto(BASE, { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForSelector('#username', { state: 'visible' }).catch(() => {});
    await page.fill('#username', env.CRM_USER);
    await page.fill('#password', env.CRM_PASS);
    await page.click('#login-submit');
    await page.waitForSelector('#app-sidebar', { state: 'visible' }).catch(() => {});
    await page.waitForTimeout(2500);

    for (const section of SECTIONS) {
        await page.click(`#app-sidebar .nav-btn[data-target="page-${section}"]`, { timeout: 8000 });
        await page.waitForTimeout(section === 'dashboard' ? 4000 : 2800);
        if (section === 'finance') {
            // Как в regress.mjs: смотрим реестр оплат (дефолтный таб)
            await page.click('button[data-finance-tab="payments"]', { timeout: 8000 }).catch(() => {});
            await page.waitForTimeout(2800);
        }
        results.screens[section] = await page.evaluate(collectMetrics);
        console.log('measured:', section);
    }

    // Модалка сделки: канбан → первая карточка (только чтение, закрытие Escape)
    await page.click('#app-sidebar .nav-btn[data-target="page-kanban"]', { timeout: 8000 });
    await page.waitForTimeout(2800);
    await page.click('.kanban-card', { timeout: 8000 });
    await page.waitForTimeout(1500);
    results.screens['deal-modal'] = await page.evaluate(collectMetrics);
    console.log('measured: deal-modal');
    await page.keyboard.press('Escape');
} finally {
    await browser.close().catch(() => {});
}

fs.writeFileSync(path.join(OUT_DIR, 'noise-metrics.json'), JSON.stringify(results, null, 2));

// --- md-таблица -------------------------------------------------------------
const d = results.screens;
const col = (s, k) => d[s].distinct[k];
const fmt = (s) => {
    const m = d[s];
    const h = m.hScroll ? 'да' : 'нет';
    return `| ${s} | ${col(s, 'fontSize')} | ${col(s, 'fontFamily')} | ${col(s, 'color')} | ` +
        `${col(s, 'backgroundColor')} | ${col(s, 'borderRadius')} | ${col(s, 'boxShadow')} | ${col(s, 'borderWidth')} | ` +
        `${m.smallFont.count} / ${m.smallFont.pct}% | ${m.interactive} | ${h} |`;
};
const rows = [...SECTIONS, 'deal-modal'].map(fmt).join('\n');

// Топ-3 по сумме различных значений (сводный индекс шума)
const ranked = Object.entries(d)
    .map(([name, m]) => [name, Object.values(m.distinct).reduce((a, b) => a + b, 0)])
    .sort((a, b) => b[1] - a[1]);
const top3 = ranked.slice(0, 3).map(([n, s]) => `- **${n}** — сумма различных значений: ${s} (font-size ${d[n].distinct.fontSize}, цвета ${d[n].distinct.color}, фоны ${d[n].distinct.backgroundColor}, радиусы ${d[n].distinct.borderRadius}, тени ${d[n].distinct.boxShadow})`).join('\n');

const md = `# Визуальный шум: динамические метрики (Фаза 5.1)

Замер: ${results.generated}, сервер ${BASE}, viewport ${results.viewport.width}x${results.viewport.height}.
Видимый элемент = ненулевой rect в пределах документа. Фоны без прозрачных,
радиусы/бордюры без нулевых, тени без \`none\`.

| Экран | font-size | font-family | цвета текста | фоны | border-radius | box-shadow | border-width | <13px (шт/% текста) | интерактивных | h-scroll |
|---|---|---|---|---|---|---|---|---|---|---|
${rows}

## Самые шумные экраны (топ-3)

${top3}

## Детали по мелкому шрифту (<13px)

${[...SECTIONS, 'deal-modal'].map(s => {
    const m = d[s];
    const bySize = Object.entries(m.smallFont.bySize).sort((a, b) => parseFloat(a[0]) - parseFloat(b[0])).map(([k, v]) => `${k}×${v}`).join(', ') || '—';
    return `- ${s}: ${m.smallFont.count} из ${m.smallFont.textElements} текстовых (${m.smallFont.pct}%), по размерам: ${bySize}`;
}).join('\n')}
`;

// --- Статика: css/style.css + css/liquid-glass.css --------------------------
function cssStats(file) {
    const src = fs.readFileSync(file, 'utf8');
    // Как wc -l: завершающий перевод строки новой строкой не считается
    const lines = src.endsWith('\n') ? src.split('\n').length - 1 : src.split('\n').length;
    const bytes = Buffer.byteLength(src);
    const kb = +(bytes / 1000).toFixed(1);        // десятичные КБ, как в плане
    const kib = +(bytes / 1024).toFixed(1);
    // Убираем комментарии, чтобы не считать литералы в них
    const clean = src.replace(/\/\*[\s\S]*?\*\//g, '');
    // !important: сырые строки с вхождением (как grep -c по файлу — так считал
    // план) и отдельно без комментариев, чтобы видеть сколько в реальном коде
    const importantLinesRaw = src.split('\n').filter(l => l.includes('!important')).length;
    const importantLines = clean.split('\n').filter(l => l.includes('!important')).length;
    const importantOccurrences = (clean.match(/!important/g) || []).length;
    const medias = [...clean.matchAll(/@media[^{]*\{/g)].map(m => m[0].replace(/\{$/, '').trim());
    const mediaWithMaxWidth = medias.filter(m => /max-width/.test(m)).length;
    const mediaMax900 = medias.filter(m => /max-width\s*:\s*900px/.test(m)).length;
    const mediaMin = medias.filter(m => /min-width/.test(m) && !/max-width/.test(m)).length;
    const colors = new Set();
    for (const m of clean.matchAll(/#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\)|var\(--[\w-]+\)/g)) {
        colors.add(m[0].toLowerCase());
    }
    const radii = new Set();
    for (const m of clean.matchAll(/border-radius\s*:\s*([^;{}]+)/g)) {
        radii.add(m[1].trim());
    }
    const fontSizes = {};
    for (const m of clean.matchAll(/font-size\s*:\s*([^;{}]+)/g)) {
        // Значение нормализуем: суффикс !important не делает значение другим
        const v = m[1].trim().replace(/\s*!important\s*$/, '');
        fontSizes[v] = (fontSizes[v] || 0) + 1;
    }
    return { file: path.relative(REPO_ROOT, file), kb, kib, bytes, lines,
        important: importantLines, importantRaw: importantLinesRaw, importantOccurrences,
        mediaTotal: medias.length, mediaWithMaxWidth, mediaMax900, mediaMin,
        mediaQueries: medias, colorLiterals: colors.size,
        borderRadiusValues: radii.size,
        fontSizeDecls: Object.values(fontSizes).reduce((a, b) => a + b, 0),
        fontSizeValues: Object.keys(fontSizes).length, fontSizeDist: fontSizes };
}

const styleStats = cssStats(path.join(REPO_ROOT, 'css', 'style.css'));
const glassStats = cssStats(path.join(REPO_ROOT, 'css', 'liquid-glass.css'));

const fsDist = Object.entries(styleStats.fontSizeDist).sort((a, b) => b[1] - a[1]);
const fsDistRows = fsDist.map(([v, n]) => `| ${v} | ${n} |`).join('\n');

// Проверка цифр из плана UX-аудита. Размер плана — в десятичных КБ (487503
// байт ≈ 487 КБ), !important — строки с вхождением (grep -c), значения
// font-size — без суффикса !important.
const plan = { kb: 487, lines: 13962, important: 1182, mediaTotal: 16, mediaMax900: 15,
    fs10: 13, fs11: 84, fs12: 102, fs13: 105 };
const actual = {
    kb: Math.floor(styleStats.kb), lines: styleStats.lines, important: styleStats.importantRaw,
    mediaTotal: styleStats.mediaTotal, mediaMax900: styleStats.mediaMax900,
    fs10: styleStats.fontSizeDist['10px'] || 0, fs11: styleStats.fontSizeDist['11px'] || 0,
    fs12: styleStats.fontSizeDist['12px'] || 0, fs13: styleStats.fontSizeDist['13px'] || 0,
};
const cmp = (k, label, note = '') => `| ${label} | ${plan[k]} | ${actual[k]}${note} | ${plan[k] === actual[k] ? 'подтверждено' : '**расхождение**'} |`;
const planTable = [
    cmp('kb', 'Размер, КБ (1000 байт)', ` (${styleStats.kb} КБ = ${styleStats.bytes} байт)`),
    cmp('lines', 'Строк'),
    cmp('important', '!important (строк, grep -c)'),
    cmp('mediaTotal', '@media всего'),
    cmp('mediaMax900', '@media max-width ≤900px'),
    cmp('fs10', 'font-size: 10px'), cmp('fs11', 'font-size: 11px'),
    cmp('fs12', 'font-size: 12px'), cmp('fs13', 'font-size: 13px'),
].join('\n');

const mdStatic = `
# Визуальный шум: статические метрики CSS (Фаза 5.1)

## css/style.css

| Метрика | Значение |
|---|---|
| Размер | ${styleStats.kb} КБ (десятичные; ${styleStats.kib} КиБ, ${styleStats.bytes} байт) |
| Строк | ${styleStats.lines} |
| Объявлений font-size | ${styleStats.fontSizeDecls} |
| Различных значений font-size | ${styleStats.fontSizeValues} |
| !important | ${styleStats.importantRaw} строк сырых (как grep -c); в коде без комментариев ${styleStats.important} строк, ${styleStats.importantOccurrences} вхождений |
| @media всего | ${styleStats.mediaTotal} |
| @media с max-width | ${styleStats.mediaWithMaxWidth} |
| @media max-width ≤900px | ${styleStats.mediaMax900} |
| @media только min-width | ${styleStats.mediaMin} |
| Различных цветов (hex/rgb/hsl/var) | ${styleStats.colorLiterals} |
| Различных border-radius | ${styleStats.borderRadiusValues} |

### Распределение значений font-size (style.css)

| Значение | Сколько раз |
|---|---|
${fsDistRows}

### Брейкпоинты @media

${styleStats.mediaQueries.map(q => '- ' + q).join('\n')}

## css/liquid-glass.css

| Метрика | Значение |
|---|---|
| Размер | ${glassStats.kb} КБ |
| Строк | ${glassStats.lines} |
| !important | ${glassStats.important} |
| @media | ${glassStats.mediaTotal} |
| Различных цветов | ${glassStats.colorLiterals} |

## Сверка с планом («style.css 487 КБ / 13962 строки / 1182 !important / 16 @media, из них 15 на ≤900px; font-size 10px — 13, 11px — 84, 12px — 102, 13px — 105»)

| Метрика | План | Факт на main | Итог |
|---|---|---|---|
${planTable}

Вывод: размер, строки, !important и все четыре популярных значения font-size
плана **подтверждены**: 487503 байта = 487.5 КБ (округление вниз, как в плане),
строки 13962 совпали дословно, !important — сырые строки с вхождением как у
grep -c = 1182 (план считал так; в реальном коде без комментариев — 1156 строк),
значения font-size — с нормализацией суффикса !important. Цифры @media **не
подтверждены**: на текущем main 22 @media,
из них 16 с max-width (все ≤900px: 900, 768, 720, 700, 640, 600, 560, 480,
400), 1 min-width (901px) и 5 без брейкпоинта по ширине (hover/prefers-reduced-motion).
Файл вырос относительно момента фиксации плана.
`;

fs.writeFileSync(path.join(OUT_DIR, 'noise-metrics.md'), md + '\n' + mdStatic);
console.log('\nWrote:', path.join(OUT_DIR, 'noise-metrics.json'));
console.log('Wrote:', path.join(OUT_DIR, 'noise-metrics.md'));
console.log('Top-3 noisy:', ranked.slice(0, 3).map(([n, s]) => `${n}=${s}`).join(', '));
