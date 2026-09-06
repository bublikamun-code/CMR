// === handlers.js — единая точка навешивания обработчиков ===
// CSP без 'unsafe-inline' (аудит 06.09, С5): все onclick/onchange в HTML и в
// JS-шаблонах заменены на декларативные атрибуты data-handler/data-tab/data-arg.
// Этот файл грузится ПОСЛЕДНИМ (defer) на всех страницах: делегирование на
// document покрывает и контент, отрисованный динамически после загрузки.
(function() {
    'use strict';

    // === SIDEBAR TOGGLE (общий сайдбар всех страниц) ===
    // admin_page.js определяет свою (упрощённую) версию — не перекрываем её.
    if (!window.toggleSidebar) {
        window.toggleSidebar = function() {
            const sidebar = document.getElementById('app-sidebar');
            if (!sidebar) return;
            const toggle = sidebar.querySelector('.sidebar-toggle');
            sidebar.classList.toggle('collapsed');
            const isCollapsed = sidebar.classList.contains('collapsed');
            if (toggle) {
                toggle.innerHTML = isCollapsed
                    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>'
                    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>';
                toggle.setAttribute('aria-label', isCollapsed ? 'Развернуть меню' : 'Свернуть меню');
            }
            try { localStorage.setItem('crm_sidebar_collapsed', isCollapsed ? '1' : '0'); }
            catch (e) {}
        };
    }

    (function restoreSidebar() {
        let saved = null;
        try { saved = localStorage.getItem('crm_sidebar_collapsed'); } catch (e) {}
        // FIX 2026-09-03 (аудит): на узких экранах без явного выбора
        // пользователя сайдбар свёрнут — больше места таблицам.
        if (saved === '1' || saved === 'true' || (saved === null && window.innerWidth <= 1100)) {
            const sidebar = document.getElementById('app-sidebar');
            if (sidebar) {
                sidebar.classList.add('collapsed');
                const toggle = sidebar.querySelector('.sidebar-toggle');
                if (toggle) {
                    toggle.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
                    toggle.setAttribute('aria-label', 'Развернуть меню');
                }
            }
        }
    })();

    // === ДЕЛЕГИРОВАНИЕ data-handler ===
    // <button data-handler="SettingsUI.createObj"> — без аргумента
    // <button data-handler="SettingsUI.openObj" data-arg="5"> — один аргумент
    // (числовые значения приводятся к Number). Случаи с несколькими аргументами
    // или составные действия биндятся программно в своих модулях.
    function resolveHandler(path) {
        let cur = window;
        for (const part of String(path).split('.')) {
            if (cur == null) return null;
            cur = cur[part];
        }
        return typeof cur === 'function' ? cur : null;
    }

    function coerceArg(raw) {
        if (raw === undefined || raw === '') return undefined;
        return /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : raw;
    }

    document.addEventListener('click', function(e) {
        const el = e.target.closest('[data-handler]');
        if (!el) return;
        const fn = resolveHandler(el.dataset.handler);
        if (!fn) {
            console.warn('handlers: обработчик не найден:', el.dataset.handler);
            return;
        }
        // Ссылки-«кнопки» (<a href="#") не должны вести по href.
        if (el.tagName === 'A') e.preventDefault();
        if (el.dataset.arg !== undefined) {
            fn(coerceArg(el.dataset.arg));
            return;
        }
        // Несколько аргументов: data-args="2026,8,14" (только простые значения
        // без запятых внутри — числа/короткие токены).
        if (el.dataset.args) {
            fn(...el.dataset.args.split(',').map(coerceArg));
            return;
        }
        fn();
    });

    // === ДЕЛЕГИРОВАНИЕ data-change (onchange у инпутов/селектов) ===
    // data-change="TasksUI.toggleDone" data-arg="5" — один аргумент;
    // data-change-checked — добавить el.checked; data-change-value — el.value.
    document.addEventListener('change', function(e) {
        const el = e.target.closest('[data-change]');
        if (!el) return;
        const fn = resolveHandler(el.dataset.change);
        if (!fn) {
            console.warn('handlers: обработчик не найден:', el.dataset.change);
            return;
        }
        const args = [coerceArg(el.dataset.arg)];
        if (el.hasAttribute('data-change-checked')) args.push(el.checked);
        if (el.hasAttribute('data-change-value')) args.push(el.value);
        fn(...args);
    });

    // === ВКЛАДКИ НАСТРОЕК ===
    // index.html (SPA): SettingsUI.switchTab(tab); settings.html: локальный
    // switchTab(tab, btn) из settings_page.js.
    document.querySelectorAll('[data-tab]').forEach(function(btn) {
        btn.addEventListener('click', function() {
            const tab = btn.dataset.tab;
            if (window.SettingsUI && typeof SettingsUI.switchTab === 'function') {
                SettingsUI.switchTab(tab);
            } else if (typeof window.switchTab === 'function') {
                switchTab(tab, btn);
            }
        });
    });

    // === КАНБАН: переключение вида и фильтры ===
    // Бывшие onclick="switchKanbanView('board'|'list')" и onchange="applyKanbanFilters()".
    const viewBoard = document.getElementById('view-board');
    const viewList = document.getElementById('view-list');
    if (typeof window.switchKanbanView === 'function') {
        if (viewBoard) viewBoard.addEventListener('click', function() { switchKanbanView('board'); });
        if (viewList) viewList.addEventListener('click', function() { switchKanbanView('list'); });
    }
    if (typeof window.applyKanbanFilters === 'function') {
        ['filter-store', 'filter-amount-min', 'filter-amount-max', 'filter-client', 'filter-priority']
            .forEach(function(id) {
                const el = document.getElementById(id);
                if (el) el.addEventListener('change', applyKanbanFilters);
            });
    }

    // === БРАУЗЕРНЫЕ УВЕДОМЛЕНИЯ (только index: crmOn из crm_store.js) ===
    // Бывший инлайн-блок конца index.html, без изменений по поведению.
    if (window.crmOn) {
        function requestNotificationPermission() {
            if ('Notification' in window && Notification.permission === 'default') {
                Notification.requestPermission();
            }
        }
        function sendBrowserNotification(title, body, icon) {
            if ('Notification' in window && Notification.permission === 'granted') {
                new Notification(title, { body: body, icon: icon || '/favicon.ico' });
            }
        }
        document.addEventListener('DOMContentLoaded', function() {
            setTimeout(requestNotificationPermission, 3000);
        });
        // Уведомления о новых сделках. Источник данных — периодический опрос
        // в js/realtime.js (WebSocket не используется), событие приходит после
        // каждой успешной загрузки доски.
        crmOn('kanban:loaded', function(data) {
            const prev = parseInt(sessionStorage.getItem('kanban_card_count') || '0');
            if (prev > 0 && data.count > prev) {
                sendBrowserNotification('Новая сделка', 'Добавлена новая сделка на Kanban-доске');
            }
            sessionStorage.setItem('kanban_card_count', data.count);
        });
    }
})();
