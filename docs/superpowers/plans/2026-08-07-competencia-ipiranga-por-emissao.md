# Competência Ipiranga por emissão da NF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calcular créditos Ipiranga pela competência da emissão da NF, sem alterar a regra de crédito emitido no mês seguinte para 003 e 004.

**Architecture:** A apuração de litros e a recuperação de NFs no workspace passam a usar `COALESCE(invoice_issue_date, purchase_date)` nas regras Ipiranga `distributor_credit`. As demais regras continuam com a data operacional de entrada.

**Tech Stack:** Python 3, SQLAlchemy, Pytest.

## Global Constraints

- Não escrever no ERP.
- Preservar `purchase_date` como data operacional do ERP.
- Aplicar somente à bonificação Ipiranga do tipo `distributor_credit`.
- O extrato Ipiranga continua sendo a prova financeira primária para 003 e 004.

---

### Task 1: Cobrir a virada de competência por emissão

**Files:**
- Modify: `backend/tests/test_reconciliation_matching.py`
- Modify: `backend/app/services/reconciliation.py`

**Interfaces:**
- Consumes: `Purchase.invoice_issue_date` e `Purchase.purchase_date`.
- Produces: valores esperados mensais pelo mês da emissão para `distributor_credit` Ipiranga.

- [ ] **Step 1: Write the failing test**

```python
def test_ipiranga_credit_uses_invoice_issue_month_when_entry_crosses_month():
    assert april.expected_value == Decimal("480.00")
    assert april.observed_value == Decimal("480.00")
    assert may.expected_value == Decimal("0.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reconciliation_matching.py::test_ipiranga_credit_uses_invoice_issue_month_when_entry_crosses_month -q`

Expected: failure because the prior calculation uses `Purchase.purchase_date` and puts the 8.000 L in May.

- [ ] **Step 3: Write minimal implementation**

```python
contractual_date = func.coalesce(Purchase.invoice_issue_date, Purchase.purchase_date)
```

Use `contractual_date` in `_purchase_totals()` only when the rule is an
Ipiranga `distributor_credit` rule.

- [ ] **Step 4: Run targeted test to verify it passes**

Run: `python -m pytest tests/test_reconciliation_matching.py::test_ipiranga_credit_uses_invoice_issue_month_when_entry_crosses_month -q`

Expected: pass.

### Task 2: Align audit context and validate historical effect

**Files:**
- Modify: `backend/app/services/reconciliation_workspace.py`
- Test: `backend/tests/test_reconciliation_workspace.py`

**Interfaces:**
- Consumes: the same contractual-date expression from Task 1.
- Produces: detail rows whose listed NFs equal the financial calculation.

- [ ] **Step 1: Write a failing workspace test**

```python
assert [purchase.invoice_number for purchase in purchases] == ["3055164"]
```

- [ ] **Step 2: Run the targeted test and verify it fails**

Run: `python -m pytest tests/test_reconciliation_workspace.py -q`

Expected: failure because the detail still filters by ERP entry date.

- [ ] **Step 3: Implement the same Ipiranga contractual-date filter**

Keep the order and display date operational where needed, but select the
month's purchases by invoice issue date for Ipiranga distributor credits.

- [ ] **Step 4: Run full validation and production rebuild**

Run: `python -m pytest -q`, `ruff check app tests`, and `npm run build`.
Deploy `contracts_app` and `contracts_sync`, run the reconciliation rebuild,
then validate unit 003 April/May and unit 004 June/July against ERP and portal evidence.
