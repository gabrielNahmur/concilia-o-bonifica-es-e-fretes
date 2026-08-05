# Solicitações de informação no drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exibir solicitações internas de informação no detalhe da conciliação com linguagem operacional e sem alterar a regra financeira.

**Architecture:** Um helper puro no frontend transforma o estado já recebido do item conciliável em um modelo de apresentação. `QueueReconciliationDetail` renderiza o modelo em uma seção visível; a auditoria reutiliza um mapeamento de ações amigáveis. Não haverá mudanças em API, banco ou ERP.

**Tech Stack:** React, Vite, Node test runner, CSS existente.

## Global Constraints

- O ERP permanece somente leitura.
- A ação não envia e-mail nem notificação externa.
- A visualização não pode alterar valores, status financeiro ou evidências.
- O texto técnico `item_needs_information` não aparece para gestores.

---

### Task 1: Modelo de apresentação da solicitação

**Files:**
- Modify: `frontend/src/reconciliation-actions.js`
- Modify: `frontend/src/reconciliation-actions.test.js`

**Interfaces:**
- Consumes: itens de `detail.workspace.items` com `review_status`, `source_document`, `reviewed_by`, `reviewed_at` e `review_notes`.
- Produces: `informationRequests(items)`, uma lista com `document`, `requestedBy`, `requestedAt`, `reason` e `notes`.

- [ ] **Step 1: Write the failing test**

```js
assert.deepEqual(informationRequests([{ source_document: "2958247", review_status: "needs_information", reviewed_by: "Gustavo", reviewed_at: "2026-08-05T13:00:00Z", review_notes: "[boleto_pendente] Solicitar boleto." }]), [{ document: "2958247", requestedBy: "Gustavo", requestedAt: "2026-08-05T13:00:00Z", reason: "boleto pendente", notes: "Solicitar boleto." }]);
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test frontend/src/reconciliation-actions.test.js`

Expected: FAIL because `informationRequests` is not exported.

- [ ] **Step 3: Write minimal implementation**

```js
export function informationRequests(items = []) {
  return items.filter((item) => item.review_status === "needs_information").map(toInformationRequest);
}
```

`toInformationRequest` separates the optional `[motivo]` prefix from the justification and replaces underscores with spaces.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test frontend/src/reconciliation-actions.test.js`

Expected: PASS.

### Task 2: Seção operacional e auditoria traduzida

**Files:**
- Modify: `frontend/src/App.jsx: QueueReconciliationDetail`
- Modify: `frontend/src/styles.css: queue drawer styles`

**Interfaces:**
- Consumes: `informationRequests(detail.workspace?.items)`.
- Produces: seção “Solicitações de informação” e função local de rótulo de auditoria.

- [ ] **Step 1: Render the visible request section**

```jsx
{requests.length > 0 && <section className="detail-section information-requests">
  <div className="section-title">...</div>
  {requests.map((request) => <article key={request.itemId}>...</article>)}
</section>}
```

O cartão exibirá estado, documento, solicitante, data, motivo e justificativa.

- [ ] **Step 2: Translate audit actions**

```js
const auditActionLabel = (action) => ({
  item_needs_information: "Solicitação de informação registrada",
}[action] || action);
```

- [ ] **Step 3: Add focused styles**

Use o padrão visual de cartões de detalhe, com tom azul de acompanhamento e sem alterar os cards de valores.

- [ ] **Step 4: Build the frontend**

Run: `npm run build --prefix frontend`

Expected: exit code 0.

### Task 3: Regressão e publicação

**Files:**
- Modify: arquivos das Tasks 1 e 2.

- [ ] **Step 1: Run complete verification**

Run: `node --test frontend/src/reconciliation-actions.test.js && .\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q && .\\.venv\\Scripts\\ruff.exe check backend && npm run build --prefix frontend && git diff --check`

Expected: todos os testes passam, lint sem erros, build concluído e diff sem whitespace errors.

- [ ] **Step 2: Deploy the frontend**

Copy `App.jsx`, `styles.css` and `reconciliation-actions.js` to `/home/ubuntu/apps/contracts-gbi/frontend/src/`, then run `docker compose -f docker-compose.prod.yml up -d --build`.

- [ ] **Step 3: Validate production**

Run the API healthcheck in `contracts_app` and open a reviewed invoice in `/conciliacoes` to confirm the visible section and the friendly audit label.
