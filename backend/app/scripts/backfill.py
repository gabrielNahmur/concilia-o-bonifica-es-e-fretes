from datetime import date

from app.database import SessionLocal
from app.services.analytics import get_contract_metrics
from app.services.erp_sync import process_sync_run, queue_sync
from app.services.seed import seed_reference_data


CONTROL_TOTALS = {
    "001": 2_683_000,
    "002": 1_931_000,
    "003": 332_000,
    "004": 1_277_000,
    "006": 2_215_900,
    "008": 4_532_000,
    "054": 2_897_000,
}
CONTROL_TOLERANCE_PERCENT = 0.01


def main():
    with SessionLocal() as db:
        seed_reference_data(db)
        run = process_sync_run(db, queue_sync(db, "full"))
        if run.status != "success":
            raise SystemExit(f"Backfill falhou: {run.error_message}")
        cutoff = date(2026, 7, 10)
        metrics = {row["unit_code"]: row["accumulated_liters"] for row in get_contract_metrics(db, cutoff)}
        actual = {code: round(metrics.get(code, 0)) for code in CONTROL_TOTALS}
        deviations = {code: actual[code] - expected for code, expected in CONTROL_TOTALS.items()}
        outside_tolerance = {
            code: deviation
            for code, deviation in deviations.items()
            if abs(deviation) > CONTROL_TOTALS[code] * CONTROL_TOLERANCE_PERCENT
        }
        print({
            "run_id": run.id,
            "rows_processed": run.rows_processed,
            "control_totals": actual,
            "deviations": deviations,
            "tolerance_percent": CONTROL_TOLERANCE_PERCENT * 100,
            "outside_tolerance": outside_tolerance,
        })
        if outside_tolerance:
            raise SystemExit("Backfill concluído, mas os totais de controle divergiram")


if __name__ == "__main__":
    main()
