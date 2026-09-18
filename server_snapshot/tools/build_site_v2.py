#!/usr/bin/env python3
"""Сборка site-v2 из прототипа tools/mockups (единый источник правды).

Генерирует развёртываемое приложение site-v2/:
- index.html: базовые стили вынесены в css/shell-v2-base.css, вместо демо-модулей
  подключается загрузчик js/v2/boot.js (api.js + boot.js), добавляется оверлей входа;
- js/ и css/ модули копируются из прототипа как есть;
- js/v2/api.js и js/v2/boot.js — адаптер источника данных (DEMO/API) и авторизация.

Запуск: python3 tools/build_site_v2.py
"""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCKUPS = ROOT / "tools" / "mockups"
OUT = ROOT / "site-v2"

API_JS = (ROOT / "tools" / "v2-api-template.js").read_text(encoding="utf-8")

BOOT_JS = (ROOT / "tools" / "v2-boot-template.js").read_text(encoding="utf-8")

V2_EXTRA_CSS = r"""/* site-v2: вход и статус источника (нет в прототипе) */
.login-overlay {
    position: fixed; inset: 0; background: var(--bg);
    display: flex; align-items: center; justify-content: center; z-index: 200;
}
.login-box {
    width: 360px; max-width: 92vw; background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--r-xl);
    box-shadow: var(--sh-2); padding: var(--s6); display: flex; flex-direction: column; gap: var(--s3);
}
.login-box h1 { font-size: var(--font-h1); letter-spacing: -.3px; }
.login-box .login-sub { color: var(--muted); font-size: var(--font-ui); margin-top: -6px; }
.login-box label { display: flex; flex-direction: column; gap: 4px; font-size: var(--font-ui); font-weight: 600; color: var(--muted); }
.login-box input[type="text"], .login-box input[type="password"] {
    font: inherit; font-size: var(--font-body); color: var(--text);
    background: var(--surface-2); border: 1px solid var(--border-strong);
    border-radius: var(--r-md); padding: 9px 11px; width: 100%;
}
.login-remember { display: flex; align-items: center; gap: 8px; min-height: 24px; font-size: var(--font-ui); color: var(--muted); cursor: pointer; }
.login-remember input { accent-color: var(--accent); }
.login-error { color: var(--danger); font-size: var(--font-ui); font-weight: 600; min-height: 1em; }
/* Фидбек 18.09: системное синее выделение текста — спокойный акцент */
::selection { background: rgba(99, 102, 241, .18); }
[data-theme="dark"] ::selection { background: rgba(129, 140, 248, .32); }
[hidden] { display: none !important; }
"""

HTML_TRANSFORMS = [
    # Демо-модуль данных и остальные скрипты подключает boot.js (см. regex ниже);
    # select.js меняем на пару адаптер+загрузчик
    ('<script src="./shell-v2-select.js" defer></script>', '<script src="js/v2/api.js"></script>\n<script src="js/v2/boot.js"></script>'),
    ('    </header>', '      <div class="topbar-right"><span id="v2-user" class="row" style="gap:8px"></span><button type="button" class="btn btn-ghost btn-sm" id="v2-bell" title="Уведомления" aria-label="Уведомления">🔔<span id="v2-bell-count" class="pill warn" hidden></span></button><div id="v2-notif-panel" class="card" hidden style="position:fixed;top:60px;right:16px;z-index:120;width:360px;max-height:60vh;overflow:auto"></div></div>\n    </header>'),
    # Оверлей входа перед закрытием body
    ('</body>', """<div class="login-overlay" id="login-overlay" hidden>
  <form class="login-box" id="login-form">
    <h1>Свет в доме · CRM</h1>
    <p class="login-sub">Новый интерфейс (v2) · вход в существующий аккаунт</p>
    <label for="login-username">Логин<input id="login-username" type="text" autocomplete="username" required></label>
    <label for="login-password">Пароль<input id="login-password" type="password" autocomplete="current-password" required></label>
    <label class="login-remember"><input id="login-remember" type="checkbox"> Запомнить меня (30 дней)</label>
    <p class="login-error" id="login-error" role="alert"></p>
    <button class="btn btn-primary" id="login-submit" type="submit">Войти</button>
  </form>
</div>
</body>"""),
]

def main():
    html = (MOCKUPS / "shell-v2-prototype.html").read_text(encoding="utf-8")

    # 1. Базовые стили прототипа → отдельный файл
    style_match = re.search(r"<style>\n(.*?)\n</style>", html, re.S)
    assert style_match, "не найден <style> блок прототипа"  # noqa: S101
    base_css = style_match.group(1)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "css").mkdir(parents=True)
    (OUT / "js" / "v2").mkdir(parents=True)

    (OUT / "css" / "shell-v2-base.css").write_text(base_css + "\n\n" + V2_EXTRA_CSS, encoding="utf-8")

    # 2. Модульные css/js — как есть
    for css in MOCKUPS.glob("shell-v2-*.css"):
        shutil.copyfile(css, OUT / "css" / css.name)
    for js in MOCKUPS.glob("shell-v2-*.js"):
        shutil.copyfile(js, OUT / "js" / js.name)

    body = html

    # 3. Фин-скрипт прототипа (inline) → модуль js/shell-v2-fin.js
    fin_start = body.find("<script>\n(function () {\n    'use strict';\n    const root = document.getElementById('view-fin');")
    assert fin_start >= 0, "не найден inline-скрипт финансов"  # noqa: S101
    fin_end = body.index("</script>", fin_start) + len("</script>")
    fin_js = body[fin_start + len("<script>"):fin_end - len("</script>")].strip()
    (OUT / "js" / "shell-v2-fin.js").write_text(fin_js + "\n", encoding="utf-8")
    body = body[:fin_start] + body[fin_end:]

    # 3a. index.html: пути, вынос стилей, boot вместо демо-скриптов, оверлей входа
    body = body.replace('<style>\n' + base_css + '\n</style>', '<link rel="stylesheet" href="css/shell-v2-base.css">')
    for src in sorted(MOCKUPS.glob("shell-v2-*.css")):
        body = body.replace(f'<link rel="stylesheet" href="./{src.name}">', f'<link rel="stylesheet" href="css/{src.name}">')
    for old, new in HTML_TRANSFORMS:
        assert old in body, "не найден фрагмент: " + old[:60]  # noqa: S101
        body = body.replace(old, new)
    body = re.sub(r'<script src="\./shell-v2-[^"]*" defer></script>\n?', '', body)
    body = body.replace('<title>Доска сделок — Свет в доме · Предпросмотр</title>',
                        '<title>Свет в доме · CRM v2</title>')
    body = body.replace('<html lang="ru" data-theme="light">', '<html lang="ru" data-theme="light">\n<!-- Сгенерировано tools/build_site_v2.py из tools/mockups — не править вручную -->')
    (OUT / "index.html").write_text(body, encoding="utf-8")

    # 4. Адаптер и загрузчик
    (OUT / "js" / "v2" / "api.js").write_text(API_JS, encoding="utf-8")
    (OUT / "js" / "v2" / "boot.js").write_text(BOOT_JS, encoding="utf-8")

    # 4a. Кэш-штампы: без них браузер держал старый boot/fin после деплоя,
    # и правки «не применялись» (жалоба 18.09 «галочки не совпадают»).
    # Один штамп на сборку: подставляется в статику index.html и в
    # динамические загрузки boot.js (APP_SCRIPTS/DEMO_SCRIPTS).
    import hashlib
    ver = hashlib.sha1()
    for f in sorted((OUT / "js").rglob("*.js")) + sorted((OUT / "css").glob("*.css")):
        ver.update(f.read_bytes())
    stamp = ver.hexdigest()[:10]

    # Глобальная версия ДО штампов: тег api.js ещё без ?v= — замена сработает.
    body = body.replace('<script src="js/v2/api.js"',
                        f'<script>window.V2_ASSET_VER="{stamp}"</script>\n<script src="js/v2/api.js"')

    def _stamp(m):
        return f'{m.group(1)}{m.group(2)}?v={stamp}{m.group(4)}'

    body = re.sub(r'((?:href|src)=")((?:css|js)/[^"?]+)(\?v=[a-f0-9]+)?(")', _stamp, body)
    (OUT / "index.html").write_text(body, encoding="utf-8")

    # 5. README для каталога
    (OUT / "README.md").write_text(
        "# site-v2 — новый фронтенд (генерируется)\n\n"
        "Собирается из прототипа `tools/mockups` командой:\n\n"
        "    python3 tools/build_site_v2.py\n\n"
        "Вручную не править: правки делаются в прототипе или в "
        "`tools/build_site_v2.py` (встроенные api.js/boot.js), затем пересборка.\n\n"
        "Режимы: по умолчанию — CRM API (вход по учётке CRM); `?demo=1` — демо-данные.\n",
        encoding="utf-8")

    print(f"site-v2 собран: {OUT}")
    for f in sorted(OUT.rglob('*')):
        if f.is_file():
            print(' ', f.relative_to(OUT))

if __name__ == "__main__":
    main()
