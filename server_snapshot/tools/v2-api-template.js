/* site-v2: сессия и fetch к CRM API. Токен — Bearer из localStorage
   (конвенция старого фронта) плюс httpOnly-кука, которую ставит сервер. */
(function () {
    'use strict';
    const TOKEN_KEY = 'crm_token';
    const ROLE_KEY = 'crm_role';
    // Подтверждённая /auth/me роль держится и в памяти модуля: браузер с
    // заблокированным localStorage входит по httpOnly-куке, и без памяти
    // админ увидел бы скрытым собственный раздел «Управление».
    let roleMemory = null;
    // Подтверждённый /auth/me id текущего пользователя — только в памяти, в
    // localStorage его нет: прошлый id пережил бы выход и ошибся бы при входе
    // под другим человеком без F5, а boot перечитывает /auth/me на каждом старте.
    let userIdMemory = null;
    // База API: на проде фронт и API за одним nginx — пустая строка (same-origin).
    // Для локального стенда задаётся в localStorage: crm_api_base=http://127.0.0.1:8125
    const API_BASE = (function () {
        try { return localStorage.getItem('crm_api_base') || window.V2_API_BASE || ''; }
        catch (e) { return window.V2_API_BASE || ''; }
    })();
    function token() {
        try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
    }
    function save(tokenValue, role, meId) {
        if (role) roleMemory = role;
        // Третий аргумент — id из /auth/me (boot читает его на каждом старте).
        // Ответ /auth/login id не отдаёт, поэтому «значения нет» означает
        // «не менять»: подтверждённый id не затирается догадками.
        if (meId !== undefined && meId !== null && meId !== '' && isFinite(Number(meId))) {
            userIdMemory = Number(meId);
        }
        try {
            localStorage.setItem(TOKEN_KEY, tokenValue);
            if (role) localStorage.setItem(ROLE_KEY, role);
            localStorage.setItem('crm_logged_in', '1');
        } catch (e) { /* без хранилища — только httpOnly-кука */ }
    }
    function clear() {
        roleMemory = null;
        userIdMemory = null;
        try {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(ROLE_KEY);
            localStorage.removeItem('crm_logged_in');
        } catch (e) { /* ignore */ }
        document.cookie = 'crm_token=; path=/; max-age=0';
    }
    // ── Роль (пункт 9 плана: ролевая модель в UI) ──────────────────────
    // Единственная точка чтения роли для всего интерфейса: то же значение,
    // которое boot кладёт сюда после /auth/me через save() (B6), с падением
    // в localStorage — серверная роль подтверждена при каждом старте.
    function currentRole() {
        if (roleMemory) return roleMemory;
        try { return localStorage.getItem(ROLE_KEY); } catch (e) { return null; }
    }
    // Совпадает с серверным require_admin() (auth.py): права администратора
    // только у admin и superadmin. Роль неизвестна — предпросмотр мокапа по
    // file:// или ?demo=1, где сеанса нет: ничего не скрываем, там UI показан
    // целиком намеренно. Есть токен, но нет роли — считаем не-админом.
    function isAdmin() {
        const role = currentRole();
        if (!role) return !token();
        return role === 'admin' || role === 'superadmin';
    }
    // ── Свой id (пункт 15 плана: «себя не отключить и не удалить») ───────
    // Единственная точка чтения «это я или нет» — то же значение, которое boot
    // кладёт сюда после /auth/me через save() (там же, где роль). Отдельного
    // запроса ради этого не делаем. Сеанса нет (предпросмотр мокапа по file://,
    // ?demo=1) или /auth/me не ответил — null; потребители обязаны это переживать.
    function userId() { return userIdMemory; }
    async function api(path, opts) {
        opts = opts || {};
        const headers = Object.assign({}, opts.headers || {});
        const t = token();
        if (t) headers['Authorization'] = 'Bearer ' + t;
        if (opts.form) headers['Content-Type'] = 'application/x-www-form-urlencoded';
        else if (opts.body) headers['Content-Type'] = 'application/json';
        const response = await fetch(API_BASE + path, {
            method: opts.method || 'GET',
            headers: headers,
            credentials: 'include',
            body: opts.form ? new URLSearchParams(opts.form) : (opts.body ? JSON.stringify(opts.body) : undefined)
        });
        if (response.status === 401) {
            const err = new Error('unauthorized');
            err.unauthorized = true;
            throw err;
        }
        if (!response.ok) {
            const err = new Error('HTTP ' + response.status);
            err.status = response.status;
            try { const data = await response.json(); err.detail = data && data.detail || null; } catch (e) { /* без тела */ }
            throw err;
        }
        return response.json();
    }
    // Скачивание файлов: сырой Response с авторизацией (токен + кука),
    // тело читает вызывающий (blob) — api() парсит JSON и не годится.
    async function download(path, opts) {
        opts = opts || {};
        const headers = {};
        const t = token();
        if (t) headers['Authorization'] = 'Bearer ' + t;
        return fetch(API_BASE + path, {
            headers: headers,
            credentials: 'include',
            signal: opts.signal
        });
    }
    // Загрузка файла: multipart с авторизацией (Content-Type ставит браузер)
    async function upload(path, file) {
        const headers = {};
        const t = token();
        if (t) headers['Authorization'] = 'Bearer ' + t;
        const fd = new FormData();
        fd.append('file', file);
        return fetch(API_BASE + path, { method: 'POST', headers: headers, credentials: 'include', body: fd });
    }
    // ── Хелперы ────────────────────────────────────────────────────────

    // Рубли → копейки (целое). Устойчив к float (0.07 → 7),
    // строкам с пробелами и запятой ('1 234,56' → 123456).
    // Контракт: number|string → int; NaN/undefined/null → 0.
    function cents(value) {
        if (value === null || value === undefined || value === '') return 0;
        if (typeof value === 'number') {
            return isFinite(value) ? Math.round(value * 100) : 0;
        }
        var s = String(value).replace(/\s/g, '').replace(',', '.');
        var n = parseFloat(s);
        return isNaN(n) ? 0 : Math.round(n * 100);
    }

    // Сегодня по UTC в формате YYYY-MM-DD (не зависит от локального пояса).
    function todayUTC() {
        var d = new Date();
        var y = d.getUTCFullYear();
        var m = String(d.getUTCMonth() + 1).padStart(2, '0');
        var day = String(d.getUTCDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    // ── Двойной сабмит: in-flight блокировка POST-созданий ────────────
    var _inflight = {};
    function _guardCreate(key, fn) {
        if (_inflight[key]) return Promise.resolve(null);
        _inflight[key] = true;
        return Promise.resolve().then(fn).then(function (r) {
            delete _inflight[key]; return r;
        }, function (e) { delete _inflight[key]; throw e; });
    }

    // ── V2Api (обратно совместимый) ────────────────────────────────────
    window.V2Api = {
        token: token,
        save: save,
        clear: clear,
        currentRole: currentRole,
        isAdmin: isAdmin,
        userId: userId,
        api: api,
        download: download,
        upload: upload,
        // B5: in-flight guard для POST-созданий (используется boot.js)
        _guardCreate: _guardCreate,
        login: function (username, password, remember) {
            return api('/auth/login', { method: 'POST', form: { username: username, password: password, remember: remember ? 'true' : '' } });
        },
        me: function () { return api('/auth/me'); },
        users: function () { return api('/auth/users'); }
    };

    // ── KBApi (публичный слой данных v2) ───────────────────────────────
    // Все экранные модули читают KBData и вызывают KBApi.*;
    // V2Api остаётся для обратной совместимости.
    window.KBApi = {
        // Авторизация
        token: token,
        save: save,
        clear: clear,
        // HTTP
        api: api,
        download: download,
        upload: upload,
        // Утилиты
        cents: cents,
        todayUTC: todayUTC,
        // Баланс клиента (A11): GET /clients/{id}/balance
        clientBalance: function (id) {
            return api('/clients/' + id + '/balance');
        },
        // Служебное: boot.js регистрирует загрузчик данных здесь
        _setFetcher: function (fn) { this._fetcher = fn; },
        _fetcher: null,
        // Перезагрузка всех коллекций с сервера (B9: коалесинг).
        // Параллельные вызовы возвращают один Promise; данные обновляются
        // только если загрузка осталась последней (generation counter).
        all: function () {
            if (!this._fetcher) return Promise.resolve(null);
            if (this._inFlight) return this._inFlight;
            var self = this;
            var gen = (this._gen || 0) + 1;
            this._gen = gen;
            this._inFlight = this._fetcher().then(function (data) {
                self._inFlight = null;
                if (self._gen !== gen) return data; // стартует более новая — не применяем
                return data;
            }, function (err) {
                self._inFlight = null;
                throw err; // ошибка пробрасывается, не кешируется
            });
            return this._inFlight;
        },
        _inFlight: null,
        _gen: 0
    };
})();
