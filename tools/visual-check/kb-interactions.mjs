import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import { selectKBOption, kbSelectTrigger } from './kb-select-helper.mjs';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
 const p = await browser.newPage({ viewport: { width: 1440, height: 900 } });
 const errors = []; p.on('pageerror', e => { console.error(e.stack || e.message); errors.push(e.message); });
 await p.goto(new URL('../mockups/shell-v2-prototype.html', import.meta.url).href);
 await p.click('[data-view="board"]');
 const ids = stage => p.locator(`[data-stage="${stage}"] .kb-card`).evaluateAll(es => es.map(e => e.dataset.card));
 const original = await ids('new');
 // Native mouse drag into the upper half of the second card in another column.
 const source = p.locator(`[data-card="${original[0]}"]`);
 const target = p.locator('[data-stage="work"] .kb-card').nth(1);
 const targetId = await target.getAttribute('data-card');
 const start = await source.boundingBox(), end = await target.boundingBox();
 await p.mouse.move(start.x + 30, start.y + 20); await p.mouse.down();
 await p.mouse.move(start.x + 40, start.y + 30, { steps: 5 });
 await p.mouse.move(end.x + 40, end.y + 15, { steps: 15 });
 await p.waitForTimeout(150);
 assert.equal(await p.locator('.kb-drop-placeholder').count(), 1);
 await p.screenshot({ path: '/tmp/kb-drag-slot.png' });
 await p.mouse.up();
 const moved = await ids('work');
 assert.equal(moved.indexOf(original[0]) + 1, moved.indexOf(targetId));
 assert.equal(await p.locator('.kb-drop-placeholder').count(), 0);
 // Same-column reordering.
 await p.locator(`[data-card="${original[0]}"]`).dragTo(p.locator('[data-stage="work"] .kb-card').first(), { targetPosition: { x: 30, y: 10 } });
 assert.equal((await ids('work'))[0], original[0]);
 await p.locator(`[data-card="${original[0]}"]`).click();
 await p.waitForTimeout(350);
 await selectKBOption(p, '#kb-deal-paymentTerms', 'deferred');
 await p.click('#kb-tab-procurement');
 for (let i = 0; i < await p.locator('[data-flag="received"]').count(); i++) {
  const ordered = p.locator(`[data-check="${i}"][data-flag="ordered"]`);
  if (!await ordered.isChecked()) await ordered.check();
  await p.locator(`[data-check="${i}"][data-flag="received"]`).check();
 }
 await p.keyboard.press('Escape');
 const face = p.locator(`[data-card="${original[0]}"]`);
 assert.equal(await face.locator('.kb-counter').count(), 2);
 assert.equal(await face.locator('[data-counter="ordered"]').innerText(), await face.locator('[data-counter="received"]').innerText());
 await face.click();
 assert.equal(await p.inputValue('#kb-deal-paymentTerms'), 'deferred');
 assert.deepEqual(await p.locator('#kb-deal-paymentTerms option').evaluateAll(es => es.map(e => [e.value, e.textContent])), [
  ['', 'Не выбраны'], ['deferred', 'Отсрочка'], ['full', 'Оплата 100%'], ['partial_deferred', 'Частичная оплата + отсрочка платежа']
 ]);
 for (const terms of ['', 'full', 'partial_deferred']) {
  await selectKBOption(p, '#kb-deal-paymentTerms', terms);
  assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
  await p.keyboard.press('Escape'); await face.click();
  assert.equal(await p.inputValue('#kb-deal-paymentTerms'), terms);
 }
 assert.equal(await p.inputValue('#kb-deal-paymentTerms'), 'partial_deferred');
 await p.click('#kb-tab-overview');
 assert.equal(await p.getAttribute('#kb-hide-filled', 'aria-pressed'), 'false');
 assert.equal(await p.locator('#kb-overview-fields .kb-edit-field:visible').count(), 7);
 // Numeric option zero is filled, not an empty manager.
 await selectKBOption(p, '#kb-deal-manager', '0');
 await p.click('#kb-hide-filled');
 assert.equal(await p.locator('#kb-overview-fields .kb-edit-field:visible').count(), 0);
 assert(await p.isVisible('#kb-overview-empty'));
 assert(await kbSelectTrigger(p, '#kb-deal-paymentTerms').isVisible()); // Header is never filtered.
 await p.click('#kb-hide-filled');
 await p.fill('#kb-deal-note', ''); await p.locator('#kb-deal-note').press('Tab');
 await p.click('#kb-hide-filled');
 assert(await p.isVisible('#kb-deal-note'));
 assert(!await p.isVisible('#kb-overview-empty'));
 await p.fill('#kb-deal-note', 'Заполнено без исчезновения');
 assert(await p.isVisible('#kb-deal-note'));
 await p.locator('#kb-deal-note').press('Tab');
 assert(await p.isVisible('#kb-deal-note'));
 await p.keyboard.press('Escape'); await face.click();
 assert.equal(await p.getAttribute('#kb-hide-filled', 'aria-pressed'), 'true');
 assert(await p.isVisible('#kb-overview-empty'));
 await p.click('#kb-hide-filled');
 assert.equal(await p.inputValue('#kb-deal-note'), 'Заполнено без исчезновения');
 // Invalid nonempty amount stays visible when filtering; correcting it must not hide it.
 await p.fill('#kb-deal-amount', '0'); await p.locator('#kb-deal-amount').dispatchEvent('change');
 assert.equal(await p.getAttribute('#kb-deal-amount', 'aria-invalid'), 'true');
 await p.click('#kb-hide-filled');
 assert(await p.isVisible('#kb-deal-amount'));
 await p.fill('#kb-deal-amount', '9999'); await p.locator('#kb-deal-amount').dispatchEvent('change');
 assert(await p.isVisible('#kb-deal-amount'));
 assert(await p.locator('#kb-deal-amount').evaluate(e => document.activeElement === e));
 await p.locator('#kb-deal-amount').press('Tab');
 assert(await p.isVisible('#kb-deal-amount'));
 await p.click('#kb-hide-filled');
 assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
 await selectKBOption(p, '#kb-deal-stage', 'writeoff');
 await p.click('#kb-issue');
 await p.fill('#kb-date', '2026-09-16'); await p.fill('#kb-series', 'ТН'); await p.fill('#kb-number', 'E2E-1');
 await p.click('#kb-issue-form [type="submit"]');
 await p.click('[data-view="fin"]'); await p.click('[data-fin-tab="documents"]');
 const row = p.locator('#fin-document-rows tr[data-kanban]');
 assert.equal(await row.count(), 1); assert.match(await row.innerText(), /E2E-1/);
 await row.locator('input').check();
 await p.click('[data-view="board"]'); await p.click('#kb-q-history');
 await p.locator(`#kb-queue-body [data-card="${original[0]}"]`).click(); await p.click('#kb-tab-invoices');
 assert(await p.isChecked('[data-originals="0"]'));
 assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
 await p.uncheck('[data-originals="0"]');
 await p.keyboard.press('Escape');
 await p.click('[data-view="fin"]'); assert(!await row.locator('input').isChecked());
 console.log('PASS: mouse drop exact order, same-column reorder, counters, payment persistence, issuance → finance, independent originals both directions');
 await p.reload(); await p.click('[data-view="board"]');
 for (const width of [375, 430, 1280, 1440, 1600, 1920]) {
  await p.setViewportSize({ width, height: 900 });
  for (const theme of ['light', 'dark']) {
   await p.evaluate(t => document.documentElement.dataset.theme = t, theme);
   await p.click('#kb-q-board');
   const layout = await p.evaluate(() => ({ page: document.documentElement.scrollHeight, bottom: document.querySelector('#kb-board').getBoundingClientRect().bottom }));
   assert.equal(layout.page, 900); assert(Math.abs(layout.bottom - 888) < 2);
   await p.click('#kb-search-toggle'); await p.waitForTimeout(200);
   assert(!await p.locator('#kb-search-toggle').isVisible());
   await p.click('#kb-search-close');
   await p.locator('.kb-card').first().click(); await p.waitForTimeout(350);
   for (const selector of ['#kb-deal-paymentTerms-trigger', '.kb-dialog-foot']) {
    const r = await p.locator(selector).boundingBox(); assert(r.y >= 0 && r.y + r.height <= 900, `${width} ${theme} ${selector}`);
   }
   assert(await p.locator('#kb-dialog').evaluate(e => e.scrollWidth <= e.clientWidth));
   assert.equal(await p.getAttribute('#kb-hide-filled', 'aria-pressed'), 'false');
   for (const selector of ['#kb-deal-title', '#kb-deal-paymentTerms', '#kb-deal-note']) {
    const control = selector === '#kb-deal-paymentTerms' ? kbSelectTrigger(p, selector) : p.locator(selector);
    await control.hover(); await p.waitForTimeout(200);
    const neutralState = () => control.evaluate(e => {
     const probe = document.createElement('span'); probe.style.color = 'var(--muted)'; e.parentNode.append(probe);
     const muted = getComputedStyle(probe).color; probe.remove();
     const s = getComputedStyle(e);
     const surface = e.closest('.kb-select-surface') || e;
      return { border: getComputedStyle(surface).borderTopColor, outline: s.outlineColor, outlineStyle: s.outlineStyle,
      outlineWidth: s.outlineWidth, shadow: s.boxShadow, muted, focused: document.activeElement === e };
    });
    let style = await neutralState();
    assert.equal(style.border, style.muted, `${width} ${theme} ${selector} hover`);
    assert.equal(style.shadow, 'none');
    await control.focus(); await p.keyboard.press('Tab'); await p.keyboard.press('Shift+Tab');
    await p.waitForTimeout(200);
    style = await neutralState();
    assert(style.focused, `${width} ${theme} ${selector} keyboard focus`);
    assert.equal(style.border, style.muted); assert.equal(style.outline, style.muted);
    assert.equal(style.outlineStyle, 'solid'); assert.equal(style.outlineWidth, '2px');
    assert.equal(style.shadow, 'none');
    const selection = await control.evaluate(e => {
     const probe = document.createElement('span'); probe.style.backgroundColor = 'var(--border-strong)'; e.parentNode.append(probe);
     const expected = getComputedStyle(probe).backgroundColor; probe.remove();
     return { actual: getComputedStyle(e, '::selection').backgroundColor, expected };
    });
    assert.equal(selection.actual, selection.expected, `${width} ${theme} neutral text selection`);
   }
   if (width === 1440 || width === 375) await p.screenshot({ path: `/tmp/kb-card-${width}-${theme}.png` });
   await p.keyboard.press('Escape'); await p.click('#kb-q-pending');
   await p.locator('#kb-queue').hover(); await p.mouse.wheel(0, 600); await p.waitForTimeout(150);
   assert(await p.locator('#kb-queue').evaluate(e => e.scrollTop > 0 && window.scrollY === 0));
  }
 }
 assert.deepEqual(errors, []);
 console.log('PASS: drawer/payment/footer, internal scroll, search close, six widths × two themes, no JS errors');
} finally { await browser.close(); }
