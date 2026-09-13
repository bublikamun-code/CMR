#!/usr/bin/env python3
"""D8-срез 2: хирургия !important в css/style.css.

Позиции !important индексируются по порядку появления.
  list                  — количество и позиции
  remove 3,7,12         — снять эти !important (в текущем индексе)
  restore 3,7           — вернуть (по индексу, снятых позиций нет в файле)
Индекс пересчитывается после каждой операции.
"""
import sys
from pathlib import Path

path = Path('css/style.css')
src = path.read_text(encoding='utf-8')
TOKEN = '!important'  # noqa: S105 — css-токен, не пароль

positions = []
i = src.find(TOKEN)
while i != -1:
    positions.append(i)
    i = src.find(TOKEN, i + len(TOKEN))

cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'

if cmd == 'list':
    print(f'всего: {len(positions)}')
    for n, pos in enumerate(positions):
        line = src.count('\n', 0, pos) + 1
        ctx = src[max(0, pos - 40):pos].replace('\n', ' ')
        print(f'{n}: строка {line} …{ctx}')
elif cmd == 'remove':
    idxs = sorted((int(x) for x in sys.argv[2].split(',')), reverse=True)
    for n in idxs:
        pos = positions[n]
        src = src[:pos] + src[pos + len(TOKEN):]
    path.write_text(src)
    print(f'снято: {len(idxs)}; осталось: {len(positions) - len(idxs)}')
elif cmd == 'remove_offsets':
    # снять !important по точным байтовым смещениям (по убыванию)
    offs = sorted((int(x) for x in sys.argv[2].split(',')), reverse=True)
    for pos in offs:
        src = src[:pos] + src[pos + len(TOKEN):]
    path.write_text(src)
    print(f'снято по смещениям: {len(offs)}')
elif cmd == 'restore_offsets':
    # вернуть по точным смещениям (по убыванию — смещения остаются валидными)
    offs = sorted((int(x) for x in sys.argv[2].split(',')), reverse=True)
    for pos in offs:
        src = src[:pos] + TOKEN + src[pos:]
    path.write_text(src)
    print(f'возвращено по смещениям: {len(offs)}')
elif cmd == 'restore':
    # восстановить по ТЕКУЩЕМУ индексу: вставить TOKEN перед each pos
    idxs = sorted((int(x) for x in sys.argv[2].split(',')), reverse=True)
    for n in idxs:
        pos = positions[n]
        src = src[:pos] + TOKEN + src[pos:]
    path.write_text(src)
    print(f'возвращено: {len(idxs)}; всего: {len(positions) + len(idxs)}')
else:
    print(__doc__)
