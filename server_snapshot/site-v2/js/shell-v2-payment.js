/* Preview payment drafts. No network/storage: valid snapshots and raw drafts are separate. */
(function () {
    'use strict';
    var drafts = new WeakMap();
    function iso(date) {
        return String(date.getFullYear()).padStart(4, '0') + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
    }
    function calendar(value) {
        var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
        if (!match || +match[1] < 1000) return null;
        var date = new Date(+match[1], +match[2] - 1, +match[3], 12);
        return iso(date) === value ? date : null;
    }
    function draft(c) {
        if (!drafts.has(c)) drafts.set(c, { terms: c.paymentTerms, mode: '', end: '', start: iso(new Date()), days: '', prepay: '' });
        return drafts.get(c);
    }
    function deferred(d) { return d.terms === 'deferred' || d.terms === 'partial_deferred'; }
    function validate(c, parseMoney) {
        var d = draft(c), errors = {}, due = '', prepay = null;
        if (deferred(d)) {
            if (!['date', 'days'].includes(d.mode)) errors.mode = 'Выберите конечную дату или срок в днях.';
            if (d.mode === 'date') {
                if (!calendar(d.end)) errors.end = 'Укажите существующую конечную дату (год 1000–9999).';
                else due = d.end;
            }
            if (d.mode === 'days') {
                var start = calendar(d.start);
                if (!start) errors.start = 'Укажите дату начала отсрочки.';
                if (!/^[1-9]\d*$/.test(d.days) || !Number.isSafeInteger(+d.days) || +d.days > 36500) errors.days = 'Срок: целое число от 1 до 36500 дней.';
                if (start && !errors.days) {
                    // Local noon + calendar days, not milliseconds or UTC date parsing (DST safe).
                    start.setDate(start.getDate() + +d.days);
                    if (start.getFullYear() > 9999) errors.days = 'Конечная дата должна быть не позднее 31.12.9999.';
                    else due = iso(start);
                }
            }
            if (d.terms === 'partial_deferred') {
                prepay = parseMoney(d.prepay);
                if (!Number.isSafeInteger(prepay) || prepay <= 0 || prepay >= c.amount) errors.prepay = 'Предоплата должна быть больше 0 и меньше суммы сделки; не более двух знаков после запятой.';
            }
        }
        return { errors: errors, due: due, prepay: prepay, valid: !Object.keys(errors).length };
    }
    function field(d, key, label, type) {
        var e = window.KBPayment.escape;
        return '<label class="kb-edit-field" for="kb-payment-' + key + '"><span>' + label + '</span><input id="kb-payment-' + key + '" data-payment-field="' + key + '" type="' + type + '" value="' + e(d[key]) + '" aria-describedby="kb-payment-' + key + '-error" required' + (key === 'prepay' ? ' inputmode="decimal"' : key === 'days' ? ' inputmode="numeric" maxlength="5"' : ' min="1000-01-01" max="9999-12-31"') + '><small class="kb-field-error" id="kb-payment-' + key + '-error"></small></label>';
    }
    function render(c) {
        var d = draft(c), e = window.KBPayment.escape;
        return '<section id="kb-payment-details" aria-labelledby="kb-payment-heading"><h3 id="kb-payment-heading" class="kb-detail-section-title">Детали оплаты</h3>' +
            (deferred(d) ? '<div class="kb-deal-fields">' +
                (d.terms === 'partial_deferred' ? field(d, 'prepay', 'Предоплата, BYN', 'text') : '') +
                '<label class="kb-edit-field" for="kb-payment-mode"><span>Как задать отсрочку</span><select id="kb-payment-mode" data-payment-field="mode" required aria-describedby="kb-payment-mode-error">' + [['', 'Выберите способ'], ['date', 'Конечная дата'], ['days', 'Срок в днях']].map(function(pair) { return '<option value="' + pair[0] + '"' + (d.mode === pair[0] ? ' selected' : '') + '>' + pair[1] + '</option>'; }).join('') + '</select><small class="kb-field-error" id="kb-payment-mode-error"></small></label>' +
                (d.mode === 'date' ? field(d, 'end', 'Оплатить до', 'date') : d.mode === 'days' ? field(d, 'start', 'Начало отсрочки', 'date') + field(d, 'days', 'Календарных дней после начала', 'text') : '') + '</div>' : '<p class="kb-detail-hint">' + e(d.terms === 'full' ? 'Оплата 100% — условие, не отметка о получении денег.' : 'Условия оплаты не выбраны.') + '</p>') +
            '<p id="kb-payment-status" role="status" aria-live="polite"></p></section>';
    }
    window.KBPayment = { draft: draft, render: render, validate: validate };
})();
