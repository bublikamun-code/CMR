import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import { kbSelectMenu, kbSelectTrigger, selectKBOption } from './kb-select-helper.mjs';

// Run: node tools/visual-check/payment-menu-verify.mjs
// Contract tests for the preview only; no production API, storage or test hooks.
const url = new URL('../mockups/shell-v2-prototype.html', import.meta.url).href;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const errors = [];
async function newPage(timezoneId = 'America/Los_Angeles', mobile = false) {
  const context = await browser.newContext({ timezoneId, viewport: { width: mobile ? 375 : 1440, height: 900 }, isMobile: mobile, hasTouch: mobile });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on('pageerror', e => errors.push(e.stack || e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.addInitScript(() => {
    const RealDate = Date, instant = RealDate.parse('2026-03-08T07:30:00Z');
    window.Date = class extends RealDate {
      constructor(...args) { super(...(args.length ? args : [instant])); }
      static now() { return instant; }
    };
  });
  await page.goto(url, { waitUntil: 'load' });
  // Observe actual board cards via the existing public component API. Do not
  // expose/replace its WeakMap, inject drafts, or invent a production debug API.
  await page.evaluate(() => {
    if (!window.KBPayment) throw new Error('KBPayment must load before the board');
    window.__paymentCards = new Map();
    const original = KBPayment.draft;
    KBPayment.draft = function (card) {
      window.__paymentCards.set(card.id, card);
      return original(card);
    };
  });
  await page.click('[data-view="board"]');
  return page;
}

async function state(page, id) {
  return page.evaluate(id => {
    const c = window.__paymentCards.get(id);
    if (!c) throw new Error(`Board did not pass ${id} to KBPayment.draft`);
    const d = KBPayment.draft(c);
    return { terms: c.paymentTerms, amount: c.amount, paidAmount: c.paidAmount,
      snapshot: c.paymentDetails ?? null, draft: { ...d },
      snapshotIsDraft: c.paymentDetails === d, stableDraft: KBPayment.draft(c) === d };
  }, id);
}
async function edit(page, key, value) {
  const input = page.locator(`#kb-payment-${key}`);
  assert.equal(await input.getAttribute('data-payment-field'), key);
  await input.fill(value);
  await input.dispatchEvent('change');
  assert.equal(await input.inputValue(), value, `${key}: preserve the user's raw text`);
}
async function status(page, valid, due) {
  const status = page.locator('#kb-payment-status');
  assert(await status.isVisible(), 'payment feedback is visible, not just a hidden native error');
  const text = (await status.innerText()).trim();
  assert.match(text, valid ? /^Сохранено/ : /^Не сохранено/);
  if (!valid) assert.match(text.replace(/^Не сохранено\s*[·:—-]?\s*/, ''), /[а-яё]{3}/i, 'include the validation reason');
  if (due) {
    const [year, month, day] = due.split('-');
    assert(text.includes(due) || text.includes(`${day}.${month}.${year}`), `status must include due ${due}: ${text}`);
  }
}
async function closeCard(page) {
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#kb-dialog').evaluate(d => d.open), false);
}
async function openCard(page, id) {
  await page.locator(`#kb-board .kb-card[data-card="${id}"]`).click();
  await page.locator('#kb-panel-overview').waitFor({ state: 'visible' });
}
async function saved(page, id, due, cents) {
  await status(page, true, due);
  const s = await state(page, id);
  assert(s.snapshot && typeof s.snapshot === 'object', 'valid details saved as a card snapshot');
  assert.equal(s.snapshotIsDraft, false, 'saved details must not alias the mutable raw draft');
  assert(s.stableDraft, 'same card retains the same raw draft object');
  assert.equal(s.terms, s.draft.terms);
  // Snapshot field names are not prescribed beyond paymentDetails. Require the
  // computed ISO deadline and integer cents to actually be present in it.
  const values = Object.values(s.snapshot);
  if (due) assert(values.includes(due), `saved snapshot contains computed ISO due: ${JSON.stringify(s.snapshot)}`);
  if (cents !== undefined) assert(values.includes(cents), 'snapshot saves prepay as integer cents');
  return s;
}

try {
  const p = await newPage();
  // Browsers sanitize impossible <input type=date> values before JS can see
  // them. Exercise the component calendar boundary directly as well as UI blanks.
  const calendarCases = await p.evaluate(() => {
    return ['2028-02-29', '2027-02-29', '2026-04-31', '2026-13-01', '0999-12-31', '10000-01-01', '2026-1-01'].map(end => {
      const card = { paymentTerms: 'deferred', amount: 10000 };
      Object.assign(KBPayment.draft(card), { mode: 'date', end });
      const result = KBPayment.validate(card, () => null);
      return { end, valid: result.valid, due: result.due };
    });
  });
  assert.equal(calendarCases[0].valid, true);
  assert.equal(calendarCases[0].due, '2028-02-29');
  for (const c of calendarCases.slice(1)) assert.equal(c.valid, false, `impossible calendar date ${c.end}`);
  const ids = await p.locator('#kb-board .kb-card').evaluateAll(es => es.slice(0, 2).map(e => e.dataset.card));
  const [a, b] = ids;
  assert(b, 'two independent cards required');
  await openCard(p, a);
  await selectKBOption(p, '#kb-deal-paymentTerms', 'deferred');
  assert(await p.locator('#kb-panel-overview').isVisible());
  await status(p, false);
  assert.equal((await state(p, a)).terms, 'deferred', 'terms commit even without valid details');
  assert.equal(await p.getAttribute('#kb-payment-mode', 'data-payment-field'), 'mode');
  assert.deepEqual(await p.locator('#kb-payment-mode option').evaluateAll(es => es.map(e => e.value)), ['', 'date', 'days']);
  await selectKBOption(p, '#kb-payment-mode', 'date');
  await status(p, false);
  await edit(p, 'end', '2028-02-29');
  let snapshot = (await saved(p, a, '2028-02-29')).snapshot;
  await edit(p, 'end', '');
  await status(p, false);
  assert.deepEqual((await state(p, a)).snapshot, snapshot, 'invalid draft cannot overwrite last valid snapshot');
  await closeCard(p); await openCard(p, a);
  assert.equal(await p.inputValue('#kb-payment-end'), '');
  await status(p, false);
  await selectKBOption(p, '#kb-payment-mode', 'days');
  assert.equal(await p.inputValue('#kb-payment-start'), '2026-03-07', 'local today, not UTC March 8');
  await edit(p, 'days', '1'); await saved(p, a, '2026-03-08');
  for (const [start, days, due] of [
    ['2026-03-07', '2', '2026-03-09'], // spring DST
    ['2026-10-31', '2', '2026-11-02'], // fall DST
    ['2028-02-28', '1', '2028-02-29'],
    ['2026-12-31', '1', '2027-01-01'],
  ]) {
    await edit(p, 'start', start); await edit(p, 'days', days);
    await saved(p, a, due);
  }
  snapshot = (await state(p, a)).snapshot;
  for (const raw of ['', '0', '-1', '1.5', '1e2', 'abc', '36501']) {
    await edit(p, 'days', raw); await status(p, false);
    assert.equal(await p.getAttribute('#kb-payment-days', 'aria-invalid'), 'true');
    assert.deepEqual((await state(p, a)).snapshot, snapshot);
  }
  await closeCard(p); await openCard(p, a);
  assert.equal(await p.inputValue('#kb-payment-days'), '36501');
  await edit(p, 'start', '9999-12-31'); await edit(p, 'days', '1');
  await status(p, false); // computed year 10000 is invalid
  await edit(p, 'start', '2028-02-28'); await edit(p, 'days', '1');
  await saved(p, a, '2028-02-29');
  await edit(p, 'start', ''); await status(p, false);
  await edit(p, 'start', '2028-02-28');
  await p.fill('#kb-deal-amount', '100,00');
  await p.locator('#kb-deal-amount').dispatchEvent('change');
  assert.equal((await state(p, a)).amount, 10000);
  await selectKBOption(p, '#kb-deal-paymentTerms', 'partial_deferred');
  await status(p, false);
  assert.equal((await state(p, a)).terms, 'partial_deferred');
  await edit(p, 'prepay', '0,01');
  snapshot = (await saved(p, a, '2028-02-29', 1)).snapshot;
  for (const raw of ['', '0', '-0,01', '100', '100,01', '1,001', '1e1', 'no money']) {
    await edit(p, 'prepay', raw); await status(p, false);
    assert.equal(await p.getAttribute('#kb-payment-prepay', 'aria-invalid'), 'true');
    const s = await state(p, a);
    assert.equal(s.draft.prepay, raw);
    assert.deepEqual(s.snapshot, snapshot, `invalid ${JSON.stringify(raw)} cannot poison saved cents`);
  }
  await edit(p, 'prepay', '99,99'); await saved(p, a, '2028-02-29', 9999);
  await edit(p, 'prepay', '12.34'); await saved(p, a, '2028-02-29', 1234);
  await edit(p, 'prepay', '12,34'); await saved(p, a, '2028-02-29', 1234);
  await edit(p, 'prepay', '1,234'); await status(p, false);
  await closeCard(p); await openCard(p, b);
  assert.equal(await p.inputValue('#kb-deal-paymentTerms'), '');
  assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
  await selectKBOption(p, '#kb-deal-paymentTerms', 'deferred');
  await selectKBOption(p, '#kb-payment-mode', 'date');
  await edit(p, 'end', '2030-01-02');
  const second = await saved(p, b, '2030-01-02');
  await closeCard(p); await openCard(p, a);
  assert.equal(await p.inputValue('#kb-payment-prepay'), '1,234');
  assert.equal(await p.inputValue('#kb-payment-days'), '1');
  assert.equal(await p.inputValue('#kb-payment-start'), '2028-02-28');
  assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
  await status(p, false);
  // Header terms must reveal the existing overview from another tab.
  for (const terms of ['full', '', 'deferred', 'partial_deferred']) {
    await p.click('#kb-tab-procurement');
    await selectKBOption(p, '#kb-deal-paymentTerms', terms);
    assert(await p.locator('#kb-panel-overview').isVisible(), `${terms}: reveal overview`);
    assert.equal(await p.getAttribute('#kb-tab-overview', 'aria-selected'), 'true');
    assert.equal(await p.locator('#kb-panel-overview').count(), 1);
    assert.equal((await state(p, a)).terms, terms);
    assert.equal((await state(p, a)).paidAmount, 0, 'terms must not mark payment received');
  assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
  }
  assert.equal(await p.inputValue('#kb-payment-prepay'), '1,234', 'term switches retain raw draft');
  await status(p, false);
  await selectKBOption(p, '#kb-payment-mode', 'date');
  assert.equal(await p.inputValue('#kb-payment-end'), '', 'mode switch retains empty date draft');
  await edit(p, 'end', '2029-04-05');
  await selectKBOption(p, '#kb-payment-mode', 'days');
  assert.equal(await p.inputValue('#kb-payment-days'), '1');
  assert.equal(await p.inputValue('#kb-payment-start'), '2028-02-28');
  await edit(p, 'prepay', '25,00');
  snapshot = (await saved(p, a, '2028-02-29', 2500)).snapshot;
  await p.click('#kb-hide-filled');
  assert.equal(await p.getAttribute('#kb-hide-filled', 'aria-pressed'), 'true');
  for (const selector of ['#kb-deal-paymentTerms', '#kb-payment-mode']) assert(await kbSelectTrigger(p, selector).isVisible());
  for (const key of ['start', 'days', 'prepay']) assert(await p.locator(`#kb-payment-${key}`).isVisible(), `${key} excluded from hide-filled`);
  await edit(p, 'prepay', 'bad'); await status(p, false);
  assert(await p.locator('#kb-payment-prepay').isVisible());
  await p.click('#kb-hide-filled');
  await edit(p, 'prepay', '25,00');
  // Amount commits; conflicting saved prepay is invalidated, never clamped.
  await p.fill('#kb-deal-amount', '20,00');
  await p.locator('#kb-deal-amount').dispatchEvent('change');
  const reduced = await state(p, a);
  assert.equal(reduced.amount, 2000);
  assert.notEqual(await p.getAttribute('#kb-deal-amount', 'aria-invalid'), 'true');
  assert.equal(reduced.snapshot, null, 'conflicting snapshot invalidated');
  assert.equal(reduced.draft.prepay, '25,00');
  await status(p, false);
  await closeCard(p); await openCard(p, a);
  assert.equal(await p.inputValue('#kb-payment-prepay'), '25,00');
  await status(p, false);
  await edit(p, 'prepay', '19,99'); await saved(p, a, '2028-02-29', 1999);
  await closeCard(p); await openCard(p, b);
  assert.deepEqual((await state(p, b)).snapshot, second.snapshot, 'other card unaffected');
  assert.equal(await p.locator('[data-deal-field="paid"]').count(), 0);
  console.log('PASS: payment dates/days, DST/leap/year, cents, raw/saved isolation, cards/terms/modes, paid, overview, amount conflict');

  await closeCard(p); await openCard(p, a);
  const termsTrigger = kbSelectTrigger(p, '#kb-deal-paymentTerms');
  const originalTerms = await p.inputValue('#kb-deal-paymentTerms');
  await kbSelectMenu(p, '#kb-deal-paymentTerms');
  await p.keyboard.press('Home'); await p.keyboard.press('ArrowDown');
  let active = await termsTrigger.getAttribute('aria-activedescendant');
  assert.equal(await p.locator(`[id="${active}"]`).textContent(), 'Отсрочка');
  assert.equal(await p.inputValue('#kb-deal-paymentTerms'), originalTerms, 'arrows never commit');
  await p.keyboard.press('Escape');
  assert.equal(await termsTrigger.getAttribute('aria-expanded'), 'false');
  assert(await p.locator('#kb-dialog').evaluate(d => d.open), 'first Escape must not dismiss drawer');
  assert(await termsTrigger.evaluate(e => e === document.activeElement));
  await kbSelectMenu(p, '#kb-deal-paymentTerms');
  await p.keyboard.press('Home'); await p.keyboard.press('Tab');
  assert.equal(await termsTrigger.getAttribute('aria-expanded'), 'false');
  assert.equal(await p.inputValue('#kb-deal-paymentTerms'), originalTerms);
  assert(await p.locator('#kb-dialog').evaluate(d => d.contains(document.activeElement)));
  assert(!await termsTrigger.evaluate(e => e === document.activeElement), 'Tab follows DOM order');
  await kbSelectMenu(p, '#kb-deal-paymentTerms');
  await p.keyboard.press('End'); await p.keyboard.press('ArrowUp');
  active = await termsTrigger.getAttribute('aria-activedescendant');
  assert.equal(await p.locator(`[id="${active}"]`).textContent(), 'Оплата 100%');
  await p.keyboard.press('Enter');
  assert.equal(await p.inputValue('#kb-deal-paymentTerms'), 'full');
  // Playwright cannot map a Cyrillic keyboard.press key to a physical keycode.
  await termsTrigger.focus(); await termsTrigger.dispatchEvent('keydown', { key: 'ч', bubbles: true });
  active = await termsTrigger.getAttribute('aria-activedescendant');
  assert.equal(await p.locator(`[id="${active}"]`).textContent(), 'Частичная оплата + отсрочка платежа');
  await p.keyboard.press('Enter');
  assert.equal(await p.inputValue('#kb-deal-paymentTerms'), 'partial_deferred');
  await kbSelectMenu(p, '#kb-payment-mode');
  await p.keyboard.press('Home'); await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter');
  assert.equal(await p.inputValue('#kb-payment-mode'), 'date');
  await saved(p, a, '2029-04-05', 1999);
  await closeCard(p);
  console.log('PASS: arrow/Home/End/Enter/typeahead, Escape priority, Tab without commit');

  // Same UTC instant is the next local day in a positive-offset timezone.
  const east = await newPage('Pacific/Kiritimati');
  await east.locator('#kb-board .kb-card').first().click();
  await selectKBOption(east, '#kb-deal-paymentTerms', 'deferred');
  await selectKBOption(east, '#kb-payment-mode', 'days');
  assert.equal(await east.inputValue('#kb-payment-start'), '2026-03-08');
  await edit(east, 'days', '1'); await status(east, true, '2026-03-09');
  await east.context().close();

  const mobile = await newPage('America/Los_Angeles', true);
  for (const page of [p, mobile]) {
    for (const width of (page === mobile ? [375, 430] : [1280, 1440, 1920])) {
      await page.setViewportSize({ width, height: 900 });
      for (const theme of ['light', 'dark']) {
        await page.evaluate(t => document.documentElement.dataset.theme = t, theme);
        await page.locator('#kb-board .kb-card').first().click();
        await selectKBOption(page, '#kb-deal-paymentTerms', 'partial_deferred');
        await selectKBOption(page, '#kb-payment-mode', 'days');
        for (const selector of ['#kb-deal-paymentTerms', '#kb-payment-mode', '#kb-deal-manager']) {
          const trigger = kbSelectTrigger(page, selector);
          assert(!await page.locator(selector).isVisible(), 'native select must not be interactive');
          await trigger.scrollIntoViewIfNeeded();
          const closed = await trigger.boundingBox();
          const menu = await kbSelectMenu(page, selector);
          const geometry = await trigger.evaluate(button => {
            const surface = button.closest('.kb-select-surface');
            const list = surface.querySelector('[role="listbox"]');
            const rect = e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom }; };
            const r = list.getBoundingClientRect();
            const hit = document.elementFromPoint(r.x + r.width / 2, r.y + Math.min(12, r.height / 2));
            return { surface: rect(surface), button: rect(button), list: rect(list),
              shared: button.parentElement === list.parentElement,
              triggerBorder: getComputedStyle(button).borderTopWidth,
              surfaceBorder: getComputedStyle(surface).borderTopWidth,
              hit: list.contains(hit), horizontal: surface.scrollWidth <= surface.clientWidth + 1,
              viewport: { width: innerWidth, height: innerHeight } };
          });
          const label = `${width}/${theme}/${selector}`;
          assert(geometry.shared, `${label}: one unified surface`);
          assert.equal(geometry.triggerBorder, '0px', label);
          assert.equal(geometry.surfaceBorder, '1px', label);
          assert(geometry.surface.x >= 0 && geometry.surface.y >= 0, label);
          assert(geometry.surface.right <= geometry.viewport.width + 1 && geometry.surface.bottom <= geometry.viewport.height + 1, `${label}: no viewport clipping`);
          assert(Math.abs(geometry.list.y - geometry.button.bottom) <= 1, `${label}: menu touches trigger`);
          assert(Math.abs(geometry.button.width - geometry.list.width) <= 1, label);
          assert(geometry.horizontal && geometry.hit, `${label}: list not clipped by drawer`);
          assert(await menu.isVisible());
          if (page === mobile) assert(geometry.button.height >= 44, `${label}: touch target`);
          await page.keyboard.press('Escape');
          const restored = await trigger.boundingBox();
          for (const key of ['x', 'y', 'width', 'height']) assert(Math.abs(restored[key] - closed[key]) <= 1, `${label}: restored ${key}`);
        }
        assert(await page.locator('#kb-dialog').evaluate(d => d.scrollWidth <= d.clientWidth + 1));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
        await closeCard(page);
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log('PASS: local today/timezones, mobile/desktop × themes, unified surface/geometry/clipping/touch targets, no JS errors');
} finally {
  await browser.close();
}

