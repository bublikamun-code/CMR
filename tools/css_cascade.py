#!/usr/bin/env python3
"""Анализ каскада CSS: какие !important избыточны.

Задача. В style.css больше тысячи !important. Часть из них не нужна:
правило и без !important побеждает по специфичности или порядку в файле.
Снимать их вслепую нельзя — где-то !important держит вёрстку. Скрипт
доказывает избыточность для каждого случая отдельно.

Метод. !important на объявлении D (свойство P, правило R) избыточен, если
для КАЖДОГО правила R', которое объявляет P и может совпасть с тем же
элементом, правило R без !important всё равно выигрывает каскад:
(важность, специфичность, порядок в файле).

Ключевая сложность — «может совпасть с тем же элементом». Проверка
односторонняя: возвращаем «не пересекаются» только когда это доказано.
Любая неопределённость трактуется в пользу сохранения !important.

Доказательства непересечения:
  - разные теги          tr    vs div
  - разные id            #a    vs #b
  - конфликт атрибутов   [data-status="true"] vs [data-status="false"]
  - классы не встречаются вместе ни на одном реальном элементе

Последнее требует данных о DOM. Он собирается из HTML-файлов и из
шаблонов в JS. Классы, которыми JS управляет через classList, считаются
добавляемыми к любому элементу — иначе вывод был бы неверным для
состояний вроде .dragging или .active.

Использование:
    python3 tools/css_cascade.py report            # анализ, ничего не менять
    python3 tools/css_cascade.py strip [--apply]   # снять избыточные
    python3 tools/css_cascade.py prune [--apply]   # снять не влияющие на результат
    python3 tools/css_cascade.py winners <out>     # снимок вычисленных стилей
    python3 tools/css_cascade.py diff <a> <b>      # сравнить два снимка

По умолчанию рассматриваются только правила тёмной темы. Флаг --all
распространяет анализ на весь файл. Флаг --no-synth отключает достройку
гипотетических элементов (см. synthesize_elements) для prune и winners.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
CSS_PATH = os.path.join(SITE, "css", "style.css")
DARK_MARKER = '[data-theme="dark"]'

# Запись разрешена только в проект или /tmp: инструмент не должен уметь
# писать произвольные пути, переданные из CLI.
_WRITE_ROOTS = [os.path.realpath(ROOT), os.path.realpath("/tmp")]


def safe_write(path, content, encoding="utf-8"):
    resolved = os.path.realpath(path)
    if not any(resolved == r or resolved.startswith(r + os.sep) for r in _WRITE_ROOTS):
        raise SystemExit(f"Запись запрещена вне разрешённых каталогов: {path}")
    Path(resolved).write_text(content, encoding=encoding)
    return resolved

# ---------------------------------------------------------------- утилиты

def strip_comments(text: str) -> str:
    """Убирает комментарии, сохраняя позиции символов (нумерация строк цела)."""
    out = list(text)
    for m in re.finditer(r"/\*.*?\*/", text, re.S):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


# ---------------------------------------------------------------- CSS

DECL_RE = re.compile(r"([-\w]+)\s*:\s*([^;]*?)(\s*!important)?\s*(?:;|$)", re.I)


class Rule:
    __slots__ = ("selector", "media", "body", "line", "body_start", "order",
                 "variants", "in_scope")

    def __init__(self, selector, media, body, line, body_start, order):
        self.selector = selector
        self.media = media
        self.body = body
        self.line = line
        self.body_start = body_start
        self.order = order
        self.variants = [v.strip() for v in split_selector(selector) if v.strip()]
        # Область анализа: из этих правил берутся кандидаты на снятие
        # !important, и их контексты служат опорой при разбиении на миры.
        # Признак селекторный, не зависит от расстановки !important, поэтому
        # состав корзин одинаков до и после правки.
        self.in_scope = True

    def declarations(self):
        """(свойство, значение, важность, span в пределах body)."""
        for m in DECL_RE.finditer(self.body):
            prop = m.group(1).lower()
            if prop.startswith("--"):
                continue
            yield prop, (m.group(2) or "").strip(), bool(m.group(3)), m.span()

    @property
    def is_dark(self):
        return DARK_MARKER in self.selector


def split_selector(sel: str):
    """Делит по запятым верхнего уровня (запятая бывает внутри :not(), rgb())."""
    parts, depth, buf = [], 0, []
    for ch in sel:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_css(text: str):
    """Разбирает CSS в плоский список правил, отслеживая контекст @media."""
    clean = strip_comments(text)
    n = len(clean)
    rules, at_stack = [], []
    i = seg_start = 0
    order = 0
    while i < n:
        ch = clean[i]
        if ch == "{":
            raw_prelude = clean[seg_start:i]
            prelude = raw_prelude.strip()
            # Строка правила — там, где начинается селектор, а не там, где
            # закончилось предыдущее правило: между ними стоит перевод строки.
            sel_start = seg_start + (len(raw_prelude) - len(raw_prelude.lstrip()))
            if prelude.startswith("@"):
                at_stack.append(prelude)
                i += 1
                seg_start = i
                continue
            depth, j = 1, i + 1
            while j < n and depth:
                if clean[j] == "{":
                    depth += 1
                elif clean[j] == "}":
                    depth -= 1
                j += 1
            rules.append(Rule(
                selector=prelude,
                media=" ".join(at_stack),
                body=clean[i + 1:j - 1],
                line=clean.count("\n", 0, sel_start) + 1,
                body_start=i + 1,
                order=order,
            ))
            order += 1
            i = j
            seg_start = i
            continue
        if ch == "}":
            if at_stack:
                at_stack.pop()
            i += 1
            seg_start = i
            continue
        i += 1
    return rules


# ---------------------------------------------------------------- селекторы

PSEUDO_ELEMENTS = {
    "before", "after", "first-line", "first-letter", "placeholder", "selection",
    "backdrop", "marker",
}
# Состояния: могут появиться на элементе в рантайме.
STATE_PSEUDOS = {
    "hover", "focus", "active", "checked", "disabled", "focus-visible",
    "focus-within", "target", "visited", "link", "indeterminate",
}
# Структурные: зависят от позиции в DOM, не от взаимодействия.
STRUCTURAL_PSEUDOS = {
    "nth-child", "nth-of-type", "first-child", "last-child", "only-child",
    "first-of-type", "last-of-type", "empty", "root",
}


class Compound:
    """Одна простая часть селектора: div.a.b[x=y]:hover"""

    __slots__ = ("tag", "ident", "classes", "attrs", "states", "structural",
                 "pseudo_element", "functional")

    def __init__(self):
        self.tag = None
        self.ident = None
        self.classes = set()
        self.attrs = []           # (имя, оператор, значение)
        self.states = set()
        self.structural = set()
        self.pseudo_element = None
        self.functional = []      # :not(...), :has(...) — не интерпретируем

    def __repr__(self):
        return f"<{self.tag or '*'}#{self.ident} .{sorted(self.classes)}>"


TOKEN_RE = re.compile(r"""
      (?P<pe>::[\w-]+)
    | (?P<pseudo>:[\w-]+)(?P<pargs>\([^()]*(?:\([^()]*\)[^()]*)*\))?
    | \[\s*(?P<attr>[-\w]+)\s*
        (?:(?P<op>[~^$*|]?=)\s*(?P<val>"[^"]*"|'[^']*'|[^\]]*?)\s*)?
        (?:[iIsS]\s*)?\]
    | (?P<cls>\.[-\w]+)
    | (?P<ident>\#[-\w]+)
    | (?P<tag>\*|[a-zA-Z][-\w]*)
""", re.X)


def parse_compound(text: str) -> Compound:
    c = Compound()
    for m in TOKEN_RE.finditer(text):
        if m.group("pe"):
            c.pseudo_element = m.group("pe")[2:]
        elif m.group("pseudo"):
            name = m.group("pseudo")[1:].lower()
            args = m.group("pargs")
            if name in PSEUDO_ELEMENTS and not args:
                c.pseudo_element = name
            elif args:
                c.functional.append((name, args))
                if name in STRUCTURAL_PSEUDOS:
                    c.structural.add(name)
                elif name in STATE_PSEUDOS:
                    c.states.add(name)
            elif name in STATE_PSEUDOS:
                c.states.add(name)
            elif name in STRUCTURAL_PSEUDOS:
                c.structural.add(name)
            else:
                c.functional.append((name, None))
        elif m.group("attr"):
            name, op, val = m.group("attr"), m.group("op"), m.group("val")
            if val is not None:
                val = val.strip().strip("\"'")
            c.attrs.append((name.lower(), op or None, val))
        elif m.group("cls"):
            c.classes.add(m.group("cls")[1:])
        elif m.group("ident"):
            c.ident = m.group("ident")[1:]
        elif m.group("tag"):
            t = m.group("tag")
            if t != "*":
                c.tag = t.lower()
    return c


def parse_selector(sel: str):
    """Возвращает [(комбинатор, Compound)], комбинатор первого — None."""
    sel = sel.strip()
    parts, buf, depth = [], [], 0
    i = 0
    while i < len(sel):
        ch = sel[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if depth == 0 and (ch.isspace() or ch in ">+~"):
            comb = " "
            if ch in ">+~":
                comb = ch
            j = i
            while j < len(sel) and (sel[j].isspace() or sel[j] in ">+~"):
                if sel[j] in ">+~":
                    comb = sel[j]
                j += 1
            if buf:
                parts.append(("".join(buf), comb))
                buf = []
            i = j
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append(("".join(buf), None))

    out, prev_comb = [], None
    for text, comb in parts:
        out.append((prev_comb, parse_compound(text)))
        prev_comb = comb
    return out


def specificity(sel: str):
    """(id, класс, элемент) по CSS Selectors Level 3."""
    ids = cls = el = 0
    for _comb, c in parse_selector(sel):
        if c.ident:
            ids += 1
        cls += len(c.classes) + len(c.attrs) + len(c.states) + len(c.structural)
        for name, args in c.functional:
            if name == "not" and args:
                inner = max((specificity(p) for p in split_selector(args[1:-1])),
                            default=(0, 0, 0))
                ids += inner[0]
                cls += inner[1]
                el += inner[2]
            elif name not in STRUCTURAL_PSEUDOS and name not in STATE_PSEUDOS:
                cls += 1
        if c.tag:
            el += 1
        if c.pseudo_element:
            el += 1
    return (ids, cls, el)


# ---------------------------------------------------------------- DOM

ATTR_RE = re.compile(r"""([-\w:]+)\s*=\s*("([^"]*)"|'([^']*)')""")
TAG_RE = re.compile(r"<([a-zA-Z][-\w]*)((?:[^<>\"']|\"[^\"]*\"|'[^']*')*)>")


class DomEvidence:
    """Что реально встречается в разметке: наборы классов на элементах.

    Хранит две популяции. `elements` — наблюдаемая разметка; только она служит
    доказательной базой для непересечения селекторов и для free_classes.
    `synth` — гипотетические формы, достроенные по парам конкурирующих
    селекторов (см. synthesize_elements). Синтетика расширяет множество
    проверяемых элементов, но никогда не участвует в доказательствах: иначе
    выдуманный элемент «подтверждал» бы сочетание классов, которого нет в
    проекте, и, попав в индекс классов, сузил бы free_classes — то есть
    отменил бы часть проверок вместо того, чтобы добавить новые.
    """

    def __init__(self):
        self.elements = []          # (tag|None, frozenset(classes), {attr: value})
        self.synth = {}             # форма -> свойства, ради которых она нужна
        self.toggled_classes = set()   # классы из classList.add/remove/toggle
        self.known_classes = set()

    def add_element(self, tag, classes, attrs):
        self.elements.append((tag, frozenset(classes), attrs))
        self.known_classes |= set(classes)

    def add_synthetic(self, tag, classes, attrs, prop):
        """Гипотетическая форма. Проверять её нужно только по тому свойству,
        ради конкуренции за которое она построена, иначе на каждую форму
        навешиваются все свойства файла и корзин становится десятки миллионов."""
        key = (tag, frozenset(classes), tuple(sorted(attrs.items())))
        got = self.synth.get(key)
        if got is None:
            got = self.synth[key] = set()
        got.add(prop)

    def all_elements(self):
        """(тег, классы, атрибуты, свойства). props=None — проверять все."""
        for tag, classes, attrs in self.elements:
            yield tag, classes, attrs, None
        for (tag, classes, attrs), props in self.synth.items():
            yield tag, classes, dict(attrs), props

    def finalize(self):
        # Запись с неизвестным тегом совместима с любым тегом и потому глушит
        # доказательства. Если тот же набор классов уже наблюдался с известным
        # тегом, запись без тега лишняя — отбрасываем её.
        known = {classes for tag, classes, _a in self.elements if tag is not None}
        self.elements = [e for e in self.elements
                         if e[0] is not None or e[1] not in known]

        self._index = defaultdict(set)
        for idx, (_tag, classes, _a) in enumerate(self.elements):
            for cl in classes:
                self._index[cl].add(idx)
        # Свободным считаем только класс, который нигде не встречается в
        # разметке или в присваивании className, а лишь навешивается через
        # classList. Такой класс может оказаться на любом элементе, и данных
        # о его сочетаниях у нас нет. Если же класс наблюдался на конкретных
        # элементах, мы знаем его сочетания и пользуемся этим знанием.
        self.free_classes = self.toggled_classes - set(self._index)

    def can_cooccur(self, classes):
        """Может ли один элемент нести все эти классы одновременно."""
        required = set(classes) - self.free_classes
        if len(required) < 2:
            return True
        # Неизвестный класс — нет данных, считаем возможным.
        if any(cl not in self.known_classes for cl in required):
            return True
        sets = [self._index[cl] for cl in required]
        return bool(set.intersection(*sets))

    def elements_with(self, classes, tag=None, ident=None):
        """Элементы, несущие все указанные классы. tag=None у элемента —
        разметка не известна (класс пришёл из className), считаем совместимым."""
        classes = set(classes)
        res = []
        for tg, cls, attrs in self.elements:
            if tag and tg is not None and tg != tag:
                continue
            if ident and attrs.get("id") != ident:
                continue
            if not classes <= cls:
                continue
            res.append((tg, cls, attrs))
        return res


def collect_dom(site_dir: str) -> DomEvidence:
    ev = DomEvidence()
    for name in sorted(os.listdir(site_dir)):
        if not name.endswith(".html"):
            continue
        text = open(os.path.join(site_dir, name), encoding="utf-8").read()
        _scan_markup(text, ev)

    js_dir = os.path.join(site_dir, "js")
    if os.path.isdir(js_dir):
        for name in sorted(os.listdir(js_dir)):
            if not name.endswith(".js"):
                continue
            text = open(os.path.join(js_dir, name), encoding="utf-8").read()
            _scan_markup(text, ev)          # HTML внутри шаблонных строк
            _scan_js_classes(text, ev)
    ev.finalize()
    return ev


def _scan_markup(text: str, ev: DomEvidence):
    for m in TAG_RE.finditer(text):
        tag = m.group(1).lower()
        attrs = {k.lower(): (v3 if v3 is not None else v4)
                 for k, _q, v3, v4 in ATTR_RE.findall(m.group(2))}
        raw = attrs.get("class", "")
        # В шаблонах бывает class="wf-badge ${w.is_active?'wf-active':'wf-inactive'}".
        # Статические части — обязательные классы, литералы внутри ${...} —
        # взаимоисключающие варианты. Каждый вариант даёт свой набор классов.
        base = [c for c in re.sub(r"\$\{[^}]*\}", " ", raw).split()
                if re.fullmatch(r"[-\w]+", c)]
        variants = []
        for interp in re.findall(r"\$\{[^}]*\}", raw):
            for lit in re.findall(r"[\"'`]([^\"'`]*)[\"'`]", interp):
                extra = [c for c in lit.split() if re.fullmatch(r"[-\w]+", c)]
                if extra:
                    variants.append(extra)
                    ev.toggled_classes.update(extra)
                    ev.known_classes.update(extra)
        for extra in variants:
            ev.add_element(tag, base + extra, attrs)
        ev.add_element(tag, base, attrs)


CLASSLIST_OP_RE = re.compile(
    r"classList\s*\.\s*(?:add|remove|toggle|contains|replace)\s*\(([^)]*)\)")
# className = 'kanban-card writeoff-card' — свидетельство элемента, несущего
# именно этот набор классов. Тег из присваивания не виден, остаётся None.
CLASSNAME_ASSIGN_RE = re.compile(
    r"""\.className\s*=\s*([`"'])(.*?)\1""", re.S)


CREATE_EL_RE = re.compile(
    r"""(?:const|let|var)\s+([\w$]+)\s*=\s*document\s*\.\s*createElement\s*\(\s*["'`]([a-zA-Z][-\w]*)["'`]""")


def _scan_js_classes(text: str, ev: DomEvidence):
    # createElement фиксирует тег для переменной: без этого элементы,
    # собранные в JS, имеют неизвестный тег и не отличаются от tr, input и др.
    var_tags = {var: tag.lower() for var, tag in CREATE_EL_RE.findall(text)}

    for m in re.finditer(r"([\w$]+)\.className\s*=\s*([`\"'])(.*?)\2", text, re.S):
        var, literal = m.group(1), m.group(3)
        base = re.sub(r"\$\{[^}]*\}", " ", literal)
        classes = [c for c in base.split() if re.fullmatch(r"[-\w]+", c)]
        if classes:
            ev.add_element(var_tags.get(var), classes, {})

    # Выражения className = 'a' + (cond ? ' b' : '') — строковые литералы
    # правой части фиксируем как возможные добавки.
    for m in re.finditer(r"\.className\s*=\s*([^;\n]+)", text):
        for lit in re.findall(r"[\"'`]([^\"'`]*)[\"'`]", m.group(1)):
            for cl in lit.split():
                if re.fullmatch(r"[-\w]+", cl):
                    ev.toggled_classes.add(cl)
                    ev.known_classes.add(cl)

    for m in CLASSLIST_OP_RE.finditer(text):
        for lit in re.findall(r"[\"'`]([^\"'`]*)[\"'`]", m.group(1)):
            for cl in lit.split():
                if re.fullmatch(r"[-\w]+", cl):
                    ev.toggled_classes.add(cl)
                    ev.known_classes.add(cl)


# ---------------------------------------------------------------- пересечение

ATTR_EXACT_OPS = {None, "="}


def attrs_conflict(a_attrs, b_attrs):
    """Взаимоисключающие требования к одному атрибуту."""
    a_map = defaultdict(set)
    for name, op, val in a_attrs:
        if op in ATTR_EXACT_OPS and val is not None:
            a_map[name].add(val)
    for name, op, val in b_attrs:
        if op in ATTR_EXACT_OPS and val is not None:
            if name in a_map and val not in a_map[name]:
                return True
    for name, vals in a_map.items():
        if len(vals) > 1:
            return True
    return False


def compounds_disjoint(a: Compound, b: Compound, ev: DomEvidence) -> bool:
    """True только если доказано, что оба не совпадут с одним элементом."""
    if a.tag and b.tag and a.tag != b.tag:
        return True
    if a.ident and b.ident and a.ident != b.ident:
        return True
    if attrs_conflict(a.attrs, b.attrs):
        return True
    if a.pseudo_element != b.pseudo_element:
        # ::before и сам элемент — разные цели отрисовки.
        return True
    if not ev.can_cooccur(a.classes | b.classes):
        return True

    need = (a.classes | b.classes) - ev.free_classes
    ident = a.ident or b.ident
    tag = a.tag or b.tag

    # Элемент с этим id известен из разметки: проверяем, подходит ли он под
    # требования обоих селекторов. Если нет — правила нацелены на разные узлы.
    if ident:
        cands = [(tg, cls, at) for tg, cls, at in ev.elements
                 if at.get("id") == ident]
        if cands:
            fits = False
            for tg, cls, _at in cands:
                if tag and tg is not None and tg != tag:
                    continue
                if not need <= (cls | ev.free_classes):
                    continue
                fits = True
                break
            if not fits:
                return True

    # Тег известен только у одного из селекторов. Элемент обязан иметь этот тег
    # и нести все требуемые классы. Проверяем по тем классам, о которых у нас
    # есть данные: неизвестный класс не отменяет того, что известный класс
    # никогда не встречается на элементе с таким тегом.
    known_need = {cl for cl in need if cl in ev.known_classes}
    if tag and known_need and not ev.elements_with(known_need, tag=tag):
        return True
    return False


def selectors_disjoint(sel_a: str, sel_b: str, ev: DomEvidence) -> bool:
    """Сравниваем субъекты (правые части) — на них действует объявление."""
    pa, pb = parse_selector(sel_a), parse_selector(sel_b)
    if not pa or not pb:
        return False
    return compounds_disjoint(pa[-1][1], pb[-1][1], ev)


def states_of(sel: str):
    parts = parse_selector(sel)
    return parts[-1][1].states if parts else set()


# ------------------------------------------------- контекст предков
#
# Селектор действует на субъект (правую часть), но только если его предки
# нашлись выше по дереву. Без этого условия правило вида
# `[data-theme="dark"] .writeoff-cards > div` считалось бы применимым к любому
# div — со специфичностью (0,2,1) оно перебивало бы `.kanban-card` на каждой
# карточке и маскировало настоящего победителя. Тогда порча настоящего
# победителя не давала расхождений, а снятие его !important выглядело
# безопасным. Поэтому требования к предкам входят в модель.

COMPOUND_ANY = ("html", "body", "head", "main")


def compound_key(c: Compound) -> str:
    """Канонический вид простого селектора — ключ для контекста."""
    bits = [c.tag or "*"]
    if c.ident:
        bits.append("#" + c.ident)
    bits += ["." + cl for cl in sorted(c.classes)]
    bits += [f"[{n}{op or ''}{val if val is not None else ''}]"
             for n, op, val in sorted(c.attrs, key=lambda t: (t[0], t[2] or ""))]
    bits += [":" + s for s in sorted(c.states | c.structural)]
    return "".join(bits)


def is_ambient(c: Compound) -> bool:
    """Предок, который есть всегда: тема, html/body, чистый универсальный."""
    if any(n == "data-theme" for n, _op, _v in c.attrs):
        return True
    if c.ident or c.classes:
        return False
    if c.attrs or c.states or c.structural:
        return False
    return c.tag is None or c.tag in COMPOUND_ANY


_CTX_COMPOUNDS = {}


def context_of(sel: str):
    """Требования к предкам: frozenset канонических ключей."""
    keys = set()
    for _comb, c in parse_selector(sel)[:-1]:
        if is_ambient(c):
            continue
        k = compound_key(c)
        _CTX_COMPOUNDS.setdefault(k, c)
        keys.add(k)
    return frozenset(keys)


def compound_satisfies(req: Compound, have: Compound) -> bool:
    """Подходит ли предок `have` под требование `req`."""
    if req.tag and have.tag and req.tag != have.tag:
        return False
    if req.tag and not have.tag:
        return False
    if req.ident and req.ident != have.ident:
        return False
    if not req.classes <= have.classes:
        return False
    if not (req.states | req.structural) <= (have.states | have.structural):
        return False
    have_attrs = {n: v for n, op, v in have.attrs if op in ATTR_EXACT_OPS}
    for name, op, val in req.attrs:
        if name not in have_attrs:
            return False
        if op in ATTR_EXACT_OPS and val is not None and have_attrs[name] != val:
            return False
    return True


_CTX_OK = {}


def context_holds(need: frozenset, active: frozenset) -> bool:
    """Выполнены ли все требования к предкам в данном контексте элемента."""
    if not need:
        return True
    memo_key = (need, active)
    got = _CTX_OK.get(memo_key)
    if got is None:
        got = True
        for req_key in need:
            if req_key in active:
                continue
            req = _CTX_COMPOUNDS[req_key]
            if not any(compound_satisfies(req, _CTX_COMPOUNDS[h]) for h in active):
                got = False
                break
        _CTX_OK[memo_key] = got
    return got


# ---------------------------------------------------------------- анализ

class Candidate:
    __slots__ = ("rule", "variant", "prop", "value", "span", "blockers")

    def __init__(self, rule, variant, prop, value, span):
        self.rule = rule
        self.variant = variant
        self.prop = prop
        self.value = value
        self.span = span
        self.blockers = []


def wins_without_important(r_sel, r_rule, o_sel, o_rule, o_important):
    """Побеждает ли r без !important правило o."""
    if o_important:
        return False
    rs, os_ = specificity(r_sel), specificity(o_sel)
    if rs > os_:
        return True
    if rs < os_:
        return False
    return r_rule.order > o_rule.order


def analyse(rules, ev, only_dark=True):
    by_prop = defaultdict(list)
    for rule in rules:
        for prop, value, important, span in rule.declarations():
            for variant in rule.variants:
                by_prop[prop].append((rule, variant, important, value))

    candidates = []
    for rule in rules:
        if only_dark and not rule.is_dark:
            continue
        for prop, value, important, span in rule.declarations():
            if not important:
                continue
            for variant in rule.variants:
                cand = Candidate(rule, variant, prop, value, span)
                r_states = states_of(variant)
                for o_rule, o_variant, o_important, o_value in by_prop[prop]:
                    if o_rule is rule:
                        continue
                    # Разный media-контекст: не обязательно активны вместе,
                    # но и исключить нельзя — сравниваем только одинаковые.
                    if o_rule.media != rule.media and o_rule.media and rule.media:
                        continue
                    if selectors_disjoint(variant, o_variant, ev):
                        continue
                    # Правило с состоянием применяется поверх нашего только
                    # когда состояние активно; наше правило без состояния
                    # проигрывает ему в этот момент — это настоящий конфликт.
                    if wins_without_important(variant, rule, o_variant, o_rule,
                                              o_important):
                        continue
                    cand.blockers.append((o_rule, o_variant, o_important, o_value))
                candidates.append(cand)

    removable, kept = [], []
    for c in candidates:
        (kept if c.blockers else removable).append(c)
    return removable, kept


# ---------------------------------------------------------------- отчёт

def cmd_report(only_dark=True):
    text = open(CSS_PATH, encoding="utf-8").read()
    rules = parse_css(text)
    ev = collect_dom(SITE)
    removable, kept = analyse(rules, ev, only_dark=only_dark)

    scope = "тёмной темы" if only_dark else "всего файла"
    print(f"Правил разобрано: {len(rules)}")
    print(f"Элементов в DOM-модели: {len(ev.elements)}, "
          f"классов известно: {len(ev.known_classes)}, "
          f"свободных: {len(ev.free_classes)}")
    print()
    print(f"!important в правилах {scope}: {len(removable) + len(kept)}")
    print(f"  избыточны:   {len(removable)}")
    print(f"  нужны:       {len(kept)}")
    print()
    if removable:
        print("=== Свойства среди избыточных ===")
        for p, n in Counter(c.prop for c in removable).most_common(15):
            print(f"  {n:4}  {p}")
        print()
        print("=== Примеры избыточных ===")
        for c in removable[:8]:
            print(f"  L{c.rule.line:5} {c.variant[:60]}")
            print(f"         {c.prop}: {c.value}")
    if kept:
        print()
        print("=== Примеры сохраняемых (кто мешает) ===")
        for c in kept[:5]:
            print(f"  L{c.rule.line:5} {c.variant[:58]}  {c.prop}")
            for o_rule, o_variant, o_imp, o_value in c.blockers[:2]:
                mark = "!" if o_imp else " "
                print(f"        [{mark}] L{o_rule.line} {o_variant[:50]} -> {o_value[:28]}")
    return removable, kept


def cmd_strip(apply=False, only_dark=True):
    text = open(CSS_PATH, encoding="utf-8").read()
    rules = parse_css(text)
    ev = collect_dom(SITE)
    removable, kept = analyse(rules, ev, only_dark=only_dark)

    # Абсолютные позиции "!important" в исходном тексте.
    cuts = set()
    for c in removable:
        body_off = c.rule.body_start
        seg = text[body_off + c.span[0]: body_off + c.span[1]]
        m = re.search(r"\s*!important", seg, re.I)
        if not m:
            continue
        cuts.add((body_off + c.span[0] + m.start(), body_off + c.span[0] + m.end()))

    out = text
    for start, end in sorted(cuts, reverse=True):
        out = out[:start] + out[end:]

    print(f"Найдено избыточных: {len(removable)}, вырезано вхождений: {len(cuts)}")
    print(f"!important до: {text.count('!important')}, после: {out.count('!important')}")
    if apply:
        safe_write(CSS_PATH, out)
        print(f"Записано: {CSS_PATH}")
    else:
        safe_write("/tmp/style.stripped.css", out)
        print("Черновик: /tmp/style.stripped.css (для применения добавьте --apply)")
    return len(cuts)


def set_scope(rules, only_dark=True):
    """Помечает правила, из которых берутся кандидаты и опорные контексты."""
    for rule in rules:
        rule.in_scope = rule.is_dark if only_dark else True
    return rules


def build_buckets(rules, ev):
    """Раскладывает объявления по «корзинам» (элемент, свойство, состояние).

    Внутри корзины конкурируют только те объявления, которые борются за одно
    и то же вычисленное значение. Индекс объявления сохраняется, чтобы можно
    было выключить конкретный !important и пересчитать только задетые корзины.
    """
    decls = []
    for rule in rules:
        parsed = {v: parse_selector(v)[-1][1] for v in rule.variants}
        specs = {v: specificity(v) for v in rule.variants}
        for prop, value, important, span in rule.declarations():
            for variant in rule.variants:
                decls.append({
                    "subj": parsed[variant], "spec": specs[variant],
                    "order": rule.order, "media": rule.media, "prop": prop,
                    "value": value, "important": important,
                    "rule": rule, "variant": variant, "span": span,
                })

    # Перебирать все объявления для каждой формы элемента слишком дорого
    # (18k форм x 5900 объявлений). Индексируем по самому редкому требованию
    # субъекта: тег, id или класс. Объявление без всяких требований (например
    # `*` или `:root`) попадает в общий список.
    by_ident = defaultdict(list)
    by_class = defaultdict(list)
    by_tag = defaultdict(list)
    universal = []
    class_freq = Counter()
    for d in decls:
        for cl in d["subj"].classes:
            class_freq[cl] += 1
    for idx, d in enumerate(decls):
        subj = d["subj"]
        if subj.ident:
            by_ident[subj.ident].append(idx)
        elif subj.classes:
            pick = min(subj.classes, key=lambda c: (class_freq[c], c))
            by_class[pick].append(idx)
        elif subj.tag:
            by_tag[subj.tag].append(idx)
        else:
            universal.append(idx)

    groups = defaultdict(list)
    seen = set()
    for tag, classes, attrs, props in ev.all_elements():
        ident = attrs.get("id")
        # Атрибуты входят в ключ: две формы, различающиеся только
        # data-status, — разные элементы, и сливать их нельзя.
        rest = ";".join(f"{k}={v}" for k, v in sorted(attrs.items())
                        if k != "id")
        elem_key = "|".join([tag or "?", ident or "",
                             ",".join(sorted(classes)), rest])
        if (elem_key, props is None) in seen:
            continue
        seen.add((elem_key, props is None))

        # Класс субъекта может отсутствовать в разметке и навешиваться из JS
        # (free_classes) — такие объявления тоже кандидаты, поэтому в выборку
        # входят и они.
        cand = list(universal)
        if ident:
            cand += by_ident[ident]
        if tag is not None:
            cand += by_tag[tag]
        else:
            for lst in by_tag.values():
                cand += lst
        for cl in classes:
            cand += by_class[cl]
        for cl in ev.free_classes:
            if cl not in classes:
                cand += by_class[cl]

        for idx in cand:
            d = decls[idx]
            if props is not None and d["prop"] not in props:
                continue
            subj = d["subj"]
            if subj.tag and tag is not None and subj.tag != tag:
                continue
            if subj.ident and subj.ident != ident:
                continue
            if not subj.classes <= classes:
                if not (subj.classes - classes) <= ev.free_classes:
                    continue
            bad = False
            for name, op, val in subj.attrs:
                if op in ATTR_EXACT_OPS and val is not None:
                    have = attrs.get(name)
                    if have is not None and have != val:
                        bad = True
                        break
            if bad:
                continue
            state = ",".join(sorted(subj.states | subj.structural |
                                    (subj.classes - classes)))
            key = (elem_key, d["prop"], state, subj.pseudo_element or "",
                   d["media"])
            groups[key].append(idx)

    # Одна группа — один элемент и одно свойство, но разные объявления требуют
    # разных предков и потому не обязаны действовать одновременно. Разбиваем
    # группу на «миры»: набор предков, который считаем активным.
    #
    # Мир — объединение пары контекстов, где первый берётся из объявлений в
    # области анализа. Пары достаточно: чтобы снятие !important с объявления D
    # осталось незамеченным, нужен мир, где D побеждает, а после снятия
    # побеждает X с другим значением; такой мир сводится к паре (контекст D,
    # контекст X), потому что добавление третьего контекста только повышает
    # планку и действует одинаково до и после снятия.
    #
    # Состав мира зависит лишь от селекторов, не от расстановки !important,
    # поэтому набор корзин одинаков до и после правки и снимки сравнимы.
    needs = {}
    buckets = {}
    for key, idxs in groups.items():
        ctxs = set()
        for i in idxs:
            need = needs.get(i)
            if need is None:
                need = needs[i] = context_of(decls[i]["variant"])
            ctxs.add(need)
        if ctxs == {EMPTY_CTX}:
            buckets[key + ("",)] = idxs
            continue
        for world, members in world_variants(decls, idxs, needs, ctxs):
            buckets[key + (world,)] = members
    return decls, buckets, groups, needs


EMPTY_CTX = frozenset()


def world_variants(decls, idxs, needs, ctxs, anchors=(EMPTY_CTX,)):
    """Составы группы в разных предположениях о предках.

    Мир — объединение опорного контекста и одного из контекстов группы. По
    умолчанию опора одна (пустая), то есть миры — это отдельные контексты:
    столько корзин помещается в снимок. Для доказательства безопасности
    снятия конкретного !important опорой служит контекст самого кандидата,
    и тогда перебираются пары (см. safe_to_disable).

    Состав мира зависит только от селекторов, не от расстановки !important,
    поэтому набор корзин одинаков до и после правки и снимки сравнимы.
    """
    bits = [needs[i] for i in idxs]
    base = {}
    for c in set(ctxs) | set(anchors):
        m = 0
        for bit, need in enumerate(bits):
            if context_holds(need, c):
                m |= 1 << bit
        base[c] = m
    multi = [(bit, need) for bit, need in enumerate(bits) if len(need) > 1]

    emitted = {}
    for a in anchors:
        for b in ctxs:
            mask = base[a] | base[b]
            world = a | b
            # Требование из нескольких предков может выполниться только
            # объединением, а не каждой половиной по отдельности.
            for bit, need in multi:
                if not mask >> bit & 1 and context_holds(need, world):
                    mask |= 1 << bit
            if not mask or mask in emitted:
                continue
            emitted[mask] = world
    out = []
    for mask, world in emitted.items():
        out.append(("&".join(sorted(world)),
                    [i for bit, i in enumerate(idxs) if mask >> bit & 1]))
    return out


def winner_of(decls, indices, disabled=frozenset()):
    best = None
    for idx in indices:
        d = decls[idx]
        imp = d["important"] and idx not in disabled
        rank = (imp, d["spec"], d["order"])
        if best is None or rank >= best[0]:
            best = (rank, d["value"])
    return best[1] if best else None


def safe_to_disable(decls, groups, needs, targets, by_group, disabled):
    """Меняет ли выключение `targets` победителя хоть в одной корзине.

    Проверяются только группы, куда входят цели, зато в каждой перебираются
    миры с опорой на контекст цели. Опора важна: правило вида
    `.writeoff-cards > div` конкурирует с `.kanban-card` лишь тогда, когда
    предок действительно есть, а мир из одного контекста этого не выражает —
    в нём либо предка нет и правило выпадает, либо оно есть без второго
    участника. Пара (контекст цели, контекст конкурента) даёт именно тот мир,
    где оба участника живы одновременно.

    Возвращает первое найденное расхождение или None.
    """
    seen_groups = set()
    for t in targets:
        for gkey in by_group.get(t, ()):
            if gkey in seen_groups:
                continue
            seen_groups.add(gkey)
            idxs = groups[gkey]
            ctxs = {needs[i] for i in idxs}
            anchors = {needs[t] for t in targets if t in idxs} | {EMPTY_CTX}
            for world, members in world_variants(decls, idxs, needs, ctxs,
                                                 anchors=sorted(anchors, key=sorted)):
                before = winner_of(decls, members)
                after = winner_of(decls, members, disabled)
                if before != after:
                    return (gkey, world, before, after)
    return None


def synthesize_elements(rules, ev, only_dark=True):
    """Достраивает гипотетические элементы для пар конкурирующих селекторов.

    Зачем. Проверка победителей идёт по формам элементов, вычитанным из
    разметки, — их 1765. Но классы в проекте часто собираются в рантайме:
    store-${card.store_location}, wf-status-${r.status}, ${roleClass},
    ${statusClass}, ${widthClass}. Такой формы в разметке нет, значит нет и
    корзины, значит !important на ней выглядит «ни на что не влияющим» просто
    потому, что его не на чем проверить.

    Что делаем. Для каждого правила из области анализа и каждого конкурента по
    тому же свойству, который не доказано непересекается, строим элемент,
    несущий требования обоих субъектов сразу: тег, объединение классов,
    точные атрибуты, id. Это худший случай — момент, когда оба правила
    действительно борются за один элемент. Если такой элемент невозможен,
    compounds_disjoint отсёк пару раньше.

    Синтетика складывается отдельно от разметки (add_synthetic) и не влияет на
    доказательства непересечения: выдуманный элемент не может служить
    свидетельством того, что классы сочетаются.

    Набор синтетики не зависит от того, где стоят !important, — только от
    селекторов и имён свойств. Это важно: снимок winners до и после правки
    обязан считаться по одному и тому же множеству элементов, иначе diff
    покажет расхождения на пустом месте.
    """
    subjects = []
    for rule in rules:
        for v in rule.variants:
            parts = parse_selector(v)
            if parts:
                subjects.append((rule, parts[-1][1]))

    by_prop = defaultdict(list)
    for rule, subj in subjects:
        for prop, _val, _imp, _span in rule.declarations():
            by_prop[prop].append((rule, subj))

    # Пар — десятки тысяч, а различных субъектов меньше двух тысяч; результат
    # доказательства зависит только от пары субъектов, поэтому кэшируем.
    memo = {}

    def disjoint(a, b):
        k = (id(a), id(b))
        got = memo.get(k)
        if got is None:
            got = memo[k] = compounds_disjoint(a, b, ev)
        return got

    # Формы, уже присутствующие в разметке, повторять незачем.
    real_shapes = {(tg, cls, tuple(sorted(at.items())))
                   for tg, cls, at in ev.elements}
    for rule, subj in subjects:
        if only_dark and not rule.is_dark:
            continue
        for prop, _val, _imp, _span in rule.declarations():
            for o_rule, o_subj in by_prop[prop]:
                if o_rule is rule:
                    continue
                if disjoint(subj, o_subj):
                    continue
                tag = subj.tag or o_subj.tag
                classes = subj.classes | o_subj.classes
                attrs = {}
                ident = subj.ident or o_subj.ident
                if ident:
                    attrs["id"] = ident
                for name, op, val in list(subj.attrs) + list(o_subj.attrs):
                    if op in ATTR_EXACT_OPS and val is not None:
                        attrs[name] = val
                if (tag, frozenset(classes),
                        tuple(sorted(attrs.items()))) in real_shapes:
                    continue
                ev.add_synthetic(tag, classes, attrs, prop)
    return len(ev.synth)


def cmd_prune(apply=False, only_dark=True, synth=True):
    """Снимает каждый !important, который не меняет ни одного победителя.

    Проверка эмпирическая: для каждого кандидата пересчитываются только те
    корзины, куда он входит. Затем весь отобранный набор снимается разом и
    результат сверяется целиком — так ловятся взаимные зависимости.
    """
    text = open(CSS_PATH, encoding="utf-8").read()
    rules = parse_css(text)
    ev = collect_dom(SITE)
    set_scope(rules, only_dark)
    if synth:
        added = synthesize_elements(rules, ev, only_dark=only_dark)
        print(f"Форм из разметки: {len(ev.elements)}, достроено синтетикой: {added}")
    decls, buckets, groups, needs = build_buckets(rules, ev)
    print(f"Объявлений: {len(decls)}, групп: {len(groups)}, корзин: {len(buckets)}")

    by_group = defaultdict(list)
    for gkey, idxs in groups.items():
        for idx in idxs:
            by_group[idx].append(gkey)

    # Единица снятия — физическое объявление в тексте, а не запись в модели.
    # В правиле с несколькими селекторами одно объявление даёт несколько
    # записей с общим span, и вырезание `!important` гасит их все сразу.
    # Проверять их надо тоже вместе, иначе безопасность одного варианта
    # выдаётся за безопасность остальных.
    phys = defaultdict(list)
    for i, d in enumerate(decls):
        if d["important"] and (not only_dark or d["rule"].is_dark):
            phys[(id(d["rule"]), d["span"])].append(i)
    candidates = list(phys.values())
    print(f"Кандидатов !important: {sum(len(v) for v in candidates)} записей, "
          f"{len(candidates)} объявлений в тексте")

    safe = []
    for group in candidates:
        off = frozenset(group)
        if safe_to_disable(decls, groups, needs, group, by_group, off) is None:
            safe.append(group)

    print(f"Не влияют на вычисленные значения по одному: {len(safe)}")

    # Поодиночке — не значит вместе. Типичный случай: два !important с одним и
    # тем же значением в одной корзине. Снять любой из них по отдельности
    # безопасно, потому что второй всё ещё перебивает конкурента; снять оба —
    # и побеждает третье правило с другим значением.
    #
    # Поэтому набор набирается по одному: кандидат принимается, только если он
    # не ломает ни одну свою корзину при уже принятых. Так сохраняется по
    # одному представителю из каждой такой пары, тогда как отбрасывание всех
    # участников регрессии выкидывало бы обоих.
    accepted = set()
    kept = []
    for group in safe:
        trial = frozenset(accepted | set(group))
        if safe_to_disable(decls, groups, needs, group, by_group,
                           trial) is None:
            accepted = set(trial)
            kept.append(group)
    safe = kept
    print(f"Безопасны при совместном снятии: {len(safe)}")

    if not safe:
        return 0

    # Итоговый контроль: пересчёт по всем задетым группам во всех парных мирах
    # при снятом наборе целиком.
    off_all = frozenset(accepted)
    bad = safe_to_disable(decls, groups, needs, off_all, by_group, off_all)
    print(f"Полная сверка: {'расхождений нет' if bad is None else bad}")
    if bad is not None:
        print("Набор небезопасен, ничего не снимаю")
        return 0

    props = Counter(decls[g[0]]["prop"] for g in safe)
    print("\n=== Свойства среди снимаемых ===")
    for p, n in props.most_common(12):
        print(f"  {n:4}  {p}")

    cuts = set()
    for group in safe:
        d = decls[group[0]]
        base = d["rule"].body_start + d["span"][0]
        seg = text[base: d["rule"].body_start + d["span"][1]]
        m = re.search(r"\s*!important", seg, re.I)
        if m:
            cuts.add((base + m.start(), base + m.end()))

    out = text
    for start, end in sorted(cuts, reverse=True):
        out = out[:start] + out[end:]

    print(f"\n!important до: {text.count('!important')}, после: {out.count('!important')}")
    target = CSS_PATH if apply else "/tmp/style.pruned.css"
    safe_write(target, out)
    print(f"Записано: {target}")
    return len(cuts)


# ------------------------------------------------- снимок вычисленных стилей

def cmd_winners(css_path, out_path, only_dark=True, synth=True):
    """Для каждого элемента и свойства — победитель каскада.

    Снимок берётся до и после правок: расхождение означает регрессию.
    Состояния учитываются: элемент оценивается для каждого набора состояний,
    который встречается в конкурирующих правилах.

    Считается по тем же корзинам, что и prune, чтобы проверка и гарантия
    опирались на одну модель, а не на два похожих куска кода.
    """
    text = open(css_path, encoding="utf-8").read()
    rules = parse_css(text)
    ev = collect_dom(SITE)
    set_scope(rules, only_dark)
    if synth:
        synthesize_elements(rules, ev, only_dark=only_dark)
    decls, buckets, _groups, _needs = build_buckets(rules, ev)

    snapshot = defaultdict(dict)
    for (elem_key, prop, state, pe, media, world), idxs in buckets.items():
        snapshot[elem_key][f"{prop}@{state}@{pe}@{media}@{world}"] = \
            winner_of(decls, idxs)

    safe_write(out_path, json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=0))
    total = sum(len(v) for v in snapshot.values())
    print(f"Снимок: {len(snapshot)} элементов, {total} вычисленных значений -> {out_path}")


def cmd_diff(path_a, path_b):
    a = json.load(open(path_a))
    b = json.load(open(path_b))
    keys = set(a) | set(b)
    diffs = []
    for k in sorted(keys):
        va, vb = a.get(k, {}), b.get(k, {})
        for prop in sorted(set(va) | set(vb)):
            if va.get(prop) != vb.get(prop):
                diffs.append((k, prop, va.get(prop), vb.get(prop)))
    print(f"Расхождений: {len(diffs)}")
    for k, prop, x, y in diffs[:40]:
        print(f"  {k}")
        print(f"    {prop}: {x!r} -> {y!r}")
    return len(diffs)


def _cli_path(p):
    """CLI-пути валидируются сразу при разборе аргументов: запись разрешена
    только в проект или /tmp (см. safe_write)."""
    resolved = Path(p).resolve()
    for root in _WRITE_ROOTS:
        if resolved == Path(root) or resolved.is_relative_to(root):
            return resolved
    raise SystemExit(f"Путь вне разрешённых каталогов: {p}")


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    cmd = args[0] if args else "report"
    only_dark = "--all" not in flags
    synth = "--no-synth" not in flags

    if cmd == "report":
        cmd_report(only_dark=only_dark)
    elif cmd == "strip":
        cmd_strip(apply="--apply" in flags, only_dark=only_dark)
    elif cmd == "prune":
        cmd_prune(apply="--apply" in flags, only_dark=only_dark, synth=synth)
    elif cmd == "winners":
        css = _cli_path(args[2]) if len(args) > 2 else CSS_PATH
        cmd_winners(css, str(_cli_path(args[1])), only_dark=only_dark, synth=synth)
    elif cmd == "diff":
        sys.exit(1 if cmd_diff(args[1], args[2]) else 0)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
