"""Приоритет E — метатест атомарности: не более одного commit на запрос.

Это защита от ЦЕЛОГО КЛАССА дефектов, а не от одного. Промежуточный
db.commit() внутри хендлера частично фиксирует запрос: при падении после
него база остаётся в промежуточном состоянии (карточка создана, а журнал
не записан; задача сохранена, а снимки названий — нет), и это невидимо
для юнит-тестов отдельных веток.

Фаза 4 свела четыре таких хендлера к одному commit'у (kanban create_card,
tasks create_task/update_task, writeoff_groups remove_card_from_group) —
заменой промежуточных commit на flush. Этот тест не даёт откатиться:
AST-анализ всех роутеров, у любого хендлера с двумя и более вызовами
db.commit()/session.commit() прогон падает.

Легальные исключения (например, cron-цикл с накопительным commit) —
только через явный ALLOWED ниже, с обязательным комментарием почему два
commit неизбежны. Список на старте пустой.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROUTERS_DIR = ROOT / "routers"

# ("файл", "имя_хендлера") — с комментарием, почему два commit законны.
ALLOWED: list[tuple[str, str]] = [
]


def _iter_handlers():
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_handler = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "router"
                for d in node.decorator_list
            )
            if is_handler:
                yield path.name, node


def _commit_calls(node):
    return [
        sub for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "commit"
        and isinstance(sub.func.value, ast.Name)
        and sub.func.value.id in ("db", "session")
    ]


def test_single_commit_per_handler():
    violators = []
    for fname, handler in _iter_handlers():
        commits = _commit_calls(handler)
        if len(commits) > 1 and (fname, handler.name) not in ALLOWED:
            lines = ", ".join(str(c.lineno) for c in commits)
            violators.append(f"{fname}:{handler.name} — commit() на строках {lines}")
    assert not violators, (
        "Хендлеры с более чем одним commit на запрос (порядок операций и "
        "ответы менять нельзя — сводите через flush, легализуйте через "
        "ALLOWED с комментарием):\n" + "\n".join(violators)
    )
