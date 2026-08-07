# Créditos Ipiranga no mês seguinte - Unidades 003 e 004 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conciliar mensalmente os créditos Ipiranga de 003 e 004 pelo extrato emitido no mês seguinte, sem inferir ciclos ou encaixes por valor.

**Architecture:** A reconstrução mensal seleciona os eventos `postpaid` do portal em uma janela fechada de um mês posterior à competência. O workspace materializa uma linha por competência usando apenas esses eventos como prova. A divergência permanece quando os valores não fecham.

**Tech Stack:** Python 3, SQLAlchemy, FastAPI, Pytest.

## Global Constraints

- Aplicar somente a 003 e 004, Ipiranga, `distributor_credit`.
- Extrato Ipiranga é a prova primária; nenhuma escrita no ERP.
- Não fazer rateio, FIFO, ciclo de NFs ou ajuste automático por valor.
- Preservar evidências e ajustes históricos para auditoria.

---

### Task 1: Cobrir a associação por mês seguinte

**Files:**
- Modify: `backend/tests/test_reconciliation_matching.py`
- Modify: `backend/app/services/reconciliation.py`

**Interfaces:**
- Produces: `_next_month_ipiranga_portal_credit_evidence(pool, reference_month)` returning the full issued value and portal evidence for the following calendar month.

- [x] **Step 1: Write the failing test**

```python
def test_003_and_004_use_all_postpaid_portal_credit_issued_in_next_month(db):
    # June/2026 expected 7700.00; July portal issue is 7350.00.
    rebuild_reconciliations(db, today=date(2026, 8, 7))
    assert june_004.observed_value == Decimal("7350.00")
    assert june_004.difference_value == Decimal("350.00")
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reconciliation_matching.py::test_003_and_004_use_all_postpaid_portal_credit_issued_in_next_month -q`

Expected: failure because the former algorithm only uses an exact amount or leaves the competence at zero.

- [x] **Step 3: Write minimal implementation**

```python
next_month = add_months(month_start(reference_month), 1)
credits = [item for item in pool if next_month <= item["date"] < add_months(next_month, 1)]
observed = money(sum((item["raw_value"] for item in credits), ZERO))
```

Create evidence with the portal date, full source value, full allocated value,
and a reason naming the monthly-next rule. Bypass exact reservations, FIFO and
unassigned-credit logic only for the two approved rules.

- [x] **Step 4: Run the targeted tests**

Run: `python -m pytest tests/test_reconciliation_matching.py -q`

Expected: pass.

### Task 2: Materialize the same monthly evidence in the operational queue

**Files:**
- Modify: `backend/app/services/reconciliation_workspace.py`
- Modify: `backend/tests/test_reconciliation_matching.py`

**Interfaces:**
- Consumes the monthly `IPIRANGA_PORTAL` evidence produced by Task 1.
- Produces one competence item for 003 and 004, with the exact observed total and readable policy reason.

- [x] **Step 1: Write the failing test**

```python
assert item.item_type == "distributor_credit"
assert item.observed_value == Decimal("7350.00")
assert item.status == "overdue"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reconciliation_matching.py::test_003_and_004_use_all_postpaid_portal_credit_issued_in_next_month -q`

Expected: failure because 004 currently materializes a portal-usage history item instead of the competence.

- [x] **Step 3: Write minimal implementation**

Remove the active 004 portal-usage special case from the workspace’s monthly
payload selection. Keep its source records untouched, but build the normal
`distributor_credit` competence item from the current monthly evidence.

- [x] **Step 4: Run targeted and full backend tests**

Run: `python -m pytest tests/test_reconciliation_matching.py tests/test_monthly_routine.py -q`

Expected: pass.

### Task 3: Validate and apply the production rule

**Files:**
- Verify: `C:/Users/confi/Downloads/Extrato (36).pdf`
- Deploy: `backend/app/services/reconciliation.py`, `backend/app/services/reconciliation_workspace.py`

- [x] **Step 1: Inspect the supplied PDF**

Confirm it contains the July/2026 unit 004 postpaid credit of R$ 7.350,00 and
does not introduce a duplicate portal event.

- [x] **Step 2: Run complete verification**

Run: `python -m pytest -q`, `ruff check app tests`, and `npm run build` from
the respective backend/frontend directories.

- [x] **Step 3: Deploy both runtime services and rebuild only application data**

Build `contracts_app` and `contracts_sync`, run the reconciliation rebuild,
and validate health. The rebuild updates the application database only; it
does not write to the ERP.
