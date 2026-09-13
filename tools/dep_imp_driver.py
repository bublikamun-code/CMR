#!/usr/bin/env python3
"""D8-срез 2: драйвер снятия !important батчами с бисекцией.

Контроль каждого состояния: regress.mjs (44 эталона) + extra-shots.mjs
compare (5 доп-кадров непокрытых страниц). Батч, дающий расхождение,
делится пополам; минимальные «несущие» !important возвращаются.

Запуск: python3 tools/dep_imp_driver.py [chunk_size]
Прогресс: tmp/dep-imp-progress.md
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path('.')
CHUNK = int(sys.argv[1]) if len(sys.argv) > 1 else 50
LOG = ROOT / 'tmp' / 'dep-imp-progress.md'

def log(msg):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(msg + '\n')
    print(msg, flush=True)

def sh(cmd, **kw):
    # shell=True: команды фиксированные, собираются из констант этого скрипта
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)  # noqa: S602

def css_positions():
    src = (ROOT / 'css' / 'style.css').read_text(encoding='utf-8')
    out, i = [], src.find('!important')
    while i != -1:
        out.append(i)
        i = src.find('!important', i + 10)
    return out

def check():
    r = sh('cd tools/visual-check && TZ=Europe/Minsk node regress.mjs')
    if r.returncode != 0:
        return False
    e = sh('cd tools/visual-check && node extra-shots.mjs compare')
    return e.returncode == 0

def remove_offsets(offs):
    if not offs:
        return
    sh(f'python3 tools/dep_imp.py remove_offsets {",".join(map(str, sorted(offs, reverse=True)))}')

def restore_offsets(offs):
    if not offs:
        return
    sh(f'python3 tools/dep_imp.py restore_offsets {",".join(map(str, sorted(offs, reverse=True)))}')

def try_range(offsets, lo, hi, depth, tag, shift=0):
    """Пытаемся снять offsets[lo:hi]. Возврат: (разобрано, снято).

    shift — байты, уже снятые ЛЕВЕЕ диапазона в этом раунде: несущие
    возвращаются на место (сдвига не дают), а успешно снятые укорачивают
    файл на 10 байт каждый, поэтому смещения правее надо уменьшать —
    иначе remove/restore уедут мимо токенов и испортят CSS.
    """
    chunk = [o - shift for o in offsets[lo:hi]]
    if not chunk:
        return [], []
    remove_offsets(chunk)
    if check():
        log(f'{tag}: снято {len(chunk)} OK')
        return chunk, chunk
    restore_offsets(chunk)
    if hi - lo == 1:
        log(f'{tag}: НЕСУЩИЙ !important, возвращён (offset {offsets[lo]})')
        return chunk, []
    mid = (lo + hi) // 2
    log(f'{tag}: батч {hi-lo} дал расхождение — бисекция (глубина {depth})')
    resolved1, removed1 = try_range(offsets, lo, mid, depth + 1, tag + '.a', shift)
    # len('!important') == 10
    resolved2, removed2 = try_range(
        offsets, mid, hi, depth + 1, tag + '.b', shift + 10 * len(removed1))
    return resolved1 + resolved2, removed1 + removed2

log(f'=== D8-срез 2: снятие !important, чанк {CHUNK} ===')
total0 = len(css_positions())
total_kept = 0
rounds = 0
skip = 0  # несущих найдено: только они остаются в пересчитанном списке
while True:
    offsets = css_positions()
    if skip >= len(offsets):
        log('дошли до конца: все позиции обработаны')
        break
    if rounds > 60:
        log('лимит раундов — стоп')
        break
    rounds += 1
    lo = skip
    hi = min(skip + CHUNK, len(offsets))
    resolved, removed = try_range(offsets, lo, hi, 0, f'r{rounds}')
    skip += len(resolved) - len(removed)
    total_kept += len(resolved) - len(removed)
    left = len(css_positions())
    log(f'--- раунд {rounds}: разобрано {hi-lo}, снято {len(removed)}, '
        f'несущих {len(resolved) - len(removed)}, в файле осталось {left}')

left = len(css_positions())
log(f'=== ИТОГ: снято {total0 - left}; в файле осталось !important: {left}; несущих: {total_kept} ===')
