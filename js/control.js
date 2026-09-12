/**
 * Вкладка «Контроль» (Финансы): дебиторка и очередь на списание.
 * Данные: /kanban/cards (paid_amount/total_amount уже считает бэкенд)
 * и /payments/transactions?grouped=false (выписанные ТН без списания).
 * Никаких новых эндпоинтов — только уже отдающиеся поля.
 */
async function loadControlBoard() {
    const panel = document.getElementById('page-control');
    if (!panel) return;

    let cards, txs;
    try {
        [cards, txs] = await Promise.all([
            apiFetch('/kanban/cards'),
            apiFetch('/payments/transactions?grouped=false')
        ]);
    } catch (error) {
        panel.querySelectorAll('tbody').forEach(tb => {
            tb.innerHTML = '<tr><td colspan="7">Ошибка загрузки: ' + escapeHtml(error.message) + '</td></tr>';
        });
        return;
    }

    const now = Date.now();
    const daysSince = (d) => {
        if (!d) return null;
        const t = new Date(d.length === 10 ? d + 'T00:00:00' : d).getTime();
        return isNaN(t) ? null : Math.max(0, Math.floor((now - t) / 86400000));
    };
    const fmtDays = (n) => (n === null || n === undefined) ? '—' : String(n);
    const esc = (s) => escapeHtml(String(s ?? ''));

    // Bento-плитка: одна плитка = одна метрика (план 5.2). Отсутствие
    // элемента не валит загрузку — плитки опциональны в разметке.
    const setTile = (id, value, sub) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
        const subEl = document.getElementById(id + '-sub');
        if (subEl) subEl.textContent = sub;
    };

    // --- Дебиторка: сделки из «Сборки» и «Ждет оплаты» с непогашенным
    // остатком (фидбек 2026-09-04: ранние статусы — ещё не дебиторка) ---
    const DEBTOR_STATUSES = ['Сборка', 'Ждет оплаты'];
    const debtors = cards
        .map(c => {
            const total = parseFloat(c.total_amount) || 0;
            const paid = parseFloat(c.paid_amount) || 0;
            return { c, total, paid, debt: Math.round((total - paid) * 100) / 100 };
        })
        .filter(x => x.debt > 0.01 && DEBTOR_STATUSES.includes(x.c.status))
        .sort((a, b) => b.debt - a.debt);

    const debtBody = panel.querySelector('#control-debt-table tbody');
    const debtTotal = debtors.reduce((s, x) => s + x.debt, 0);
    setTile('control-tile-debt', formatMoneyBYN(debtTotal),
        debtors.length ? `${debtors.length} сделок` : 'всё оплачено');

    // Просрочка дебиторки: долг 30+ дней от создания сделки — сигнал
    // «пора взыскивать», порог совпадает с age-warn в таблице ниже.
    const overdue = debtors.filter(x => {
        const d = daysSince(x.c.created_at);
        return d !== null && d >= 30;
    });
    setTile('control-tile-overdue',
        overdue.length ? formatMoneyBYN(overdue.reduce((s, x) => s + x.debt, 0)) : '—',
        overdue.length ? `${overdue.length} сделок 30+ дней` : 'нет долгов 30+ дней');

    debtBody.innerHTML = debtors.length ? '' :
        '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">Долгов нет</td></tr>';
    debtors.forEach(({ c, total, paid, debt }) => {
        const days = daysSince(c.created_at);
        const ageCls = days >= 60 ? 'age-danger' : (days >= 30 ? 'age-warn' : '');
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.title = 'Открыть карточку сделки';
        tr.innerHTML = `
            <td class="td-clamp" title="${esc(c.title || '')}">${esc(c.title || '')}</td>
            <td>${esc(c.store_location || '')}</td>
            <td>${esc(c.status || '')}</td>
            <td class="tabular-nums">${formatMoneyBYN(total)}</td>
            <td class="tabular-nums">${formatMoneyBYN(paid)}</td>
            <td class="tabular-nums" style="font-weight:700">${formatMoneyBYN(debt)}</td>
            <td class="tabular-nums ${ageCls}">${fmtDays(days)}</td>`;
        tr.addEventListener('click', () => { if (typeof openCardModal === 'function') openCardModal(c.id); });
        debtBody.appendChild(tr);
    });

    // --- Очередь на списание: ТН выписана, со склада не списана ---
    const queue = txs
        .filter(t => !t.is_document && !t.is_warehouse_writeoff && (t.invoice_number || '').trim())
        .map(t => {
            const ageFrom = t.invoice_date || (t.date ? String(t.date).slice(0, 10) : null);
            return { t, days: daysSince(ageFrom) };
        })
        .sort((a, b) => (b.days ?? -1) - (a.days ?? -1));

    const queueBody = panel.querySelector('#control-queue-table tbody');
    const queueTotal = queue.reduce((s, x) => s + (parseFloat(x.t.amount) || 0), 0);
    setTile('control-tile-queue', formatMoneyBYN(queueTotal),
        queue.length ? `${queue.length} ТН` : 'пусто');

    queueBody.innerHTML = queue.length ? '' :
        '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">Очередь пуста — всё списано</td></tr>';
    queue.forEach(({ t, days }) => {
        const ageCls = days >= 14 ? 'age-danger' : (days >= 7 ? 'age-warn' : '');
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.title = 'Открыть карточку сделки';
        tr.innerHTML = `
            <td style="font-weight:600">${esc(t.invoice_number || '—')}</td>
            <td class="tabular-nums">${esc(t.invoice_date || (t.date ? String(t.date).slice(0, 10) : '—'))}</td>
            <td>${esc(t.company_name || '')}</td>
            <td>${esc(t.store_location || '')}</td>
            <td class="tabular-nums">${formatMoneyBYN(parseFloat(t.amount) || 0)}</td>
            <td class="tabular-nums ${ageCls}">${fmtDays(days)}</td>`;
        tr.addEventListener('click', () => { if (t.card_id && typeof openCardModal === 'function') openCardModal(t.card_id); });
        queueBody.appendChild(tr);
    });

    const upd = document.getElementById('control-updated');
    if (upd) upd.textContent = 'обновлено: ' + new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

window.loadControlBoard = loadControlBoard;
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('control-refresh');
    if (btn) btn.addEventListener('click', () => loadControlBoard());
});
