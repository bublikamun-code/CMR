# CRM Svetvdome — Docker Deployment Guide

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

## Production Deployment

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

```bash
# Stop services
docker compose -f docker-compose.production.yml down

# Restore database (backups are gzipped)
gunzip -c /backups/crm/crm_app_<timestamp>.db.gz > /tmp/restore.db
docker cp /tmp/restore.db crm-backend-prod:/app/data/crm_app.db

# Start services
docker compose -f docker-compose.production.yml up -d
```

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
- [ ] SSL certificate installed and HTTPS enabled
- [ ] Backend port (8000) not exposed to internet
- [ ] Firewall configured (80/443 only)
- [ ] Database backups scheduled and tested
- [ ] CORS origins configured for your domain
- [ ] Rate limiting verified
- [ ] Monitoring & alerting configured
- [ ] IDOR endpoints audited and fixed
- [ ] `.secret_key` removed from git history

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
