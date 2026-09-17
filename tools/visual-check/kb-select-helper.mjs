import assert from 'node:assert/strict';

// Native selects remain useful for reading values/options, never for interaction.
// Resolve by ID because an open KBSelect surface is portalled out of its anchor.
export function kbSelectTrigger(page, selector) {
  assert.match(selector, /^#[\w-]+$/, 'KBSelect helper expects a native select ID');
  return page.locator(selector === '#kb-store-select' ? '#kb-store' : `${selector}-trigger`);
}

export async function kbSelectMenu(page, selector) {
  const trigger = kbSelectTrigger(page, selector);
  await trigger.waitFor({ state: 'visible' });
  if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click();
  const id = await trigger.getAttribute('aria-controls');
  assert(id, `${selector}: trigger must identify its listbox`);
  const menu = page.locator(`[id="${id}"]`);
  await menu.waitFor({ state: 'visible' });
  assert.equal(await menu.getAttribute('role'), 'listbox');
  return menu;
}

export async function selectKBOption(page, selector, value) {
  const option = await page.locator(selector).evaluate((select, wanted) => {
    const index = Array.from(select.options).findIndex(o => o.value === String(wanted));
    return index < 0 ? null : { index, label: select.options[index].label };
  }, value);
  assert(option, `${selector}: unknown option ${JSON.stringify(value)}`);
  const menu = await kbSelectMenu(page, selector);
  const row = menu.locator(`[role="option"][data-index="${option.index}"]`);
  assert.notEqual(await row.getAttribute('aria-disabled'), 'true');
  await row.click();
  assert.equal(await page.locator(selector).inputValue(), String(value));
  assert.equal(await kbSelectTrigger(page, selector).getAttribute('aria-expanded'), 'false');
  assert.equal(await kbSelectTrigger(page, selector).locator('.kb-select-value').textContent(), option.label);
}
