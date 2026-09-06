/**
 * Календарное представление — дедлайны сделок и оплаты.
 */
(function() {
    let currentDate = new Date();
    let cards = [];
    let transactions = [];

    const MONTHS = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    const DAYS = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];

    const TYPE_DEAL = { key: 'deal', label: 'Сделка', color: '#4f46e5', bg: 'rgba(79,70,229,0.12)' };
    const TYPE_PAYMENT = { key: 'payment', label: 'Оплата', color: '#10b981', bg: 'rgba(16,185,129,0.12)' };
    const TYPE_OVERDUE = { key: 'overdue', label: 'Просрочено', color: '#ef4444', bg: 'rgba(239,68,68,0.12)' };

    async function loadData() {
        try {
            const token = getToken();
            const [cardsRes, txRes] = await Promise.all([
                fetch('/kanban/cards', { headers: { 'Authorization': `Bearer ${token}` } }),
                fetch('/payments/transactions', { headers: { 'Authorization': `Bearer ${token}` } })
            ]);
            if (cardsRes.ok) cards = await cardsRes.json();
            if (txRes.ok) transactions = await txRes.json();
        } catch(e) { console.error(e); }
    }

    function getDaysInMonth(year, month) {
        return new Date(year, month + 1, 0).getDate();
    }

    function getFirstDayOfMonth(year, month) {
        let day = new Date(year, month, 1).getDay();
        return day === 0 ? 6 : day - 1; // Пн = 0
    }

    function isOverdue(card, today) {
        if (!card.due_date || card.status === 'Закрыто') return false;
        const d = new Date(card.due_date + 'T00:00:00');
        return d < today;
    }

    function getDayEvents(year, month, day) {
        const events = [];
        const dayStart = new Date(year, month, day, 0, 0, 0);
        const today = new Date();
        today.setHours(0, 0, 0, 0);

        cards.forEach(c => {
            if (!c.due_date) return;
            const d = new Date(c.due_date + 'T00:00:00');
            if (d.getFullYear() === year && d.getMonth() === month && d.getDate() === day) {
                const overdue = isOverdue(c, today);
                events.push({
                    type: overdue ? TYPE_OVERDUE : TYPE_DEAL,
                    title: c.title,
                    status: c.status,
                    amount: c.total_amount,
                    cardId: c.id
                });
            }
        });

        transactions.forEach(t => {
            if (!t.date) return;
            const d = new Date(t.date);
            if (d.getFullYear() === year && d.getMonth() === month && d.getDate() === day) {
                events.push({
                    type: TYPE_PAYMENT,
                    title: t.company_name || 'Оплата',
                    amount: t.amount,
                    cardId: t.card_id
                });
            }
        });

        return events;
    }

    function renderCalendar() {
        const container = document.getElementById('calendar-container');
        if (!container) return;

        const year = currentDate.getFullYear();
        const month = currentDate.getMonth();
        const daysInMonth = getDaysInMonth(year, month);
        const firstDay = getFirstDayOfMonth(year, month);

        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const isCurrentMonth = today.getFullYear() === year && today.getMonth() === month;

        let hasEvents = false;

        let html = `
            <div class="calendar-toolbar">
                <button class="btn btn-secondary" data-handler="CalendarView.prevMonth"><svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg> Назад</button>
                <div class="calendar-title">
                    <h2>${MONTHS[month]} ${year}</h2>
                    <button class="btn btn-sm btn-secondary" data-handler="CalendarView.today">Сегодня</button>
                </div>
                <button class="btn btn-secondary" data-handler="CalendarView.nextMonth">Вперёд <svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg></button>
            </div>
            <div class="calendar-legend">
                <span class="calendar-legend-item"><span class="calendar-dot" style="background:${TYPE_DEAL.color}"></span>${TYPE_DEAL.label}</span>
                <span class="calendar-legend-item"><span class="calendar-dot" style="background:${TYPE_PAYMENT.color}"></span>${TYPE_PAYMENT.label}</span>
                <span class="calendar-legend-item"><span class="calendar-dot" style="background:${TYPE_OVERDUE.color}"></span>${TYPE_OVERDUE.label}</span>
            </div>
            <div class="calendar-grid">
                ${DAYS.map((d, i) => `<div class="calendar-weekday ${i >= 5 ? 'weekend' : ''}">${d}</div>`).join('')}
        `;

        for (let i = 0; i < firstDay; i++) {
            html += '<div class="calendar-cell calendar-cell-empty"></div>';
        }

        for (let day = 1; day <= daysInMonth; day++) {
            const isToday = isCurrentMonth && day === today.getDate();
            const dayOfWeek = (firstDay + day - 1) % 7;
            const isWeekend = dayOfWeek >= 5;
            const events = getDayEvents(year, month, day);
            if (events.length) hasEvents = true;

            const visible = events.slice(0, 3);
            const more = events.length - visible.length;

            html += `<div class="calendar-cell ${isToday ? 'today' : ''} ${isWeekend ? 'weekend' : ''}">
                <div class="calendar-day-number">${day}</div>
                <div class="calendar-events">
                    ${visible.map(e => `
                        <div class="calendar-chip" style="--chip-color:${e.type.color};--chip-bg:${e.type.bg};"
                             title="${escapeHtml(e.title)}${e.status ? ' (' + escapeHtml(e.status) + ')' : ''}${e.amount ? ' — ' + formatMoneyBYN(e.amount) : ''}"
                             data-handler="CalendarView.openEvent" data-arg="${e.cardId || 0}">
                            ${escapeHtml(e.title)}
                        </div>
                    `).join('')}
                    ${more > 0 ? `<div class="calendar-chip calendar-chip-more" data-handler="CalendarView.showDayEvents" data-args="${year},${month},${day}">ещё ${more}</div>` : ''}
                </div>
            </div>`;
        }

        html += '</div>';

        if (!hasEvents) {
            html += `
                <div class="empty-state-wrapper" style="margin-top:24px;">
                    <div class="empty-state-icon">
                        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                    </div>
                    <div class="empty-state-title">Нет событий</div>
                    <div class="empty-state-desc">У сделок с датой окончания и платежей появятся события в календаре.</div>
                </div>
            `;
        }

        container.innerHTML = html;
    }

    function showDayEvents(year, month, day) {
        const events = getDayEvents(year, month, day);
        const d = new Date(year, month, day);
        const title = `События ${d.toLocaleDateString('ru-RU')}`;
        const rows = events.map(e => `
            <tr style="cursor:pointer" data-handler="CalendarView.openEventFromDay" data-arg="${e.cardId || 0}">
                <td><span class="calendar-dot" style="background:${e.type.color};margin-right:6px;"></span>${escapeHtml(e.type.label)}</td>
                <td>${escapeHtml(e.title)}</td>
                <td class="tabular-nums">${e.amount ? formatMoneyBYN(e.amount) : '—'}</td>
            </tr>
        `).join('');

        let modal = document.getElementById('day-events-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'day-events-modal';
            modal.className = 'modal-overlay';
            document.body.appendChild(modal);
        }
        modal.innerHTML = `
            <div class="modal-content" style="max-width:520px;">
                <button class="close-btn" data-handler="CalendarView.hideDayEvents">${ICON_CROSS}</button>
                <h3>${title}</h3>
                <table class="data-table" style="margin-top:12px;"><thead><tr><th>Тип</th><th>Событие</th><th>Сумма</th></tr></thead><tbody>${rows}</tbody></table>
            </div>
        `;
        modal.classList.remove('hidden');
    }

    window.CalendarView = {
        async init() {
            await loadData();
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
        today() {
            currentDate = new Date();
            renderCalendar();
        },
        openEvent(id) {
            if (id && typeof openCardModal === 'function') openCardModal(id);
        },
        // Открытие события из модалки «События дня»: сначала закрыть её
        // (бывший второй statement в inline-onclick, запрещённом CSP).
        openEventFromDay(id) {
            document.getElementById('day-events-modal')?.classList.add('hidden');
            this.openEvent(id);
        },
        hideDayEvents() {
            document.getElementById('day-events-modal')?.classList.add('hidden');
        },
        showDayEvents
    };

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
