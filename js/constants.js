// Константы приложения. Меняются в одном месте, подтягиваются всеми модулями.
// Это исключает расхождение наборов магазинов между фильтром канбана,
// карточкой сделки и модалкой создания сделки.
//
// «БН» (безнал) — способ оплаты, а не физическая точка, поэтому склада
// у него нет. Доска списаний в index.html перечисляет только реальные
// магазины и намеренно не строится из этого списка: writeoffs.js берёт
// набор складов из самой разметки (.writeoff-store-block).
// При добавлении магазина со складом правьте и этот файл, и блоки списаний.

const APP_STORES = [
    { value: 'Матусевича', label: 'Матусевича' },
    { value: 'Богдановича', label: 'Богдановича' },
    { value: 'БН', label: 'Безнал (БН)' }
];

// Классы бейджей статуса оплаты. Используются в kanban.js и payments.js.
const PAYMENT_STATUS_CLASSES = {
    'Не оплачен': 'pay-unpaid',
    'Частично': 'pay-partial',
    'Оплачен': 'pay-paid',
    'Отсрочка': 'pay-deferred'
};

// Заполняем фильтр магазинов из APP_STORES, чтобы набор совпадал
// с дропдаунами в карточке сделки и модалке создания.
document.addEventListener('DOMContentLoaded', function() {
    var filterStore = document.getElementById('filter-store');
    if (filterStore) {
        APP_STORES.forEach(function(s) {
            var opt = document.createElement('option');
            opt.value = s.value;
            opt.textContent = s.label;
            filterStore.appendChild(opt);
        });
    }
    // Фильтры канбана — кастомные dropdown вместо нативных селектов
    // (createDropdown/syncEnhancedSelect к этому моменту уже определены:
    // все defer-скрипты выполняются до DOMContentLoaded)
    if (typeof enhanceSelectToDropdown === 'function') {
        enhanceSelectToDropdown(filterStore);
        enhanceSelectToDropdown(document.getElementById('filter-priority'));
    }
});
