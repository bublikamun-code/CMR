/* Prototype-only hash routing. No production routes, storage or API calls. */
(() => {
    'use strict';
    const routes = {
        board: { view: 'board', title: 'Доска сделок' },
        day: { view: 'day', title: 'Пульт дня' },
        tasks: { view: 'tasks', title: 'Задачи' },
        fin: { view: 'fin', title: 'Финансы' },
        clients: { view: 'clients', title: 'Клиенты' },
        suppliers: { view: 'suppliers', title: 'Поставщики' },
        admin: { view: 'admin', title: 'Админ-панель' }
    };
    // Календарь — часть экрана «Задачи»; старый адрес ведёт туда же.
    const legacy = { calendar: 'tasks' };
    const links = [...document.querySelectorAll('.rail [data-view]')];
    const title = document.getElementById('shell-title');
    const content = document.getElementById('shell-content');
    let frame;
    function render(focus = false) {
        let key = location.hash.slice(1);
        if (Object.hasOwn(legacy, key)) {
            key = legacy[key];
            history.replaceState(null, '', '#' + key);
        }
        if (!Object.hasOwn(routes, key)) {
            key = 'board';
            history.replaceState(null, '', '#board');
        }
        const route = routes[key];
        document.querySelectorAll('.view').forEach(view => {
            const active = view.id === 'view-' + route.view;
            view.hidden = !active;
            view.classList.toggle('active', active);
        });
        links.forEach(link => {
            const active = link.dataset.view === key;
            link.classList.toggle('active', active);
            if (active) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        });
        title.textContent = route.title;
        document.title = route.title + ' — Свет в доме · Предпросмотр';
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            if (focus) content.focus({ preventScroll: true });
            window.scrollTo(0, 0);
            // Keep the active destination in the one mobile navigation strip.
            const current = links.find(link => link.dataset.view === key);
            if (matchMedia('(max-width: 720px)').matches) {
                const rail = document.querySelector('.rail');
                rail.scrollLeft += current.getBoundingClientRect().left - rail.getBoundingClientRect().left - 8;
            }
        });
    }
    links.forEach(link => link.addEventListener('click', event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (location.hash !== link.hash) history.pushState(null, '', link.hash);
        render(true);
    }));
    // hashchange also covers back/forward through same-document history.
    window.addEventListener('hashchange', () => render(true));
    document.querySelector('.shell-skip').addEventListener('click', event => {
        event.preventDefault();
        content.focus({ preventScroll: true });
        if (!document.getElementById('view-board').classList.contains('active')) content.scrollIntoView();
    });
    const theme = document.getElementById('theme-btn');
    theme.addEventListener('click', () => {
        const dark = document.documentElement.dataset.theme !== 'dark';
        document.documentElement.dataset.theme = dark ? 'dark' : 'light';
        theme.setAttribute('aria-pressed', String(dark));
    });
    render();
})();
