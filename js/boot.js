// === boot.js — ранняя инициализация темы/стекла до первой отрисовки ===
// Был инлайн-скриптом в <head> каждой страницы; вынесен в файл для CSP без
// 'unsafe-inline' в script-src (аудит 06.09, С5). Подключается БЕЗ defer
// в <head> ДО таблиц стилей — блокирующее исполнение даёт тот же эффект
// «без вспышки старой темы», что и прежний инлайн.
(function() {
    // Фикс аудита 10.09: при запрете данных сайта обращение к localStorage
    // бросает SecurityError — скрипт умирал целиком до применения темы.
    // Остальное приложение этот случай уже обрабатывает (auth.js, api.js).
    var theme = null, glass = null, compact = null;
    try {
        theme = localStorage.getItem('crm_theme');
        glass = localStorage.getItem('crm_glass');
        compact = localStorage.getItem('crm_compact');
    } catch (e) {}
    if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    if (glass === 'on') document.documentElement.setAttribute('data-glass', 'on');
    if (compact === 'true') document.addEventListener('DOMContentLoaded', function() {
        document.body.classList.add('compact-mode');
    });
})();
