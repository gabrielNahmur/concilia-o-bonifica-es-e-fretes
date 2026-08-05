# Reversão da Navegação de Conciliações Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restaurar `/conciliacoes` como uma página direta da Fila de conciliação, sem o seletor de quatro visões.

**Architecture:** O componente público `Reconciliations` continuará sendo usado pela rota, mas passará a compor apenas o cabeçalho anterior e `ReconciliationQueuePanel`. Os componentes de Exceções, Competências e Cobertura permanecem no código e as correções de backend não serão alteradas.

**Tech Stack:** React 19, React Router 7, Vitest, Testing Library e CSS existente.

## Global Constraints

- Remover somente o seletor de quatro visões e seu estado local.
- Preservar filtros, parâmetros de URL, paginação, tratamento de erros e drawer da fila.
- Não excluir componentes ou endpoints das visões ocultadas.
- Não publicar em produção.

---

### Task 1: Restaurar a Fila como página única

**Files:**
- Modify: `frontend/src/reconciliation-tabs.test.jsx`
- Modify: `frontend/src/App.jsx:3046-3167`
- Modify: `frontend/src/styles.css:2574-2599,3081-3082,3333-3334`
- Modify: `docs/qa/2026-08-05-matriz-funcional.md`
- Modify: `docs/qa/2026-08-05-relatorio-auditoria-funcional.md`

**Interfaces:**
- Consumes: `ReconciliationQueuePanel({ user })` e os parâmetros atuais de `location.search`.
- Produces: `Reconciliations({ user })`, renderizando diretamente a fila sem seletor de visões.

- [ ] **Step 1: Escrever o teste de regressão que falha**

Substituir as expectativas de troca entre abas por um cenário que renderiza `Reconciliations`, aguarda o cabeçalho da fila e verifica que não existem botões exatos com os nomes das quatro visões:

```jsx
it("opens the reconciliation queue directly without the additional view selector", async () => {
  renderReconciliations();

  expect(await screen.findByRole("heading", { name: "Fila de conciliação" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Fila", exact: true })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Exceções", exact: true })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Competências", exact: true })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cobertura automática", exact: true })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Executar o teste e confirmar a falha correta**

Run: `cd frontend; npx vitest run src/reconciliation-tabs.test.jsx`

Expected: FAIL porque o título ainda é `Conciliações` e o botão exato `Fila` ainda existe.

- [ ] **Step 3: Aplicar a reversão mínima no componente**

Manter `ReconciliationQueuePanel` e remover dele o cabeçalho interno `Fila operacional`. Substituir o corpo de `Reconciliations` por:

```jsx
export function Reconciliations({ user }) {
  return <>
    <PageHeader
      eyebrow="CONCILIAÇÃO"
      title="Fila de conciliação"
      subtitle="Comece pelo que precisa de ação. Os confirmados ficam no histórico, sem esconder a rastreabilidade."
    />
    <ReconciliationQueuePanel user={user} />
  </>;
}
```

Não alterar `ExceptionQueue`, `CompetencesPanel`, `ReconciliationCoverage` nem os endpoints usados por esses componentes.

- [ ] **Step 4: Remover CSS sem consumidor**

Excluir somente os blocos `.reconciliation-tabs` e suas regras responsivas. Não alterar estilos da fila, filtros ou drawers.

- [ ] **Step 5: Atualizar a documentação de QA**

Registrar que QA-REC-006 foi revertida por decisão de produto e que `/conciliacoes` voltou a expor somente a fila. Preservar como aprovadas as correções de filtros, contagens e severidade.

- [ ] **Step 6: Executar verificações da tarefa**

Run: `cd frontend; npx vitest run src/reconciliation-tabs.test.jsx`

Expected: PASS.

Run: `cd frontend; npm test; npm run build`

Expected: 5 testes Node e todos os testes Vitest aprovados; build Vite concluído.

- [ ] **Step 7: Verificar o diff sem incorporar alterações paralelas**

Run: `git diff --check -- frontend/src/App.jsx frontend/src/styles.css frontend/src/reconciliation-tabs.test.jsx docs/qa/2026-08-05-matriz-funcional.md docs/qa/2026-08-05-relatorio-auditoria-funcional.md`

Expected: nenhuma falha de whitespace. Como `App.jsx` e `styles.css` já contêm alterações paralelas no worktree, não criar commit da implementação sem separar esses hunks com segurança.
