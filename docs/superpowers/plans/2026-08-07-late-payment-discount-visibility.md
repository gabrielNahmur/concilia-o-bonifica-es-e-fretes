# Late-payment discount visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar descontos nativos lançados no ERP para títulos pagos com atraso, sem contá-los como bonificação contratual.

**Architecture:** O motor de conciliação já grava `raw_discount_value` no `details_json` do item. A API de detalhe/fila converterá esse valor em um campo explícito apenas para `late_payment`; o frontend apresentará o campo separado do valor observado contratual.

**Tech Stack:** FastAPI, SQLAlchemy, React, Vitest, pytest.

## Global Constraints

- Não gravar nem corrigir o ERP.
- Pagamento após vencimento continua inelegível para bonificação contratual.
- O desconto identificado é informativo e não altera `observed_value`, `difference_value` ou totais confirmados.

---

### Task 1: Expor o desconto identificado em itens com atraso

**Files:**
- Modify: `backend/app/api/reconciliations.py`, `backend/app/services/reconciliation_detail.py`
- Test: `backend/tests/test_api.py`, `backend/tests/test_reconciliation_detail.py`

**Interfaces:**
- Consumes: `ReconciliationItem.details_json.raw_discount_value` e `ReconciliationItem.status`.
- Produces: `identified_discount_value: float | None` nas respostas de fila e no item do detalhe.

- [ ] **Step 1: Write the failing test**

```python
assert queue_row["status"] == "late_payment"
assert queue_row["observed_value"] == 0.0
assert queue_row["identified_discount_value"] == 920.0
assert detail_item["identified_discount_value"] == 920.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api.py -k late_payment_identified_discount -q`

Expected: FAIL because `identified_discount_value` is absent.

- [ ] **Step 3: Write minimal implementation**

```python
identified_discount_value = (
    float(money(Decimal(str(details.get("raw_discount_value") or 0))))
    if status == "late_payment"
    else None
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api.py -k late_payment_identified_discount -q`

Expected: PASS.

### Task 2: Mostrar o desconto sem confundir com bonificação aplicável

**Files:**
- Modify: `frontend/src/App.jsx`
- Test: `frontend/src/reconciliation-queue-chain.test.jsx`

**Interfaces:**
- Consumes: `identified_discount_value` e `status == "late_payment"` recebidos da API.
- Produces: rótulo “Desconto identificado no ERP” e texto de não apropriação por atraso.

- [ ] **Step 1: Write the failing test**

```jsx
expect(screen.getByText("Desconto identificado no ERP")).toBeInTheDocument()
expect(screen.getByText("R$ 920,00")).toBeInTheDocument()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --run reconciliation-queue-chain.test.jsx`

Expected: FAIL because the label is absent.

- [ ] **Step 3: Write minimal implementation**

```jsx
{activeItem?.status === "late_payment" && activeItem?.identified_discount_value > 0 && (
  <Metric label="Desconto identificado no ERP" value={money(activeItem.identified_discount_value)} />
)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --run reconciliation-queue-chain.test.jsx`

Expected: PASS.

### Task 3: Verificação integrada

**Files:**
- Test: `backend/tests/test_api.py`, `frontend/src/reconciliation-queue-chain.test.jsx`

- [ ] **Step 1: Run backend coverage**

Run: `python -m pytest -q`

Expected: PASS.

- [ ] **Step 2: Run frontend tests and build**

Run: `npm test -- --run && npm run build`

Expected: PASS.
