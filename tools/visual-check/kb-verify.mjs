import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import { kbSelectMenu } from './kb-select-helper.mjs';

const browser = await chromium.launch({channel:'chrome', headless:true});
try {
 const p = await browser.newPage({viewport:{width:1600,height:1000}});
 p.setDefaultTimeout(5000);
 const errors=[]; p.on('pageerror', e=>errors.push(e.message));
 await p.goto(new URL('../mockups/shell-v2-prototype.html', import.meta.url).href,{waitUntil:'domcontentloaded'});
 await p.click('[data-view="board"]');
 assert.equal(await p.locator('.kb-col').count(),4);
 const initialCount = await p.locator('.kb-card').count();
 await p.click('#kb-search-toggle');
 assert.equal(await p.locator('#kb-search').evaluate(e => e === document.activeElement),true);
 await p.fill('#kb-search','несуществующая сделка');
 assert.equal(await p.locator('.kb-card').count(),0);
 await p.keyboard.press('Escape');
 assert.equal(await p.locator('.kb-card').count(),initialCount);
 await kbSelectMenu(p, '#kb-store-select'); await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter');
 assert.equal(await p.locator('#kb-store .kb-select-value').textContent(),'Матусевича');
 await kbSelectMenu(p, '#kb-store-select'); await p.keyboard.press('Home'); await p.keyboard.press('Enter');
 await p.click('#kb-density'); assert.equal(await p.getAttribute('#kb-density','aria-pressed'),'false');
 await p.click('#kb-density');
 await p.locator('[data-stage="assembly"] .kb-card').first().click();
 await p.click('#kb-tab-procurement');
 assert.equal(await p.locator('#kb-panel-procurement').isVisible(),true);
 await p.click('#kb-send');
 assert.equal(await p.locator('.kb-card').count(),initialCount-1);
 await p.click('#kb-q-pending');
 assert.equal(await p.locator('#kb-board').isVisible(),false);
 const id=await p.locator('#kb-queue-body [data-card]').first().getAttribute('data-card');
 const card=()=>p.locator('#kb-queue-body [data-card="'+id+'"]');
 async function form(number) {
  await card().click(); await p.click('#kb-issue');
  const amount=await p.inputValue('#kb-amount');
  await p.fill('#kb-date','2026-09-16'); await p.fill('#kb-series','ТН'); await p.fill('#kb-number',number);
  assert(await p.locator('#kb-issue-form').evaluate(f=>f.checkValidity()));
  return amount;
 }
 const initial=await form('901');
 await p.fill('#kb-amount','0'); await p.click('[type="submit"]');
 assert.match(await p.textContent('#kb-error'),/больше нуля/);
 await p.fill('#kb-amount','999999999'); await p.click('[type="submit"]');
 assert.match(await p.textContent('#kb-error'),/не превышать/);
 await p.fill('#kb-amount','1,00'); await p.click('[type="submit"]');
 assert.equal(await p.locator('#kb-dialog').evaluate(d=>d.open),false);
 assert.equal(await card().count(),1);
 assert.match(await p.textContent('#kb-toast'),/частичная/);
 const rest=await form('901');
 await p.fill('#kb-amount','1,00'); await p.click('[type="submit"]');
 assert.match(await p.textContent('#kb-error'),/уже выписана/);
 await p.fill('#kb-number','902'); await p.fill('#kb-amount',rest);
 await p.click('[type="submit"]');
 assert.equal(await p.locator('#kb-dialog').evaluate(d=>d.open),false);
 assert.equal(await card().count(),0);
 assert.match(await p.textContent('#kb-toast'),/полностью/);
 await p.click('#kb-see-history');
 assert.equal(await p.locator('#kb-queue-body [data-card="'+id+'"]').count(),1);
 await p.click('#kb-queue-body [data-card="'+id+'"]');
 assert.match(await p.textContent('#kb-dialog'),/901/); assert.match(await p.textContent('#kb-dialog'),/902/);
 await p.keyboard.press('Escape');
 console.log('PASS: validation, partial, duplicate, full, history; initial='+initial+' rest='+rest);
 await p.reload({waitUntil:'domcontentloaded'}); await p.click('[data-view="board"]');
 for (const width of [1280,1440,1600,1920,375,430]) {
  await p.setViewportSize({width,height:900});
  for (const theme of ['light','dark']) {
   await p.evaluate(t=>document.documentElement.setAttribute('data-theme',t),theme);
   const size=await p.evaluate(()=>({body:document.documentElement.scrollWidth-document.documentElement.clientWidth,board:document.querySelector('#kb-board').scrollWidth-document.querySelector('#kb-board').clientWidth}));
   assert.equal(size.body,0,JSON.stringify({width,theme,size}));
   if(width>=1280) assert(size.board<=1,JSON.stringify({width,size}));
  }
  console.log('PASS: widths/themes',width);
 }
 await p.setViewportSize({width:1600,height:1000});
 await p.evaluate(()=>document.documentElement.setAttribute('data-theme','light'));
 await p.screenshot({path:'/tmp/kb-verified.png'});
 assert.deepEqual(errors,[]); console.log('PASS: no JS errors');
} finally {await browser.close();}
