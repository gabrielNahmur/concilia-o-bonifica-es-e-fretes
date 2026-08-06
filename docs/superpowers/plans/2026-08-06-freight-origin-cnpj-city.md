# Freight Origin CNPJ City Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich freight-origin CNPJs resolved from NF-es with their registered city and UF, and show that reference in freight administration and reconciliation without changing financial logic.

**Architecture:** Add a local `FreightOrigin` registry and an isolated CNPJ.ws HTTP client. The ERP worker invokes a bounded registry refresh after freight synchronization, while API serializers resolve the stored city/UF for presentation only. The existing `FreightRate.origin_cnpj` continues to be the sole tariff-scoping key.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, `httpx`, PostgreSQL/SQLite tests, React 19, Vite, Vitest.

## Global Constraints

- Query CNPJs of resolved NF-e suppliers, never the carrier CNPJ as a proxy for loading origin.
- Keep city/UF as `origem cadastral`; do not treat it as proof of physical loading.
- Do not alter existing `FreightRate` selection, reconciliation values, statuses or ERP data.
- Call the external service server-side only, cache results locally, and tolerate its failure without failing the ERP synchronization.
- Normalize CNPJs to 14 digits before lookup and persist source plus lookup timestamps.

---

### Task 1: Registry model, migration and isolated lookup client

**Files:**
- Modify: `backend/app/models.py:658-677`
- Create: `backend/migrations/versions/0013_freight_origin_registry.py`
- Create: `backend/app/services/cnpj_registry.py`
- Create: `backend/tests/test_freight_origins.py`

**Interfaces:**
- Produces `FreightOrigin(cnpj, legal_name, city, state, source, last_lookup_at, last_success_at, last_error, next_retry_at)`.
- Produces `refresh_freight_origins(db: Session, cnpjs: Iterable[str], fetch: Callable[[str], CnpjLocation | None] = fetch_cnpj_location) -> int`.
- `CnpjLocation` is a frozen dataclass with `cnpj: str`, `legal_name: str | None`, `city: str | None`, `state: str | None`, `source: str`.

- [ ] **Step 1: Write the failing service tests**

```python
def test_refresh_creates_normalized_cached_origin_and_does_not_repeat_lookup(db):
    calls = []
    def fetch(cnpj):
        calls.append(cnpj)
        return CnpjLocation(cnpj, "RAIZEN S.A.", "Esteio", "RS", "cnpj_ws")

    assert refresh_freight_origins(db, ["33.453.598/0137-05"], fetch=fetch) == 1
    assert refresh_freight_origins(db, ["33453598013705"], fetch=fetch) == 0
    assert calls == ["33453598013705"]
    assert db.get(FreightOrigin, "33453598013705").city == "Esteio"


def test_refresh_records_lookup_failure_without_raising_or_creating_city(db):
    def fetch(_):
        raise CnpjLookupError("serviço indisponível")

    assert refresh_freight_origins(db, ["33453598013705"], fetch=fetch) == 0
    row = db.get(FreightOrigin, "33453598013705")
    assert row.city is None
    assert "indisponível" in row.last_error
```

- [ ] **Step 2: Run the failing tests**

Run: `pytest tests/test_freight_origins.py -q`

Expected: FAIL because `FreightOrigin`, `CnpjLocation` and `refresh_freight_origins` do not exist.

- [ ] **Step 3: Add the persistence schema and migration**

```python
class FreightOrigin(Base):
    __tablename__ = "freight_origins"
    cnpj: Mapped[str] = mapped_column(String(14), primary_key=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    state: Mapped[str | None] = mapped_column(String(2), index=True)
    source: Mapped[str | None] = mapped_column(String(40))
    last_lookup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
```

Create revision `0013_freight_origin_registry`, with `down_revision = "0012_reconciliation_information_requests"`; create the table and city/state/retry indexes, and drop them/table in `downgrade`.

- [ ] **Step 4: Implement a bounded CNPJ.ws client and cache refresh**

```python
def fetch_cnpj_location(cnpj: str) -> CnpjLocation | None:
    response = httpx.get(
        f"https://publica.cnpj.ws/cnpj/{cnpj}",
        timeout=httpx.Timeout(8.0, connect=3.0),
        headers={"User-Agent": "GBI-Contratos/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    establishment = payload.get("estabelecimento") or {}
    return CnpjLocation(
        cnpj=cnpj,
        legal_name=payload.get("razao_social"),
        city=establishment.get("cidade", {}).get("nome"),
        state=establishment.get("estado", {}).get("sigla"),
        source="cnpj_ws",
    )
```

Normalize CNPJ with digits only; reject anything other than 14 digits. Skip records with a populated `city` and `state`. For failures, upsert the row with `last_lookup_at`, `last_error` and `next_retry_at = now + 24 hours`; catch only lookup/network/response errors so programming errors still surface in tests.

- [ ] **Step 5: Run the new service tests**

Run: `pytest tests/test_freight_origins.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the isolated registry foundation**

```bash
git add backend/app/models.py backend/app/services/cnpj_registry.py backend/migrations/versions/0013_freight_origin_registry.py backend/tests/test_freight_origins.py
git commit -m "feat(freight): cache registered origin cities"
```

### Task 2: Populate from resolved NF-es and expose presentation data

**Files:**
- Modify: `backend/app/services/erp_sync.py:721-753`
- Modify: `backend/app/api/admin.py:1-15,529-557`
- Modify: `backend/app/api/freights.py:125-250`
- Modify: `backend/tests/test_freight_origins.py`

**Interfaces:**
- Consumes `refresh_freight_origins(db, cnpjs)` from Task 1.
- Produces `GET /api/admin/freight-rates` rows with `origin: {cnpj, legal_name, city, state, source} | null`.
- Produces freight detail `origins: list[{cnpj, legal_name, city, state, source}]`, derived only from resolved purchases.

- [ ] **Step 1: Write failing integration tests**

```python
def test_sync_origin_refresh_failure_does_not_fail_or_change_freight_reconciliation(monkeypatch, db):
    before = _freight_snapshot(db)
    monkeypatch.setattr("app.services.cnpj_registry.fetch_cnpj_location", lambda _: (_ for _ in ()).throw(CnpjLookupError("offline")))
    assert refresh_origins_from_resolved_freight_invoices(db) == 0
    assert _freight_snapshot(db) == before


def test_rate_and_detail_payload_expose_registered_origin_city(db):
    db.add(FreightOrigin(cnpj="11111111000111", legal_name="ORIGEM TESTE", city="Esteio", state="RS", source="cnpj_ws"))
    db.commit()
    assert _freight_rate(rate, {"11111111000111": origin})["origin"]["city"] == "Esteio"
    assert _detail_payload(db, reconciliation)["origins"][0]["state"] == "RS"
```

- [ ] **Step 2: Run the failing tests**

Run: `pytest tests/test_freight_origins.py -q`

Expected: FAIL because the sync helper and serialized `origin`/`origins` fields do not exist.

- [ ] **Step 3: Add origin collection to the worker flow**

```python
def refresh_origins_from_resolved_freight_invoices(db: Session) -> int:
    cnpjs = db.scalars(
        select(Purchase.supplier_cnpj)
        .join(FreightCteInvoice, FreightCteInvoice.resolved_purchase_entry_id == Purchase.erp_entry_id)
        .where(Purchase.supplier_cnpj.is_not(None))
        .distinct()
    ).all()
    return refresh_freight_origins(db, cnpjs)
```

Call this helper after `sync_freights` has completed and before rebuilding freight reconciliations. Wrap lookup-specific exceptions inside the helper, log them and return zero; do not mark the `SyncRun` failed. Commit the normal ERP snapshot before outbound lookups as the existing synchronization already does between steps.

- [ ] **Step 4: Add read-only serializers without influencing rate selection**

```python
def _origin_payload(row: FreightOrigin | None) -> dict | None:
    if row is None:
        return None
    return {
        "cnpj": row.cnpj,
        "legal_name": row.legal_name,
        "city": row.city,
        "state": row.state,
        "source": row.source,
    }
```

Load origins in one query per endpoint, build a `dict[str, FreightOrigin]`, and pass it to `_freight_rate` and the detail serializer. Keep `find_freight_rate`, `rebuild_freight_reconciliations`, expected values and statuses untouched.

- [ ] **Step 5: Run backend tests**

Run: `pytest tests/test_freight_origins.py tests/test_freights.py tests/test_sync.py -q`

Expected: PASS, including the existing exact-origin tariff test.

- [ ] **Step 6: Commit worker and API presentation**

```bash
git add backend/app/services/erp_sync.py backend/app/api/admin.py backend/app/api/freights.py backend/tests/test_freight_origins.py
git commit -m "feat(freight): show registered origin city"
```

### Task 3: Show the registered origin in the administration and freight detail

**Files:**
- Modify: `frontend/src/App.jsx:1130-1160,4026-4160`
- Create: `frontend/src/freight-origin.test.jsx`

**Interfaces:**
- Consumes `row.origin` from `/api/admin/freight-rates` and `detail.origins` from `/api/freights/{id}`.
- Produces an explicit label `Origem cadastral: Cidade/UF` or `Cidade cadastral não identificada`.

- [ ] **Step 1: Write the failing UI test**

```jsx
it("labels the city as registered origin rather than loading proof", () => {
  render(<FreightOriginSummary origin={{ cnpj: "33453598013705", city: "Esteio", state: "RS" }} />)
  expect(screen.getByText("Origem cadastral: Esteio/RS")).toBeInTheDocument()
  expect(screen.queryByText(/base comprovada/i)).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run the failing UI test**

Run: `npm run test:ui -- freight-origin.test.jsx`

Expected: FAIL because `FreightOriginSummary` does not exist.

- [ ] **Step 3: Implement a small presentational component and use it in both views**

```jsx
function FreightOriginSummary({ origin }) {
  if (!origin?.city || !origin?.state) {
    return <small className="table-subline">Cidade cadastral não identificada</small>
  }
  return <small className="table-subline">Origem cadastral: {origin.city}/{origin.state}</small>
}
```

Render it under the CNPJ in the tariff list and inside the freight detail near the existing tariff scope. Do not display a city when there is no resolved origin. Do not use the city to filter, calculate or choose a rate.

- [ ] **Step 4: Run frontend validation**

Run: `npm run test:ui -- freight-origin.test.jsx && npm run build`

Expected: PASS and Vite emits the production bundle.

- [ ] **Step 5: Commit the presentation**

```bash
git add frontend/src/App.jsx frontend/src/freight-origin.test.jsx
git commit -m "feat(freight): display registered origin location"
```

### Task 4: Release validation and initial controlled enrichment

**Files:**
- Create: `backend/app/scripts/backfill_freight_origins.py`
- Modify: `docs/qa/freight-origin-city.md`
- Modify: `backend/tests/test_freight_origins.py`

**Interfaces:**
- Consumes `refresh_origins_from_resolved_freight_invoices(db)` from Task 2.
- Produces a safe one-off command `python -m app.scripts.backfill_freight_origins` that reports checked, enriched and pending CNPJs without touching ERP data or tariffs.

- [ ] **Step 1: Write the failing script helper test**

```python
def test_backfill_reports_enriched_and_pending_counts(monkeypatch, db, capsys):
    monkeypatch.setattr(script, "refresh_origins_from_resolved_freight_invoices", lambda _: 3)
    assert script.run(db) == 3
    assert "3 origem(ns) enriquecida(s)" in capsys.readouterr().out
```

- [ ] **Step 2: Run the failing helper test**

Run: `pytest tests/test_freight_origins.py::test_backfill_reports_enriched_and_pending_counts -q`

Expected: FAIL because the script module and `run` function do not exist.

- [ ] **Step 3: Implement the safe backfill command and QA runbook**

```python
def run(db: Session) -> int:
    enriched = refresh_origins_from_resolved_freight_invoices(db)
    db.commit()
    print(f"{enriched} origem(ns) enriquecida(s); tarifas e conciliações não foram recalculadas.")
    return enriched
```

Document the production sequence: Alembic upgrade, deploy only `contracts_app` and `contracts_sync`, run the backfill inside `contracts_app`, inspect the five known origin CNPJs, and confirm that reconciliation count/value snapshots did not change. The runbook must include rollback via Alembic downgrade only before data writes are relied upon; otherwise disable display and keep the local registry harmless.

- [ ] **Step 4: Run full validation**

Run: `pytest -q && npm test && npm run build && git diff --check`

Expected: all backend and frontend checks pass; no whitespace errors.

- [ ] **Step 5: Commit release support**

```bash
git add backend/app/scripts/backfill_freight_origins.py backend/tests/test_freight_origins.py docs/qa/freight-origin-city.md
git commit -m "docs(freight): add origin registry release checks"
```
