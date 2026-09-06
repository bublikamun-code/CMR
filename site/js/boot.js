// === boot.js — ранняя инициализация темы/стекла до первой отрисовки ===
// Был инлайн-скриптом в <head> каждой страницы; вынесен в файл для CSP без
// 'unsafe-inline' в script-src (аудит 06.09, С5). Подключается БЕЗ defer
// в <head> ДО таблиц стилей — блокирующее исполнение даёт тот же эффект
// «без вспышки старой темы», что и прежний инлайн.
(function() {
    var theme = localStorage.getItem('crm_theme');
    if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    if (localStorage.getItem('crm_glass') !== 'off') document.documentElement.setAttribute('data-glass', 'on');
    var compact = localStorage.getItem('crm_compact');
    if (compact === 'true') document.addEventListener('DOMContentLoaded', function() {
        document.body.classList.add('compact-mode');
    });
})();
