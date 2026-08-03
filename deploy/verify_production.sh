#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/apps/contracts-gbi
docker compose -f docker-compose.prod.yml up -d contracts_sync
sleep 4
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=12 contracts_sync
/usr/local/sbin/backup-contracts-gbi
curl -fsS -H 'Host: contratos.atendimento-gbi.online' http://127.0.0.1/api/health
printf '\n'
docker exec -i crm_postgres psql -U crm_user -d contracts_gbi -At <<'SQL'
SELECT 'purchases=' || count(*) FROM purchases;
SELECT 'reconciliations=' || count(*) FROM reconciliations;
SELECT 'reports=' || count(*) FROM report_runs;
SQL
docker exec -i contracts_app python - <<'PY'
from datetime import date
from app.database import SessionLocal
from app.services.reporting import create_and_optionally_send_report

with SessionLocal() as db:
    report = create_and_optionally_send_report(db, date(2026, 6, 1), False)
    print(f"report={report.status}:{report.file_path}")
PY
ls -lh /home/ubuntu/backups/contracts-gbi | tail -3
