# CRM Svetvdome — Deployment Guide

This project has **two deployment paths**. Pick the one that matches your
target environment:

| Path | Where it runs | Status | When to use |
|---|---|---|---|
| **Bare-metal (PM2)** ← *active production* | `87.232.64.12` (hoster.by) | ✅ Live | The current production server |
| **Docker Compose** | Any Docker host | 🧪 Tested, not on prod | New deployments, local dev, migration target |

> The Docker path is fully built and tested (both containers healthy, data
> survives `down`/`up`), but the live production server still runs on PM2.
> See [`docs/phases/`](../phases/) for the migration roadmap.

---

## Quick Start (Local Development)

```bash
# Start services
docker compose up -d

# Check status
docker ps
docker compose logs -f backend

# Access
http://localhost
API: http://localhost:8000
```

---

## Локальная проверка перед передачей изменений

Из корня проекта:

```bash
bash scripts/check.sh
# Другой уже подготовленный Python:
PYTHON=/absolute/path/to/venv/bin/python bash scripts/check.sh
```

Нужны Bash, Node.js, macOS JavaScriptCore (его использует `tools/check_js.sh`),
зависимости `server_snapshot/requirements.txt`, pytest и httpx. По умолчанию
выбирается `server_snapshot/.venv/bin/python`. Скрипт ничего не устанавливает:
отсутствие предпосылок завершает его с кодом 2, без пропуска проверок.

Проверяются shell-синтаксис, JS-синтаксис, статические ссылки `data-handler`,
штампы ресурсов и весь `server_snapshot/tests`. После проверки предпосылок
выполняются все этапы с сохранением первого ненулевого кода. Pytest работает
во временной папке с тестовыми ключами и изолированными БД/uploads; отдельного
импорта приложения в рабочем каталоге нет. Существующий почтовый тест делает
попытку IMAP-подключения к `127.0.0.1`; сетевые службы для тестов не запускаются.

Это локальная проверка, не деплой и не подтверждение состояния прода. Она не
запускает Docker, SSH, PM2, seed или сервер приложения. Статический анализ
обработчиков не заменяет проверку интерфейса в браузере. Полный гейт пока
привязан к macOS из-за JavaScriptCore; отсутствие этого инструмента не считается
успешной проверкой на другой ОС.

---

## Production: Bare-metal via PM2 (active)

The current production runs on a shared hoster.by server. Deployment is done
over SSH+rsync with `tools/deploy.sh` — this replaced the legacy FTP flow.

### Prerequisites

- SSH key at `~/.ssh/crm_svetvdome_deploy` (key-based auth, no password)
- Server: `h212005@87.232.64.12`, app at `/var/www/h212005/data/www/cmr-svetvdome.online/`
- PM2 process named `crm` running `server.py` on port 20008

### Deploy frontend

```bash
tools/deploy.sh front
```

Runs JS syntax check (`tools/check_js.sh`) and asset-version verification
(`tools/stamp_assets.py --check`), then rsyncs `site/` to the server webroot.
Excludes `*.db`, `uploads/`, `tenants/`, `.secret_key`, `.env` — server-side
state is never overwritten. Static files need no restart.

### Deploy backend

```bash
tools/deploy.sh back main.py routers/clients_router.py
```

Runs `py_compile` locally, syncs the listed files, then **restarts the PM2
process** (`pm2 restart crm --update-env`) — Python code is loaded at import
time and won't take effect otherwise. Checks error logs after restart.

Перед рестартом обязателен доступный каталог `REMOTE_DIR` с `.pm2.env`:
переменные экспортируются до `pm2 restart --update-env`. Ошибка перехода,
отсутствие файла или ненулевой результат его загрузки прерывают операцию без
рестарта. Это не проверка полноты всех переменных внутри файла.
`tools/deploy.sh --dry-run restart` не вызывает SSH, PM2 или ожидание.
Изменения скрипта проверены локальными заглушками, не перезапуском прода.

### Operational commands

```bash
tools/deploy.sh status        # PM2 process list
tools/deploy.sh restart       # Restart PM2 process only
tools/deploy.sh logs 50       # Last 50 lines of error log
tools/deploy.sh --dry-run front   # Preview without transferring
```

### Environment variables

The script reads these (all have sensible defaults, override via env):

| Variable | Default | Purpose |
|---|---|---|
| `SSH_KEY` | `~/.ssh/crm_svetvdome_deploy` | Path to private key |
| `SSH_USER` | `h212005` | Server user |
| `SSH_HOST` | `87.232.64.12` | Server host |
| `REMOTE_DIR` | `/var/www/h212005/data/www/cmr-svetvdome.online` | Web root |
| `PM2_APP` | `crm` | PM2 process name |

### Backup on the server

The server has its own backup scripts under `server_snapshot/scripts/`
(`backup_crm.sh` uses the SQLite backup API — **not** `cp`, which corrupts
WAL databases). For Docker, see the backup section below.

> **⚠️ The legacy FTP flow (`tools/ftp_sync.py`) was removed** (2026-08-29):
> it sent the password in cleartext and failed the security review. Use
> `tools/deploy.sh` (SSH + rsync) instead.

---

## Production: Docker Compose (alternative)

### Prerequisites

- Docker & Docker Compose installed
- SSL certificate (or Let's Encrypt ready)
- `.env.production` file with secrets (NOT in git)

### Step 1: Generate Security Keys

```bash
bash scripts/generate_keys.sh > .env.production
```

**IMPORTANT:** Never commit `.env.production` to git!

### Step 2: Configure SSL (HTTPS)

**Option A: Self-signed certificate (testing only)**

```bash
bash scripts/setup_ssl.sh --self-signed
```

**Option B: Let's Encrypt (recommended)**

Port 80 must be reachable from the internet and the domain must already resolve
to this host.

```bash
bash scripts/setup_ssl.sh your-domain.com admin@your-domain.com
```

Both write `ssl/cert.pem` and `ssl/key.pem` — the paths `nginx/https.conf` expects.

### Step 3: Point the config at your domain

The HTTPS config already exists at `nginx/https.conf`; nothing needs
uncommenting. Set the real domain:

```bash
# nginx/https.conf
server_name your-domain.com;
```

`docker-compose.production.yml` already mounts `nginx/https.conf`, the shared
snippets and `ssl/`. The HTTP block keeps serving `/.well-known/acme-challenge/`
(for renewals) and `/health` (for the container healthcheck) over plain HTTP, and
redirects everything else to HTTPS.

> The old instruction to uncomment an HTTPS block inside `nginx.conf` no longer
> applies. That block contained a stale copy of the location rules proxying the
> non-existent `/api/` prefix, so enabling it would have broken every API call.
> HTTP and HTTPS now include the same snippets from `nginx/snippets/`.

### Step 4: Deploy

```bash
# Build images
docker compose -f docker-compose.production.yml build

# Start services
docker compose -f docker-compose.production.yml up -d

# Check status
docker compose -f docker-compose.production.yml ps
docker compose -f docker-compose.production.yml logs backend
```

### Step 5: Test

```bash
# API health
curl https://your-domain.com/health

# Frontend
open https://your-domain.com

# Security headers
curl -I https://your-domain.com
# Should include Strict-Transport-Security, X-Content-Type-Options, etc.
```

---

## Cron Job Configuration

For scheduled email sync (`/email-parser/sync-all`), you need the CRON_TOKEN.

### Get the token

```bash
# From .env.production
grep CRON_TOKEN .env.production

# Or from running container
docker exec crm-backend cat /app/.cron_token
```

### Schedule the cron job

**On your host machine:**

```bash
# Create sync script
cat > /usr/local/bin/crm-sync-emails.sh << 'EOF'
#!/bin/bash
CRON_TOKEN=$(cat /path/to/.env.production | grep CRON_TOKEN | cut -d= -f2)
curl -X POST https://your-domain.com/email-parser/sync-all \
  -H "X-Cron-Token: $CRON_TOKEN" \
  -H "Content-Type: application/json"
EOF

chmod +x /usr/local/bin/crm-sync-emails.sh

# Add to crontab (runs every 15 minutes)
(crontab -l 2>/dev/null; echo "*/15 * * * * /usr/local/bin/crm-sync-emails.sh") | crontab -
```

### Notification overdue sync

`POST /notifications/sync-overdue` (same `X-Cron-Token` guard) creates
notifications about overdue tasks, deals and payments. Run it once a day
(so a repeated failure of one day's run does not spam duplicates — unread
duplicates are suppressed per recipient/entity):

```bash
cat > /usr/local/bin/crm-sync-overdue.sh << 'EOF'
#!/bin/bash
CRON_TOKEN=$(cat /path/to/.env.production | grep CRON_TOKEN | cut -d= -f2)
curl -X POST https://your-domain.com/notifications/sync-overdue \
  -H "X-Cron-Token: $CRON_TOKEN"
EOF
chmod +x /usr/local/bin/crm-sync-overdue.sh

(crontab -l 2>/dev/null; echo "30 6 * * * /usr/local/bin/crm-sync-overdue.sh") | crontab -
```

---

## Database Backup & Recovery

### Automated Backup

Use `scripts/backup_db.sh`. It backs up the main database and every tenant
database, verifies each copy with `PRAGMA integrity_check`, compresses it and
prunes old generations.

```bash
CONTAINER=crm-backend-prod bash scripts/backup_db.sh /backups/crm
```

Schedule it daily at 03:00:

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * cd $PWD && CONTAINER=crm-backend-prod bash scripts/backup_db.sh /backups/crm >> /backups/crm/backup.log 2>&1") | crontab -
```

Keep more generations with `KEEP=30` (default 14).

`KEEP` должен быть целым числом от 1 до 999999999. Основная БД обязательна;
каждый найденный tenant сохраняется отдельно. Снимки создаются в уникальном
каталоге контейнера, копируются в приватный локальный staging и сжимаются до
публикации/ротации. При отказе snapshot/copy/gzip старые архивы не удаляются.
Ротация оставляет KEEP архивов **каждой отдельной БД**, а не KEEP на всех tenants.
Новые имена включают timestamp и уникальный суффикс; старые timestamp-имена
также участвуют в ротации. Архивы имеют права 600. Очистка касается только
временных каталогов текущего запуска.

Отдельный `scripts/backup_crm_prod.sh` — локальная копия скрипта для PM2-пути,
с переменными `APP`, `DATA`, `DST`, `KEEP`. Он требует sqlite3/gzip/tar, сохраняет
БД, имеющиеся uploads, код и настройки. Все обязательные части создаются до
ротации; отсутствующая БД и ошибки архиваторов завершают запуск с ошибкой.
Секреты в этих архивах **не шифруются**: права 600 не заменяют шифрование хранения.
Не путайте его с `server_snapshot/scripts/backup_crm.sh` и не заменяйте им
установленную encrypted-версию без отдельного решения. Ни запуск на production,
ни изменение cron этой локальной проверкой не выполнялись.

> **Do not back up with `cp crm_app.db`.** The database runs in WAL mode, so
> recent commits live in `crm_app.db-wal` and are not yet in the main file.
> Copying only the main file gives a stale — or entirely unreadable — database.
> Measured on a 500-row table: the `cp` copy failed with *"no such table"*, while
> the SQLite backup API returned all 500 rows. Earlier revisions of this document
> recommended `cp`; that advice was wrong.

### Manual Backup

```bash
bash scripts/backup_db.sh          # -> backups/
```

### Recovery from Backup

Восстановление — отдельная согласованная операция с остановкой записи. Локальный
гейт не выполняет её. Прежняя последовательность `down` → `docker cp` была
неверной: `down` удаляет контейнер, в который затем предлагалось копировать БД.

1. До остановки сервиса распакуйте выбранный архив в новый временный каталог.
   Проверьте `PRAGMA integrity_check` и ожидаемые таблицы/данные. Убедитесь, что
   выбраны нужная БД (основная или конкретного тенанта) и совместимая версия кода.
2. Уточните фактический том и `CRM_DATA_DIR` целевого сервиса; `/app/data` —
   значение Docker-конфигурации, а не универсальный путь PM2-сервера.
3. Остановите backend через `docker compose ... stop backend`, не `down`;
   остановите также остальные процессы, пишущие в тот же том. Убедитесь, что
   запись прекратилась. Сохраните исходную БД вместе с её WAL/SHM отдельно для
   обратного восстановления.
4. На остановленном томе замените выбранную БД проверенным снимком. Старые
   `-wal`/`-shm` не должны остаться рядом с восстановленным файлом; сохраните
   владельца и права, необходимые пользователю приложения. Работу с томом
   выполняйте обслуживающим контейнером с тем же томом, без запуска приложения.
5. Проверьте восстановленную БД на томе, затем запустите backend и проверьте
   health, вход и чтение ожидаемых данных. При ошибке не объявляйте восстановление
   успешным; используйте сохранённый исходный комплект для согласованного отката.

Не копируйте файл поверх работающей SQLite-БД и не удаляйте том (`down -v`).

---

## Monitoring & Logging

### View logs

```bash
# Backend logs
docker compose -f docker-compose.production.yml logs backend

# Frontend logs
docker compose -f docker-compose.production.yml logs frontend

# Follow in real-time
docker compose -f docker-compose.production.yml logs -f backend
```

### Resource usage

```bash
docker stats crm-backend-prod crm-frontend-prod
```

---

## Troubleshooting

### Backend not healthy

```bash
docker compose logs backend | tail -50
docker exec crm-backend python -c "from database import engine; print('DB OK')"
```

### Frontend not connecting to backend

```bash
# Check network connectivity
docker exec crm-frontend-prod curl -v http://backend:8000/health

# Verify nginx config
docker exec crm-frontend-prod nginx -t
```

### SSL certificate issues

```bash
# Test certificate
openssl x509 -in ssl/cert.pem -text -noout

# Check renewal (Let's Encrypt)
sudo certbot renew --dry-run
```

### Port conflicts

```bash
# Check what's using port 80/443
lsof -i :80
lsof -i :443

# Stop conflicting service
sudo systemctl stop nginx  # or apache2
```

---

## Security Checklist

- [ ] `.env.production` created and NOT in git
- [ ] `email_settings.json` NOT in git (contains IMAP password — use `email_settings.example.json` as template)
- [ ] SSL certificate installed and HTTPS enabled
- [ ] Backend port (8000) not exposed to internet
- [ ] Firewall configured (80/443 only)
- [ ] Database backups scheduled and tested
- [ ] CORS origins configured for your domain
- [ ] Rate limiting verified
- [ ] Monitoring & alerting configured
- [ ] IDOR endpoints audited and fixed
- [ ] `.secret_key` removed from git history
- [ ] IMAP password rotated if it was ever committed

---

## Upgrading

```bash
# Pull latest code
git pull origin main

# Rebuild images
docker compose -f docker-compose.production.yml build

# Zero-downtime update
docker compose -f docker-compose.production.yml up -d

# Verify
docker compose -f docker-compose.production.yml ps
```

---

## Support & Security Issues

- Found a bug? Create an issue on GitHub
- Security vulnerability? Email security@your-domain.com (do NOT create public issue)
- Need help? See DEPLOYMENT_SECURITY.md

---

**Last updated:** August 2026  
**Version:** 2.1.0
