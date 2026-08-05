# Créditos apropriados em cartões cumulativos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conciliar cartões cumulativos Ipiranga por crédito apropriado, expondo saldo residual apenas como informação.

**Architecture:** `monthly_routine.py` continuará consultando os lançamentos brutos do portal para o histórico, mas obterá o valor conciliável a partir de `ReconciliationAllocation` e `ReconciliationEvidence`. O retorno distinguirá crédito bruto, apropriado e residual; o frontend apenas apresenta estes valores já calculados.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, React/Vite.

## Global Constraints

- Não alterar regras financeiras, compras, títulos ou ERP.
- Não criar exceção por unidade; manter a política exclusiva já existente da unidade 004.
- Usar tolerância monetária de R$ 0,01 e preservar auditoria.

---

### Task 1: Cobertura de regressão do saldo residual

**Files:**
- Modify: `backend/tests/test_monthly_routine.py`
- Modify: `backend/app/api/monthly_routine.py`

**Interfaces:**
- Consumes: `ReconciliationAllocation`, `ReconciliationEvidence` e `PortalBonusEvent`.
- Produces: cartão com `observed_value`, `portal_credit_total_value` e `portal_unallocated_value`.

- [ ] **Step 1: Write the failing test**

```python
assert card["observed_value"] == 200.0
assert card["portal_credit_total_value"] == 250.0
assert card["portal_unallocated_value"] == 50.0
assert card["difference_value"] == 0.0
assert card["situation"] == "automatic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_monthly_routine.py -k cumulative_uses_allocated_portal_credit -q`

- [ ] **Step 3: Write minimal implementation**

Query allocations for portal evidence, sum only allocations whose item belongs
to the rule's reconciliations, and expose raw minus allocated as residual.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_monthly_routine.py -k cumulative_uses_allocated_portal_credit -q`

### Task 2: Presentation and deployment verification

**Files:**
- Modify: `frontend/src/App.jsx` only if the card needs the residual label.
- Modify: `frontend/src/styles.css` only if existing styles cannot present the label.

- [ ] **Step 1: Keep the existing card values tied to the reconciled amount**

Render “Créditos apropriados” for `observed_value`; render a secondary
informative line only when `portal_unallocated_value` is greater than R$ 0,01.

- [ ] **Step 2: Verify automated coverage and builds**

Run backend targeted and full tests, Ruff, and `npm run build` in `frontend`.

- [ ] **Step 3: Deploy only contracts_app and verify production**

Build the application, run `docker compose up -d --no-deps contracts_app`,
health-check inside the container, and query the production card for unit 001.
