// Статическая сверка: каждый data-handler / data-change в HTML и JS-шаблонах
// должен указывать на реально определённую функцию (window.Part.fn).
// Использование: node tools/check_handlers.js
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..', 'site');

const files = [];
(function walk(d) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p);
    else if (/\.(js|html)$/.test(f)) files.push(p);
  }
})(root);

const handlers = new Map();
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  const re = /data-(?:handler|change)=["']([^"']+)["']/g;
  let m;
  while ((m = re.exec(src))) {
    const line = src.slice(0, m.index).split('\n').length;
    if (!handlers.has(m[1])) handlers.set(m[1], []);
    handlers.get(m[1]).push(`${path.relative(root, f)}:${line}`);
  }
}

const jsAll = files.filter((f) => f.endsWith('.js')).map((f) => fs.readFileSync(f, 'utf8')).join('\n');

function resolves(h) {
  const parts = h.split('.');
  const first = parts[0];
  const defRe = new RegExp('(window\\.' + first + '\\s*=|const ' + first + '\\s*=|let ' + first + '\\s*=|var ' + first + '\\s*=|function ' + first + '\\b)');
  if (!defRe.test(jsAll)) return false;
  if (parts.length === 1) return true;
  const prop = parts[1];
  const propRe = new RegExp('(window\\.' + first + '\\s*=\\s*\\{[\\s\\S]{0,6000}?\\b' + prop + '\\b|' + first + '\\.' + prop + '\\s*=|\\b' + prop + '\\s*[:=]\\s*(async\\s*)?function|\\b' + prop + '\\s*:\\s*(async\\s*)?\\()');
  return propRe.test(jsAll);
}

let bad = 0;
for (const [h, locs] of [...handlers.entries()].sort()) {
  if (!resolves(h)) {
    bad++;
    console.log('NOT RESOLVED:', h, '->', locs.slice(0, 3).join(', '));
  }
}
console.log('total handlers:', handlers.size, 'unresolved:', bad);
