# Компоненты

Инвентарь фактических классов (style.css, ~885 селекторов). Новый UI строится из
этих классов; новый класс появляется, только если существующий не покрывает
задачу. Обязательное правило стеклянных поверхностей — см. themes.md.

## Кнопки

База `.btn` + варианты: `.btn-primary`, `.btn-secondary`, `.btn-danger`,
`.btn-success`, размер `.btn-sm`. Табы во всех секциях — `.settings-tab`
(финансы, задачи, настройки). Специализированные (`nav-btn`, `view-toggle-btn`,
`density-btn`, `filter-reset`, `logout-btn`) — самостоятельные семейства.

**Правило:** новая кнопка = `.btn` + вариант. Исторические ad-hoc кнопки
(`btn-trash`, `btn-export`, `btn-restore`, `btn-add-contact`…) при касании
переводить на `.btn`-базу, сохраняя специфичность поведения.

## Таблицы

`.table-container` (обёртка со скроллом) → `table.data-table` (+`.sortable`,
`.cb-col`, sticky-колонки). Итоги — `tr.table-totals`. Пагинация —
`.pagination-bar`. Пустые строки таблиц — паттерн `empty-state` внутри `td`.
Скелетон загрузки — классы `.skeleton-*` (не инлайн-стили).

## Канбан

`.kanban-board / -column / -card`, статус левой полосы:
`.card-assembly / .card-paid / .card-overdue / .card-due-soon` (цвета полосы —
только `--status-*`). Теги — `.card-tag`. Плотность — через `.compact-mode` и
`crm_kanban_density`, не через новые размеры.

## Модалки

Оверлей `.modal-overlay`, окно `.modal` + `.modal-content` / `-wide` /
`-split` (сплит: `.modal-split-left/-right/-header`). Закрытие — `.close-btn`.
Внутри: `.modal-section`, `.modal-label`, `.modal-actions` (липкая панель).
У админки параллельное семейство `.admin-modal-*` — не смешивать с основным.

## Формы

`.form-group` + `label`; текстовые/селекты: `.search-input` (тулбар),
`.input-settings` / `.select-settings` (настройки/модалки), `.filter-select`
(фильтры), `.login-input` / `.admin-form-group` (свои экраны). Подсказки —
`.field-hint`. Кастомные дропдауны строит только `features.js`
(`createDropdown` / `enhanceSelectToDropdown`): меню — `.dropdown-menu` /
`.dropdown-item`, при открытии переносится в `<body>`
(`.dropdown-menu-portal`, координаты ставит JS).

## Бейджи и статусы

Магазины: `.store-badge` + исторические `.store-Матусевича / .store-Богдановича
/ .store-БН` (кириллические классы — легаси, не переименовывать без массовой
замены в JS). Оплаты: `.pay-badge` + `pay-unpaid / pay-partial / pay-paid /
pay-deferred` (значения из `constants.js`, `PAYMENT_STATUS_CLASSES`). Софт-цвета
бейджей — только через `--*-soft-*` токены (tokens.md §1).

## Тосты

Только `showToast()` из `features.js`; `.toast` + `.toast-success/-error/-info`.
Новых inline-тостов не создавать.

## Пустые состояния и алерты

`.empty-state` (иконка + заголовок + описание) — единый паттерн для таблиц,
досок и панелей. Сообщения/ошибки — `.alert` (`renderAlert` в JS для
динамических). Скелетоны загрузки — `.skeleton-row`, `.skeleton-line`,
`.skeleton-card`.

## Инлайн-стили: что разрешено

`style="…"` в HTML и JS-шаблонах допустим ТОЛЬКО для динамических значений,
которые не знает CSS:

- ширина прогресс-баров: `style="width:${pct}%"`;
- координаты портала дропдауна (`positionDropdownMenu`);
- цвет из данных (теги, события календаря) — паттерн CSS-переменных:
  `style="--chip-color:${c};--chip-bg:${bg}"` + класс `.calendar-chip`;
- ширины колонок `<th>` — разрешены, но предпочтительны классы `w-*` в CSS,
  если ширина статична.

Всё остальное (скелетоны, пустые состояния, чекбоксы-строки, сетки настроек) —
классы. При встрече статичного инлайна в своей задаче — переносить в класс.
