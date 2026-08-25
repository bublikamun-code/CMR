"""
Очистка тела письма перед импортом в карточку.
Добавлено 05.08.2026: раньше письмо клалось в описание целиком —
вместе с историей переписки, подписями и трекинговыми ссылками
(рекорд — 42 000 символов на одну карточку).
"""
import re
import html

# Маркеры начала цитируемой истории переписки
_QUOTE_MARKERS = [
    r'^\s*>',                                        # классическое цитирование
    r'^-{2,}\s*Original Message\s*-{2,}',
    r'^-{2,}\s*Пересылаемое сообщение\s*-{2,}',
    r'^\s*On .{5,80}\s+wrote:\s*$',                  # On Mon, 5 Aug ... wrote:
    r'^\s*\d{1,2}\.\d{1,2}\.\d{2,4}[,\s].{0,60}(писал|wrote|пишет)',
    r'^\s*(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье),\s+\d{1,2}\s+\w+\s+\d{4}',
    r'^\s*(От|From|Кому|To|Тема|Subject|Отправлено|Sent)\s*:\s*.+$',
    r'^\s*-{4,}\s*$',
]
_QUOTE_RE = re.compile('|'.join(_QUOTE_MARKERS), re.IGNORECASE | re.MULTILINE)

# Маркеры начала подписи
_SIGNATURE_RE = re.compile(
    r'^\s*(--\s*$|__+\s*$|\*?С уважением,?\*?\s*$|Best regards|Kind regards|'
    r'С наилучшими пожеланиями)',
    re.IGNORECASE | re.MULTILINE
)

_URL_RE = re.compile(r'https?://\S+')
_MAX_LEN = 4000


def _shorten_urls(text: str) -> str:
    """Трекинговые ссылки на 300+ символов заменяем коротким видом."""
    def repl(m):
        url = m.group(0)
        if len(url) <= 90:
            return url
        host = re.sub(r'^https?://([^/]+).*', r'\1', url)
        return f'[ссылка: {host}]'
    return _URL_RE.sub(repl, text)


def clean_email_body(body: str) -> str:
    if not body:
        return ''

    text = body.replace('\r\n', '\n').replace('\r', '\n')

    # 1. Отрезаем историю переписки по первому маркеру цитирования
    m = _QUOTE_RE.search(text)
    quoted = False
    if m and m.start() > 40:          # не режем, если цитата с первой строки
        text = text[:m.start()]
        quoted = True
    elif m and m.start() <= 40:
        # письмо целиком состоит из цитаты — оставляем как есть, но подрежем
        quoted = True

    # 2. Отрезаем подпись
    sig = _SIGNATURE_RE.search(text)
    if sig and sig.start() > 30:
        text = text[:sig.start()]

    # 3. Схлопываем трекинговые ссылки
    text = _shorten_urls(text)

    # 4. Нормализуем пробелы, но сохраняем структуру отступов:
    #    - убираем только хвостовые пробелы в строках,
    #    - схлопываем серии пустых строк до двух,
    #    - не трогаем начальные пробелы (они формируют отступы в списках/таблицах).
    lines = text.split('\n')
    cleaned_lines = []
    blank_count = 0
    for line in lines:
        line = line.rstrip()
        if line == '':
            blank_count += 1
            if blank_count <= 2:
                cleaned_lines.append(line)
        else:
            blank_count = 0
            cleaned_lines.append(line)
    # убираем лишние пустые строки в конце
    while cleaned_lines and cleaned_lines[-1] == '':
        cleaned_lines.pop()
    text = '\n'.join(cleaned_lines)

    # 5. Жёсткий предел длины
    if len(text) > _MAX_LEN:
        text = text[:_MAX_LEN].rstrip() + '\n\n[...текст письма обрезан]'

    if quoted and text:
        text += '\n\n— (история переписки скрыта)'

    return text or 'Текст письма пуст'


def html_to_text(html: str) -> str:
    """Fallback, когда у письма нет text/plain части (частый случай форм с сайта).
    Сохраняем структуру: абзацы, списки, таблицы, отступы."""
    if not html:
        return ''
    h = re.sub(r'(?is)<(script|style|head).*?</\1>', ' ', html)
    h = re.sub(r'(?i)<br\s*/?>', '\n', h)
    # Списки: каждый li — на новой строке с маркером
    h = re.sub(r'(?i)<li[^>]*>', '\n• ', h)
    # Блочные элементы — перевод строки
    h = re.sub(r'(?i)</(p|div|tr|h[1-6]|section|article|blockquote|pre)>', '\n', h)
    h = re.sub(r'(?i)<(p|div|h[1-6]|section|article|blockquote)\b[^>]*>', '\n', h)
    # Таблицы: ячейки через табуляцию
    h = re.sub(r'(?i)</td>', '\t', h)
    h = re.sub(r'(?i)<th[^>]*>', '\n', h)
    # Ссылки: оставляем текст + URL в скобках, если текст не равен URL
    def link_repl(m):
        href = (m.group(2) or '').strip()
        txt = (m.group(1) or '').strip()
        if not txt or txt == href:
            return href or ''
        return f'{txt} ({href})'
    h = re.sub(r'(?i)<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', link_repl, h, flags=re.DOTALL)
    # Остальные теги удаляем
    h = re.sub(r'<[^>]+>', '', h)
    # HTML-сущности — полный decode
    h = html.unescape(h)
    # Нормализуем пробелы, сохраняя структуру отступов
    lines = h.split('\n')
    out = []
    blank = 0
    for line in lines:
        line = line.rstrip()
        if line == '':
            blank += 1
            if blank <= 2:
                out.append(line)
        else:
            blank = 0
            out.append(line)
    while out and out[-1] == '':
        out.pop()
    return '\n'.join(out).strip()


def normalize_subject(subject: str) -> str:
    """Убирает Re:/Fwd:/[номер] для сравнения тем."""
    if not subject:
        return ''
    s = subject.strip()
    for _ in range(6):
        new = re.sub(r'^\s*(RE|Re|FW|Fwd|FWD|ПЕР|Ответ)\s*(\[\d+\])?\s*:\s*', '', s)
        if new == s:
            break
        s = new
    return re.sub(r'\s+', ' ', s).strip().lower()
