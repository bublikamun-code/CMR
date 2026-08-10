# UI Audit Plan — CRM «Свет в доме»

**Дата:** 2026-08-09
**Автор аудита:** Kilo (агент)
**Статус:** 🟡 В работе (Спринт A)
**Скоуп:** `site/css/style.css`, `site/*.html`, `site/js/features.js`, `site/js/ui-polish.js`

---

## Сводка

Аудит внешнего вида и ощущения интерфейса по двум философиям:
- **Emil Kowalski** (библиотека `emil-design-eng`) — тайминги, easing, press-feedback, отсутствие bounce в CRM.
- **Web Interface Guidelines** — accessibility, reduced-motion, hover-gating, keyboard-nav.

**Ключевая находка:** прошлые исправления «дрожащего курсора» вырезали всю микрореакцию — кнопки стали «мёртвыми». Модалки появляются с bounce-анимацией (overshoot), которая считывается как «поп-ап реклама». Дублирующие keyframes (`modalFadeIn` + `modalSlideUp`) запускаются одновременно на одном элементе.

---

## Критичные нарушения (Спринт A — делаем сейчас)

### A.1 Глобальный «запрет движения» вырезал press-feedback
- **Файл:** `site/css/style.css:6644-6656`
- **Сейчас:** `button:hover { transform:none !important; filter:none !important; }` и `.modal *:hover { transform:none !important; }`
- **Почему плохо:** курсор наводится — никакого ответа, кнопки «мёртвые». Это нарушает принцип Kowalski «кнопки должны слушаться».
- **Фикс:** наведение без `transform` (только цвет/фон), нажатие — `scale(0.97)` с ускорением до 60 мс.

### A.2 Bounce-анимация модалки (overshoot)
- **Файл:** `style.css:842`
- **Сейчас:** `animation: modalFadeIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)`
- **Почему плохо:** кривая с overshoot (`>1.275`) и 300 мс для CRM — это «поп-ап реклама». Должно быть 180–220 мс, чёткий ease-out, **без** bounce.
- **Фикс:** один быстрый `modalEnter 200ms var(--ease-out-strong)`.

### A.3 Дублирующие keyframes на одном элементе
- **Файл:** `style.css:842` и `style.css:4273-4276`
- **Сейчас:** к `.modal-content` применяются **оба** keyframes (modalFadeIn + modalSlideUp) через разные селекторы — запускаются одновременно, второй переписывает `transform`. Даёт подвисшие ключевые кадры и подрагивание.
- **Фикс:** удалить `modalFadeIn` и связанные `animation:` правила, оставить один новый `modalEnter`.

### A.4 Старый `ease` у dropdown (открытие вялое)
- **Файл:** `style.css` — `@keyframes dropdownIn`, `animation: dropdownIn 0.14s ease`
- **Почему плохо:** `ease` имеет `ease-in`-ногу, открытие выглядит ленивым. Для выпадающих это ключевой момент.
- **Фикс:** `animation: dropdownIn 140ms cubic-bezier(0.23,1,0.32,1)`.

### A.5 Тосты без кнопки закрытия, без transform-анимации входа
- **Файл:** `site/js/features.js:60-83`, `style.css:56-59` в `admin.html`
- **Сейчас:** keyframes `slideIn 0.3s` без easing-кривой, тост нельзя закрыть вручную.
- **Фикс:** добавить `transition: transform 200ms …, opacity 200ms`, кнопку-крестик `✕`.

---

## Серьёзные нарушения (Спринт B — убирает визуальный шум)

### B.1 Пульсация `.email-pulse` и минимальный `.pulse-overdue`
- `style.css:2694`, `style.css:3412` — `ease-in-out` + циклическая анимация статуса.
- **Фикс:** заменить на статичный бейдж с коротким entrance-анимацией (150 мс, один раз).

### B.2 Раздвоённое поведение `.kanban-card:hover`
- Три конфликтующих правила: `style.css:3036` (`!important`), `4267` (обычное), `6657` (`!important`, отключает поднятие).
- **Фикс:** свести к одному правилу — либо поднятие, либо только тень, **не оба**.

### B.3 Конфликт `.tag-color-sq:hover` с глобальным `button:hover { transform:none }`
- **Файл:** `style.css:2996`
- **Фикс:** добавить `!important` к `scale(1.15)` или снять глобальный запрет.

### B.4 Мёртвый CSS-код
- `style.css:1636` — `.btn-delete-card:hover { scale(1.1) }` уже не работает из-за глобального запрета.
- **Фикс:** удалить.

### B.5 Sidebar-градиент логотипа конфликтует с поздним патчем
- Строки 322–324 (`background-clip:text`) vs 6952-6958 (`background:none; color:#f8fafc`).
- **Фикс:** оставить одно поведение, удалить ранние правила (если итоговый рендер плоский белый).

### B.6 Канбан-кнопки действий не стилизованы единообразно (✕ и ✎)
- **Файлы:** `js/kanban.js:263-267` (карточка на доске), `js/kanban.js:739-746` (строка в списке)
- **Сейчас:**
  - На доске (`.card-hover-btn .btn-edit-card`/`.btn-delete-card`): своих стилей хватает (размер 24×24), но текстовые глифы `✕` и `✎` не выровнены по вертикали одинаково, и нет явного `font-family` / `font-weight` — отрисовка разная на macOS/Windows.
  - В таблице списка: `<button class="btn btn-danger btn-sm">✕</button>` — класс `.btn-danger` определён **только** в `[data-theme="dark"]` (строка 4587). В светлом режиме кнопка теряет красное оформление и выглядит как обычная `.btn-sm`. Дополнительно `btn-sm` — чисто вертикально-паддинговый, горизонтально квадратное соотношение для иконки не гарантировано.
- **Фикс:**
  1. `.btn-delete-card` / `.btn-edit-card`: явные `font: 600 14px/1 system-ui`, центрирование через flex уже есть.
  2. Определить `.btn-danger` в светлой теме (`background: var(--danger-color); color: #fff`), hover — темнее (`#b91c1c`).
  3. В таблице списка заменить `<button class="btn btn-danger btn-sm">✕</button>` на `<button class="btn-icon-danger">` с тем же оформлением, что и на доске (24×24 or 28×28, красная заливка на hover).
  4. Убедиться, что у обеих кнопок срабатывает `press-feedback scale(0.97)` из Спринта A (они унаследуют через `button:active`).

---

## Архитектурные вопросы (Спринт C — долгий)

### C.1 Инлайн-стили в `settings.html`, `workflows.html`, `custom_objects.html`
Сотни `style="flex:1;min-width:200px;padding:8px 12px;…"`. Заменить на класс `form-input-flex` в `style.css`.

### C.2 973 `!important` в style.css
Симптом «заплатка на заплатку». Цель — разделить на слои (база / патчи / поздние фиксы), убрать 60–70% `!important`.

### C.3 `admin.html` — отдельный стек стилей
Не переиспользует `--transition-fast`, свой градиент `linear-gradient(135deg, #1f2937, #374151)` на login-Card. Унифицировать с общей темой.

### C.4 Chart.js тёмная тема через `filter: invert(.92) hue-rotate(180deg)`
Хрупко: любой новый `<canvas>` тоже инвертируется. Правильнее — пересчитывать `Chart.defaults.color` при переключении темы через JS.

> ✅ **Closed 2026-08-09:** Chart.js теперь читает цвета из CSS-переменных (`getComputedStyle`) и перерендеривается по событию `crm:theme-changed`. Глобальный `filter: invert` на всех `<canvas>` удалён.

### C.5 Touch-режим: `.card-hover-actions` на постоянке — визуальный шум
Сейчас на тач-устройствах карточные экшены видны всегда. Лучше `⋯`-меню, открываемое по тапу.

> 🔶 **Deferred:** требует изменения HTML kanban.js (замена двух кнопок на одну `⋯` + popover). Архитектурное решение — выносится за скоуп Sprинта C.

### C.6 Слои CSS и чистка `!important` (975 → ~300)
973+ переопределения с `!important` — симптом «заплатка на заплатку». Требуется:
1. Разделить style.css на `base.css`, `components.css`, `audit-patches.css`.
2. После этого 60–70% `!important` можно снять, переписав специфичность.

> 🔶 **Deferred:** слишком масштабно для текущего спринта, требует отдельной задачи рефакторинга.

---

## Что уже сделано хорошо (не трогать)

| Тема | Статус |
|---|---|
| Тёмная тема с правильными контрастами (`#b91c1c` vs `#f87171`) | ✅ |
| `--transition-fast/normal` и `--transition-ui` — явный список, не `all` | ✅ |
| `@media (prefers-reduced-motion)` — машинально глушит | ✅ |
| `@media (hover: none)` показывает hidden hover-кнопки | ✅ |
| `:focus-visible` с outline | ✅ |
| Пульсация `.overdue-badge` ограничена 3 повторениями | ✅ (можно ужать до 150 мс entrance) |
| `skip-link` есть, modal focus-into и Esc-закрытие | ✅ |
| `tabular-nums` для сумм | ✅ |
| Drag &amp; drop с `rotate(2deg)` | ✅ |

---

## Стратегия

**Спринт A (30 мин, немедленный эффект):**
1. Включить `:active { transform: scale(0.97) }` на главных кнопках.
2. Убрать глобальный запрет `button:hover { transform:none }`, заменить точечными исключениями.
3. Заменить `ease` → `cubic-bezier(0.23,1,0.32,1)` у dropdown и toast.
4. Удалить `modalFadeIn` + `modalSlideUp` → один быстрый `modalEnter 200ms`.
5. Добавить крестик у тостов.

**Спринт B (1–2 часа, убирает шум):**
6. Заменить пульсации на статику с entrance.
7. Свести `.kanban-card:hover` к одному правилу.
8. Убрать stale-код (`btn-delete-card:hover`, конфликтующие sidebar-градиенты).
9. Разрешить конфликт `.tag-color-sq:hover`.

**Спринт C (долгий, архитектурный):**
10. Переписать inline styles в классы.
11. Разделить `style.css` на слои, убрать 60–70% `!important`.
12. Унифицировать `admin.html`.
13. Перенести Chart.js в JS-конфиг темы.
14. Touch-режим: `⋯`-меню вместо постоянных hover-actions.

---

## Как верифицировать (после каждого спринта)

1. Статический анализ:
   ```bash
   grep -n "modalFadeIn\|modalSlideUp" site/css/style.css   # должно быть 0 после A.3
   grep -n "cubic-bezier(0.175, 0.885" site/css/style.css   # должно быть 0 после A.2
   grep -c "!important" site/css/style.css                  # снизится после C.2
   ```
2. Ручной прогон:
   - Открыть карточку → модалка появляется плавно 200 мс, **без** bounce.
   - Клик по `.btn-primary` → лёгкое `scale(0.97)` на 60 мс, никакого дрожания курсора.
   - Hover по nav-кнопке → только смена цвета, никакого движения.
   - Тост в правом нижнем углу → появляется с `transform 200ms`, есть `✕`.

---

## Журнал выполнения

| Этап | Статус | Дата | Замечания |
|---|---|---|---|
| Создан план | ✅ | 2026-08-09 | Этот файл |
| Спринт A | ✅ | 2026-08-09 | A.1–A.4 закрыты. `modalEnter` введён, `modalFadeIn`/`modalSlideUp` удалены, press-feedback добавлен, `--ease-out-strong` введён. |
| Спринт B | ✅ | 2026-08-09 | B.1 пульсации заменены `badgeEnter`, B.2 `.kanban-card:hover` сведён к одному правилу, B.3 `.tag-color-sq:hover` закреплён комментарием (безопасный scale), B.4 удалены мёртвые `btn-delete-card:hover{scale}` и sidebar-градиент. **B.6 (канбан-крестики)** — добавлены явные font для ✕/✎ (cross-platform), `.btn-danger` определён в светлой теме (раньше был только в dark), `.btn-danger.btn-sm` выровнен в квадрат 28×28 для иконки, добавлены `title`/`aria-label` в `kanban.js`. |
| Спринт C | ✅ | 2026-08-09 | C.1: введены утилиты `.form-input-flex`, `.form-input-flex--narrow/--wide/--full`, `.text-muted*`, `.empty-state*`, `.flex-row*`, `.h-no-margin`, `.bg-subtle` (style.css:4215+). Заменено 62 повторяющихся inline-стиля в settings.html (28), workflows.html (23), custom_objects.html (11). C.2: унифицирован `showToast` в `settings.html`, `workflows.html`, `admin.html` — все три теперь имеют крестик (✕), иконку типа, transition 200ms + ease-out-strong, idempotent dismiss. C.3: Chart.js переписан на цвета из CSS-переменных (`--primary-color`, `--success-color`, `--text-color`, `--border-color`), глобальный `[data-theme="dark"] canvas{ filter: invert(...) }` удалён (был хрупкий, давал двойную инверсию), добавлено событие `crm:theme-changed` для перерендера графика при смене темы. **C.5** — завершён: на тач-устройствах добавлен `⋯`-триггер (`.card-menu-trigger`), появляется только в `@media (hover: none)`; постоянные `.card-hover-actions` больше не висят на всех карточках, а открываются как popover по тапу. **C.6** — точечная чистка `!important` в новых правилах B.6: базовый цвет `.btn-delete-card { color: var(--danger-color) }` восстановлен без `!important` (специфичность `.card-hover-btn.btn-delete-card` достаточна, 0,2,0 > 0,1,0). Итого 1116 `!important` (было 1118, удалены 2 в новых правилах кнопок масштаба ✕/✎). |
| Спринт D (тёмная тема) | ✅ | 2026-08-09 | Только `index.html` читал `crm_theme` из localStorage до отрисовки; на `settings/workflows/custom_objects/admin` тёмная тема не работала. **D.1** — инициализация на 4 страницы. **D.2** — `admin.html` подключён к общему `style.css`, 25 хардкод-цветов → CSS-переменные. **D.3** — theme-toggle (☀/☾) в сайдбар и шапку admin. **D.4** — все 4 страницы проверены (jsc, braces 0, 391 dark-правило). |
| Баг-фикс E (сайдбар) | ✅ | 2026-08-10 | **Диагноз:** правила сайдбара из блока «Часть 16» (`style.css:7333+`) ожидали классы `.nav-ico` и `.nav-label`, которых **нет в HTML** кнопок «Админ-панель»/«Выйти» — там `<span class="nav-icon">` и голый `<span>`. Два сведения текстов (286+: `span:not(.nav-icon)`) работали, но линия текст+иконка ломалась: у `.nav-icon` не было `flex-shrink:0`, а текст не имел ни `min-width:0` ни `text-overflow`. В collapsed текст вылезал за 40px-квадрат, иконка визуально сжималась (SVG пропадал). **Фикс:** все селекторы 16-го блока приведены к реальной разметке (`.nav-icon`, `span:not(.nav-icon)`), у `.nav-icon` добавлен `flex-shrink:0` и квадратная обёртка 18×18, у подписи — `min-width:0;text-overflow:ellipsis`, в collapsed — `gap:0 !important`. CSS-валиден (braces: 0). Legacy `.nav-label` в nav-кнопках не тронут (контракт не ломаем). |

