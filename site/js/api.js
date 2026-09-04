// Базовый адрес нашего Python-сервера
const API_BASE_URL = '';

// Ключи хранилища по умолчанию. admin.html использует 'admin_token' —
// передаётся третьим аргументом в apiFetch и явным параметром в getToken/и т.д.
const DEFAULT_TOKEN_KEY = 'crm_token';
const DEFAULT_ROLE_KEY = 'crm_role';

/**
 * Экранирование пользовательских данных перед вставкой через innerHTML.
 * Защищает от XSS: названия сделок, компаний, заметки и т.п. могут содержать < > " & '.
 */
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Есть ли сохранённый токен (используем перед загрузкой данных,
// чтобы не дёргать защищённые эндпоинты на экране логина).
/**
 * Чтение токена с учётом запасных хранилищ.
 * Если Chrome блокирует localStorage (запрет данных сайта, инкогнито,
 * «удалять при закрытии»), вход не срабатывал вообще и без сообщений.
 *
 * tokenKey — имя ключа ('crm_token' для основного приложения,
 * 'admin_token' для админ-панели).
 */
function getToken(tokenKey = DEFAULT_TOKEN_KEY) {
    try {
        const t = localStorage.getItem(tokenKey);
        if (t) return t;
    } catch (e) {}
    try {
        const t = sessionStorage.getItem(tokenKey);
        if (t) return t;
    } catch (e) {}
    try {
        const m = document.cookie.match(new RegExp('(?:^|;\\s*)' + tokenKey + '=([^;]+)'));
        if (m) return decodeURIComponent(m[1]);
    } catch (e) {}
    return null;
}

function getRole(roleKey = DEFAULT_ROLE_KEY) {
    try {
        const r = localStorage.getItem(roleKey);
        if (r) return r;
    } catch (e) {}
    try {
        const r = sessionStorage.getItem(roleKey);
        if (r) return r;
    } catch (e) {}
    try {
        const m = document.cookie.match(new RegExp('(?:^|;\\s*)' + roleKey + '=([^;]+)'));
        if (m) return decodeURIComponent(m[1]);
    } catch (e) {}
    return null;
}

function clearToken(tokenKey = DEFAULT_TOKEN_KEY, roleKey = DEFAULT_ROLE_KEY) {
    try { localStorage.removeItem(tokenKey); localStorage.removeItem(roleKey); } catch (e) {}
    try { sessionStorage.removeItem(tokenKey); sessionStorage.removeItem(roleKey); } catch (e) {}
    // P2-1: маркер входа гасим вместе с токеном (токен теперь в
    // httpOnly-cookie, которую JS не видит — её гасит /auth/logout)
    try { localStorage.removeItem('crm_logged_in'); } catch (e) {}
    try { sessionStorage.removeItem('crm_logged_in'); } catch (e) {}
    try {
        document.cookie = tokenKey + '=; path=/; max-age=0';
        document.cookie = roleKey + '=; path=/; max-age=0';
        document.cookie = 'crm_token=; path=/; max-age=0';
    } catch (e) {}
}

function hasToken(tokenKey = DEFAULT_TOKEN_KEY) {
    if (getToken(tokenKey)) return true;
    // P2-1: сессия на httpOnly-cookie — сам токен JS не виден. Ориентируемся
    // на несекретный маркер входа; реальную валидность сессии проверит
    // сервер, при просрочке сработает 401-хук ниже (clearToken → логин).
    try { if (localStorage.getItem('crm_logged_in')) return true; } catch (e) {}
    try { if (sessionStorage.getItem('crm_logged_in')) return true; } catch (e) {}
    return false;
}

// Предельное время ожидания ответа. Без него оборванное соединение
// (мобильная сеть, спящий ноутбук, перезапуск сервера) оставляет запрос
// висеть неопределённо долго: спиннер крутится, ошибка не показывается,
// пользователь не понимает, сохранились данные или нет.
const API_TIMEOUT_MS = 30000;

/* ---------- Индикатор сетевой активности ----------
   Тонкая полоска сверху экрана: горит, пока идёт хотя бы один запрос.
   Появляется не мгновенно (150мс) — быстрые запросы не должны мерцать. */
const _netIndicator = (() => {
    let bar = null, active = 0, showTimer = null;
    function ensureBar() {
        if (bar && document.body.contains(bar)) return bar;
        bar = document.createElement('div');
        bar.id = 'net-activity-bar';
        document.body.appendChild(bar);
        return bar;
    }
    return {
        start() {
            active++;
            if (active === 1 && !showTimer) {
                showTimer = setTimeout(() => {
                    showTimer = null;
                    const b = ensureBar();
                    b.classList.add('visible');
                }, 150);
            }
        },
        stop() {
            active = Math.max(0, active - 1);
            if (active === 0) {
                if (showTimer) { clearTimeout(showTimer); showTimer = null; }
                if (bar) bar.classList.remove('visible');
            }
        }
    };
})();

/**
 * Универсальный API-клиент.
 *
 * tokenKey — какой ключ токена использовать. Основное приложение
 * работает с 'crm_token' (по умолчанию), админ-панель — с 'admin_token'.
 */
async function apiFetch(endpoint, options = {}, tokenKey = DEFAULT_TOKEN_KEY) {
    const token = getToken(tokenKey);

    // Подготавливаем заголовки
    const headers = {
        ...options.headers,
    };

    // Если мы отправляем файлы (FormData), браузер сам установит нужный Content-Type,
    // в остальных случаях мы явно указываем, что общаемся в формате JSON.
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    // Если токен есть, прикрепляем его как "пропуск"
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    // Загрузка файлов идёт дольше обычных запросов, ей даём больше времени.
    const timeoutMs = options.timeoutMs
        || (options.body instanceof FormData ? API_TIMEOUT_MS * 4 : API_TIMEOUT_MS);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    _netIndicator.start();
    try {
        // Отправляем запрос
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            ...options,
            headers,
            signal: options.signal || controller.signal
        });

        // Если сервер ответил 401 (Не авторизован) - токен истек или неверный
        if (response.status === 401) {
            clearToken(tokenKey);
            // Хук для 401: основное приложение перезагружает страницу,
            // админ-панель показывает экран входа без reload.
            // Переопределяется через window.__onApiUnauthorized.
            const onLoginPage = !token || !!document.getElementById('login-screen');
            if (!window.__reloading && !onLoginPage) {
                window.__reloading = true;
                if (typeof window.__onApiUnauthorized === 'function') {
                    window.__onApiUnauthorized(tokenKey);
                } else {
                    showToast('Сессия истекла. Пожалуйста, войдите снова.', 'error');
                    setTimeout(() => window.location.reload(), 500);
                }
            }
            throw new Error("Не авторизован");
        }

        // Если сервер вернул пустой успешный ответ (например, при удалении)
        if (response.status === 204) {
            return null;
        }

        // Проверяем Content-Type — если сервер вернул HTML (ошибка nginx/сервера)
        const ct = response.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
            const text = await response.text().catch(() => '');
            if (text.startsWith('<html') || text.startsWith('<!DOCTYPE')) {
                throw new Error('Сервер вернул ошибку (возможно, проблема с прокси или размером файла). Статус: ' + response.status);
            }
        }

        // Превращаем ответ сервера в удобный JavaScript-объект
        const data = await response.json();

        // Если статус ошибки (400, 404, 422, 500 и т.д.)
        if (!response.ok) {
            // FastAPI при 422 кладёт в detail МАССИВ объектов —
            // конкатенация со строкой давала «[object Object]».
            let msg = data.detail;
            if (Array.isArray(msg)) {
                msg = msg.map(e => {
                    const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : '';
                    return field ? `${field}: ${e.msg}` : e.msg;
                }).join('; ');
            } else if (msg && typeof msg === 'object') {
                msg = msg.msg || JSON.stringify(msg);
            }
            throw new Error(msg || `Ошибка сервера (${response.status})`);
        }

        return data;
    } catch (error) {
        console.error(`Ошибка при запросе к ${endpoint}:`, error);
        // AbortError без пояснения выглядит для пользователя как «ничего не
        // произошло». Превращаем его в понятную причину.
        if (error.name === 'AbortError') {
            throw new Error(`Сервер не ответил за ${Math.round(timeoutMs / 1000)} с. Проверьте соединение и повторите.`);
        }
        // fetch отклоняется с TypeError, когда сети нет вовсе.
        if (error instanceof TypeError) {
            throw new Error('Нет связи с сервером. Проверьте интернет-соединение.');
        }
        throw error; // Пробрасываем ошибку дальше, чтобы ее мог обработать конкретный скрипт
    } finally {
        clearTimeout(timer);
        _netIndicator.stop();
    }
}