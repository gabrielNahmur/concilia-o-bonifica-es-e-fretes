# Filtro detalhado de situações da conciliação Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir filtrar a fila de conciliação por situações financeiras específicas, sem alterar cálculos, evidências ou regras contratuais.

**Architecture:** O endpoint continuará materializando as linhas a partir de `Reconciliation` e `ReconciliationItem`, mas acrescentará uma categoria de filtro derivada dos valores, do vencimento, da revisão e dos ajustes históricos. O frontend enviará essas categorias detalhadas como parâmetros repetíveis e manterá a compatibilidade com os escopos operacionais existentes.

**Tech Stack:** FastAPI, SQLAlchemy, React, Vitest, pytest.

## Global Constraints

- Preservar integralmente o cálculo financeiro e os campos `status` e `state` já expostos.
- Manter seleção múltipla no filtro de situações.
- Não exibir itens `not_applicable` ou excluídos contratualmente na fila operacional.
- A seleção inicial deve continuar mostrando somente casos que exigem ação.

---

### Task 1: Classificação detalhada no endpoint da fila

**Files:**
- Modify: `backend/app/api/reconciliations.py:421-715`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `ReconciliationItem.status`, `ReconciliationItem.review_status`, valores esperado/identificado/diferença, `Reconciliation.due_date` e `evidence_json`.
- Produces: campo serializado `situation` com um de `overpaid`, `underpaid`, `pending`, `overdue`, `confirmed`, `late_payment`, `in_review` ou `approved_adjustment`.

- [ ] **Step 1: Write the failing test**

```python
response = client.get("/api/reconciliations/work-queue?unit=005&state=overpaid")
assert response.status_code == 200
assert [row["document"] for row in response.json()["items"]] == ["NF-OVER"]
assert response.json()["items"][0]["situation"] == "overpaid"
```

Create fixtures for one row of each detailed category and assert that `state=underpaid`, `state=pending`, `state=overdue`, `state=confirmed`, `state=late_payment`, `state=in_review`, and `state=approved_adjustment` return only the matching document.

- [ ] **Step 2: Run test to verify it fails**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests\\test_api.py::test_work_queue_filters_by_detailed_situation -q`

Expected: FAIL because `overpaid` is rejected as an invalid state.

- [ ] **Step 3: Write minimal implementation**

```python
def _queue_situation(status, state, expected, observed, difference, due_date, in_review, has_management_adjustment):
    if has_management_adjustment:
        return "approved_adjustment"
    if in_review or status == "review_required":
        return "in_review"
    if status == "late_payment":
        return "late_payment"
    if state == "confirmed":
        return "confirmed"
    if status == "overdue":
        return "overdue"
    if observed > 0 and difference < 0:
        return "overpaid"
    if observed > 0 and difference > 0:
        return "underpaid"
    return "pending"
```

Add `situation` to each queue row. Validate the new values in the `state` query parameter and apply filtering by `item["situation"]`; preserve legacy `actionable`, `waiting`, `confirmed`, and `all` behavior when no detailed filter is selected.

- [ ] **Step 4: Run test to verify it passes**

Run: `..\\.venv\\Scripts\\python.exe -m pytest tests\\test_api.py::test_work_queue_filters_by_detailed_situation -q`

Expected: PASS with every category isolated.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/reconciliations.py backend/tests/test_api.py
git commit -m "feat(reconciliation): add detailed queue situations"
```

### Task 2: Seletor e seleção inicial da fila

**Files:**
- Modify: `frontend/src/App.jsx:3060-3165`
- Test: `frontend/src/reconciliation-tabs.test.jsx`

**Interfaces:**
- Consumes: `work-queue` rows com `situation` e o parâmetro repetível `state`.
- Produces: opções visíveis `Pago maior`, `Pago menor`, `Pendentes`, `Vencidos`, `Confirmados`, `Pago em atraso`, `Em análise` e `Ajuste aprovado`.

- [ ] **Step 1: Write the failing test**

```jsx
render(<MemoryRouter initialEntries={["/conciliacoes"]}><Reconciliations user={user} /></MemoryRouter>);
expect(await screen.findByText("Pago maior")).toBeInTheDocument();
expect(screen.getByText("Ajuste aprovado")).toBeInTheDocument();
expect(get).toHaveBeenCalledWith(expect.stringContaining("state=pending"));
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- reconciliation-tabs.test.jsx`

Expected: FAIL because the selector exposes only Para tratar, Aguardando and Confirmados.

- [ ] **Step 3: Write minimal implementation**

```jsx
const stateOptions = [
  { value: "overpaid", label: "Pago maior" },
  { value: "underpaid", label: "Pago menor" },
  { value: "pending", label: "Pendentes" },
  { value: "overdue", label: "Vencidos" },
  { value: "confirmed", label: "Confirmados" },
  { value: "late_payment", label: "Pago em atraso" },
  { value: "in_review", label: "Em análise" },
  { value: "approved_adjustment", label: "Ajuste aprovado" },
];
```

Set the default to the actionable set `pending`, `underpaid`, `overpaid`, `overdue`, `in_review`. Update the empty-state wording to describe the detailed selection rather than the old `actionable` scope.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- reconciliation-tabs.test.jsx`

Expected: PASS and request URL contains the selected detailed states.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/reconciliation-tabs.test.jsx
git commit -m "feat(reconciliation): expose detailed situation filters"
```

### Task 3: Verificação integrada e publicação

**Files:**
- Modify: none expected

**Interfaces:**
- Consumes: API and interface changes from Tasks 1 and 2.
- Produces: buildable application with stable queue filtering.

- [ ] **Step 1: Run backend suite**

Run: `..\\.venv\\Scripts\\python.exe -m pytest -q` from `backend`.

Expected: all tests pass.

- [ ] **Step 2: Run frontend tests and build**

Run: `npm test` then `npm run build` from `frontend`.

Expected: all tests and production build pass.

- [ ] **Step 3: Publish only changed application files**

Copy changed backend and frontend source to `/home/ubuntu/apps/contracts-gbi`, rebuild `contracts_app`, verify `/api/health`, and check the work-queue response includes `situation`.

- [ ] **Step 4: Commit and push the integration state**

```bash
git push
```

## Self-review

- Spec coverage: Task 1 implements all eight detailed situations and compatibility; Task 2 exposes them with multiselect; Task 3 validates and publishes them.
- Placeholder scan: no TBD/TODO markers or unspecified code paths remain.
- Type consistency: `situation` is a string returned by the API and used as the same repeated `state` query value in the frontend.
