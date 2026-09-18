/* site-v2: сессия и fetch к CRM API. Токен — Bearer из localStorage
   (конвенция старого фронта) плюс httpOnly-кука, которую ставит сервер. */
(function () {
    'use strict';
    const TOKEN_KEY = 'crm_token';
    const ROLE_KEY = 'crm_role';
    // База API: на проде фронт и API за одним nginx — пустая строка (same-origin).
    // Для локального стенда задаётся в localStorage: crm_api_base=http://127.0.0.1:8125
    const API_BASE = (function () {
        try { return localStorage.getItem('crm_api_base') || window.V2_API_BASE || ''; }
        catch (e) { return window.V2_API_BASE || ''; }
    })();
    function token() {
        try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
    }
    function save(tokenValue, role) {
        try {
            localStorage.setItem(TOKEN_KEY, tokenValue);
            if (role) localStorage.setItem(ROLE_KEY, role);
            localStorage.setItem('crm_logged_in', '1');
        } catch (e) { /* без хранилища — только httpOnly-кука */ }
    }
    function clear() {
        try {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(ROLE_KEY);
            localStorage.removeItem('crm_logged_in');
        } catch (e) { /* ignore */ }
        document.cookie = 'crm_token=; path=/; max-age=0';
    }
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
    async function download(path) {
        const headers = {};
        const t = token();
        if (t) headers['Authorization'] = 'Bearer ' + t;
        return fetch(API_BASE + path, { headers: headers, credentials: 'include' });
    }
    window.V2Api = {
        token: token,
        save: save,
        clear: clear,
        api: api,
        download: download,
        login: function (username, password, remember) {
            return api('/auth/login', { method: 'POST', form: { username: username, password: password, remember: remember ? 'true' : '' } });
        },
        me: function () { return api('/auth/me'); },
        users: function () { return api('/auth/users'); }
    };
})();
