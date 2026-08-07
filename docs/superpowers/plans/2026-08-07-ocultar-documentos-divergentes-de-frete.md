# Ocultação de documentos divergentes de frete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remover `document_mismatch` da visão operacional de fretes sem excluir seus dados de auditoria.

**Architecture:** Todas as telas de fretes partem de `_statement` em `backend/app/api/freights.py`. O filtro será aplicado nessa consulta única, preservando os modelos `FreightReconciliation`, `FreightCte` e `FreightCteInvoice` no banco.

**Tech Stack:** Python, SQLAlchemy, FastAPI, pytest.

## Global Constraints

- Não escrever no ERP.
- Não apagar registros ou evidências de frete.
- Permitir reaparecimento automático quando a sincronização alterar o status.

---

### Task 1: Excluir divergências da visão operacional compartilhada

**Files:**
- Modify: `backend/tests/test_freights.py`
- Modify: `backend/app/api/freights.py`

**Interfaces:**
- Consumes: `FreightReconciliation.primary_status`.
- Produces: `_statement(...)` sem linhas com `primary_status == "document_mismatch"`.

- [ ] **Step 1: Write the failing test**

```python
assert [row.erp_cte_id for row in db.scalars(_statement("2026-06", None, None, None, None)).all()] == [correct_id]
assert freight_summary(db, None, competence="2026-06")["total_ctes"] == 1
assert db.get(FreightReconciliation, divergent_id) is not None
```

O cenário contém uma reconciliação correta e outra divergente, comprovando que a segunda permanece auditável, mas não integra a operação.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_freights.py -k document_mismatch_is_hidden -q`

Expected: FAIL porque `_statement` atual ainda retorna o documento divergente.

- [ ] **Step 3: Write minimal implementation**

```python
statement = select(FreightReconciliation).join(FreightCte).where(
    FreightCte.source_active.is_(True),
    FreightCte.is_canceled.is_(False),
    FreightReconciliation.primary_status != "document_mismatch",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_freights.py -k document_mismatch_is_hidden -q`

Expected: PASS.

- [ ] **Step 5: Run freight regressions**

Run: `python -m pytest tests/test_freights.py -q`

Expected: PASS, mantendo CT-es corretos e demais pendências operacionais visíveis.
