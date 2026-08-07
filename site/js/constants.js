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
});
