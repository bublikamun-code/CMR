/* ============================================================
   CONTACTS — несколько контактов у клиента и поставщика
   6 августа 2026

   Бэкенд не перезапустить (только FTP, watch:false), поэтому
   новых таблиц и эндпоинтов не добавляем. Список контактов
   храним JSON-ом в существующем текстовом поле contact_person.

   Формат:  [{"name":"Иван","phone":"+375…","email":"a@b.by","role":"снабжение"}]

   Обратная совместимость: если в поле лежит обычный текст
   (как было до сих пор) — читаем его как единственный контакт
   без телефона. Ничего не теряется.

   Первый контакт дублируется в родные поля phone/email, чтобы
   не поехали колонки таблиц и серверный поиск по phone.
   ============================================================ */
(function () {
  'use strict';

  /* ---------- разбор и сборка ---------- */

  function parseContacts(raw, fallbackPhone, fallbackEmail) {
    var list = [];
    var text = (raw == null ? '' : String(raw)).trim();

    if (text.charAt(0) === '[') {
      try {
        var arr = JSON.parse(text);
        if (Array.isArray(arr)) {
          list = arr.filter(function (c) { return c && typeof c === 'object'; })
                    .map(function (c) {
                      return {
                        name: (c.name || '').toString().trim(),
                        phone: (c.phone || '').toString().trim(),
                        email: (c.email || '').toString().trim(),
                        role: (c.role || '').toString().trim()
                      };
                    });
        }
      } catch (e) { list = []; }
    }

    // старый формат: просто имя строкой
    if (!list.length && text) {
      list = [{ name: text, phone: '', email: '', role: '' }];
    }

    // подтягиваем родные phone/email в первый контакт, если там пусто
    if (list.length) {
      if (!list[0].phone && fallbackPhone) list[0].phone = String(fallbackPhone).trim();
      if (!list[0].email && fallbackEmail) list[0].email = String(fallbackEmail).trim();
    } else if (fallbackPhone || fallbackEmail) {
      list = [{
        name: '',
        phone: fallbackPhone ? String(fallbackPhone).trim() : '',
        email: fallbackEmail ? String(fallbackEmail).trim() : '',
        role: ''
      }];
    }

    return list;
  }

  function serializeContacts(list) {
    var clean = (list || []).map(function (c) {
      return {
        name: (c.name || '').trim(),
        phone: (c.phone || '').trim(),
        email: (c.email || '').trim(),
        role: (c.role || '').trim()
      };
    }).filter(function (c) { return c.name || c.phone || c.email || c.role; });

    if (!clean.length) return { contact_person: null, phone: null, email: null };

    // один контакт без роли — храним как обычный текст,
    // чтобы поле осталось читаемым в старых местах
    if (clean.length === 1 && !clean[0].role) {
      return {
        contact_person: clean[0].name || null,
        phone: clean[0].phone || null,
        email: clean[0].email || null
      };
    }

    return {
      contact_person: JSON.stringify(clean),
      phone: clean[0].phone || null,
      email: clean[0].email || null
    };
  }

  /* ---------- краткая запись для таблицы ---------- */

  function summary(raw, fallbackPhone, fallbackEmail) {
    var list = parseContacts(raw, fallbackPhone, fallbackEmail);
    if (!list.length) return { text: '—', title: '', extra: 0 };
    var first = list[0];
    var label = first.name || first.phone || first.email || '—';
    if (first.role) label += ' · ' + first.role;
    var title = list.map(function (c, i) {
      var parts = [c.name || '(без имени)'];
      if (c.role) parts.push(c.role);
      if (c.phone) parts.push(c.phone);
      if (c.email) parts.push(c.email);
      return (i + 1) + '. ' + parts.join(' — ');
    }).join('\n');
    return { text: label, title: title, extra: list.length - 1 };
  }

  /* ---------- редактор в модалке ---------- */

  var ICON_TRASH = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none"' +
    ' stroke="currentColor" stroke-width="1.75" stroke-linecap="round"' +
    ' stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/>' +
    '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

  function rowHTML(c, idx) {
    c = c || {};
    return '' +
      '<div class="contact-row" data-idx="' + idx + '">' +
        '<div class="contact-row-head">' +
          '<span class="contact-row-num">' + (idx + 1) + '</span>' +
          '<input type="text" class="contact-name" placeholder="Имя и фамилия"' +
            ' value="' + escAttr(c.name) + '">' +
          '<input type="text" class="contact-role" placeholder="Должность"' +
            ' value="' + escAttr(c.role) + '">' +
          '<button type="button" class="contact-del" title="Удалить контакт"' +
            ' aria-label="Удалить контакт">' + ICON_TRASH + '</button>' +
        '</div>' +
        '<div class="contact-row-body">' +
          '<input type="tel" class="contact-phone" placeholder="+375 XX XXX-XX-XX"' +
            ' value="' + escAttr(c.phone) + '">' +
          '<input type="email" class="contact-email" placeholder="email@company.by"' +
            ' value="' + escAttr(c.email) + '">' +
        '</div>' +
      '</div>';
  }

  function escAttr(v) {
    return (v == null ? '' : String(v))
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renumber(box) {
    var rows = box.querySelectorAll('.contact-row');
    for (var i = 0; i < rows.length; i++) {
      rows[i].dataset.idx = i;
      var n = rows[i].querySelector('.contact-row-num');
      if (n) n.textContent = i + 1;
      // единственный контакт удалять нечем — прячем корзину
      var del = rows[i].querySelector('.contact-del');
      if (del) del.style.visibility = rows.length > 1 ? 'visible' : 'hidden';
    }
  }

  /**
   * Отрисовать редактор контактов.
   * @param {string} boxId  id контейнера
   * @param {Array}  list   массив контактов
   */
  function render(boxId, list) {
    var box = document.getElementById(boxId);
    if (!box) return;
    var items = (list && list.length) ? list : [{ name: '', phone: '', email: '', role: '' }];
    box.innerHTML = items.map(rowHTML).join('');
    renumber(box);

    if (box.dataset.bound === '1') return;
    box.dataset.bound = '1';

    box.addEventListener('click', function (e) {
      var del = e.target.closest && e.target.closest('.contact-del');
      if (!del) return;
      e.preventDefault();
      var row = del.closest('.contact-row');
      if (!row) return;
      if (box.querySelectorAll('.contact-row').length <= 1) return;
      row.remove();
      renumber(box);
    });
  }

  /** Добавить пустую строку контакта. */
  function add(boxId) {
    var box = document.getElementById(boxId);
    if (!box) return;
    var idx = box.querySelectorAll('.contact-row').length;
    box.insertAdjacentHTML('beforeend', rowHTML({}, idx));
    renumber(box);
    var rows = box.querySelectorAll('.contact-row');
    var last = rows[rows.length - 1];
    var inp = last && last.querySelector('.contact-name');
    if (inp) inp.focus();
  }

  /** Собрать контакты из редактора. */
  function collect(boxId) {
    var box = document.getElementById(boxId);
    if (!box) return [];
    return [].slice.call(box.querySelectorAll('.contact-row')).map(function (r) {
      var g = function (cls) {
        var el = r.querySelector('.' + cls);
        return el ? el.value.trim() : '';
      };
      return { name: g('contact-name'), role: g('contact-role'),
               phone: g('contact-phone'), email: g('contact-email') };
    });
  }

  window.CRM_CONTACTS = {
    parse: parseContacts,
    serialize: serializeContacts,
    summary: summary,
    render: render,
    add: add,
    collect: collect
  };
})();
