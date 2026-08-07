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

    function isBusy() {
        if (document.hidden) return true;
        if (typeof hasToken === 'function' && !hasToken()) return true;
        if (document.querySelector('#card-modal:not(.hidden), #create-modal:not(.hidden), #trash-modal:not(.hidden), #client-modal:not(.hidden), #supplier-modal:not(.hidden)')) return true;
        if (document.querySelector('.dropdown.open')) return true;
        if (document.querySelector('.dragging')) return true;
        if (document.querySelector('.tag-dropdown-menu')) return true;
        const el = document.activeElement;
        if (el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return true;
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
        if (isBusy()) return;
        ticking = true;
        try {
            const activePage = document.querySelector('.page-section.active');
            if (!activePage) return;

            if (activePage.id !== lastActivePage) {
                lastActivePage = activePage.id;
                ticking = false;
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
