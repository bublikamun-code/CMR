document.addEventListener('DOMContentLoaded', () => {
    const token = (typeof getToken === 'function') ? getToken() : null;
    const appContainer = document.querySelector('.app-container');

    if (!token) {
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
    }

    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            // чистим все три хранилища, иначе токен из sessionStorage/cookie
            // остался бы и пользователь не смог бы выйти
            if (typeof clearToken === 'function') {
                clearToken();
            } else {
                try {
                    localStorage.removeItem('crm_token');
                    localStorage.removeItem('crm_role');
                } catch(e) {}
            }
            window.location.reload();
        });
    }
});

function renderLoginScreen() {
    const loginDiv = document.createElement('div');
    loginDiv.id = 'login-screen';
    loginDiv.className = 'login-screen';

    loginDiv.innerHTML = `
        <div class="login-card">
            <h2 class="login-title">Вход в CRM</h2>
            <form id="login-form" novalidate>
                <input type="text" id="username" placeholder="Логин">
                <input type="password" id="password" placeholder="Пароль">
                <button type="submit" class="btn-primary btn-login" onclick="document.getElementById('login-form').dispatchEvent(new Event('submit', {cancelable: true, bubbles: true}))">Войти</button>
            </form>
            <div id="login-error" class="login-error"></div>
        </div>
    `;

    document.body.appendChild(loginDiv);

    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const errorDiv = document.getElementById('login-error');

        const params = new URLSearchParams();
        params.append('username', username);
        params.append('password', password);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/login`, {
                method: 'POST',
                body: params 
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'Неверный логин или пароль');
            }

            // Если localStorage заблокирован (режим инкогнито, запрет сторонних
            // данных, очистка при выходе), setItem падал в ПУСТОЙ catch:
            // токен не сохранялся, страница перезагружалась и снова показывала
            // экран логина — без любых сообщений. Со стороны: «не могу войти».
            let stored = false;
            try {
                localStorage.setItem('crm_token', data.access_token);
                if (data.role) localStorage.setItem('crm_role', data.role);
                stored = localStorage.getItem('crm_token') === data.access_token;
            } catch (e) { stored = false; }

            if (!stored) {
                // запасной путь: сессионное хранилище, затем cookie на сессию
                try {
                    sessionStorage.setItem('crm_token', data.access_token);
                    if (data.role) sessionStorage.setItem('crm_role', data.role);
                    stored = sessionStorage.getItem('crm_token') === data.access_token;
                } catch (e) { stored = false; }
            }
            if (!stored) {
                try {
                    document.cookie = 'crm_token=' + encodeURIComponent(data.access_token) + '; path=/; SameSite=Lax';
                    if (data.role) document.cookie = 'crm_role=' + encodeURIComponent(data.role) + '; path=/; SameSite=Lax';
                    stored = document.cookie.indexOf('crm_token=') >= 0;
                } catch (e) { stored = false; }
            }

            if (!stored) {
                throw new Error('Браузер блокирует сохранение данных сайта. ' +
                    'В Chrome: Настройки → Конфиденциальность → Файлы cookie — разрешите ' +
                    'данные для этого сайта и отключите «Удалять данные при закрытии».');
            }

            window.location.reload();

        } catch (error) {
            errorDiv.textContent = error.message;
            errorDiv.classList.add('visible');
        }
    });
}

function initNavigation() {
    const navButtons = document.querySelectorAll('.nav-btn');
    const pages = document.querySelectorAll('.page-section');

    function activatePage(targetId) {
        if (!document.getElementById(targetId)) return;
        navButtons.forEach(b => b.classList.toggle('active', b.getAttribute('data-target') === targetId));
        pages.forEach(p => p.classList.toggle('active', p.id === targetId));
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
