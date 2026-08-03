import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ReportRun, SyncRun
from app.services.erp_sync import process_sync_run, queue_sync
from app.services.reporting import create_and_optionally_send_report, monitor_report_deliveries, previous_month
from app.services.seed import seed_reference_data


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("contracts_worker")
settings = get_settings()


def process_queue_job():
    with SessionLocal() as db:
        row = db.scalar(select(SyncRun).where(SyncRun.status == "queued").order_by(SyncRun.created_at).limit(1))
        if row:
            logger.info("Processando sincronização %s (%s)", row.id, row.kind)
            process_sync_run(db, row)


def hourly_sync_job():
    with SessionLocal() as db:
        row = queue_sync(db, "incremental")
        logger.info("Sincronização horária enfileirada: %s", row.id)


def recover_interrupted_syncs() -> int:
    """Release a run left as running when the worker was stopped mid-sync."""
    now = datetime.now(timezone.utc)
    stale_after = timedelta(minutes=max(settings.erp_sync_interval_minutes * 2, 10))
    cutoff = now - stale_after
    with SessionLocal() as db:
        rows = db.scalars(
            select(SyncRun).where(
                SyncRun.status == "running",
                SyncRun.started_at.is_not(None),
                SyncRun.started_at < cutoff,
            )
        ).all()
        for row in rows:
            row.status = "failed"
            row.finished_at = now
            row.error_message = "Sincronizacao interrompida por reinicio do worker; nova tentativa enfileirada."
        if rows:
            db.commit()
    return len(rows)


def monthly_report_job():
    reference = previous_month()
    with SessionLocal() as db:
        existing = db.scalar(
            select(ReportRun).where(
                ReportRun.reference_month == reference,
                ReportRun.status.in_({"sent", "delivery_delayed", "delivered"}),
            ).limit(1)
        )
        if existing:
            return
        run = create_and_optionally_send_report(db, reference, send=True)
        logger.info("Relatório mensal %s finalizado com status %s", run.id, run.status)


def report_delivery_job():
    with SessionLocal() as db:
        result = monitor_report_deliveries(db)
        if any(result.values()):
            logger.info("Monitoramento de entrega de relatórios: %s", result)


def main():
    with SessionLocal() as db:
        seed_reference_data(db)
    recovered = recover_interrupted_syncs()
    if recovered:
        logger.warning("Recuperadas %s sincronizacoes interrompidas", recovered)
    scheduler = BlockingScheduler(timezone=settings.timezone)
    scheduler.add_job(process_queue_job, "interval", minutes=1, id="process_queue", max_instances=1, coalesce=True)
    scheduler.add_job(hourly_sync_job, "interval", minutes=settings.erp_sync_interval_minutes, id="hourly_sync", max_instances=1, coalesce=True)
    scheduler.add_job(report_delivery_job, "interval", minutes=5, id="report_delivery_monitor", max_instances=1, coalesce=True)
    hour, minute = [int(value) for value in settings.report_time.split(":", 1)]
    if settings.monthly_reports_enabled:
        scheduler.add_job(monthly_report_job, "cron", day=settings.report_day, hour=hour, minute=minute, id="monthly_report", max_instances=1, coalesce=True)
    logger.info(
        "Worker iniciado: sync a cada %s min; relatório mensal %s",
        settings.erp_sync_interval_minutes,
        f"dia {settings.report_day} às {settings.report_time}" if settings.monthly_reports_enabled else "desativado",
    )
    # A restarted worker must not wait a full interval before restoring the
    # current snapshot and clearing a stale-sync warning.
    report_delivery_job()
    hourly_sync_job()
    process_queue_job()
    scheduler.start()


if __name__ == "__main__":
    main()
