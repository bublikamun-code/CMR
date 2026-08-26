let _dashCards = [], _dashTransactions = [], _dashClients = [], _dashSuppliers = [];
let _dashSelectedMonth = 'all';
let _dashLoaded = false;

document.addEventListener('DOMContentLoaded', () => {
    const dashBtn = document.querySelector('[data-target="page-dashboard"]');
    if (dashBtn) dashBtn.addEventListener('click', loadDashboard);
    if (document.getElementById('page-dashboard')?.classList.contains('active')) loadDashboard();

    if (window.CRM_STORE) {
        CRM_STORE.on('kanban:loaded', (data) => {
            _dashCards = CRM_STORE.get('cards') || [];
            if (_dashLoaded) renderDashboard();
        });
        CRM_STORE.on('payments:loaded', (data) => {
            _dashTransactions = CRM_STORE.get('transactions') || [];
            if (_dashLoaded) renderDashboard();
        });
        CRM_STORE.on('clients:loaded', (data) => {
            _dashClients = CRM_STORE.get('clients') || [];
            if (_dashLoaded) renderDashboard();
        });
        CRM_STORE.on('suppliers:loaded', (data) => {
            _dashSuppliers = CRM_STORE.get('suppliers') || [];
            if (_dashLoaded) renderDashboard();
        });
        CRM_STORE.on('realtime:tick', () => {
            if (_dashLoaded) {
                _dashCards = CRM_STORE.get('cards') || [];
                _dashTransactions = CRM_STORE.get('transactions') || [];
                _dashClients = CRM_STORE.get('clients') || [];
                _dashSuppliers = CRM_STORE.get('suppliers') || [];
                renderDashboard();
            }
        });
    }
});

async function loadDashboard() {
    if (!hasToken()) return;
    const container = document.getElementById('dashboard-container');
    if (!container) return;

    try {
        if (!_dashLoaded) {
            container.innerHTML = `
                <div class="dash-grid" style="opacity: 0.6;">
                    <div class="dash-card">
                        <div class="skeleton-line" style="width: 30%; height: 20px; margin-bottom: 20px;"></div>
                        <div class="dash-summary-grid">
                            <div class="dash-stat reveal reveal-fast"><div class="skeleton-line" style="height: 48px;"></div></div>
                            <div class="dash-stat reveal reveal-fast"><div class="skeleton-line" style="height: 48px;"></div></div>
                            <div class="dash-stat reveal reveal-fast"><div class="skeleton-line" style="height: 48px;"></div></div>
                            <div class="dash-stat reveal reveal-fast"><div class="skeleton-line" style="height: 48px;"></div></div>
                        </div>
                    </div>
                    <div class="dash-card">
                        <div class="skeleton-line" style="width: 40%; height: 20px; margin-bottom: 20px;"></div>
                        <div class="skeleton-line" style="height: 24px; margin-bottom: 12px;"></div>
                        <div class="skeleton-line" style="height: 24px; margin-bottom: 12px;"></div>
                        <div class="skeleton-line" style="height: 24px; margin-bottom: 12px;"></div>
                        <div class="skeleton-line" style="height: 24px;"></div>
                    </div>
                    <div class="dash-card">
                        <div class="skeleton-line" style="width: 50%; height: 20px; margin-bottom: 20px;"></div>
                        <div class="skeleton-line" style="height: 32px; margin-bottom: 12px;"></div>
                        <div class="skeleton-line" style="height: 32px; margin-bottom: 12px;"></div>
                        <div class="skeleton-line" style="height: 32px;"></div>
                    </div>
                </div>
            `;
        }

        // UI FIX 2026-08-26: каждый источник проверяется отдельно. Раньше
        // хватало наличия cards в кэше, чтобы пропустить ВСЕ fetch — если
        // клиенты/поставщики/оплаты в стор ещё не загружались их модулями,
        // дашборд показывал «Клиентов: 0» при существующих клиентах.
        const has = (key) => window.CRM_STORE && Array.isArray(CRM_STORE.get(key)) && CRM_STORE.get(key).length > 0;
        const needFetch = {
            cards: !has('cards'),
            transactions: !has('transactions'),
            clients: !has('clients'),
            suppliers: !has('suppliers')
        };
        const fetches = {};
        if (needFetch.cards) fetches.cards = apiFetch('/kanban/cards').catch(() => []);
        if (needFetch.transactions) fetches.transactions = apiFetch('/payments/transactions').catch(() => []);
        if (needFetch.clients) fetches.clients = apiFetch('/clients').catch(() => []);
        if (needFetch.suppliers) fetches.suppliers = apiFetch('/suppliers').catch(() => []);
        const fetched = Object.fromEntries(await Promise.all(
            Object.entries(fetches).map(async ([k, p]) => [k, await p])
        ));

        _dashCards = needFetch.cards ? fetched.cards : CRM_STORE.get('cards');
        _dashTransactions = needFetch.transactions ? fetched.transactions : CRM_STORE.get('transactions') || [];
        _dashClients = needFetch.clients ? fetched.clients : CRM_STORE.get('clients') || [];
        _dashSuppliers = needFetch.suppliers ? fetched.suppliers : CRM_STORE.get('suppliers') || [];

        if (window.CRM_STORE) {
            CRM_STORE.set('cards', _dashCards);
            CRM_STORE.set('transactions', _dashTransactions);
            CRM_STORE.set('clients', _dashClients);
            CRM_STORE.set('suppliers', _dashSuppliers);
        }

        buildMonthFilter('dashboard-month', _dashCards, c => c.created_at, _dashSelectedMonth, (val) => {
            _dashSelectedMonth = val;
            renderDashboard();
        });

        _dashLoaded = true;
        renderDashboard();
    } catch (error) {
        container.innerHTML = '';
        container.appendChild(renderAlert({ type: 'error', title: 'Ошибка загрузки дашборда', message: error.message, onRetry: () => loadDashboard() }));
    }
}

function renderDashboard() {
    const container = document.getElementById('dashboard-container');
    if (!container) return;

    let cards = _dashCards;
    if (_dashSelectedMonth !== 'all') {
        cards = cards.filter(c => monthKeyOf(c.created_at) === _dashSelectedMonth);
    }

    const funnelData = {};
    const KANBAN_COLS = ["Новый запрос", "В работе", "Ждет оплаты", "Сборка"];
    KANBAN_COLS.forEach(s => { funnelData[s] = 0; });
    cards.forEach(c => {
        if (funnelData[c.status] !== undefined) funnelData[c.status]++;
    });
    funnelData['Закрыто'] = cards.filter(c => c.status === 'Закрыто').length;

    const totalAmount = cards.reduce((a, c) => a + (parseFloat(c.total_amount) || 0), 0);
    const closedAmount = cards.filter(c => c.status === 'Закрыто').reduce((a, c) => a + (parseFloat(c.total_amount) || 0), 0);

    const managerStats = {};
    cards.forEach(c => {
        const name = c.owner ? c.owner.username : 'Неизвестно';
        if (!managerStats[name]) managerStats[name] = { count: 0, amount: 0, closed: 0 };
        managerStats[name].count++;
        managerStats[name].amount += parseFloat(c.total_amount) || 0;
        if (c.status === 'Закрыто') managerStats[name].closed++;
    });

    const storeStats = {};
    cards.forEach(c => {
        const store = c.store_location || 'Не привязан';
        if (!storeStats[store]) storeStats[store] = 0;
        storeStats[store]++;
    });

    const maxFunnel = Math.max(...Object.values(funnelData), 1);
    const funnelHtml = KANBAN_COLS.map(status => {
        const count = funnelData[status];
        const pct = Math.round((count / maxFunnel) * 100);
        return '<div class="dash-funnel-row">' +
            '<div class="dash-funnel-label">' + escapeHtml(status) + '</div>' +
            '<div class="dash-funnel-bar-track"><div class="dash-funnel-bar" style="width:' + pct + '%"></div></div>' +
            '<div class="dash-funnel-count">' + count + '</div>' +
        '</div>';
    }).join('') + '<div class="dash-funnel-row dash-funnel-closed">' +
        '<div class="dash-funnel-label">Закрыто</div>' +
        '<div class="dash-funnel-bar-track"><div class="dash-funnel-bar" style="width:' + (funnelData["Закрыто"] ? Math.round((funnelData["Закрыто"] / maxFunnel) * 100) : 0) + '%"></div></div>' +
        '<div class="dash-funnel-count">' + funnelData["Закрыто"] + '</div>' +
    '</div>';

    const managerRows = Object.entries(managerStats)
        .sort((a, b) => b[1].count - a[1].count)
        .map(([name, s]) => '<tr>' +
            '<td><b>' + escapeHtml(name) + '</b></td>' +
            '<td>' + s.count + '</td>' +
            '<td>' + s.closed + '</td>' +
            '<td class="tabular-nums">' + formatMoneyBYN(s.amount) + '</td>' +
        '</tr>').join('');

    const storeRows = Object.entries(storeStats)
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => '<tr><td>' + escapeHtml(name) + '</td><td>' + count + '</td></tr>').join('');

    container.innerHTML = '<div class="dash-grid">' +
        '<div class="dash-card dash-summary">' +
            '<h3>Обзор</h3>' +
            '<div class="dash-summary-grid">' +
                '<div class="dash-stat reveal reveal-fast">' +
                    '<div class="dash-stat-icon">' +
                        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>' +
                    '</div>' +
                    '<div class="dash-stat-info">' +
                        '<div class="dash-stat-value">' + cards.length + '</div>' +
                        '<div class="dash-stat-label">Всего сделок</div>' +
                    '</div>' +
                '</div>' +
                '<div class="dash-stat reveal reveal-fast">' +
                    '<div class="dash-stat-icon">' +
                        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/><line x1="6" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="18" y2="12"/></svg>' +
                    '</div>' +
                    '<div class="dash-stat-info">' +
                        '<div class="dash-stat-value tabular-nums">' + formatMoneyBYN(totalAmount) + '</div>' +
                        '<div class="dash-stat-label">Общая сумма</div>' +
                    '</div>' +
                '</div>' +
                '<div class="dash-stat reveal reveal-fast">' +
                    '<div class="dash-stat-icon">' +
                        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>' +
                    '</div>' +
                    '<div class="dash-stat-info">' +
                        '<div class="dash-stat-value tabular-nums">' + formatMoneyBYN(closedAmount) + '</div>' +
                        '<div class="dash-stat-label">Закрытые сделки</div>' +
                    '</div>' +
                '</div>' +
                '<div class="dash-stat reveal reveal-fast">' +
                    '<div class="dash-stat-icon">' +
                        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>' +
                    '</div>' +
                    '<div class="dash-stat-info">' +
                        '<div class="dash-stat-value">' + _dashClients.length + '</div>' +
                        '<div class="dash-stat-label">Клиентов</div>' +
                    '</div>' +
                '</div>' +
                '<div class="dash-stat reveal reveal-fast">' +
                    '<div class="dash-stat-icon">' +
                        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>' +
                    '</div>' +
                    '<div class="dash-stat-info">' +
                        '<div class="dash-stat-value">' + _dashSuppliers.length + '</div>' +
                        '<div class="dash-stat-label">Поставщиков</div>' +
                    '</div>' +
                '</div>' +
                '<div class="dash-stat reveal reveal-fast">' +
                    '<div class="dash-stat-icon">' +
                        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>' +
                    '</div>' +
                    '<div class="dash-stat-info">' +
                        '<div class="dash-stat-value">' + _dashTransactions.length + '</div>' +
                        '<div class="dash-stat-label">Транзакций</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '<div class="dash-card"><h3>Воронка продаж</h3><div class="dash-funnel">' + funnelHtml + '</div>' +
            '<div style="margin-top:12px;font-size:13px;color:var(--text-muted);">Конверсия: ' +
            (cards.length > 0 ? Math.round((funnelData['Закрыто'] / cards.length) * 100) : 0) + '% закрытых</div></div>' +
        '<div class="dash-card"><h3>Топ менеджеров</h3><table class="data-table dash-table"><thead><tr><th>Менеджер</th><th>Сделок</th><th>Закрыто</th><th>Сумма</th></tr></thead><tbody>' + managerRows + '</tbody></table></div>' +
        '<div class="dash-card"><h3>По магазинам</h3><table class="data-table dash-table"><thead><tr><th>Магазин</th><th>Сделок</th></tr></thead><tbody>' + storeRows + '</tbody></table></div>' +
        '<div class="dash-card"><h3>Топ клиентов</h3><div id="dash-top-clients"></div></div>' +
        '<div class="dash-card" style="grid-column: 1 / -1;"><h3>Продажи по месяцам</h3><div style="height:250px;"><canvas id="dash-chart-monthly"></canvas></div></div>' +
    '</div>';

    // Топ клиентов
    const clientStats = {};
    cards.forEach(c => {
        if (c.client) {
            const name = c.client.name;
            if (!clientStats[name]) clientStats[name] = { count: 0, amount: 0 };
            clientStats[name].count++;
            clientStats[name].amount += parseFloat(c.total_amount) || 0;
        }
    });
    const topClients = Object.entries(clientStats)
        .sort((a, b) => b[1].amount - a[1].amount)
        .slice(0, 10);
    const topClientsEl = document.getElementById('dash-top-clients');
    if (topClientsEl && topClients.length > 0) {
        topClientsEl.innerHTML = '<table class="data-table dash-table"><thead><tr><th>Клиент</th><th>Сделок</th><th>Сумма</th></tr></thead><tbody>' +
            topClients.map(([name, s]) => `<tr><td><b>${escapeHtml(name)}</b></td><td>${s.count}</td><td class="tabular-nums">${formatMoneyBYN(s.amount)}</td></tr>`).join('') +
            '</tbody></table>';
    } else if (topClientsEl) {
        topClientsEl.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:12px;">Нет данных</p>';
    }

    // График продаж по месяцам
    setTimeout(() => renderMonthlyChart(cards), 100);

    // Активировать reveal-анимации для свежесозданных элементов
    if (typeof window.revealRefresh === 'function') window.revealRefresh();
}

function renderMonthlyChart(cards) {
    const canvas = document.getElementById('dash-chart-monthly');
    if (!canvas || typeof Chart === 'undefined') return;

    const monthlyData = {};
    const baseMonths = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
    baseMonths.forEach((m, i) => { monthlyData[i] = { count: 0, amount: 0 }; });

    // Определяем год для подписей: берём год самой свежей сделки или текущий.
    let year = new Date().getFullYear();
    const years = cards.map(c => c.created_at ? new Date(c.created_at).getFullYear() : null).filter(Boolean);
    if (years.length) year = Math.max(...years);

    cards.forEach(c => {
        if (c.created_at) {
            const d = new Date(c.created_at);
            if (d.getFullYear() === year) {
                const month = d.getMonth();
                monthlyData[month].count++;
                monthlyData[month].amount += parseFloat(c.total_amount) || 0;
            }
        }
    });

    const months = baseMonths.map(m => `${m} ${year}`);

    const ctx = canvas.getContext('2d');
    if (canvas._chart) canvas._chart.destroy();

    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const primaryColor = cssVar('--primary-color') || '#4a57a4';
    const successColor = cssVar('--success-color') || '#10b981';
    const textColor = cssVar('--text-color') || '#1e293b';
    const borderColor = cssVar('--border-color') || 'rgba(0,0,0,0.1)';

    const formatAxis = (value) => {
        if (value >= 1000000) return (value / 1000000).toFixed(1).replace('.', ',') + ' млн';
        if (value >= 1000) return Math.round(value / 1000) + ' тыс.';
        return String(value);
    };

    canvas._chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: months,
            datasets: [{
                label: 'Сумма (BYN)',
                data: baseMonths.map((_, i) => monthlyData[i].amount),
                backgroundColor: primaryColor + '99',
                borderColor: primaryColor,
                borderWidth: 1,
                borderRadius: 4
            }, {
                label: 'Сделок',
                data: baseMonths.map((_, i) => monthlyData[i].count),
                type: 'line',
                borderColor: successColor,
                backgroundColor: successColor + '1a',
                tension: 0,
                fill: true,
                yAxisID: 'y1'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: textColor, usePointStyle: true, padding: 16 }
                }
            },
            scales: {
                x: {
                    ticks: { color: textColor },
                    grid: { color: borderColor }
                },
                y: {
                    beginAtZero: true,
                    title: { display: true, text: 'BYN', color: textColor },
                    ticks: { color: textColor, callback: formatAxis },
                    grid: { color: borderColor }
                },
                y1: {
                    beginAtZero: true,
                    position: 'right',
                    title: { display: true, text: 'Сделок', color: textColor },
                    ticks: { color: textColor, precision: 0 },
                    grid: { drawOnChartArea: false }
                }
            }
        }
    });
}

// UI Audit (2026-08-09, C.3): при смене темы перерендер графика с новыми цветами.
// Хранить последние cards глобально, чтобы перерендерить без повторной загрузки.
let _lastCards = null;
const _origRender = renderMonthlyChart;
renderMonthlyChart = function(cards) {
    _lastCards = cards;
    return _origRender(cards);
};
document.addEventListener('crm:theme-changed', () => {
    if (_lastCards) {
        // Даём CSS-переменным время примениться прежде, чем мы их читаем
        setTimeout(() => _origRender(_lastCards), 50);
    }
});
