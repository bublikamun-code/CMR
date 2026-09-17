import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
const url = new URL('../mockups/shell-v2-prototype.html', import.meta.url).href;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  for (const width of [375, 1440]) {
    const p = await browser.newPage({ viewport: { width, height: 900 } });
    p.setDefaultTimeout(4000);
    const errors = [];
    p.on('pageerror', e => errors.push(e.message));
    await p.goto(url);
    await p.click('[data-view="suppliers"]');
    await p.locator('#view-suppliers').waitFor({ state: 'visible' });
    await p.click('#mgmt-supplier-new');
    await p.locator('#mgmt-dialog').waitFor({ state: 'visible' });
    await p.fill('#mgmt-name', 'Тест поставщика');
    await p.fill('#mgmt-unp', '123456789');
    await p.click('#mgmt-save');
    await p.fill('#mgmt-supplier-search', '123456789');
    assert.equal(await p.locator('#mgmt-supplier-rows tr').count(), 1);
    await p.locator('#mgmt-supplier-rows [data-edit]').click();
    await p.fill('#mgmt-name', 'Новое название');
    await p.click('#mgmt-save');
    assert.match(await p.locator('#mgmt-supplier-rows').textContent(), /Новое название/);
    assert(await p.evaluate(() => window.KBSuppliers.list().some(s => s[1] === 'Новое название')));
    await p.click('[data-view="admin"]');
    await p.locator('#view-admin').waitFor({ state: 'visible' });
    await p.click('#mgmt-user-new');
    await p.fill('#mgmt-username', 'test-manager');
    await p.click('#mgmt-save');
    assert.match(await p.locator('#mgmt-user-rows').textContent(), /test-manager/);
    for (const theme of ['light', 'dark']) {
      await p.evaluate(t => document.documentElement.dataset.theme = t, theme);
      for (const route of ['suppliers', 'admin', 'fin']) {
        await p.click(`[data-view="${route}"]`);
        assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await p.screenshot({ path: `/tmp/management-${route}-${width}-${theme}.png` });
      }
    }
    const calc = p.locator('#fin-calculated-c104');
    const posted = p.locator('#fin-posted-c104');
    const total = await p.locator('#fin-payment-total').textContent();
    await calc.check(); assert.equal(await posted.isChecked(), false);
    await posted.check(); await calc.uncheck(); assert.equal(await posted.isChecked(), true);
    await p.fill('#fin-payment-search', 'no-match'); await p.fill('#fin-payment-search', '');
    assert.equal(await posted.isChecked(), true);
    assert.equal(await calc.isChecked(), false);
    assert.equal(await p.locator('#fin-payment-total').textContent(), total);
    assert.equal(await p.locator('#fin-payment-table th').count(), 8);
    assert.deepEqual(errors, []);
    console.log(`PASS management ${width}: supplier create/edit/search, user create, routes/themes/bounds, independent registry flags`);
    await p.close();
  }
} finally { await browser.close(); }
