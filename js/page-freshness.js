/**
 * Свежесть данных разделов при переключении.
 *
 * Раньше: данные грузились только при загрузке приложения и polling'ом
 * каждые 15с (realtime.js), причём только активной страницы. При возврате
 * на раздел пользователь видел устаревшие данные до ближайшего тика, а
 * затем polling резко перерисовывал весь контент.
 *
 * Теперь: разделы НЕ перегружаются при каждом переключении — DOM каждой
 * секции и есть кеш последнего состояния. При переключении, если данные
 * раздела старше STALE_MS, они обновляются сразу в фоне; перерисовка
 * идёт с плавным затуханием (.page-refreshing), а не миганием.
 */
(function () {
    'use strict';

    // Обновляем данные раздела, если они старше 12 секунд. Меньше интервала
    // polling (15с): почти всегда переключение попадает в свежее окно и
    // запроса не будет вовсе.
    const STALE_MS = 12000;

    // Карта лоадеров вынесена сюда из realtime.js (он же остаётся пользователем)
    const PAGE_LOADERS = {
        'page-kanban':     () => typeof loadKanbanBoard    === 'function' && loadKanbanBoard(),
        // Задачи и Финансы: обновляем активный вид
        'page-tasks':      () => typeof loadTasks          === 'function' && loadTasks(),
        // Финансы: обновляем только активную вкладку (оплаты/списание/документы)
        'page-finance':    () => {
            const active = document.querySelector('.finance-panel.active');
            if (!active) return;
            if (active.id === 'page-control')   { if (typeof loadControlBoard   === 'function') loadControlBoard(); }
            if (active.id === 'page-payments')  { if (typeof loadPaymentsTable  === 'function') loadPaymentsTable(); }
            if (active.id === 'page-writeoffs') { if (typeof loadWriteoffsBoard === 'function') loadWriteoffsBoard(); }
            if (active.id === 'page-documents') { if (typeof loadDocumentsTable === 'function') loadDocumentsTable(); }
            if (active.id === 'page-nakladnye') { if (typeof loadNakladnyeTable === 'function') loadNakladnyeTable(); }
        },
        'page-clients':    () => typeof loadClientsTable   === 'function' && loadClientsTable(),
        'page-suppliers':  () => typeof loadSuppliersTable === 'function' && loadSuppliersTable(),
        'page-dashboard':  () => typeof loadDashboard      === 'function' && loadDashboard(),
    };
    window.CRM_PAGE_LOADERS = PAGE_LOADERS;

    const lastLoaded = {};   // pageId -> timestamp последней загрузки
    let refreshing = null;   // pageId текущей фоновой загрузки

    /** Отметить данные раздела свежими (вызывается после каждой загрузки). */
    function markFresh(pageId) {
        if (pageId) lastLoaded[pageId] = Date.now();
    }

    window.CRM_FRESHNESS = {
        markFresh,

        /** Обновить раздел, если данные устарели. Показ идёт сразу из DOM. */
        refreshIfStale(pageId) {
            const loader = PAGE_LOADERS[pageId];
            if (!loader) return;
            const age = Date.now() - (lastLoaded[pageId] || 0);
            if (age < STALE_MS) return;
            if (refreshing === pageId) return;

            refreshing = pageId;
            const section = document.getElementById(pageId);
            // Плавное обновление: контент слегка затухает и «проявляется»
            // с новыми данными — вместо резкой подмены.
            if (section) section.classList.add('page-refreshing');

            Promise.resolve()
                .then(loader)
                .catch(() => {})
                .finally(() => {
                    markFresh(pageId);
                    refreshing = null;
                    if (section) {
                        section.classList.remove('page-refreshing');
                        // восстановить поиск/фильтры, как это делает realtime
                        const search = section.querySelector('.search-input');
                        if (search && search.value.trim()) {
                            search.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                    }
                });
        }
    };
})();
