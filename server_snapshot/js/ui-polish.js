/* ============================================================
   UI POLISH — доработки по аудиту (6 августа 2026)
   Отдельный файл. Не меняет существующую логику: только
   заменяет юникод-глифы на SVG и добавляет доступность модалок.
   Откат: убрать <script src="js/ui-polish.js"> из index.html.
   ============================================================ */
(function () {
  'use strict';

  var VERSION = 8;
  // защита от повторной инициализации: старые экземпляры продолжают
  // жить через свои MutationObserver и мешают новому
  if (window.__uiPolishVersion >= VERSION) return;
  window.__uiPolishVersion = VERSION;
  var FLAG = 'a11yV' + VERSION;

  /* ---------- Единый набор иконок (продолжение ICON_CLIP/ICON_FILE) ---------- */
  var S = '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">';
  var ICONS = {
    chevronLeft:  S + '<polyline points="15 18 9 12 15 6"/></svg>',
    chevronRight: S + '<polyline points="9 18 15 12 9 6"/></svg>',
    sun:          S + '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg>',
    moon:         S + '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
    rows:         S + '<path d="M3 6h18M3 12h18M3 18h18"/></svg>',
    pencil:       S + '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 1 1 3 3L12 15l-4 1 1-4z"/></svg>',
    close:        S + '<path d="M18 6 6 18M6 6l12 12"/></svg>'
  };

  function setIcon(el, name, label) {
    if (!el) return;
    // innerHTML порождает childList-мутацию даже при идентичной строке.
    // На body висит MutationObserver, который вызывает runAll() → setIcon,
    // поэтому безусловная запись зацикливала обход DOM ~8 раз в секунду.
    // Пишем разметку только когда иконка реально сменилась.
    if (el.dataset.uiIcon !== name || !el.firstElementChild) {
      el.innerHTML = ICONS[name];
      el.dataset.uiIcon = name;
    }
    if (label) {
      // title/aria-label не входят в attributeFilter наблюдателя,
      // но лишние записи всё равно ни к чему.
      if (el.getAttribute('title') !== label) el.setAttribute('title', label);
      if (el.getAttribute('aria-label') !== label) el.setAttribute('aria-label', label);
    }
  }

  /* ---------- 1. Глифы ◀ ▶ ☀ ☾ ⊞ ✎ × → SVG ---------- */
  function replaceGlyphs(root) {
    root = root || document;

    // сворачивание сайдбара
    root.querySelectorAll('.sidebar-toggle').forEach(function (b) {
      var collapsed = document.querySelector('.sidebar');
      collapsed = collapsed && collapsed.classList.contains('collapsed');
      setIcon(b, collapsed ? 'chevronRight' : 'chevronLeft',
              collapsed ? 'Развернуть меню' : 'Свернуть меню');
    });

    // тема
    root.querySelectorAll('#theme-toggle, .theme-toggle').forEach(function (b) {
      var dark = document.documentElement.getAttribute('data-theme') === 'dark';
      setIcon(b, dark ? 'moon' : 'sun', dark ? 'Светлая тема' : 'Тёмная тема');
    });

    // плотный режим
    root.querySelectorAll('#compact-toggle, .compact-toggle').forEach(function (b) {
      setIcon(b, 'rows', 'Плотный режим');
    });

    // действия на карточке канбана
    root.querySelectorAll('.btn-edit-card').forEach(function (b) {
      if (b.querySelector('svg')) return;
      setIcon(b, 'pencil', 'Редактировать');
    });
    root.querySelectorAll('.btn-delete-card, .btn-delete-writeoff, .btn-delete-row, .delete-checklist, .checklist-invoice-del').forEach(function (b) {
      if (b.querySelector('svg')) return;
      var t = (b.textContent || '').trim();
      if (t === '×' || t === '\u00d7' || t === 'x' || t === '') setIcon(b, 'close', 'Удалить');
    });

    // крестики закрытия модалок
    root.querySelectorAll('.close-btn').forEach(function (b) {
      if (b.querySelector('svg')) return;
      var t = (b.textContent || '').trim();
      if (t === '×' || t === '\u00d7' || t === 'x' || t === '') {
        setIcon(b, 'close', 'Закрыть');
      }
    });
  }

  var lastFocused = null;
  var lastFocusedSelector = null;

  /* ---------- 2. Название поставщика: <b> → доступная кнопка ---------- */
  function upgradeSupplierLinks(root) {
    (root || document).querySelectorAll('.supplier-name-link').forEach(function (el) {
      if (el.dataset[FLAG]) return;
      el.dataset[FLAG] = '1';
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          e.preventDefault();
          el.click();
        }
      });
      // запоминаем инициатора; таблица перерисовывается, поэтому храним
      // не ссылку на узел, а селектор для повторного поиска
      el.addEventListener('click', function () {
        lastFocusedSelector = '.supplier-name-link[data-id="' + el.dataset.id + '"]';
        lastFocused = el;
      });
    });
    // строки закупок внутри модалки
    (root || document).querySelectorAll('.sup-pur-row').forEach(function (el) {
      if (el.dataset[FLAG]) return;
      el.dataset[FLAG] = '1';
      el.setAttribute('tabindex', '0');
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); el.click(); }
      });
    });
  }

  /* ---------- 3. Модалки: фокус внутрь, Esc, возврат фокуса ---------- */
  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),' +
                  'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  function visibleModals() {
    return [].slice.call(document.querySelectorAll('.modal')).filter(function (m) {
      if (m.classList.contains('hidden')) return false;
      var s = getComputedStyle(m);
      return s.display !== 'none' && s.visibility !== 'hidden';
    });
  }

  function focusInto(modal) {
    var f = modal.querySelectorAll(FOCUSABLE);
    var target = null;
    for (var i = 0; i < f.length; i++) {
      if (f[i].offsetParent !== null && !f[i].classList.contains('close-btn')) { target = f[i]; break; }
    }
    if (!target) target = modal.querySelector('.close-btn') || modal;
    if (target === modal && !modal.hasAttribute('tabindex')) modal.setAttribute('tabindex', '-1');
    try { target.focus({ preventScroll: true }); } catch (e) { try { target.focus(); } catch (e2) {} }
  }

  function closeModal(modal) {
    var btn = modal.querySelector('.close-btn') ||
              modal.querySelector('[id$="-cancel"]');
    if (btn) { btn.click(); } else { modal.classList.add('hidden'); }
  }

  // Esc закрывает верхнюю модалку
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' && e.key !== 'Esc') return;
    var open = visibleModals();
    if (!open.length) return;
    e.preventDefault();
    closeModal(open[open.length - 1]);
  });

  // ловушка фокуса: Tab не уходит под оверлей
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return;
    var open = visibleModals();
    if (!open.length) return;
    var modal = open[open.length - 1];
    var f = [].slice.call(modal.querySelectorAll(FOCUSABLE))
              .filter(function (el) { return el.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    } else if (!modal.contains(document.activeElement)) {
      e.preventDefault(); first.focus();
    }
  });

  // следим за открытием/закрытием модалок
  var modalState = new WeakMap();
  function watchModals() {
    document.querySelectorAll('.modal').forEach(function (m) {
      var isOpen = !m.classList.contains('hidden') &&
                   getComputedStyle(m).display !== 'none';
      var was = modalState.get(m);
      if (isOpen && !was) {
        modalState.set(m, true);
        if (!lastFocused || !document.contains(lastFocused)) {
          lastFocused = document.activeElement;
        }
        replaceGlyphs(m);
        upgradeSupplierLinks(m);
        setTimeout(function () { focusInto(m); }, 30);
      } else if (!isOpen && was) {
        modalState.set(m, false);
        // узел мог быть перерисован — ищем его заново по селектору
        if (lastFocusedSelector) {
          var again = document.querySelector(lastFocusedSelector);
          if (again) lastFocused = again;
        }
        // браузер сбрасывает фокус на body после скрытия модалки —
        // возвращаем его следующим кадром
        var back = lastFocused;
        if (back && document.contains(back)) {
          if (!back.hasAttribute('tabindex') &&
              !/^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(back.tagName)) {
            back.setAttribute('tabindex', '0');
          }
          setTimeout(function () {
            try { back.focus({ preventScroll: true }); } catch (e) {}
          }, 60);
        }
        lastFocused = null;
        lastFocusedSelector = null;
      }
    });
  }

  /* ---------- 4. Защита от двойной отправки ---------- */
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.btn-primary[type="submit"], form .btn-primary');
    if (!b || b.disabled) return;
    b.disabled = true;
    setTimeout(function () { b.disabled = false; }, 1200);
  }, true);

  /* ---------- подсказка для обрезанного текста ---------- */
  // Названия компаний и заголовки таблиц режутся многоточием,
  // но полный текст узнать было негде. Вешаем title только там,
  // где обрезка реально есть, и снимаем, когда её не стало.
  function addTitles(root) {
    var sel = '.clickable-company, .supplier-name-link, th, .checklist-invoice-link';
    var nodes = (root || document).querySelectorAll(sel);

    // Две фазы. Раньше чтение scrollWidth и запись title шли вперемешку:
    // каждая запись инвалидировала layout, и следующее чтение форсировало
    // полный пересчёт. На реестре с 500 строками это давало сотни
    // forced reflow за один проход, а проход шёл каждые 120 мс.
    var plan = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      plan.push({
        el: el,
        cut: el.scrollWidth > el.clientWidth + 2,
        text: (el.textContent || '').trim()
      });
    }

    for (var j = 0; j < plan.length; j++) {
      var p = plan[j];
      if (p.cut) {
        if (p.text && p.el.getAttribute('title') !== p.text) {
          p.el.setAttribute('title', p.text);
          p.el.setAttribute('data-auto-title', '1');
        }
      } else if (p.el.getAttribute('data-auto-title') === '1') {
        p.el.removeAttribute('title');
        p.el.removeAttribute('data-auto-title');
      }
    }
  }

  /* ---------- имена вложений из писем ----------------------------
     Бэкенд кладёт в file_name сырой MIME-заголовок:
       =?UTF-8?B?0KHRh9C10YIucGRm?=   вместо   Счет.pdf
     Сам файл при этом целый (проверено: %PDF-1.5 + %%EOF на месте) —
     кривое только имя. Правильное лечение — decode_header() в
     email_parser_router.py, но бэкенд не перезапустить, поэтому
     раскодируем имя при показе. */
  function b64ToBytes(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function qpToBytes(s) {
    s = s.replace(/_/g, ' ');
    var out = [], i = 0;
    while (i < s.length) {
      if (s.charAt(i) === '=' && i + 2 < s.length) {
        var hex = s.substr(i + 1, 2);
        if (/^[0-9A-Fa-f]{2}$/.test(hex)) { out.push(parseInt(hex, 16)); i += 3; continue; }
      }
      out.push(s.charCodeAt(i)); i++;
    }
    return new Uint8Array(out);
  }

  function decodeMimeWord(charset, enc, text) {
    try {
      var bytes = (enc.toUpperCase() === 'B') ? b64ToBytes(text) : qpToBytes(text);
      var cs = charset.toLowerCase();
      // TextDecoder умеет utf-8, windows-1251, koi8-r — всё, что встречается
      try { return new TextDecoder(cs).decode(bytes); }
      catch (e) { return new TextDecoder('utf-8').decode(bytes); }
    } catch (e) { return null; }
  }

  function decodeMimeName(raw) {
    if (!raw || raw.indexOf('=?') < 0) return raw;
    var re = /=\?([^?]+)\?([BbQq])\?([^?]*)\?=/g;
    var out = raw.replace(re, function (m, cs, enc, txt) {
      var d = decodeMimeWord(cs, enc, txt);
      return d === null ? m : d;
    });
    // между MIME-словами перевод строки и пробел — служебные, убираем
    return out.replace(/\r?\n\s+/g, '').trim();
  }

  // даём доступ остальным модулям (card_modal использует при скачивании)
  window.CRM_DECODE_MIME = decodeMimeName;

  function fixAttachmentNames(root) {
    var sel = '.attachment-link, .checklist-invoice-link';
    var nodes = (root || document).querySelectorAll(sel);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.dataset.nameFixed === '1') continue;
      var txt = (el.textContent || '').trim();
      if (txt.indexOf('=?') < 0) { el.dataset.nameFixed = '1'; continue; }
      var good = decodeMimeName(txt);
      if (good && good !== txt) {
        // текст лежит рядом с иконкой — меняем только текстовые узлы
        var replaced = false;
        for (var k = 0; k < el.childNodes.length; k++) {
          var n = el.childNodes[k];
          if (n.nodeType === 3 && n.textContent.indexOf('=?') >= 0) {
            n.textContent = ' ' + good; replaced = true;
          }
        }
        if (!replaced) el.textContent = good;
        el.setAttribute('title', good);
      }
      el.dataset.nameFixed = '1';
    }
  }

  /* ---------- запуск и слежение за перерисовками ---------- */
  function runAll() {
    fixAttachmentNames();
    replaceGlyphs();
    upgradeSupplierLinks();
    watchModals();
    addTitles();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runAll);
  } else {
    runAll();
  }

  var pending = null;
  var scheduleRunAll = function () {
    if (pending) return;
    pending = setTimeout(function () { pending = null; runAll(); }, 250);
  };

  // Слушаем только childList: перерисовки таблиц и досок нам нужны.
  // attributeFilter:['class'] убран намеренно — класс здесь меняется
  // постоянно (активный раздел, состояния, drag-over на каждое событие
  // dragover ~60 раз в секунду), и каждая такая смена дёргала полный
  // обход DOM. Смена темы и сайдбара покрыта отдельным обработчиком ниже.
  new MutationObserver(scheduleRunAll)
    .observe(document.body, { childList: true, subtree: true });

  // тема/сайдбар переключаются чужим кодом — обновляем иконку после клика
  document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    if (e.target.closest('.sidebar-toggle, #theme-toggle, .theme-toggle, #compact-toggle')) {
      setTimeout(replaceGlyphs, 60);
    }
    // Раньше смену раздела ловил наблюдатель за классом .active.
    // Теперь вызываем явно: на новой странице надо пересчитать обрезку
    // заголовков и подтянуть иконки.
    if (e.target.closest('.nav-btn[data-target]')) scheduleRunAll();
  });
})();
