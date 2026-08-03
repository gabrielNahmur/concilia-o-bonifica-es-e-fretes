#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/ubuntu/apps/contracts-gbi"
CRM_DIR="/home/ubuntu/apps/crm-gbi-whatsapp"
cd "$APP_DIR"
chmod 600 .env
if ! grep -q '^RESEND_API_KEY=' .env; then
    grep '^RESEND_API_KEY=' "$CRM_DIR/.env" >> .env
fi
if ! grep -q '^RESEND_FROM_EMAIL=' .env; then
    grep '^RESEND_FROM_EMAIL=' "$CRM_DIR/.env" >> .env || true
fi

PG_ADMIN="$(docker exec crm_postgres printenv POSTGRES_USER)"
DB_PASSWORD="$(sed -n 's/^CONTRACTS_DB_PASSWORD=//p' .env | tr -d "'\"")"
docker exec -i crm_postgres psql -v ON_ERROR_STOP=1 -U "$PG_ADMIN" -d postgres --set=contract_password="$DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE contracts_app LOGIN PASSWORD %L', :'contract_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'contracts_app') \gexec
ALTER ROLE contracts_app WITH LOGIN PASSWORD :'contract_password';
SELECT 'CREATE DATABASE contracts_gbi OWNER contracts_app'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'contracts_gbi') \gexec
GRANT ALL PRIVILEGES ON DATABASE contracts_gbi TO contracts_app;
SQL

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm contracts_app alembic -c alembic.ini upgrade head
docker compose -f docker-compose.prod.yml run --rm contracts_app python -m app.scripts.init_db
docker compose -f docker-compose.prod.yml up -d contracts_app

sudo install -m 755 deploy/backup-contracts.sh /usr/local/sbin/backup-contracts-gbi
sudo install -m 755 deploy/restore-contracts.sh /usr/local/sbin/restore-contracts-gbi
echo '15 2 * * * ubuntu /usr/local/sbin/backup-contracts-gbi >> /home/ubuntu/backups/contracts-gbi.log 2>&1' | sudo tee /etc/cron.d/contracts-gbi-backup >/dev/null
sudo chmod 644 /etc/cron.d/contracts-gbi-backup

if ! grep -q 'server_name contratos.atendimento-gbi.online' "$CRM_DIR/frontend/nginx.prod.conf"; then
    cat deploy/nginx-contracts.http.conf >> "$CRM_DIR/frontend/nginx.prod.conf"
fi
docker exec crm_frontend nginx -t
docker exec crm_frontend nginx -s reload
