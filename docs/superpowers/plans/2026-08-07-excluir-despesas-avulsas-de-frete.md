# Exclusão de despesas avulsas de frete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remover da conciliação de fretes as entradas suplementares sem compra de combustível comprovada, preservando os dados brutos sincronizados.

**Architecture:** `rebuild_freight_reconciliations` continuará calculando os vínculos automáticos antes de decidir o escopo. Entradas `purchase_entry` sem nenhum vínculo resolvido serão retiradas somente da tabela materializada `FreightReconciliation`; CT-es de `MCTe` e suplementos com compra vinculada permanecem intactos.

**Tech Stack:** Python, SQLAlchemy, FastAPI, pytest.

## Global Constraints

- Não escrever no ERP.
- Não excluir `FreightCte` nem `FreightCteInvoice`.
- Não ocultar CT-e normal de `MCTe` apenas porque a NF-e está ausente ou divergente.

---

### Task 1: Filtrar reconciliações sem combustível comprovado

**Files:**
- Modify: `backend/tests/test_freights.py`
- Modify: `backend/app/services/freight_reconciliation.py`

**Interfaces:**
- Consumes: `used: dict[int, set[int]]` retornado por `_document_links`.
- Produces: `rebuild_freight_reconciliations(db)` sem `FreightReconciliation` para suplementos sem compra resolvida.

- [ ] **Step 1: Write the failing test**

```python
assert db.scalar(
    select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == -13027)
) is None
```

O cenário cria uma entrada suplementar sem compra de combustível resolvida e uma reconciliação materializada antiga, comprovando que o rebuild deve removê-la.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_freights.py -k supplemental_without_fuel -q`

Expected: FAIL porque o rebuild atual mantém ou recria a reconciliação da entrada sem vínculo.

- [ ] **Step 3: Write minimal implementation**

```python
out_of_scope_ids = [
    cte.erp_cte_id
    for cte in ctes
    if cte.source_kind == "purchase_entry" and not used.get(cte.erp_cte_id)
]
db.execute(delete(FreightReconciliation).where(FreightReconciliation.erp_cte_id.in_(out_of_scope_ids)))
ctes = [cte for cte in ctes if cte.erp_cte_id not in set(out_of_scope_ids)]
```

Execute este bloco depois de `_document_links` e antes de materializar as reconciliações, mantendo `FreightCte` e `FreightCteInvoice` sem alteração.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_freights.py -k supplemental_without_fuel -q`

Expected: PASS.

- [ ] **Step 5: Run the freight regression suite**

Run: `python -m pytest tests/test_freights.py -q`

Expected: PASS sem regressão dos lotes Talismã, VKL, Brondani e CT-es normais divergentes.
