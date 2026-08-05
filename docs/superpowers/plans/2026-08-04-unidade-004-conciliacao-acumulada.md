# Unidade 004 - Conciliação Acumulada por Créditos Ipiranga Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a unidade 004 ser conciliada pelo total de bonificação calculada das compras contratuais versus créditos postecipados comprovados no extrato Ipiranga, sem inferir que uma NF de compra originou um crédito específico.

**Architecture:** Os detalhes do portal passam a registrar uma ligação direta entre cada evento de crédito e a NF onde ele foi usado. A fila operacional deixa de criar itens por compra para a 004; em seu lugar, ela cria itens por crédito efetivamente comprovado. A rotina mensal mostra o resumo acumulado, com o residual histórico de R$ 69,99 separado dos valores ainda aguardando extrato.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pytest, React existente sem dependências novas.

## Global Constraints

- Alterar exclusivamente a regra `004 / IPIRANGA / distributor_credit`.
- Portal Ipiranga é fonte primária; o ERP permanece somente leitura.
- Não criar uma cobrança por NF quando o portal não declarar sua origem.
- Preservar a auditoria e não reclassificar R$ 69,99 histórico como inadimplência.
- Não modificar regras de 001, 003, 005, 007, 008, 014, 050, 054 ou BR 002/006.

---

### Task 1: Registrar e preservar a evidência direta de uso do crédito do portal

**Files:**
- Modify: `backend/app/services/ipiranga_portal.py:1447-1558`
- Create: `backend/app/services/unit_004_portal_usage.py`
- Create: `backend/app/scripts/apply_unit_004_portal_usage_evidence.py`
- Test: `backend/tests/test_ipiranga_portal.py`

**Interfaces:**
- Consumes: `PortalBonusEvent`, `PortalBonusMatch`, `Purchase`.
- Produces: `unit_004_portal_usage_events(db, rule) -> list[Unit004PortalUsage]` and imported matches with `status == "portal_usage_confirmed"`.

- [ ] **Step 1: Write the failing test**

```python
def test_ipiranga_matcher_preserves_unit_004_portal_usage_confirmation(db):
    event = PortalBonusEvent(
        id="unit-004-usage", unit_code="004", company_code="IPIRANGA",
        category="postpaid", portal_date=date(2026, 1, 16), value=Decimal("10360.01"),
    )
    db.add(event)
    db.flush()
    db.add(PortalBonusMatch(event_id=event.id, status="portal_usage_confirmed"))
    db.commit()

    match_ipiranga_events(db, "004")

    assert db.get(PortalBonusMatch, event.id).status == "portal_usage_confirmed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_ipiranga_portal.py::test_ipiranga_matcher_preserves_unit_004_portal_usage_confirmation -v`

Expected: FAIL because the generic matcher overwrites the explicit portal detail.

- [ ] **Step 3: Write minimal implementation**

```python
if event.unit_code == "004" and match and match.status == "portal_usage_confirmed":
    continue
```

Create a typed helper which loads only postpaid `portal_usage_confirmed` matches for the passed 004 rule and resolves the used purchase by `purchase_entry_id`.

- [ ] **Step 4: Add the idempotent evidence script**

The script must upsert the nine approved portal details by event date/value, validate the unit, supplier, NF number, gross value and ERP access key, and refuse a conflicting existing direct match. It stores `usage_invoice_number`, `usage_access_key`, `usage_invoice_gross` and `source_kind="portal_detail_transcription"` in `details_json`.

- [ ] **Step 5: Run tests to verify it passes**

Run: `pytest backend/tests/test_ipiranga_portal.py -v`

Expected: PASS.

### Task 2: Materializar somente créditos comprovados do portal na fila da 004

**Files:**
- Modify: `backend/app/services/reconciliation.py:704-1490`
- Modify: `backend/app/services/reconciliation_workspace.py:254-620`
- Modify: `backend/app/services/reconciliation_detail.py`
- Test: `backend/tests/test_reconciliation_workspace.py`
- Test: `backend/tests/test_reconciliation_matching.py`

**Interfaces:**
- Consumes: `unit_004_portal_usage_events(db, rule)` and a `PortalBonusMatch` direct match.
- Produces: one `ReconciliationItem(item_type="portal_credit_usage")` for every confirmed credit, and no 004 operational item per uninferred purchase NF.

- [ ] **Step 1: Write the failing test**

```python
def test_unit_004_materializes_portal_credit_not_each_purchase_nf(db):
    rule, row = _unit_004_rule_and_row(db)
    _add_two_unit_004_purchases(db, liters=("50000", "75000"))
    _add_direct_portal_usage(db, value="8750.00", used_entry_id=40002)

    rebuild_reconciliation_workspace(db, today=date(2026, 1, 20))

    items = db.scalars(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id)).all()
    assert [(item.item_type, item.source_document, item.expected_value, item.observed_value)] == [
        ("portal_credit_usage", "40002", Decimal("8750.00"), Decimal("8750.00")),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_reconciliation_workspace.py::test_unit_004_materializes_portal_credit_not_each_purchase_nf -v`

Expected: FAIL because the current cycle logic materializes purchase items.

- [ ] **Step 3: Write minimal implementation**

Delete the active 004 cycle allocation path. For the 004 rule, build payloads from direct portal usage events only. Each payload uses the event value as expected and identified value, has `automatic_kind=True`, and gives the portal date, NF used, access key and match basis as evidence.

- [ ] **Step 4: Keep the original aggregate monthly values informational**

Do not alter purchase volume or the base expected formula. Suppress 004 aggregate-row exceptions when it has no materialized item, so a purchase without portal origin cannot leak into the work queue as a charge.

- [ ] **Step 5: Run tests to verify it passes**

Run: `pytest backend/tests/test_reconciliation_workspace.py backend/tests/test_reconciliation_matching.py -v`

Expected: PASS.

### Task 3: Exibir o acompanhamento acumulado correto na rotina mensal

**Files:**
- Modify: `backend/app/api/monthly_routine.py:251-457`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/ui.jsx`
- Test: `backend/tests/test_monthly_routine.py`

**Interfaces:**
- Consumes: total de compras elegíveis, créditos `portal_usage_confirmed`, corte histórico aprovado `2026-05-26` e ajuste de R$ 69,99.
- Produces: cartão da 004 com `expected`, `identified`, `awaiting_statement`, `historical_adjustment` e mensagem operacional clara.

- [ ] **Step 1: Write the failing test**

```python
def test_monthly_routine_004_separates_historical_residual_from_next_statement(db):
    _seed_unit_004_expected_through(db, date(2026, 5, 26), Decimal("78540.00"))
    _seed_unit_004_portal_usage(db, Decimal("78470.01"))

    card = _find_unit(build_monthly_routine(db, today=date(2026, 8, 4)), "004")

    assert card["historical_adjustment"] == 69.99
    assert card["next_statement_expected"] == 0.0
    assert card["situation"] == "automatic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_monthly_routine.py::test_monthly_routine_004_separates_historical_residual_from_next_statement -v`

Expected: FAIL because the standard monthly logic reports the residual as an unresolved competence.

- [ ] **Step 3: Write minimal implementation**

Build a single aggregate 004 card. It reports confirmed portal credits, calculated contractual bonus through the approved historical cut-off, the R$ 69,99 historical balance as monitored adjustment, and the bonus from later purchases as `aguardando próximo extrato`. The card must never use the words `cobrar`, `vencida` or `divergente` solely because an origin NF is absent.

- [ ] **Step 4: Render the card with plain language**

Use existing card components. Show “Créditos confirmados no extrato Ipiranga”, “Aguardando próximo extrato” when later purchases exist, and “Ajuste histórico acompanhado: R$ 69,99”. Keep the direct link to the history/conciliation drawer.

- [ ] **Step 5: Run tests and build to verify it passes**

Run: `pytest backend/tests/test_monthly_routine.py -v; npm run build --prefix frontend`

Expected: all tests pass and the frontend build exits with code 0.

### Task 4: Apply the approved production evidence and verify the live behavior

**Files:**
- Execute: `backend/app/scripts/apply_unit_004_portal_usage_evidence.py`
- Verify: production API and the 004 routine after rebuild.

- [ ] **Step 1: Run the evidence script on production**

Run inside the application container with production database configuration. It must report nine direct portal links and no conflicts.

- [ ] **Step 2: Rebuild the reconciliation workspace**

Run the existing rebuild command after the evidence script. The queue must remove all U004 inferred purchase cases and retain only direct portal usage history.

- [ ] **Step 3: Verify live API result**

Check `/api/monthly-routine` and the reconciliation history using an authenticated admin session. Confirm the U004 card separates R$ 69,99 from later purchases and that detail entries show direct portal evidence.

- [ ] **Step 4: Run full backend verification**

Run: `pytest backend/tests -q; npm run build --prefix frontend`

Expected: zero failing tests and successful build before deployment.
