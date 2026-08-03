#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 || "$1" != "--yes" ]]; then
  echo "Uso: $0 --yes CAMINHO.dump CAMINHO-relatorios.tar.gz CAMINHO.sha256" >&2
  echo "A operacao substitui o banco e sobrepoe o volume de relatorios." >&2
  exit 2
fi

DUMP="$(realpath "$2")"
REPORTS="$(realpath "$3")"
MANIFEST="$(realpath "$4")"
[[ -f "$DUMP" && -f "$REPORTS" && -f "$MANIFEST" ]] || { echo "Arquivos de restauracao nao encontrados" >&2; exit 1; }
[[ "$(dirname "$DUMP")" == "$(dirname "$REPORTS")" && "$(dirname "$DUMP")" == "$(dirname "$MANIFEST")" ]] || {
  echo "Dump, relatorios e manifesto devem estar no mesmo diretorio" >&2
  exit 1
}

APP_DIR="/home/ubuntu/apps/contracts-gbi"
ENV_FILE="$APP_DIR/.env"
DB_NAME="$(sed -n 's/^CONTRACTS_DB_NAME=//p' "$ENV_FILE" | tail -1 | tr -d "'\"")"
DB_USER="$(sed -n 's/^CONTRACTS_DB_USER=//p' "$ENV_FILE" | tail -1 | tr -d "'\"")"
DB_NAME="${DB_NAME:-contracts_gbi}"
DB_USER="${DB_USER:-contracts_app}"
PG_ADMIN="$(docker exec crm_postgres printenv POSTGRES_USER)"
REPORTS_SOURCE="$(docker inspect contracts_app --format '{{range .Mounts}}{{if eq .Destination "/app/reports"}}{{.Source}}{{end}}{{end}}')"

docker exec -i crm_postgres pg_restore --list < "$DUMP" > /dev/null
tar -tzf "$REPORTS" > /dev/null
if tar -tzf "$REPORTS" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "Arquivo de relatorios contem caminho inseguro" >&2
  exit 1
fi
(
  cd "$(dirname "$MANIFEST")"
  sha256sum -c "$(basename "$MANIFEST")"
)
[[ -n "$REPORTS_SOURCE" ]] || { echo "Volume de relatorios nao localizado" >&2; exit 1; }

cd "$APP_DIR"
/usr/local/sbin/backup-contracts-gbi
docker compose -f docker-compose.prod.yml stop contracts_app contracts_sync
trap 'docker compose -f docker-compose.prod.yml up -d contracts_app contracts_sync' EXIT

docker exec crm_postgres psql -U "$PG_ADMIN" -d postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB_NAME' AND pid <> pg_backend_pid();"
docker exec crm_postgres dropdb -U "$PG_ADMIN" --if-exists "$DB_NAME"
docker exec crm_postgres createdb -U "$PG_ADMIN" -O "$DB_USER" "$DB_NAME"
docker exec -i crm_postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner < "$DUMP"
docker run --rm --entrypoint tar -v "$REPORTS_SOURCE:/target" -v "$(dirname "$REPORTS"):/backup:ro" contracts-gbi:latest \
  -C /target -xzf "/backup/$(basename "$REPORTS")"

docker compose -f docker-compose.prod.yml up -d contracts_app contracts_sync
trap - EXIT
for attempt in {1..30}; do
  if curl -fsS -H 'Host: contratos.atendimento-gbi.online' http://127.0.0.1/api/health; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "Healthcheck nao respondeu apos a restauracao" >&2
    exit 1
  fi
  sleep 2
done
echo
echo "Restauracao concluida e healthcheck aprovado."
