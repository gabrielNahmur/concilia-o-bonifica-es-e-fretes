from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Reconciliation
from app.services.reconciliation import rebuild_reconciliations


def main() -> None:
    with SessionLocal() as db:
        rebuilt = rebuild_reconciliations(db)
        rows = db.scalars(select(Reconciliation)).all()
        status_counts = Counter(row.status for row in rows)
        expected = sum((Decimal(row.expected_value) for row in rows), Decimal("0"))
        observed = sum(
            (Decimal(row.observed_value) + Decimal(row.manual_adjustment or 0) for row in rows),
            Decimal("0"),
        )
        print(
            {
                "rebuilt": rebuilt,
                "rows": len(rows),
                "expected": str(expected),
                "observed": str(observed),
                "difference": str(expected - observed),
                "status_counts": dict(status_counts),
            }
        )


if __name__ == "__main__":
    main()
