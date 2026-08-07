# Ajustes históricos em Créditos apropriados Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exibir ajustes históricos aprovados como parte do total conciliado de créditos apropriados na rotina mensal.

**Architecture:** `monthly_routine.py` continuará calculando os valores brutos do portal separadamente, mas exporá o total conciliado em `observed_value`. O frontend manterá o rótulo atual e detalhará o componente histórico em texto, usando um novo campo de leitura para o valor apropriado somente do portal.

**Tech Stack:** FastAPI, SQLAlchemy, React, Vitest e pytest.

## Global Constraints

- Não escrever no ERP nem mudar eventos importados, compras ou regras contratuais.
- Ajuste histórico não pode ser apresentado como crédito emitido pela distribuidora.
- Executar o teste de regressão em vermelho antes da implementação e a suíte relevante depois.

---

### Task 1: Expor o total conciliado do card acumulado

**Files:**
- Modify: `backend/tests/test_monthly_routine.py:240-375`
- Modify: `backend/app/api/monthly_routine.py:213-402`

**Interfaces:**
- Consumes: créditos alocados do portal e `Reconciliation.manual_adjustment`.
- Produces: `observed_value` como total conciliado e `portal_appropriated_value` como subtotal exclusivo do portal.

- [ ] **Step 1: Write the failing test**

```python
assert card["observed_value"] == 300.0
assert card["portal_appropriated_value"] == 200.0
assert card["difference_value"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_monthly_routine.py::test_monthly_routine_cumulative_includes_audited_historical_adjustment_in_observed_total -q`

Expected: failure because the current `observed_value` returns only R$ 200,00 from the portal.

- [ ] **Step 3: Write minimal implementation**

```python
portal_appropriated = identified
identified = money(portal_appropriated + historical_adjustment)
effective_difference = money(expected - identified)
```

Return `portal_appropriated_value` separately and retain the existing portal gross/residual fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_monthly_routine.py::test_monthly_routine_cumulative_includes_audited_historical_adjustment_in_observed_total -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/monthly_routine.py backend/tests/test_monthly_routine.py
git commit -m "fix(routine): include historical adjustments in identified credits"
```

### Task 2: Explicar a composição no card da rotina

**Files:**
- Modify: `frontend/src/App.jsx:3282-3289`
- Modify: `frontend/src/monthly-routine.test.jsx:1-100`

**Interfaces:**
- Consumes: `historical_adjustment_value` e `portal_appropriated_value` do card acumulado.
- Produces: nota legível que separa ajuste auditado de crédito emitido no portal.

- [ ] **Step 1: Write the failing test**

```jsx
expect(screen.getByText(/incluído em Créditos apropriados: R\$ 100,00/)).toBeInTheDocument();
expect(screen.getByText(/Créditos apropriados no portal: R\$ 200,00/)).toBeInTheDocument();
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- monthly-routine.test.jsx`

Expected: failure because the current note says the adjustment is maintained separately.

- [ ] **Step 3: Write minimal implementation**

```jsx
<small>
  Ajuste histórico aprovado incluído em Créditos apropriados: {money(card.historical_adjustment_value)}.
  Créditos apropriados no portal: {money(card.portal_appropriated_value)}.
</small>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- monthly-routine.test.jsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/monthly-routine.test.jsx
git commit -m "fix(routine): explain historical credit adjustment"
```

### Task 3: Verify the complete affected surfaces

**Files:**
- Verify: `backend/tests/test_monthly_routine.py`
- Verify: `frontend/src/monthly-routine.test.jsx`

- [ ] **Step 1: Run backend suite**

Run: `python -m pytest tests/test_monthly_routine.py -q`

Expected: all routine tests pass.

- [ ] **Step 2: Run frontend tests and build**

Run: `npm test && npm run build`

Expected: all frontend tests pass and Vite completes successfully.

- [ ] **Step 3: Review the diff**

Run: `git diff --check origin/codex/correcoes-funcionais..HEAD`

Expected: no output.
