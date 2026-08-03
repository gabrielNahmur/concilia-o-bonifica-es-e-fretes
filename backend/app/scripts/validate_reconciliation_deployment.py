from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.dependencies import current_user
from app.main import app
from app.models import ReconciliationAllocation, ReconciliationException, ReconciliationItem, User


def main() -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.role == "admin", User.active.is_(True)).limit(1))
        if not user:
            raise RuntimeError("Nenhum administrador ativo para a validação interna")
        db.expunge(user)

    app.dependency_overrides[current_user] = lambda: user
    try:
        with TestClient(app) as client:
            listing = client.get("/api/reconciliations", params={"page_size": 200})
            listing.raise_for_status()
            payload = listing.json()
            samples = {}
            for item in payload["items"]:
                samples.setdefault(item["rule_kind"], item)

            details = {}
            for kind, item in samples.items():
                response = client.get(f"/api/reconciliations/{item['id']}/detail")
                response.raise_for_status()
                detail = response.json()
                required = {"criteria", "chains", "evidence", "match_summary", "workspace", "review", "technical_evidence"}
                missing = sorted(required - detail.keys())
                if missing:
                    raise RuntimeError(f"Detalhe {kind} sem campos: {missing}")
                details[kind] = {
                    "unit": detail["unit_code"],
                    "score": detail["score"],
                    "status": detail["status"],
                    "chains": len(detail["chains"]),
                    "documents": detail["match_summary"]["document_count"],
                    "financial_entries": detail["match_summary"]["financial_entry_count"],
                    "evidence": detail["match_summary"]["evidence_count"],
                    "granular_items": detail["workspace"]["summary"]["item_count"],
                    "open_exceptions": detail["workspace"]["summary"]["open_exceptions"],
                }

            exception_response = client.get("/api/reconciliations/exceptions", params={"page_size": 5})
            exception_response.raise_for_status()
            exception_summary = exception_response.json()["summary"]

            routes = {}
            for path in ("/conciliacoes", "/administracao", "/compras", "/logo-gbi.png"):
                response = client.get(path)
                routes[path] = {"status": response.status_code, "content_type": response.headers.get("content-type")}
                response.raise_for_status()

            with SessionLocal() as db:
                normalized = {
                    "items": db.query(ReconciliationItem).count(),
                    "allocations": db.query(ReconciliationAllocation).count(),
                    "exceptions": db.query(ReconciliationException).count(),
                }
            print({
                "summary": payload["summary"],
                "exception_summary": exception_summary,
                "normalized": normalized,
                "details": details,
                "routes": routes,
            })
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
