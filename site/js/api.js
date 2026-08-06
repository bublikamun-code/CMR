// Базовый адрес нашего Python-сервера
const API_BASE_URL = '';

/**
 * Экранирование пользовательских данных перед вставкой через innerHTML.
 * Защищает от XSS: названия сделок, компаний, заметки и т.п. могут содержать < > " &.
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
 */
function getToken() {
    try {
        const t = localStorage.getItem('crm_token');
        if (t) return t;
    } catch (e) {}
    try {
        const t = sessionStorage.getItem('crm_token');
        if (t) return t;
    } catch (e) {}
    try {
        const m = document.cookie.match(/(?:^|;\s*)crm_token=([^;]+)/);
        if (m) return decodeURIComponent(m[1]);
    } catch (e) {}
    return null;
}

function getRole() {
    try {
        const r = localStorage.getItem('crm_role');
        if (r) return r;
    } catch (e) {}
    try {
        const r = sessionStorage.getItem('crm_role');
        if (r) return r;
    } catch (e) {}
    try {
        const m = document.cookie.match(/(?:^|;\s*)crm_role=([^;]+)/);
        if (m) return decodeURIComponent(m[1]);
    } catch (e) {}
    return null;
}

function clearToken() {
    try { localStorage.removeItem('crm_token'); localStorage.removeItem('crm_role'); } catch (e) {}
    try { sessionStorage.removeItem('crm_token'); sessionStorage.removeItem('crm_role'); } catch (e) {}
    try {
        document.cookie = 'crm_token=; path=/; max-age=0';
        document.cookie = 'crm_role=; path=/; max-age=0';
    } catch (e) {}
}

function hasToken() {
    return !!getToken();
}

// Предельное время ожидания ответа. Без него оборванное соединение
// (мобильная сеть, спящий ноутбук, перезапуск сервера) оставляет запрос
// висеть неопределённо долго: спиннер крутится, ошибка не показывается,
// пользователь не понимает, сохранились данные или нет.
const API_TIMEOUT_MS = 30000;

async function apiFetch(endpoint, options = {}) {
    const token = getToken();
    
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

    try {
        // Отправляем запрос
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            ...options,
            headers,
            signal: options.signal || controller.signal
        });

        // Если сервер ответил 401 (Не авторизован) - токен истек или неверный
        if (response.status === 401) {
            clearToken();
            if (!window.__reloading) {
                window.__reloading = true;
                showToast('Сессия истекла. Пожалуйста, войдите снова.', 'error');
                setTimeout(() => window.location.reload(), 500);
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
    }
}