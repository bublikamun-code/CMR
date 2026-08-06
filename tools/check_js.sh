#!/bin/bash
# Проверка синтаксиса всех JS-файлов проекта.
# Сборки в проекте нет, поэтому опечатка в скрипте обнаруживается только
# в браузере у пользователя. Этот скрипт ловит её до выкладки.
#
# Использует JavaScriptCore из macOS — Node.js на машине не установлен.

set -u

JSC=/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc
SITE="$(cd "$(dirname "$0")/../site" && pwd)"

if [ ! -x "$JSC" ]; then
    echo "Не найден JavaScriptCore: $JSC" >&2
    exit 2
fi

failed=0
for f in "$SITE"/js/*.js; do
    name="js/$(basename "$f")"
    result=$("$JSC" -e "try{new Function(readFile('$f'));print('OK')}catch(e){print('FAIL: '+e.message)}" 2>&1)
    case "$result" in
        OK) ;;
        *) echo "$name -> $result"; failed=1 ;;
    esac
done

if [ "$failed" -eq 0 ]; then
    echo "Синтаксис: все файлы в порядке"
else
    echo "Обнаружены синтаксические ошибки" >&2
fi
exit "$failed"
