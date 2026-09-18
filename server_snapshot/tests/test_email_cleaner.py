"""Тесты email_cleaner — очистка тела письма перед импортом в карточку.

Главный тест здесь — регрессия на затенение имени: `def html_to_text(html)`
делал вызов `html.unescape(h)` обращением к методу строки, поэтому функция
падала с AttributeError на любом непустом HTML. Письма из форм сайта приходят
именно в HTML (это сказано в комментарии самой функции), а исключение
глоталось в `routers/email_parser_router.py` через `except Exception: continue`
— то есть такие письма молча не импортировались в CRM.
"""
import inspect

import email_cleaner


def test_html_to_text_decodes_entities():
    """Регрессия: раньше здесь был AttributeError: 'str' object has no attribute 'unescape'."""
    assert email_cleaner.html_to_text("<p>Привет &amp; мир</p>") == "Привет & мир"


def test_html_to_text_handles_numeric_and_named_entities():
    result = email_cleaner.html_to_text("<p>&lt;цена&gt;&nbsp;100&nbsp;&mdash;&nbsp;200</p>")
    assert "<цена>" in result
    assert "100" in result and "200" in result


def test_html_to_text_empty_input():
    assert email_cleaner.html_to_text("") == ""
    assert email_cleaner.html_to_text(None) == ""


def test_html_to_text_strips_script_and_style():
    body = "<style>.a{color:red}</style><script>var x=1;</script><p>Текст</p>"
    result = email_cleaner.html_to_text(body)
    assert "Текст" in result
    assert "color:red" not in result
    assert "var x=1" not in result


def test_html_to_text_preserves_list_and_line_structure():
    body = "<ul><li>Первый</li><li>Второй</li></ul>"
    lines = [line for line in email_cleaner.html_to_text(body).split("\n") if line.strip()]
    assert lines == ["• Первый", "• Второй"]


def test_html_to_text_br_becomes_newline():
    assert email_cleaner.html_to_text("строка1<br>строка2") == "строка1\nстрока2"


def test_html_to_text_keeps_link_url():
    result = email_cleaner.html_to_text('<a href="https://example.com/order">Заказ</a>')
    assert "Заказ" in result
    assert "https://example.com/order" in result


def test_stdlib_html_module_is_not_shadowed_by_parameter():
    """Гарантия, что имя `html` в модуле снова указывает на stdlib, а не на параметр."""
    assert email_cleaner.html.unescape("&amp;") == "&"
    params = list(inspect.signature(email_cleaner.html_to_text).parameters)
    assert params != ["html"], "параметр html_to_text снова затеняет модуль html"
