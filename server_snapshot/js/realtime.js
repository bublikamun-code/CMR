// ==========================================================
// АВТО-ОПРОС (polling): периодически подтягиваем свежие данные,
// чтобы у всех пользователей доски были актуальны без ручного F5.
// ==========================================================
(function () {
    const isMobile = window.matchMedia('(max-width: 900px)').matches;
    const POLL_INTERVAL = isMobile ? 30000 : 15000;

    const PAGE_LOADERS = {
        'page-kanban':     () => typeof loadKanbanBoard    === 'function' && loadKanbanBoard(),
        'page-payments':   () => typeof loadPaymentsTable  === 'function' && loadPaymentsTable(),
        'page-writeoffs':  () => typeof loadWriteoffsBoard === 'function' && loadWriteoffsBoard(),
        'page-documents':  () => typeof loadDocumentsTable === 'function' && loadDocumentsTable(),
        'page-clients':    () => typeof loadClientsTable   === 'function' && loadClientsTable(),
        'page-suppliers':  () => typeof loadSuppliersTable === 'function' && loadSuppliersTable(),
        'page-dashboard':  () => typeof loadDashboard      === 'function' && loadDashboard(),
    };

    // Максимальное время, которое опрос может простаивать из-за активного
    // ввода. Без этого ограничения курсор, забытый в поле, останавливал
    // обновление навсегда — пользователь часами смотрел на устаревшие данные
    // и никак об этом не узнавал.
    const MAX_BLOCKED_MS = 3 * 60 * 1000;
    let blockedSince = null;

    /**
     * Держит ли пользователь несохранённое состояние, которое перерисовка
     * затрёт. Поля поиска и фильтров сюда НЕ относятся: их значение
     * восстанавливается после обновления в reapplySearch().
     */
    function hasUnsavedInput() {
        const el = document.activeElement;
        if (!el) return false;
        if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return false;
        // Поиск и фильтры переживают перерисовку без потерь.
        if (el.classList.contains('search-input')) return false;
        if (el.closest('.filter-bar, .month-filter')) return false;
        return true;
    }

    function isBusy() {
        if (document.hidden) return true;
        if (typeof hasToken === 'function' && !hasToken()) return true;
        if (document.querySelector('#card-modal:not(.hidden), #create-modal:not(.hidden), #trash-modal:not(.hidden), #client-modal:not(.hidden), #supplier-modal:not(.hidden)')) return true;
        if (document.querySelector('.dropdown.open')) return true;
        if (document.querySelector('.dragging')) return true;
        if (document.querySelector('.tag-dropdown-menu')) return true;
        if (hasUnsavedInput()) return true;
        return false;
    }

    function reapplySearch(page) {
        const search = page.querySelector('.search-input');
        if (search && search.value.trim()) {
            search.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    let ticking = false;
    let lastActivePage = null;

    async function tick() {
        if (ticking) return;
        if (isBusy()) {
            // Обновление осознанно пропущено. Отслеживаем, как долго —
            // затянувшийся простой означает, что на экране устаревшие данные,
            // и об этом должен узнать хотя бы остальной код.
            if (blockedSince === null) blockedSince = Date.now();
            else if (Date.now() - blockedSince > MAX_BLOCKED_MS && window.CRM_STORE) {
                crmEmit('realtime:stale', { blockedMs: Date.now() - blockedSince });
            }
            return;
        }
        blockedSince = null;
        ticking = true;
        try {
            const activePage = document.querySelector('.page-section.active');
            if (!activePage) return;

            // Первый тик после смены страницы только запоминает её: данные
            // уже загружены обработчиком перехода, повторный запрос не нужен.
            if (activePage.id !== lastActivePage) {
                lastActivePage = activePage.id;
                return;
            }

            const loader = PAGE_LOADERS[activePage.id];
            if (!loader) return;
            await loader();
            reapplySearch(activePage);
            if (window.CRM_STORE) crmEmit('realtime:tick', { page: activePage.id });
        } catch (e) {
            if (window.CRM_STORE) crmEmit('realtime:error', { error: e.message });
        } finally {
            ticking = false;
        }
    }

    setInterval(tick, POLL_INTERVAL);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            lastActivePage = null;
            tick();
        }
    });
})();
