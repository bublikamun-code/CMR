// Статическая сверка data-handler/data-change без исполнения кода приложения.
// Поддерживаются явные window-экспорты, литералы объектов и присваивания методов.
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..', 'site');

function tokens(src) {
  let i = 0;
  function quoted(quote) {
    i++;
    while (i < src.length) {
      if (src[i] === '\\') { i += 2; continue; }
      if (src[i] === quote) { i++; return; }
      if (quote === '`' && src.slice(i, i + 2) === '${') {
        i += 2; scan(true); continue;
      }
      i++;
    }
  }
  function scan(interpolation = false) {
    const out = [];
    let depth = 0;
    while (i < src.length) {
      const c = src[i], next = src[i + 1], prev = out[out.length - 1];
      if (/\s/.test(c)) { i++; continue; }
      if (c === '/' && next === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
      if (c === '/' && next === '*') {
        const end = src.indexOf('*/', i + 2); i = end < 0 ? src.length : end + 2; continue;
      }
      if (c === '"' || c === "'" || c === '`') { quoted(c); out.push('#literal'); continue; }
      if (c === '/' && (!prev || ['=', '(', '[', ',', ':', 'return', '!', '&&', '||', '?'].includes(prev))) {
        i++;
        let inClass = false;
        while (i < src.length) {
          if (src[i] === '\\') { i += 2; continue; }
          if (src[i] === '[') inClass = true;
          if (src[i] === ']') inClass = false;
          if (src[i++] === '/' && !inClass) break;
        }
        while (i < src.length && /[a-z]/i.test(src[i])) i++;
        out.push('#literal'); continue;
      }
      if (c === '}' && interpolation && depth === 0) { i++; return out; }
      if (c === '{') depth++;
      if (c === '}') depth--;
      const word = /^[A-Za-z_$][\w$]*|^=>|^&&|^\|\|/.exec(src.slice(i));
      if (word) { out.push(word[0]); i += word[0].length; }
      else { out.push(c); i++; }
    }
    return out;
  }
  return scan();
}

function definitions(src) {
  const ts = tokens(src), objects = new Map(), aliases = new Map(), functions = new Set();
  const pairs = new Map(), stack = [];
  for (let i = 0; i < ts.length; i++) {
    if ('({['.includes(ts[i]) && ts[i].length === 1) stack.push(i);
    else if (')}]'.includes(ts[i]) && ts[i].length === 1) {
      const start = stack.pop();
      if (start !== undefined) pairs.set(start, i);
    }
  }
  for (let i = 0; i < ts.length; i++) {
    if (ts[i] === 'function' && /^[A-Za-z_$]/.test(ts[i + 1] || '')) functions.add(ts[i + 1]);
  }
  function objectFields(start) {
    const fields = new Set(), end = pairs.get(start);
    if (end === undefined) return fields;
    let i = start + 1;
    while (i < end) {
      if (ts[i] === ',') { i++; continue; }
      if (ts[i] === 'async' && ts[i + 1] !== '(' && ts[i + 1] !== ':') i++;
      const name = ts[i++];
      if ((ts[i] === ',' || i === end) && functions.has(name)) fields.add(name);
      if (ts[i] === '(') {
        const close = pairs.get(i);
        if (close !== undefined && ts[close + 1] === '{') fields.add(name);
      } else if (ts[i] === ':') {
        let value = i + 1;
        if (ts[value] === 'async') value++;
        if (ts[value] === 'function' || ts[value + 1] === '=>' ||
            (ts[value] === '(' && ts[pairs.get(value) + 1] === '=>')) fields.add(name);
      }
      while (i < end && ts[i] !== ',') {
        i = pairs.has(i) ? pairs.get(i) + 1 : i + 1;
      }
    }
    return fields;
  }
  for (let i = 0; i < ts.length; i++) {
    if (ts[i] === 'function' && /^[A-Za-z_$]/.test(ts[i + 1] || '')) functions.add(ts[i + 1]);
    let name, eq;
    if (['const', 'let', 'var'].includes(ts[i]) && ts[i + 2] === '=') {
      name = ts[i + 1]; eq = i + 2;
    } else if (ts[i] === 'window' && ts[i + 1] === '.' && ts[i + 3] === '=') {
      name = 'window.' + ts[i + 2]; eq = i + 3;
    }
    if (name) {
      if (ts[eq + 1] === '{') objects.set(name, objectFields(eq + 1));
      else if (ts[eq + 1] === 'function' || ts[eq + 1] === 'async' ||
               ts[eq + 2] === '=>' || (ts[eq + 1] === '(' && ts[pairs.get(eq + 1) + 1] === '=>')) functions.add(name);
      else if (/^[A-Za-z_$][\w$]*$/.test(ts[eq + 1] || '')) aliases.set(name, ts[eq + 1]);
    }
  }
  function resolve(name) {
    const seen = new Set();
    while (aliases.has(name) && !seen.has(name)) { seen.add(name); name = aliases.get(name); }
    return name;
  }
  // Дополнения к объекту рассматриваем только в пределах того же файла.
  for (let i = 0; i < ts.length; i++) {
    let base = ts[i], prop = i + 2;
    if (base === 'window' && ts[i + 1] === '.') { base += '.' + ts[i + 2]; prop += 2; }
    if (ts[prop - 1] !== '.' || ts[prop + 1] !== '=') continue;
    let value = prop + 2;
    if (ts[value] === 'async') value++;
    if (ts[value] !== 'function' && ts[value + 1] !== '=>' &&
        !(ts[value] === '(' && ts[pairs.get(value) + 1] === '=>')) continue;
    if (!objects.has(base) && !aliases.has(base) &&
        (objects.has('window.' + base) || aliases.has('window.' + base))) base = 'window.' + base;
    base = resolve(base);
    if (!objects.has(base)) objects.set(base, new Set());
    objects.get(base).add(ts[prop]);
  }
  const result = new Set();
  for (const name of new Set([...objects.keys(), ...aliases.keys(), ...functions])) {
    const target = resolve(name);
    if (name.startsWith('window.')) {
      const exported = name.slice(7);
      for (const field of objects.get(target) || []) result.add(exported + '.' + field);
      if (functions.has(target)) result.add(exported);
    } else if (functions.has(name)) result.add(name);
  }
  return result;
}

const files = [];
(function walk(dir) {
  for (const f of fs.readdirSync(dir)) {
    const p = path.join(dir, f);
    if (fs.statSync(p).isDirectory()) walk(p);
    else if (/\.(js|html)$/.test(f)) files.push(p);
  }
})(root);
const handlers = new Map(), available = new Set();
for (const file of files) {
  const src = fs.readFileSync(file, 'utf8');
  if (file.endsWith('.js')) for (const name of definitions(src)) available.add(name);
  const re = /data-(?:handler|change)=["']([^"']+)["']/g;
  let m;
  while ((m = re.exec(src))) {
    if (!handlers.has(m[1])) handlers.set(m[1], []);
    handlers.get(m[1]).push(`${path.relative(root, file)}:${src.slice(0, m.index).split('\n').length}`);
  }
}
let bad = 0;
for (const [handler, locs] of [...handlers.entries()].sort()) {
  if (!available.has(handler)) {
    bad++;
    console.log('NOT RESOLVED:', handler, '->', locs.slice(0, 3).join(', '));
  }
}
console.log('total handlers:', handlers.size, 'unresolved:', bad);
process.exitCode = bad ? 1 : 0;
