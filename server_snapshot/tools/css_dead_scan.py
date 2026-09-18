#!/usr/bin/env python3
"""D8: мёртвый CSS — статический анализ.

Класс селектора style.css считается мёртвым, если его нет НИ В ОДНОМ
источнике, который может его применить: js/*.js (включая собранный
nakladnye.bundle.js), frontend/*.js, index.html, admin.html, server.py.
Класс в источнике как подстрока = живой (консервативно: может быть частью
другого слова, тогда ложно-живой — безопасное направление ошибки).

Запуск: python3 tools/css_dead_scan.py
Отчёт:  tmp/css-dead.md
"""
import contextlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'css' / 'style.css').read_text(encoding='utf-8').replace('/*الحشو*/', '')
CSS = re.sub(r'/\*[\s\S]*?\*/', '', CSS)

# --- парсер: селектор + строка в файле ---
rules = []  # (selector, line_no)
depth = 0
buf = ''
at = ''
line = 1
pos = 0
for ch in CSS:
    if ch == '\n':
        line += 1
    if ch == '{':
        sel = buf.strip()
        if sel.startswith(('@keyframes', '@font-face')):
            i = pos + 1
            d = 1
            while i < len(CSS) and d > 0:
                if CSS[i] == '{':
                    d += 1
                elif CSS[i] == '}':
                    d -= 1
                i += 1
            buf = ''
            at = ''
            depth = 0
            pos = i
            continue
        if sel.startswith('@'):
            at = sel
            buf = ''
            depth += 1
            continue
        rules.append(((at + ' :: ' if at and depth else '') + sel, line))
        buf = ''
        depth += 1
    elif ch == '}':
        depth -= 1
        if depth == 0:
            at = ''
        buf = ''
    else:
        buf += ch
    pos += 1

# --- источники живых классов ---
sources = []
for pat in ('js/*.js', 'js/vendor/*.js', 'frontend/*.js', '*.html', 'server.py', 'routers/*.py', 'services/*.py'):
    for f in ROOT.glob(pat):
        with contextlib.suppress(OSError):
            sources.append(f.read_text(encoding='utf-8', errors='ignore'))
ALL = '\n'.join(sources)

# --- классы по правилам ---
classes_of = lambda sel: sorted(set(re.findall(r'\.([a-zA-Z0-9_-]+)', sel)))
with_classes = [(sel, ln, classes_of(sel)) for sel, ln in rules]
with_classes = [x for x in with_classes if x[2]]

dead = [(sel, ln, cls) for sel, ln, cls in with_classes if all(c not in ALL for c in cls)]

out = []
out.append('# Мёртвый CSS (D8): класс отсутствует во всех источниках приложения\n')
out.append(f'Правил с классами: {len(with_classes)}; мёртвых правил: {len(dead)}\n')
out.append('Консервативно: класс считается живым, если встречается подстрокой\n')
out.append('в js/*.js, frontend/*.js, index.html, admin.html или server.py.\n\n')
for sel, ln, cls in dead:
    out.append(f'style.css:{ln}: {sel}\n    классы: {", ".join(cls)}\n\n')
(ROOT / 'tmp' / 'css-dead.md').parent.mkdir(exist_ok=True)
(ROOT / 'tmp' / 'css-dead.md').write_text(''.join(out), encoding='utf-8')
print(f'правил с классами: {len(with_classes)}; мёртвых: {len(dead)}; отчёт: tmp/css-dead.md')
