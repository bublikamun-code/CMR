/**
 * Календарное представление — дедлайны сделок на календаре.
 */
(function() {
    let currentDate = new Date();
    let cards = [];

    const MONTHS = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DAYS = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];

    async function loadCards() {
        try {
            const res = await fetch('/kanban/cards', {
                headers: { 'Authorization': `Bearer ${getToken()}` }
            });
            if (res.ok) cards = await res.json();
        } catch(e) { console.error(e); }
    }

    function getDaysInMonth(year, month) {
        return new Date(year, month + 1, 0).getDate();
    }

    function getFirstDayOfMonth(year, month) {
        let day = new Date(year, month, 1).getDay();
        return day === 0 ? 6 : day - 1; // Пн = 0
    }

    function renderCalendar() {
        const container = document.getElementById('calendar-container');
        if (!container) return;

        const year = currentDate.getFullYear();
        const month = currentDate.getMonth();
        const daysInMonth = getDaysInMonth(year, month);
        const firstDay = getFirstDayOfMonth(year, month);

        // Карточки с дедлайнами в этом месяце
        const monthCards = cards.filter(c => {
            if (!c.due_date) return false;
            const d = new Date(c.due_date + 'T00:00:00');
            return d.getFullYear() === year && d.getMonth() === month;
        });

        const today = new Date();
        const isCurrentMonth = today.getFullYear() === year && today.getMonth() === month;

        let html = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
                <button class="btn btn-secondary" onclick="CalendarView.prevMonth()">← Назад</button>
                <h2 style="margin:0;">${MONTHS[month]} ${year}</h2>
                <button class="btn btn-secondary" onclick="CalendarView.nextMonth()">Вперёд →</button>
            </div>
            <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;">
                ${DAYS.map(d => `<div style="text-align:center;padding:8px;font-weight:600;color:var(--text-muted);font-size:13px;">${d}</div>`).join('')}
        `;

        // Пустые ячейки до первого дня
        for (let i = 0; i < firstDay; i++) {
            html += '<div style="min-height:80px;"></div>';
        }

        // Дни месяца
        for (let day = 1; day <= daysInMonth; day++) {
            const isToday = isCurrentMonth && day === today.getDate();
            const dayCards = monthCards.filter(c => {
                const d = new Date(c.due_date + 'T00:00:00');
                return d.getDate() === day;
            });

            html += `<div style="min-height:80px;padding:6px;border:1px solid var(--border-color);border-radius:var(--radius-md);background:${isToday ? 'rgba(79,124,245,0.08)' : 'var(--card-bg)'};">
                <div style="font-weight:${isToday ? '700' : '500'};color:${isToday ? 'var(--primary-color)' : 'var(--text-color)'};margin-bottom:4px;">${day}</div>
                ${dayCards.map(c => {
                    const statusColors = {
                        'Новый запрос': '#64748b', 'В работе': '#4f46e5',
                        'Ждет оплаты': '#f59e0b', 'Сборка': '#10b981'
                    };
                    const color = statusColors[c.status] || '#64748b';
                    return `<div style="font-size:11px;padding:2px 4px;margin-bottom:2px;background:${color}15;color:${color};border-left:2px solid ${color};border-radius:2px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(c.title)} (${c.status})" onclick="CalendarView.openCard(${c.id})">${escapeHtml(c.title)}</div>`;
                }).join('')}
            </div>`;
        }

        html += '</div>';

        // Список дедлайнов на этот месяц
        if (monthCards.length > 0) {
            html += '<div style="margin-top:24px;"><h3>Дедлайны на этот месяц</h3>';
            html += '<table class="data-table"><thead><tr><th>Дата</th><th>Сделка</th><th>Статус</th><th>Сумма</th></tr></thead><tbody>';
            monthCards.sort((a, b) => new Date(a.due_date) - new Date(b.due_date)).forEach(c => {
                const d = new Date(c.due_date + 'T00:00:00');
                const isPast = d < today;
                html += `<tr style="${isPast ? 'background:rgba(239,68,68,0.05);' : ''}">
                    <td>${d.toLocaleDateString('ru-RU')}</td>
                    <td><b style="cursor:pointer;" onclick="CalendarView.openCard(${c.id})">${escapeHtml(c.title)}</b></td>
                    <td>${escapeHtml(c.status)}</td>
                    <td>${formatMoney(c.total_amount || 0)} BYN</td>
                </tr>`;
            });
            html += '</tbody></table></div>';
        }

        container.innerHTML = html;
    }

    function formatMoney(v) { return (v || 0).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

    window.CalendarView = {
        async init() {
            await loadCards();
            renderCalendar();
        },
        prevMonth() {
            currentDate.setMonth(currentDate.getMonth() - 1);
            renderCalendar();
        },
        nextMonth() {
            currentDate.setMonth(currentDate.getMonth() + 1);
            renderCalendar();
        },
        openCard(id) {
            if (typeof openCardModal === 'function') openCardModal(id);
        }
    };

    // Инициализация при переключении на страницу календаря
    document.addEventListener('DOMContentLoaded', () => {
        const calBtn = document.querySelector('[data-target="page-calendar"]');
        if (calBtn) {
            calBtn.addEventListener('click', () => {
                setTimeout(() => CalendarView.init(), 100);
            });
        }
        if (document.getElementById('page-calendar')?.classList.contains('active')) {
            CalendarView.init();
        }
    });
})();
