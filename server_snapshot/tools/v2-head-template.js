/* site-v2: синхронная инициализация до первой отрисовки.
   Внешний same-origin скрипт позволяет strict CSP без nonce и unsafe-inline:
   тема восстанавливается до загрузки CSS, а штамм берётся из URL этого файла. */
(function () {
    'use strict';
    var script = document.currentScript;
    try {
        var theme = localStorage.getItem('kb-theme');
        if (theme === 'dark' || theme === 'light') {
            document.documentElement.dataset.theme = theme;
        }
    } catch (error) {
        // Хранилище может быть недоступно: светлая тема из разметки остаётся.
    }
    if (script && script.src) {
        try {
            window.V2_ASSET_VER = new URL(script.src).searchParams.get('v') || '';
        } catch (error) {
            window.V2_ASSET_VER = '';
        }
    }
})();
