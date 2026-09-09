/**
 * Сохранённые представления (Saved Views).
 * Позволяет сохранять и загружать наборы фильтров/сортировки.
 */
(function() {
    const STORAGE_KEY = 'crm_saved_views';
    let currentViews = [];
    let currentPage = '';

    function getViews() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
        } catch(e) { return {}; }
    }

    function saveViews(views) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(views));
    }

    function getViewName(page) {
        return `crm_view_${page}`;
    }

    // Показать модалку сохранения
    window.showSaveViewModal = function(page, filters) {
        const name = prompt('Название представления:');
        if (!name) return;

        const views = getViews();
        const key = getViewName(page);
        if (!views[key]) views[key] = [];
        views[key].push({
            id: Date.now(),
            name: name,
            filters: filters,
            created: new Date().toISOString()
        });
        saveViews(views);
        showToast(`Представление "${name}" сохранено`, 'success');
        refreshViewsDropdown(page);
    };

    // Загрузить представление
    window.loadView = function(page, viewId) {
        const views = getViews();
        const key = getViewName(page);
        const list = views[key] || [];
        const view = list.find(v => v.id === viewId);
        if (!view) return;

        // Применяем фильтры
        if (view.filters) {
            if (view.filters.search) {
                const searchInput = document.getElementById('kanban-search');
                if (searchInput) {
                    searchInput.value = view.filters.search;
                    searchInput.dispatchEvent(new Event('input'));
                }
            }
        }
        showToast(`Загружено: ${view.name}`, 'info');
    };

    // Удалить представление
    window.deleteView = function(page, viewId) {
        const views = getViews();
        const key = getViewName(page);
        if (views[key]) {
            views[key] = views[key].filter(v => v.id !== viewId);
            saveViews(views);
            refreshViewsDropdown(page);
            showToast('Представление удалено', 'info');
        }
    };

    // Обновить дропдаун
    window.refreshViewsDropdown = function(page) {
        const dropdown = document.getElementById('views-dropdown');
        if (!dropdown) return;

        const views = getViews();
        const key = getViewName(page);
        const list = views[key] || [];

        if (list.length === 0) {
            dropdown.classList.add('hidden');
            return;
        }

        dropdown.classList.remove('hidden');
        const menu = dropdown.querySelector('.dropdown-menu');
        if (!menu) return;

        menu.innerHTML = '';
        list.forEach(v => {
            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.innerHTML = `
                <span>${escapeHtml(v.name)}</span>
                <button class="view-delete-btn" data-id="${v.id}" title="Удалить"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            `;
            item.addEventListener('click', (e) => {
                // Фикс аудита 10.09: клик по SVG-иконке внутри кнопки давал
                // e.target = svg/line, и вид ЗАГРУЖАЛСЯ вместо удаления.
                if (e.target.closest('.view-delete-btn')) {
                    e.stopPropagation();
                    deleteView(page, v.id);
                    return;
                }
                loadView(page, v.id);
                menu.parentElement.classList.remove('open');
            });
            menu.appendChild(item);
        });
    };

    // Инициализация
    document.addEventListener('DOMContentLoaded', () => {
        // Добавляем кнопку сохранения в шапку kanban
        const headerActions = document.querySelector('#page-kanban .header-actions');
        if (headerActions) {
            const saveBtn = document.createElement('button');
            saveBtn.className = 'btn-secondary';
            saveBtn.innerHTML = '<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> Сохранить вид';
            saveBtn.title = 'Сохранить текущий фильтр';
            saveBtn.onclick = () => {
                const searchInput = document.getElementById('kanban-search');
                const filters = {};
                if (searchInput && searchInput.value.trim()) {
                    filters.search = searchInput.value.trim();
                }
                showSaveViewModal('kanban', filters);
            };
            headerActions.insertBefore(saveBtn, headerActions.firstChild);
        }

        // Создаём дропдаун сохранённых видов
        const headerActions2 = document.querySelector('#page-kanban .header-actions');
        if (headerActions2) {
            const dropdown = document.createElement('div');
            dropdown.id = 'views-dropdown';
            dropdown.className = 'dropdown hidden';
            dropdown.innerHTML = `
                <button class="btn-secondary dropdown-toggle"><svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg> Мои виды <svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
                <div class="dropdown-menu"></div>
            `;
            headerActions2.insertBefore(dropdown, headerActions2.firstChild);

            dropdown.querySelector('.dropdown-toggle').addEventListener('click', (e) => {
                e.stopPropagation();
                const isOpen = dropdown.classList.contains('open');
                document.querySelectorAll('.dropdown.open').forEach(d => d.classList.remove('open'));
                if (!isOpen) {
                    dropdown.classList.add('open');
                    refreshViewsDropdown('kanban');
                }
            });

            dropdown.querySelector('.dropdown-menu').addEventListener('click', (e) => e.stopPropagation());
        }

        refreshViewsDropdown('kanban');
    });
})();
