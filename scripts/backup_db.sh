#!/bin/bash
# Consistent backup of the CRM databases from the running container.
#
#   bash scripts/backup_db.sh                  # -> backups/
#   bash scripts/backup_db.sh /mnt/nas/crm     # custom destination
#   KEEP=30 bash scripts/backup_db.sh          # keep 30 generations (default 14)
#   CONTAINER=crm-backend-prod bash scripts/backup_db.sh
#
# Why not `cp crm_app.db`:
# the database runs in WAL mode, so recent commits live in crm_app.db-wal and are
# NOT yet in the main file. Copying only the main file yields a stale - or, right
# after a checkpoint-less start, entirely unreadable - database. Verified:
# a 500-row table copied with cp produced "no such table".
#
# This uses SQLite's backup API, which takes a consistent snapshot of a live
# database, including anything still sitting in the WAL. Every backup is then
# verified with PRAGMA integrity_check before older ones are pruned.

set -euo pipefail

cd "$(dirname "$0")/.."

DEST="${1:-backups}"
CONTAINER="${CONTAINER:-crm-backend}"
KEEP="${KEEP:-14}"
TS="$(date +%Y%m%d_%H%M%S)"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "ERROR: container '$CONTAINER' is not running." >&2
    echo "       Set CONTAINER=<name> for production." >&2
    exit 1
fi

mkdir -p "$DEST"

echo "==> Backing up from container '$CONTAINER'"

# Snapshot inside the container, into a temp dir the app user can write to.
# -i is required: without it docker exec gives the process no stdin and the
# heredoc below never reaches python, which then exits without doing anything.
docker exec -i "$CONTAINER" python - "$TS" <<'PY'
import os, sqlite3, sys

ts = sys.argv[1]
data_dir = os.environ.get("CRM_DATA_DIR", "/app/data")
out_dir = "/tmp/crm-backup"
os.makedirs(out_dir, exist_ok=True)

targets = []
main_db = os.path.join(data_dir, "crm_app.db")
if os.path.exists(main_db):
    targets.append((main_db, f"crm_app_{ts}.db"))

tenants = os.path.join(data_dir, "tenants")
if os.path.isdir(tenants):
    for f in sorted(os.listdir(tenants)):
        if f.endswith(".db"):
            targets.append((os.path.join(tenants, f), f"tenant_{f[:-3]}_{ts}.db"))

if not targets:
    print("ERROR: no databases found in", data_dir, file=sys.stderr)
    sys.exit(1)

for src, name in targets:
    dst = os.path.join(out_dir, name)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)          # consistent snapshot, WAL included
    # Verify the copy before it is trusted as a backup.
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    rows = target.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    target.close()
    source.close()
    if result != "ok":
        print(f"ERROR: integrity check failed for {src}: {result}", file=sys.stderr)
        sys.exit(1)
    print(f"  {name}  ({os.path.getsize(dst)} bytes, {rows} tables, integrity ok)")
PY

# Move the snapshots out of the container.
docker exec "$CONTAINER" sh -c 'ls /tmp/crm-backup' | while read -r f; do
    [ -n "$f" ] || continue
    docker cp "$CONTAINER:/tmp/crm-backup/$f" "$DEST/$f" >/dev/null
    gzip -f "$DEST/$f"
    echo "==> Saved $DEST/$f.gz ($(du -h "$DEST/$f.gz" | cut -f1))"
done
docker exec "$CONTAINER" rm -rf /tmp/crm-backup

# Prune old generations, keeping the newest $KEEP of each database.
# `|| true` matters: with no tenant databases yet, ls exits non-zero and set -e
# would abort the script after a successful backup.
for prefix in crm_app tenant; do
    # shellcheck disable=SC2012
    ls -1t "$DEST"/${prefix}_*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
        rm -f "$old"
        echo "==> Pruned $(basename "$old")"
    done || true
done

echo "==> Done. $(ls -1 "$DEST"/*.db.gz 2>/dev/null | wc -l | tr -d ' ') backup files in $DEST/"
cat <<NOTES

Restore:
  gunzip -c $DEST/crm_app_<timestamp>.db.gz > /tmp/restore.db
  docker cp /tmp/restore.db $CONTAINER:/app/data/crm_app.db
  docker compose restart backend

Schedule (crontab -e), daily at 03:00:
  0 3 * * * cd $PWD && CONTAINER=$CONTAINER bash scripts/backup_db.sh >> backups/backup.log 2>&1
NOTES
