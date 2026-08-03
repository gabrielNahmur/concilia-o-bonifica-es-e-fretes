#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/ubuntu/apps/contracts-gbi"
ENV_FILE="$APP_DIR/.env"
BACKUP_DIR="/home/ubuntu/backups/contracts-gbi"
STAMP="$(date +%Y%m%d-%H%M%S)"

env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d "'\""
}

DB_NAME="$(env_value CONTRACTS_DB_NAME)"
DB_NAME="${DB_NAME:-contracts_gbi}"
PG_ADMIN="$(docker exec crm_postgres printenv POSTGRES_USER)"
REPORTS_SOURCE="$(docker inspect contracts_app --format '{{range .Mounts}}{{if eq .Destination "/app/reports"}}{{.Source}}{{end}}{{end}}')"
BACKUP_REMOTE="$(env_value BACKUP_REMOTE)"
BACKUP_REMOTE_REQUIRED="$(env_value BACKUP_REMOTE_REQUIRED)"

mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/$DB_NAME-$STAMP.dump"
REPORTS="$BACKUP_DIR/contracts-reports-$STAMP.tar.gz"
MANIFEST="$BACKUP_DIR/contracts-$STAMP.sha256"

docker exec crm_postgres pg_dump -U "$PG_ADMIN" -d "$DB_NAME" -Fc > "$DUMP.partial"
docker exec -i crm_postgres pg_restore --list < "$DUMP.partial" > /dev/null
mv "$DUMP.partial" "$DUMP"

if [[ -z "$REPORTS_SOURCE" ]]; then
  echo "Volume de relatorios nao localizado no container contracts_app" >&2
  exit 1
fi
docker run --rm --entrypoint tar -v "$REPORTS_SOURCE:/source:ro" contracts-gbi:latest -C /source -czf - . > "$REPORTS.partial"
tar -tzf "$REPORTS.partial" > /dev/null
mv "$REPORTS.partial" "$REPORTS"

(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$DUMP")" "$(basename "$REPORTS")" > "$(basename "$MANIFEST")"
)

if [[ -n "$BACKUP_REMOTE" ]]; then
  command -v rclone > /dev/null || { echo "BACKUP_REMOTE configurado, mas rclone nao esta instalado" >&2; exit 1; }
  for backup_file in "$DUMP" "$REPORTS" "$MANIFEST"; do
    rclone copyto "$backup_file" "$BACKUP_REMOTE/$(basename "$backup_file")"
  done
  rclone delete "$BACKUP_REMOTE" --min-age 14d \
    --include '*.dump' --include 'contracts-reports-*.tar.gz' --include 'contracts-*.sha256'
elif [[ "${BACKUP_REMOTE_REQUIRED,,}" == "true" ]]; then
  echo "Backup externo obrigatorio, mas BACKUP_REMOTE nao foi configurado" >&2
  exit 1
fi

find "$BACKUP_DIR" -type f \( -name '*.dump' -o -name 'contracts-reports-*.tar.gz' -o -name 'contracts-*.sha256' \) -mtime +14 -delete
echo "Backup validado: $DUMP, $REPORTS e $MANIFEST"
