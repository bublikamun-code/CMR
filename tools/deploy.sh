#!/usr/bin/env bash
#
# Deploy CRM frontend/backend to production over SSH.
#
# Replaces the old FTP flow. Uses key auth (no password), rsync for transfer,
# and restarts the PM2 process only when Python files change, because Python
# code is loaded into memory at import time and will not take effect otherwise.
#
# Usage:
#   tools/deploy.sh front            # sync site/ -> server webroot
#   tools/deploy.sh back <file>...   # sync backend file(s), then restart PM2
#   tools/deploy.sh restart          # restart PM2 process only
#   tools/deploy.sh status           # show PM2 status
#   tools/deploy.sh logs [N]         # tail N error log lines (default 40)
#
# Add --dry-run before the action to preview without transferring.
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/crm_svetvdome_deploy}"
SSH_USER="${SSH_USER:-h212005}"
SSH_HOST="${SSH_HOST:-87.232.64.12}"
REMOTE_DIR="${REMOTE_DIR:-/var/www/h212005/data/www/cmr-svetvdome.online}"
PM2_APP="${PM2_APP:-crm}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)

DRY_RUN=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN="--dry-run"
    shift
fi

ACTION="${1:-}"
shift || true

remote() {
    ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" "$@"
}

# PM2 lives under nvm, so its path must be sourced for non-interactive shells.
pm2_cmd() {
    remote "export NVM_DIR=\$HOME/.nvm; [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"; pm2 $*"
}

restart_app() {
    echo "==> Restarting PM2 process '$PM2_APP'"
    pm2_cmd "restart $PM2_APP --update-env"
    sleep 4
    pm2_cmd "list"
}

case "$ACTION" in
front)
    echo "==> Verifying JS syntax before deploy"
    bash "$REPO_ROOT/tools/check_js.sh"

    echo "==> Verifying asset version stamps are current"
    python3 "$REPO_ROOT/tools/stamp_assets.py" --check

    echo "==> Syncing site/ -> $REMOTE_DIR"
    # Excludes protect server-side state that must never be overwritten by a
    # frontend deploy: databases, secrets, uploads, and pre-existing backups.
    rsync -avz --human-readable $DRY_RUN \
        -e "ssh ${SSH_OPTS[*]}" \
        --exclude '.git/' \
        --exclude '*.bak' \
        --exclude '*.bak_*' \
        --exclude '*.bak-*' \
        --exclude 'uploads/' \
        --exclude 'tenants/' \
        --exclude '*.db' \
        --exclude '*.db-shm' \
        --exclude '*.db-wal' \
        --exclude '.secret_key' \
        --exclude '.env' \
        "$REPO_ROOT/site/" \
        "$SSH_USER@$SSH_HOST:$REMOTE_DIR/"
    echo "==> Frontend deployed. Static files need no restart."
    ;;

back)
    if [[ $# -eq 0 ]]; then
        echo "ERROR: specify backend file(s) relative to server_snapshot/, e.g.:" >&2
        echo "  tools/deploy.sh back main.py routers/clients_router.py" >&2
        exit 1
    fi
    echo "==> Byte-compiling Python files locally to catch syntax errors"
    for f in "$@"; do
        python3 -m py_compile "$REPO_ROOT/server_snapshot/$f"
        echo "    ok: $f"
    done

    for f in "$@"; do
        echo "==> Syncing $f"
        rsync -avz --human-readable $DRY_RUN \
            -e "ssh ${SSH_OPTS[*]}" \
            "$REPO_ROOT/server_snapshot/$f" \
            "$SSH_USER@$SSH_HOST:$REMOTE_DIR/$f"
    done

    if [[ -n "$DRY_RUN" ]]; then
        echo "==> Dry run: skipping restart"
        exit 0
    fi
    restart_app
    echo "==> Check for startup errors:"
    pm2_cmd "logs $PM2_APP --err --lines 15 --nostream"
    ;;

restart)
    restart_app
    ;;

status)
    pm2_cmd "list"
    ;;

logs)
    pm2_cmd "logs $PM2_APP --err --lines ${1:-40} --nostream"
    ;;

*)
    echo "Usage: tools/deploy.sh [--dry-run] {front|back <files>|restart|status|logs [N]}" >&2
    exit 1
    ;;
esac
