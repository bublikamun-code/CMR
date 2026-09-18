#!/bin/bash
# Резервное копирование CRM. Пишет ВНЕ www (не доступно из веба),
# ВСЕ артефакты шифруются AES-256-CBC (PBKDF2) — №3 аудита 06.09.
# Запуск: крон 0 3 * * * (см. crontab -l)
#
# 2026-09-18: с 15.09 шифрование uploads nightly обрывалось квотой аккаунта —
# в DST попадал обрезанный .enc рядом с открытой копией, а сбой не виден в логе.
# Теперь каждая часть собирается в приватном staging-каталоге, шифруется,
# проверяется обратной распаковкой и только потом публикуется в DST; сбой части
# пишется в лог словом FAIL и даёт ненулевой код выхода, старые архивы при
# отказе не удаляются. Открытые копии не покидают staging и удаляются вместе
# с ним. Поведение по шагам (db → tenants → uploads → code → crmdata_secret →
# ротация → WAL-чекпоинт) и формат восстановления сохранены.
#
# ВОССТАНОВЛЕНИЕ (нужен файл ключа $BK_KEY — его копия хранится ВНЕ сервера,
# без ключа зашифрованные бэкапы не восстановить):
#   openssl enc -d -aes-256-cbc -pbkdf2 -in db_TS.sqlite.gz.enc \
#               -out db_TS.sqlite.gz -pass file:"$BK_KEY"
#   gunzip db_TS.sqlite.gz && sqlite3 restore.sqlite "PRAGMA integrity_check;"
#   (далее — остановить приложение, заменить CRM_DATA/crm_app.db, запустить)
set -u
APP="${APP:-/var/www/h212005/data/www/cmr-svetvdome.online}"
DATA="${DATA:-/var/www/h212005/data/crm_data}"
DST="${DST:-$HOME/backups}"
KEEP="${KEEP-14}"
# Uploads — самые тяжёлые архивы; при квоте хостинга 14 поколений (~7 ГБ)
# физически не помещаются, поэтому держим 7 дней (прод-uploads при этом живы).
KEEP_UPLOADS="${KEEP_UPLOADS-7}"
BK_DIR="${BK_DIR:-/var/www/h212005/data/keys}"
BK_KEY="$BK_DIR/.backup_key"
for value in "$KEEP" "$KEEP_UPLOADS"; do
    if [[ ! "$value" =~ ^[1-9][0-9]{0,8}$ ]]; then
        echo 'FAIL: KEEP/KEEP_UPLOADS must be a positive integer (1..999999999).' >&2
        exit 1
    fi
done
log() { echo "$(date '+%F %T') $*"; }
umask 077
mkdir -p "$DST" "$BK_DIR" && chmod 700 "$BK_DIR"
if [ ! -s "$BK_KEY" ]; then
    openssl rand -base64 48 > "$BK_KEY" && chmod 600 "$BK_KEY" && log "создан новый ключ шифрования: $BK_KEY"
fi
[[ -f "$DATA/crm_app.db" ]] || { log "FAIL: missing source $DATA/crm_app.db"; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { log 'FAIL: sqlite3 not found'; exit 1; }
command -v openssl >/dev/null 2>&1 || { log 'FAIL: openssl not found'; exit 1; }

STAGE="$(mktemp -d "$DST/.crm-backup.XXXXXXXXXX")"
FAILED=0
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "${STAGE:-}" && "$STAGE" == "$DST"/.crm-backup.* ]]; then
        rm -rf -- "$STAGE" || status=1
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
TS="$(date +%Y%m%d_%H%M%S)_${STAGE##*.}"
log "=== backup start $TS ==="

# Шифрование части в staging, обратная проверка распаковкой и публикация
# готового .enc в DST. Открытая копия удаляется только после успеха.
seal_and_publish() {
    local staged="$1" final="$DST/$2"
    openssl enc -aes-256-cbc -pbkdf2 -salt -in "$staged" -out "$staged.enc" -pass file:"$BK_KEY" || return 1
    openssl enc -d -aes-256-cbc -pbkdf2 -in "$staged.enc" -pass file:"$BK_KEY" 2>/dev/null | gzip -t || return 1
    chmod 600 "$staged.enc"
    mv -- "$staged.enc" "$final" || return 1
    rm -f -- "$staged"
}

# 1) База: sqlite3 .backup — консистентный снимок на живой БД (cp в WAL-режиме
# даёт битый файл). Основная БД обязательна: любой её сбой останавливает запуск.
if (cd "$STAGE" && sqlite3 -readonly "$DATA/crm_app.db" '.backup db.sqlite'); then
    # Снимок в WAL-режиме нельзя открыть -readonly без -shm (ошибка 14); это
    # приватная копия, integrity_check ничего не пишет — открываем как обычно.
    CHECK=$(sqlite3 "$STAGE/db.sqlite" 'PRAGMA integrity_check;')
    if [ "$CHECK" = "ok" ] && gzip -f "$STAGE/db.sqlite"; then
        mv "$STAGE/db.sqlite.gz" "$STAGE/db_$TS.sqlite.gz"
        if seal_and_publish "$STAGE/db_$TS.sqlite.gz" "db_$TS.sqlite.gz.enc"; then
            log "OK db -> db_$TS.sqlite.gz.enc ($(du -h "$DST/db_$TS.sqlite.gz.enc" | cut -f1), integrity ok)"
        else
            log 'FAIL: шифрование db'; exit 1
        fi
    else
        log "FAIL: integrity_check = ${CHECK:-gzip failed}"; exit 1
    fi
else
    log 'FAIL: sqlite3 .backup'; exit 1
fi

# 1b) Тенант-базы — FIX 2026-09-03 (аудит): раньше бэкапилась только
# основная БД, данные тенантов при аварии терялись. Тот же .backup +
# integrity_check; сбой отдельной базы не мешает остальным.
shopt -s nullglob
tdbs=("$DATA"/tenants/*.db)
shopt -u nullglob
if [ "${#tdbs[@]}" -gt 0 ]; then
    TDIR="$STAGE/tenants"
    mkdir -p "$TDIR"
    for tdb in "${tdbs[@]}"; do
        name=$(basename "$tdb")
        if (cd "$TDIR" && sqlite3 -readonly "$tdb" ".backup $name"); then
            CHECK=$(sqlite3 "$TDIR/$name" 'PRAGMA integrity_check;')
            if [ "$CHECK" != "ok" ]; then
                log "FAIL: tenants/$name integrity_check = $CHECK"
                rm -f "$TDIR/$name"
                FAILED=1
            fi
        else
            log "FAIL: sqlite3 .backup tenants/$name"
            FAILED=1
        fi
    done
    shopt -s nullglob
    saved=("$TDIR"/*.db)
    shopt -u nullglob
    if [ "${#saved[@]}" -eq 0 ]; then
        log 'WARN: tenants: ни одна база не прошла проверку'
    elif tar czf "$STAGE/tenants_$TS.tar.gz" -C "$TDIR" .; then
        if seal_and_publish "$STAGE/tenants_$TS.tar.gz" "tenants_$TS.tar.gz.enc"; then
            log "OK tenants -> tenants_$TS.tar.gz.enc ($(du -h "$DST/tenants_$TS.tar.gz.enc" | cut -f1))"
        else
            log 'FAIL: шифрование tenants'; FAILED=1
        fi
    else
        log 'FAIL: tar tenants'; FAILED=1
    fi
    rm -rf "$TDIR"
fi

# 2) Загруженные файлы — ежедневно, дешевле потери
if [ -d "$DATA/uploads" ]; then
    if tar czf "$STAGE/uploads_$TS.tar.gz" -C "$DATA" uploads; then
        if seal_and_publish "$STAGE/uploads_$TS.tar.gz" "uploads_$TS.tar.gz.enc"; then
            log "OK uploads -> uploads_$TS.tar.gz.enc ($(du -h "$DST/uploads_$TS.tar.gz.enc" | cut -f1))"
        else
            log 'FAIL: шифрование uploads (проверьте место/квоту)'; FAILED=1
        fi
    else
        log 'FAIL: tar uploads'; FAILED=1
    fi
fi

# 3) Код (без мусора, venv, баз и загрузок)
if tar czf "$STAGE/code_$TS.tar.gz" -C "$APP" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.bak_*' --exclude='*.backup_*' --exclude='uploads_migrated_bak_*' \
    --exclude='.venv' --exclude='backups' --exclude='logs' \
    --exclude='uploads' --exclude='__pycache__' --exclude='.git' .; then
    if seal_and_publish "$STAGE/code_$TS.tar.gz" "code_$TS.tar.gz.enc"; then
        log "OK code -> code_$TS.tar.gz.enc ($(du -h "$DST/code_$TS.tar.gz.enc" | cut -f1))"
    else
        log 'FAIL: шифрование code'; FAILED=1
    fi
else
    log 'FAIL: tar code'; FAILED=1
fi

# 4) Чувствительное: настройки почты и ключи данных каталога
if tar czf "$STAGE/crmdata_secret_$TS.tar.gz" -C "$DATA" \
    --exclude='uploads' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='backups_legacy' .; then
    if seal_and_publish "$STAGE/crmdata_secret_$TS.tar.gz" "crmdata_secret_$TS.tar.gz.enc"; then
        log 'OK crmdata secrets (.enc, 600)'
    else
        log 'FAIL: шифрование crmdata secrets'; FAILED=1
    fi
else
    log 'FAIL: tar crmdata secrets'; FAILED=1
fi

# 5) Ротация — KEEP последних завершённых архивов КАЖДОГО вида (uploads —
# KEEP_UPLOADS: самые тяжёлые); имена строго формата TS(.суффикс), чтобы
# мусор и открытые копии ротацию не проходили.
shopt -s nullglob
for prefix in db code uploads crmdata_secret tenants; do
    if [ "$prefix" = uploads ]; then limit="$KEEP_UPLOADS"; else limit="$KEEP"; fi
    group=()
    for file in "$DST"/"${prefix}"_*; do
        name=${file##*/}
        suffix=${name#"${prefix}_"}
        if [[ "$prefix" == db ]]; then
            [[ "$suffix" =~ ^[0-9]{8}_[0-9]{6}(_[a-zA-Z0-9]+)?\.sqlite\.gz\.enc$ ]] || continue
        else
            [[ "$suffix" =~ ^[0-9]{8}_[0-9]{6}(_[a-zA-Z0-9]+)?\.tar\.gz\.enc$ ]] || continue
        fi
        group+=("$file")
    done
    if [ "${#group[@]}" -gt "$limit" ]; then
        ls -1t -- "${group[@]}" | tail -n +$((limit + 1)) | while IFS= read -r old; do
            rm -f -- "$old"
        done
    fi
done

# 6) Чекпоинт WAL, чтобы журнал не разрастался; снимок уже опубликован,
# поэтому сбой чекпоинта не влияет на результат бэкапа.
if ! sqlite3 "$DATA/crm_app.db" 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null 2>&1; then
    log 'WARN: WAL checkpoint failed (backup is complete)'
fi

if [ "$FAILED" -ne 0 ]; then
    log "=== done WITH FAILURES. Место: $(du -sh "$DST" | cut -f1), файлов: $(ls -1 "$DST" | wc -l) ==="
    exit 1
fi
log "=== done. Место: $(du -sh "$DST" | cut -f1), файлов: $(ls -1 "$DST" | wc -l) ==="
