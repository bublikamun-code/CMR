document.addEventListener('DOMContentLoaded', () => {
    // P2-1: токен теперь в httpOnly-cookie (JS его не видит). Логин-экран
    // решаем по несекретному маркеру; если маркер есть, а сессия истекла —
    // первый же 401 в api.js вернёт на логин.
    const token = (typeof getToken === 'function') ? getToken() : null;
    let marker = null;
    try { marker = localStorage.getItem('crm_logged_in'); } catch (e) {}
    if (!marker) { try { marker = sessionStorage.getItem('crm_logged_in'); } catch (e) {} }
    const appContainer = document.querySelector('.app-container');

    if (!token && !marker) {
        appContainer.style.display = 'none';
        renderLoginScreen();
    } else {
        initNavigation();
        initMobileMenu();
        const role = (typeof getRole === 'function') ? getRole() : null;
        const adminLink = document.getElementById('admin-link');
        if (adminLink && (role === 'superadmin' || role === 'admin')) {
            adminLink.classList.remove('hidden');
        }
        applyRoleToSettingsTabs(role);
    }

    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            // чистим все три хранилища, иначе маркер/роль из sessionStorage
            // остался бы и пользователь не смог бы выйти
            if (typeof clearToken === 'function') {
                clearToken();
            } else {
                try {
                    localStorage.removeItem('crm_token');
                    localStorage.removeItem('crm_role');
                } catch(e) {}
            }
            try { localStorage.removeItem('crm_logged_in'); } catch (e) {}
            try { sessionStorage.removeItem('crm_logged_in'); } catch (e) {}
            // сервер гасит httpOnly-куку (P2-1); fire-and-forget — выход
            // не должен зависеть от ответа
            try {
                fetch(`${API_BASE_URL}/auth/logout`, { method: 'POST', credentials: 'same-origin' });
            } catch (e) {}
            window.location.reload();
        });
    }
});

function renderLoginScreen() {
    const loginDiv = document.createElement('div');
    loginDiv.id = 'login-screen';
    loginDiv.className = 'login-screen';

    loginDiv.innerHTML = `
        <div class="login-brand">
            <div class="login-brand__logo">
                <img src="/static/logo.svg" alt="CRM Свет в доме" width="56" height="56">
            </div>
            <div class="login-brand__name">CRM «Снабжение и Продажи»</div>
            <div class="login-brand__tagline">Управление сделками, оплатами и документами</div>
        </div>
        <div class="login-card">
            <div class="login-card__header">
                <h1 class="login-card__title">Вход в CRM</h1>
                <p class="login-card__subtitle">Введите логин и пароль</p>
            </div>
            <div id="login-alert" class="login-alert">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                <span id="login-alert-text"></span>
            </div>
            <form id="login-form" novalidate>
                <div class="login-form-group">
                    <label for="username">Логин</label>
                    <input type="text" id="username" class="login-input" placeholder="user@example" autocomplete="username">
                </div>
                <div class="login-form-group">
                    <label for="password">Пароль</label>
                    <div class="login-input-wrap">
                        <input type="password" id="password" class="login-input" placeholder="••••••" autocomplete="current-password">
                        <button type="button" id="login-toggle-password" class="login-password-toggle" aria-label="Показать пароль">
                            <svg id="eye-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                        </button>
                    </div>
                </div>
                <label class="login-remember" for="login-remember">
                    <input type="checkbox" id="login-remember" checked>
                    <span>Запомнить меня на этом устройстве (30 дней)</span>
                </label>
                <button type="button" id="login-submit" class="btn-primary btn-login">
                    <span class="btn-text">Войти</span>
                </button>
            </form>
        </div>
        <div class="login-footer">© CRM Свет в доме</div>
    `;

    document.body.appendChild(loginDiv);

    const form = document.getElementById('login-form');
    const alertBox = document.getElementById('login-alert');
    const alertText = document.getElementById('login-alert-text');
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const toggleBtn = document.getElementById('login-toggle-password');
    const submitBtn = document.getElementById('login-submit');

    function showError(msg) {
        alertText.textContent = msg;
        alertBox.classList.add('visible');
        usernameInput.classList.add('login-input--error');
        passwordInput.classList.add('login-input--error');
    }
    function clearError() {
        alertBox.classList.remove('visible');
        usernameInput.classList.remove('login-input--error');
        passwordInput.classList.remove('login-input--error');
    }

    toggleBtn.addEventListener('click', () => {
        const isPass = passwordInput.type === 'password';
        passwordInput.type = isPass ? 'text' : 'password';
        toggleBtn.setAttribute('aria-label', isPass ? 'Скрыть пароль' : 'Показать пароль');
        toggleBtn.innerHTML = isPass
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    });

    [usernameInput, passwordInput].forEach(el => el.addEventListener('input', clearError));

    function resetButton() {
        submitBtn.disabled = false;
        const spinner = submitBtn.querySelector('.spinner');
        if (spinner) spinner.remove();
        const text = submitBtn.querySelector('.btn-text');
        if (text) text.textContent = 'Войти';
    }

    async function doLogin() {
        clearError();
        const username = usernameInput.value.trim();
        const password = passwordInput.value;

        if (!username || !password) {
            showError('Введите логин и пароль');
            return;
        }

        submitBtn.disabled = true;
        const text = submitBtn.querySelector('.btn-text');
        if (text) text.textContent = 'Вход…';
        submitBtn.insertAdjacentHTML('afterbegin', '<span class="spinner"></span>');

        const params = new URLSearchParams();
        params.append('username', username);
        params.append('password', password);
        // «Запомнить меня» (фидбек 07.09): сервер выпустит токен и куку
        // на 30 дней вместо 24 часов + включит скользящее продление.
        if (document.getElementById('login-remember')?.checked) {
            params.append('remember', '1');
        }

        const controller = new AbortController();
        const loginTimeout = setTimeout(() => controller.abort(), 15000);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/login`, {
                method: 'POST',
                body: params,
                signal: controller.signal
            });
            clearTimeout(loginTimeout);

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'Неверный логин или пароль');
            }

            // P2-1 (аудит 04.09): токен живёт в httpOnly-cookie, которую
            // ставит сервер. Здесь храним только несекретный маркер входа
            // (для выбора «логин-экран или приложение») и роль для UI.
            let stored = false;
            try {
                localStorage.setItem('crm_logged_in', '1');
                if (data.role) localStorage.setItem('crm_role', data.role);
                stored = localStorage.getItem('crm_logged_in') === '1';
            } catch (e) { stored = false; }

            if (!stored) {
                try {
                    sessionStorage.setItem('crm_logged_in', '1');
                    if (data.role) sessionStorage.setItem('crm_role', data.role);
                    stored = sessionStorage.getItem('crm_logged_in') === '1';
                } catch (e) { stored = false; }
            }

            if (!stored) {
                throw new Error('Браузер блокирует сохранение данных сайта. ' +
                    'В Chrome: Настройки → Конфиденциальность → Файлы cookie — разрешите ' +
                    'данные для этого сайта и отключите «Удалять данные при закрытии».');
            }

            window.location.reload();
        } catch (error) {
            clearTimeout(loginTimeout);
            const msg = error.name === 'AbortError'
                ? 'Сервер не отвечает. Проверьте соединение и попробуйте снова.'
                : (error.message || 'Ошибка входа');
            showError(msg);
            resetButton();
        }
    }

    // Основной вход по клику на кнопку
    submitBtn.addEventListener('click', (e) => {
        e.preventDefault();
        if (submitBtn.disabled) return;
        doLogin();
    });

    // Enter в полях формы тоже должен входить
    form.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            if (!submitBtn.disabled) doLogin();
        }
    });
}

// FIX 2026-08-30 (роли): вкладки «Кастомные объекты»/«Воркфлоу»/«Webhooks»
// на бэкенде закрыты за админом (403), прячем их и в UI, чтобы обычный
// пользователь не натыкался на ошибки. Нет сохранённой роли (старая сессия) —
// вкладки тоже скрыты: безопаснее лишний раз не показать, бэкенд всё равно
// проверит роль по токену. Дефолтной вкладкой для не-админа становится «Почта».
function applyRoleToSettingsTabs(role) {
    const isAdmin = role === 'superadmin' || role === 'admin';
    if (isAdmin) return;
    document.querySelectorAll('.settings-tab[data-admin-only]').forEach(t => t.classList.add('hidden'));
    const objectsPane = document.getElementById('stab-objects');
    const objectsTab = document.querySelector('.settings-tab[data-admin-only].active');
    if (objectsPane && objectsTab && objectsPane.classList.contains('active')) {
        objectsTab.classList.remove('active');
        objectsPane.classList.remove('active');
        // Фикс аудита 10.09: селектор с onclick мёртв ещё с ухода от
        // инлайн-обработчиков (CSP) — вкладка «Почта» не активировалась и
        // менеджер видел пустую страницу настроек.
        const emailTab = document.querySelector('.settings-tab[data-tab="email"]');
        const emailPane = document.getElementById('stab-email');
        if (emailTab && emailPane) {
            emailTab.classList.add('active');
            emailPane.classList.add('active');
        }
    }
}

function initNavigation() {
    const navButtons = document.querySelectorAll('.nav-btn');
    const pages = document.querySelectorAll('.page-section');

    function activatePage(targetId) {
        if (!document.getElementById(targetId)) return;
        navButtons.forEach(b => b.classList.toggle('active', b.getAttribute('data-target') === targetId));
        pages.forEach(p => p.classList.toggle('active', p.id === targetId));
        // Анимации для элементов, которые стали видимы после смены страницы
        if (typeof window.revealRefresh === 'function') {
            window.revealRefresh(document.getElementById(targetId));
        }
        // Раздел показывается мгновенно из текущего DOM (кеш последнего
        // состояния); если данные устарели — обновятся плавно в фоне.
        if (window.CRM_FRESHNESS) window.CRM_FRESHNESS.refreshIfStale(targetId);
    }

    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-target');
            activatePage(targetId);
            closeMobileMenu();
            try { localStorage.setItem('crm_active_page', targetId); } catch (e) {}
        });
    });

    // без try/catch здесь падала вся initNavigation, если localStorage закрыт —
    // навигация переставала работать целиком
    let saved = null;
    try { saved = localStorage.getItem('crm_active_page'); } catch (e) { saved = null; }
    if (saved) activatePage(saved);
}

function initMobileMenu() {
    const toggle = document.getElementById('mobile-menu-toggle');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.getElementById('mobile-overlay');
    if (!toggle || !sidebar || !overlay) return;

    toggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    });

    overlay.addEventListener('click', closeMobileMenu);
}

function closeMobileMenu() {
    document.querySelector('.sidebar')?.classList.remove('open');
    document.getElementById('mobile-overlay')?.classList.remove('open');
}
