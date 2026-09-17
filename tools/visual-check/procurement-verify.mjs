import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import { selectKBOption } from './kb-select-helper.mjs';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  for (const width of [375, 430, 1440]) {
    for (const theme of ['light', 'dark']) {
      const p = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      p.on('pageerror', e => errors.push(e.message));
      await p.goto(new URL('../mockups/shell-v2-prototype.html', import.meta.url).href);
      await p.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      // Pick a card with multiple checklist items to verify isolation.
      let cardId;
      for (let i = 0; i < 12; i++) {
        const card = p.locator('.kb-card').nth(i);
        cardId = await card.getAttribute('data-card');
        await card.click();
        await p.click('#kb-tab-procurement');
        if (await p.locator('[data-procurement-item]').count() > 1) break;
        await p.keyboard.press('Escape');
      }
      assert(await p.locator('[data-procurement-item]').count() > 1);
      const item = p.locator('[data-procurement-item]').first();
      const id = await item.getAttribute('data-procurement-item');
      const fileInput = item.locator('input[type=file]');
      await selectKBOption(p, '#kb-supplier-' + id, 'demo-2');
      const upload = name => fileInput.setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from('invoice test') });
      await upload('invoice.txt');
      const link = item.locator('.kb-item-download');
      const url = await link.getAttribute('href');
      assert.equal(await p.evaluate(async url => (await fetch(url)).text(), url), 'invoice test');
      const downloadEvent = p.waitForEvent('download');
      await link.click();
      assert.equal((await downloadEvent).suggestedFilename(), 'invoice.txt');
      await upload('invalid.exe');
      assert.match(await item.locator('[role=alert]').textContent(), /Выберите/);
      assert.equal(await link.getAttribute('href'), url, 'invalid replacement preserves file');
      await fileInput.setInputFiles({ name: 'large.txt', mimeType: 'text/plain', buffer: Buffer.alloc(25 * 1024 * 1024 + 1) });
      assert.match(await item.locator('[role=alert]').textContent(), /25 МБ/);
      assert.equal(await link.getAttribute('href'), url);
      await upload('replacement.txt');
      assert.notEqual(await link.getAttribute('href'), url);
      const revoked = await p.evaluate(async url => { try { await fetch(url); return false; } catch { return true; } }, url);
      assert(revoked, 'old object URL released');
      assert.equal(await p.locator('.kb-item-download').count(), 1);
      assert.equal(await p.locator('[data-item-supplier]').nth(1).inputValue(), '');
      await p.click('#kb-tab-overview'); await p.click('#kb-tab-procurement');
      await p.keyboard.press('Escape');
      await p.locator(`.kb-card[data-card="${cardId}"]`).click();
      await p.click('#kb-tab-procurement');
      assert.equal(await p.inputValue('#kb-supplier-' + id), 'demo-2');
      assert.equal(await link.textContent(), 'replacement.txt');
      await p.screenshot({ path: `/tmp/procurement-${width}-${theme}.png` });
      assert(await item.evaluate(el => el.scrollWidth <= el.clientWidth));
      await item.locator('[data-item-detach]').click();
      assert.equal(await item.locator('.kb-item-download').count(), 0);
      assert.equal(await p.inputValue('#kb-supplier-' + id), 'demo-2');
      assert.deepEqual(errors, []);
      console.log(`PASS procurement ${width}/${theme}: supplier, item isolation, download, replace/remove, limits, reopen, bounds`);
      await p.close();
    }
  }
} finally { await browser.close(); }
