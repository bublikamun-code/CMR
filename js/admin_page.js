// === admin_page.js — логика админ-панели ===
// Бывший инлайн-<script> admin.html; вынесен для CSP без 'unsafe-inline'
// (аудит 06.09, С5).
        const API = '';
        const ADMIN_TOKEN_KEY = 'admin_token';
        const ADMIN_ROLE_KEY = 'admin_role';

        window.__onApiUnauthorized = function(tokenKey) {
            logout();
        };

        let token = getToken(ADMIN_TOKEN_KEY);
        let currentRole = '';

        function toggleSidebar() {
            const sidebar = document.getElementById('app-sidebar');
            sidebar.classList.toggle('collapsed');
            try {
                localStorage.setItem('crm_sidebar_collapsed', sidebar.classList.contains('collapsed') ? '1' : '0');
            } catch (e) {}
        }

        function restoreSidebarState() {
            try {
                const _sbv = localStorage.getItem('crm_sidebar_collapsed');
                const collapsed = _sbv === '1' || _sbv === 'true';
                const sidebar = document.getElementById('app-sidebar');
                if (collapsed) sidebar.classList.add('collapsed');
            } catch (e) {}
        }

        document.addEventListener('DOMContentLoaded', () => {
            restoreSidebarState();
            initLogin();
            if (token) showAdminPanel();

            document.getElementById('logout-btn')?.addEventListener('click', logout);

            document.getElementById('compact-toggle')?.addEventListener('click', () => {
                document.body.classList.toggle('compact-mode');
                try {
                    localStorage.setItem('crm_compact', document.body.classList.contains('compact-mode') ? 'true' : 'false');
                } catch (e) {}
            });

            try {
                if (localStorage.getItem('crm_compact') === 'true') document.body.classList.add('compact-mode');
            } catch (e) {}
        });

        function initLogin() {
            const form = document.getElementById('login-form');
            const alertBox = document.getElementById('login-alert');
            const alertText = document.getElementById('login-alert-text');
            const usernameInput = document.getElementById('login-username');
            const passwordInput = document.getElementById('login-password');
            const toggleBtn = document.getElementById('login-toggle-password');
            const submitBtn = document.getElementById('login-submit');

            if (!form) return;

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

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                clearError();
                const username = usernameInput.value.trim();
                const password = passwordInput.value;
                if (!username || !password) { showError('Введите логин и пароль'); return; }

                submitBtn.disabled = true;
                const originalText = submitBtn.querySelector('.btn-text').textContent;
                submitBtn.querySelector('.btn-text').textContent = 'Вход…';
                submitBtn.insertAdjacentHTML('afterbegin', '<span class="spinner"></span>');

                try {
                    const params = new URLSearchParams();
                    params.append('username', username);
                    params.append('password', password);
                    const res = await fetch(`${API}/auth/login`, { method: 'POST', body: params });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || 'Неверный логин или пароль');
                    if (data.role !== 'superadmin' && data.role !== 'admin') {
                        throw new Error('Недостаточно прав для доступа к админке');
                    }
                    token = data.access_token;
                    currentRole = data.role;
                    localStorage.setItem(ADMIN_TOKEN_KEY, token);
                    localStorage.setItem(ADMIN_ROLE_KEY, currentRole);
                    showAdminPanel();
                } catch (err) {
                    showError(err.message);
                    submitBtn.disabled = false;
                    submitBtn.querySelector('.spinner')?.remove();
                    submitBtn.querySelector('.btn-text').textContent = originalText;
                }
            });
        }

        // UI FIX 2026-08-26: обёртка переименована из apiFetch в adminApiFetch.
        // Прежняя функция-декларация из-за хойстинга затирала глобальный
        // apiFetch из js/api.js ещё до выполнения const _globalApiFetch,
        // и обёртка вызывала сама себя: Maximum call stack size exceeded.
        // Из-за этого список пользователей в админке всегда оставался пустым.
        async function adminApiFetch(path, options = {}) {
            // window.apiFetch — глобальный из js/api.js (ключ admin_token)
            return window.apiFetch(`${API}${path}`, options, ADMIN_TOKEN_KEY);
        }

        async function showAdminPanel() {
            document.getElementById('login-screen').classList.add('hidden');
            document.getElementById('admin-app').classList.remove('hidden');
            currentRole = localStorage.getItem(ADMIN_ROLE_KEY) || '';
            document.getElementById('current-user').textContent = `Роль: ${currentRole}`;
            // Фикс аудита 10.09: сетевая ошибка loadUsers роняла showAdminPanel —
            // loadStats не запускался, панель оставалась пустой без сообщений.
            try {
                await loadUsers();
            } catch (err) {
                if (!String(err.message).includes('Не авторизован')) {
                    showToast('Не удалось загрузить пользователей: ' + err.message, 'error');
                }
            }
            await loadStats();
        }

        async function loadUsers() {
            const users = await adminApiFetch('/auth/users');
            document.getElementById('stat-users').textContent = users.length;
            const tbody = document.getElementById('users-table-body');
            const wrap = document.getElementById('users-table-wrap');
            const empty = document.getElementById('users-empty');
            tbody.innerHTML = '';

            if (users.length === 0) {
                wrap.classList.add('hidden');
                empty.classList.remove('hidden');
                empty.innerHTML = '';
                empty.appendChild(renderEmptyState({
                    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
                    title: 'Пользователи не найдены',
                    description: 'Добавьте первого пользователя, чтобы начать работу.'
                }));
                return;
            }

            wrap.classList.remove('hidden');
            empty.classList.add('hidden');
            users.forEach(u => {
                const tr = document.createElement('tr');
                const roleClass = `role-${u.role}`;
                // UI FIX 2026-08-26: компания одна — админ видит и редактирует
                // всех (раньше условие u.tenant_id всегда было ложным: поля
                // не было в ответе API, и кнопки не показывались).
                const canDelete = u.role !== 'superadmin' && (currentRole === 'superadmin' || currentRole === 'admin');
                const canEdit = (u.role !== 'superadmin' || currentRole === 'superadmin');
                tr.innerHTML = `
                    <td>${u.id}</td>
                    <td><b>${escapeHtml(u.username)}</b></td>
                    <td><span class="role-badge ${roleClass}">${u.role}</span></td>
                    <td class="actions">
                        ${canEdit ? '<button class="btn-secondary btn-sm user-edit-btn">Изменить</button>' : ''}
                        ${canDelete ? '<button class="btn-danger btn-sm user-delete-btn">Удалить</button>' : ''}
                    </td>
                `;
                // Аргументы (id/имя/роль) передаются замыканием: inline-onclick
                // запрещён CSP без 'unsafe-inline', а ручное экранирование
                // кавычек в именах пользователей было источником поломок.
                tr.querySelector('.user-edit-btn')?.addEventListener('click', () => editUser(u.id, u.username, u.role));
                tr.querySelector('.user-delete-btn')?.addEventListener('click', () => deleteUser(u.id, u.username));
                tbody.appendChild(tr);
            });
        }

        async function loadStats() {
            const container = document.getElementById('stats-error-container');
            container.innerHTML = '';
            container.classList.add('hidden');
            try {
                const [cards, clients] = await Promise.all([
                    adminApiFetch('/kanban/cards').catch(() => { throw new Error('cards'); }),
                    adminApiFetch('/clients').catch(() => { throw new Error('clients'); })
                ]);
                document.getElementById('stat-cards').textContent = Array.isArray(cards) ? cards.length : 0;
                document.getElementById('stat-clients').textContent = Array.isArray(clients) ? clients.length : 0;
            } catch (e) {
                container.classList.remove('hidden');
                container.appendChild(renderAlert({
                    type: 'error',
                    title: 'Не удалось загрузить статистику',
                    message: 'Проверьте подключение к серверу и попробуйте снова.',
                    onRetry: loadStats
                }));
            }
        }

        function showCreateModal() {
            document.getElementById('create-modal').classList.add('active');
            document.getElementById('new-username').value = '';
            document.getElementById('new-password').value = '';
            document.getElementById('new-role').value = 'manager';
        }
        function closeCreateModal() { document.getElementById('create-modal').classList.remove('active'); }

        async function createUser() {
            const username = document.getElementById('new-username').value.trim();
            const password = document.getElementById('new-password').value;
            const role = document.getElementById('new-role').value;
            if (!username || !password) { showToast('Заполните все поля', 'error'); return; }
            try {
                await adminApiFetch('/auth/users', { method: 'POST', body: JSON.stringify({ username, password, role }) });
                closeCreateModal();
                showToast('Пользователь создан', 'success');
                await loadUsers();
            } catch(e) { showToast(e.message, 'error'); }
        }

        function editUser(id, username, role) {
            document.getElementById('edit-modal').classList.add('active');
            document.getElementById('edit-user-id').value = id;
            document.getElementById('edit-username').value = username;
            document.getElementById('edit-password').value = '';
            document.getElementById('edit-role').value = role;
        }
        function closeEditModal() { document.getElementById('edit-modal').classList.remove('active'); }

        async function updateUser() {
            const id = document.getElementById('edit-user-id').value;
            const password = document.getElementById('edit-password').value;
            const role = document.getElementById('edit-role').value;
            const body = { role };
            if (password) body.password = password;
            try {
                await adminApiFetch(`/auth/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
                closeEditModal();
                showToast('Пользователь обновлён', 'success');
                await loadUsers();
            } catch(e) { showToast(e.message, 'error'); }
        }

        async function deleteUser(id, username) {
            if (!await confirmDialog(`Удалить пользователя "${username}"?`, { okText: 'Удалить', danger: true })) return;
            try {
                await adminApiFetch(`/auth/users/${id}`, { method: 'DELETE' });
                showToast('Пользователь удалён', 'success');
                await loadUsers();
            } catch(e) { showToast(e.message, 'error'); }
        }

        function logout() {
            clearToken(ADMIN_TOKEN_KEY, ADMIN_ROLE_KEY);
            token = null;
            document.getElementById('login-screen').classList.remove('hidden');
            document.getElementById('admin-app').classList.add('hidden');
        }
