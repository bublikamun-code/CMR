/* Волна F-2: глобальный поиск в шапке.
   Подключённый режим — GET /search (сервер, casefold по кириллице);
   предпросмотр мокапа — локальный фильтр KBData с той же формой ответа.
   Переходы по результату: клиент — раздел «Клиенты» + событие kb:open-client
   (insights открывает дровер), сделка — KBBoard.open (id строго строкой),
   поставщик — раздел «Поставщики». Никаких inline-обработчиков: CSP v2. */
(function () {
    'use strict';

    var input = document.getElementById('v2-search');
    if (!input) return;
    var wrap = input.closest('.v2-search');
    var panel = document.createElement('div');
    panel.className = 'v2-search-panel';
    panel.id = 'v2-search-panel';
    panel.hidden = true;
    panel.setAttribute('role', 'listbox');
    panel.setAttribute('aria-label', 'Результаты поиска');
    wrap.appendChild(panel);

    var timer = 0;
    var results = [];   // плоский список строк панели — для стрелок и Enter
    var activeIndex = -1;

    function connected() {
        return Boolean(window.V2Api && window.V2Api.token());
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // --- демо-режим прототипа: та же форма ответа, что у GET /search ---
    function localSearch(q) {
        var needle = q.trim().toLowerCase();
        var empty = { clients: [], cards: [], suppliers: [] };
        if (!needle || !window.KBData) return Promise.resolve(empty);
        var match = function (t) { return t && String(t).toLowerCase().indexOf(needle) >= 0; };
        var cap = function (arr) { return arr.slice(0, 5); };
        var clients = cap((window.KBData.clients || []).filter(function (c) {
            return match(c.name) || match(c.phone) || match(c.unp);
        }).map(function (c) {
            return { id: c.id, name: c.name, phone: c.phone, unp: c.unp };
        }));
        var cards = cap((window.KBData.cards || []).filter(function (c) {
            var cl = window.KBData.clientById ? window.KBData.clientById(c.clientId) : null;
            return match(c.title) || match(cl && cl.name) || q.trim() === String(c.id);
        }).map(function (c) {
            var cl = window.KBData.clientById ? window.KBData.clientById(c.clientId) : null;
            return { id: c.id, title: c.title, status: c.status, client_name: cl ? cl.name : null };
        }));
        var suppliers = cap((window.KBData.suppliers || []).filter(function (s) {
            return match(s.name) || match(s.phone) || match(s.unp);
        }).map(function (s) {
            return { id: s.id, name: s.name, phone: s.phone, unp: s.unp };
        }));
        return Promise.resolve({ clients: clients, cards: cards, suppliers: suppliers });
    }

    function runSearch(q) {
        var promise = connected()
            ? window.V2Api.api('/search?q=' + encodeURIComponent(q))
            : localSearch(q);
        return promise.then(function (data) {
            render(q, data || {});
        }).catch(function () {
            render(q, null);
        });
    }

    function render(q, data) {
        results = [];
        activeIndex = -1;
        var groups = [
            { key: 'clients', title: 'Клиенты', rows: data.clients || [] },
            { key: 'cards', title: 'Сделки', rows: data.cards || [] },
            { key: 'suppliers', title: 'Поставщики', rows: data.suppliers || [] }
        ];
        var html = '';
        groups.forEach(function (g) {
            if (!g.rows.length) return;
            html += '<div class="v2-search-group">' + esc(g.title) + '</div>';
            g.rows.forEach(function (row) {
                var idx = results.length;
                results.push({ kind: g.key, id: row.id });
                var main, sub;
                if (g.key === 'clients') {
                    main = row.name;
                    sub = [row.phone, row.unp].filter(Boolean).join(' · ');
                } else if (g.key === 'cards') {
                    main = '#' + row.id + ' · ' + (row.title || '');
                    sub = [row.status, row.client_name].filter(Boolean).join(' · ');
                } else {
                    main = row.name;
                    sub = [row.unp, row.phone].filter(Boolean).join(' · ');
                }
                html += '<button type="button" class="v2-search-row" role="option" aria-selected="false" data-idx="' + idx + '">' +
                    '<span class="main">' + esc(main) + '</span>' +
                    (sub ? '<span class="sub">' + esc(sub) + '</span>' : '') +
                    '</button>';
            });
        });
        if (!html) {
            html = '<p class="v2-search-empty">' +
                (data === null ? 'Не удалось выполнить поиск' : 'Ничего не найдено') + '</p>';
        }
        panel.innerHTML = html;
        panel.hidden = false;
        input.setAttribute('aria-expanded', 'true');
    }

    function setActive(index) {
        var rows = panel.querySelectorAll('.v2-search-row');
        if (!rows.length) return;
        activeIndex = (index + rows.length) % rows.length;
        rows.forEach(function (row, i) {
            var active = i === activeIndex;
            row.classList.toggle('active', active);
            row.setAttribute('aria-selected', String(active));
        });
        rows[activeIndex].scrollIntoView({ block: 'nearest' });
    }

    function closePanel() {
        panel.hidden = true;
        panel.innerHTML = '';
        results = [];
        activeIndex = -1;
        input.setAttribute('aria-expanded', 'false');
    }

    function openResult(r) {
        closePanel();
        if (r.kind === 'client') {
            location.hash = '#clients';
            document.dispatchEvent(new CustomEvent('kb:open-client', { detail: { id: String(r.id) } }));
        } else if (r.kind === 'card') {
            window.KBBoard.open(String(r.id));
        } else {
            location.hash = '#suppliers';
        }
        input.blur();
    }

    panel.addEventListener('click', function (e) {
        var row = e.target.closest('.v2-search-row');
        if (row) openResult(results[Number(row.dataset.idx)]);
    });

    input.addEventListener('input', function () {
        clearTimeout(timer);
        if (!input.value.trim()) { closePanel(); return; }
        timer = setTimeout(function () { runSearch(input.value); }, 300);
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            if (!results.length) return;
            e.preventDefault();
            setActive(activeIndex + (e.key === 'ArrowDown' ? 1 : -1));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            var r = results[activeIndex >= 0 ? activeIndex : 0];
            if (r) openResult(r);
        } else if (e.key === 'Escape') {
            closePanel();
        }
    });

    // Клик мимо панели закрывает её; фокус в поле — панель живёт.
    document.addEventListener('click', function (e) {
        if (!panel.hidden && !wrap.contains(e.target)) closePanel();
    });
    input.addEventListener('focus', function () {
        if (panel.innerHTML && input.value.trim()) panel.hidden = false;
    });

    // Ctrl+K / Cmd+K — стандартный хоткей поиска.
    document.addEventListener('keydown', function (e) {
        if (!(e.ctrlKey || e.metaKey) || e.key !== 'k' && e.key !== 'K' && e.code !== 'KeyK') return;
        e.preventDefault();
        input.focus();
        input.select();
    });
})();
