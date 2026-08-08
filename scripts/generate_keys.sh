#!/bin/bash
# Generate production secrets for CRM Svetvdome.
#
# Writes a valid env file to stdout, so this works:
#   bash scripts/generate_keys.sh > .env.production
# All human-readable notes go to stderr to keep stdout parseable.
#
# Variable names below are the ones the application actually reads
# (auth.py: CRM_SECRET_KEY / CRM_CRON_TOKEN). Using SECRET_KEY has no effect.

set -euo pipefail

echo "=== CRM key generation ===" >&2
echo "Writing env file to stdout; redirect it to .env.production" >&2

SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
CRON_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")

cat <<EOF
# Generated $(date -u +"%Y-%m-%dT%H:%M:%SZ") - keep out of git.
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1

# Set this to your real domain(s).
CRM_CORS_ORIGINS=https://crm-svetvdome.online,https://www.crm-svetvdome.online

CRM_SECRET_KEY=${SECRET_KEY}
CRM_CRON_TOKEN=${CRON_TOKEN}

CRM_DATA_DIR=/app/data
EOF

cat >&2 <<'NOTES'

Next steps:
  1. Review the generated file and set CRM_CORS_ORIGINS to your domain.
  2. Rotating CRM_SECRET_KEY invalidates all issued JWTs - users must log in again.
  3. CRM_SECRET_KEY takes priority over any .secret_key file on disk, so setting
     it here is enough - no need to delete the file first.
NOTES
