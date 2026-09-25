/* site-v2: синхронная инициализация до первой отрисовки.
   Внешний same-origin скрипт позволяет strict CSP без nonce и unsafe-inline:
   маршрут и тема восстанавливаются до загрузки CSS, а штамм берётся из URL
   этого файла. */
(function () {
    'use strict';
    var script = document.currentScript;
    // Навигация загружается с defer, поэтому маршрут нужен до первого кадра.
    var route = location.hash.slice(1);
    var queryAt = route.indexOf('?');
    if (queryAt >= 0) route = route.slice(0, queryAt);
    if (route === 'calendar') route = 'tasks';
    if (['board', 'day', 'tasks', 'fin', 'clients', 'suppliers', 'admin'].indexOf(route) < 0) {
        route = 'board';
    }
    document.documentElement.dataset.v2Route = route;
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
