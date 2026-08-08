# CRM Deployment Security Guide

## Critical Issues from SECURITY.md (Status: PARTIALLY FIXED)

### ✅ FIXED: `/email-parser/sync-all` without authentication
- **Status**: ✅ PARTIALLY FIXED  
- **Details**: Route now guarded by `require_cron_token` dependency
- **How to use**: Include header `X-Cron-Token: <token>` in scheduled requests
- **Token location**: Generated in `routers/.cron_token`, available in env var `CRM_CRON_TOKEN`

### ⚠️ TODO: IDOR vulnerabilities on ~40 endpoints
- **Status**: NOT FIXED - requires code audit  
- **Action**: Verify all endpoints check `tenant_id` after fetching objects
- **Example fix**:
  ```python
  obj = db.query(Model).filter(Model.id == id).first()
  if not obj:
      raise HTTPException(status_code=404, detail="Not found")
  # ADD THIS CHECK:
  if obj.tenant_id != current_user.tenant_id and current_user.role != "superadmin":
      raise HTTPException(status_code=403, detail="Access denied")
  ```

### ⚠️ CRITICAL: `.secret_key` committed to git
- **Status**: IN PROGRESS - needs cleanup
- **Why dangerous**: JWT tokens can be forged, passwords encrypted with this key can be decrypted
- **Immediate action**: 
  1. Generate new SECRET_KEY: `bash scripts/generate_keys.sh`
  2. Regenerate all user JWTs (force re-login)
  3. Clean git history: `git filter-branch` or use BFG
  4. Never commit `.secret_key` or `.cron_token` again

---

## Docker Deployment Security Checklist

### 1. Environment Variables (Secrets Management)

**DO NOT bake secrets into images.** Use Docker secrets or environment files:

```bash
# Generate production keys
bash scripts/generate_keys.sh > .env.production

# .env.production should contain:
SECRET_KEY=<generated-secret-key>
CRON_TOKEN=<generated-cron-token>
CRM_CORS_ORIGINS=https://your-domain.com
DEBUG=false
```

**Store `.env.production` securely:**
- NOT in git (add to `.gitignore`)
- In Docker Secrets (Swarm) or AWS Secrets Manager (ECS)
- In GitHub Secrets (for CI/CD) or similar

### 2. HTTPS/SSL Certificate

**Current setup (development only):** HTTP on port 80

**Production requirement:** HTTPS on port 443

```nginx
server {
    listen 443 ssl http2;
    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    
    # Redirect HTTP → HTTPS
    error_page 497 =301 https://$host$request_uri;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
}
```

**For Let's Encrypt (automated):**
```bash
docker run --rm -v /etc/letsencrypt:/etc/letsencrypt certbot certonly \
  -d your-domain.com --standalone
```

### 3. Network Security

**Backend should NOT be exposed to the internet:**

```yaml
# docker-compose.yml - CORRECT for production
services:
  backend:
    # Remove: ports: ["8000:8000"]  ← DELETE THIS for production
    expose: ["8000"]  # Only within network
    networks:
      - crm-network

  frontend:
    ports: ["80:80", "443:443"]  # Only frontend exposed
```

### 4. Database Backup Strategy

**Automated daily backup:**

```bash
#!/bin/bash
# backup-db.sh
BACKUP_DIR="/backups/crm"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

bash scripts/backup_db.sh   # cp is unsafe in WAL mode - see DEPLOYMENT.md

# Upload to S3
aws s3 cp "/app/data/backups/crm_app_$TIMESTAMP.db" \
  "s3://your-bucket/backups/"

# Keep only last 30 days
find "$BACKUP_DIR" -mtime +30 -delete
```

**Schedule in crontab:**
```cron
0 2 * * * /path/to/backup-db.sh
```

### 5. User Authentication & Password Policy

**Current implementation:** Bcrypt hashing ✅

**Recommended additions:**
- [ ] Enforce strong passwords (min 12 chars, special chars)
- [ ] Implement 2FA (TOTP or email verification)
- [ ] Session timeout after 30 minutes of inactivity
- [ ] Log all login attempts
- [ ] Rate limiting on `/auth/login` (already present with SlowAPI)

### 6. API Rate Limiting

**Current:** SlowAPI limits on `/login` only

**Recommended production limits:**
- `/login`: 5 requests per minute per IP
- API routes (`/kanban`, `/clients`, ...): 100 requests per minute per user
- `/email-parser/sync-all`: 1 request per hour

### 7. Input Validation

**Current:** Pydantic schemas ✅

**Ensure all endpoints validate:**
- [ ] Max length for strings (email, title, description)
- [ ] Numeric ranges (amounts, IDs)
- [ ] File upload restrictions (size, type, virus scan)
- [ ] HTML sanitization for user-generated content

### 8. Audit Logging

**Current:** ActivityLog table exists ✅

**Ensure logging of:**
- [ ] All data mutations (create, update, delete)
- [ ] Failed authentication attempts
- [ ] Permission violations
- [ ] Configuration changes
- [ ] Cron job execution

---

## Production Deployment Checklist

- [ ] Generate and store SECRET_KEY securely
- [ ] Generate CRON_TOKEN for scheduled jobs
- [ ] Configure HTTPS with valid SSL certificate
- [ ] Hide backend port (no port mapping)
- [ ] Set up database backups (daily)
- [ ] Configure monitoring & alerting
- [ ] Test disaster recovery (restore from backup)
- [ ] Review IDOR endpoints (all 40+ instances)
- [ ] Clean `.secret_key` from git history
- [ ] Set `DEBUG=false` in .env
- [ ] Configure firewall (80/443 only from CDN/LB)
- [ ] Enable CORS for specific domains only
- [ ] Review and test rate limiting

---

## Quick Start: Secure Deployment

```bash
# 1. Generate keys
bash scripts/generate_keys.sh > .env.production

# 2. Set up Docker volume for certs
mkdir -p /ssl
# Place cert.pem and key.pem in /ssl

# 3. Update nginx.conf with HTTPS config

# 4. Deploy with env file
docker compose --file docker-compose.production.yml up -d

# 5. Verify security headers
curl -I https://your-domain.com
# Should include:
#   Strict-Transport-Security
#   X-Content-Type-Options: nosniff
#   X-Frame-Options: DENY
```

---

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
- [Let's Encrypt](https://letsencrypt.org/)
