# Ocultar volume auxiliar do CT-e Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remover o volume auxiliar declarado no CT-e do detalhe visual de fretes.

**Architecture:** O frontend deixa de renderizar exclusivamente o card que consome `detail.cte.cargo_liters`. A API, sincronização e dados técnicos continuam inalterados; o card de litros comprovados permanece como a única medida de volume apresentada à pessoa usuária.

**Tech Stack:** React 19, Vitest, Testing Library, Vite.

## Global Constraints

- Não alterar `cargo_liters`, `MCTe_Carga`, API, banco de dados ou regras de conciliação.
- Remover apenas o campo visual **Volume auxiliar da carga** do bloco **CT-e e cobrança**.
- Manter todos os demais cards desse bloco, inclusive **Finalidade**.
- Manter **Litros comprovados** no resumo do detalhe de frete.

---

### Task 1: Remover o card de volume auxiliar da interface

**Files:**
- Modify: `frontend/src/freight-origin.test.jsx`
- Modify: `frontend/src/App.jsx:1049-1055,1128-1137`

**Interfaces:**
- Consumes: `detail: { reference_date, issue_date, sender_name, origins, cte }` no novo componente `FreightCteFacts`.
- Produces: bloco **CT-e e cobrança** sem referência visual a `detail.cte.cargo_liters`.

- [ ] **Step 1: Write the failing test**

```jsx
it("does not expose the auxiliary cargo volume in CT-e facts", () => {
  render(<FreightCteFacts detail={{ reference_date: "2026-07-09", issue_date: "2026-07-09", sender_name: "Fornecedor", origins: [], cte: { access_key: "chave", sender_cnpj: "123", purpose: 0, cargo_liters: 15000 } }} />);

  expect(screen.queryByText(/Volume auxiliar da carga/i)).not.toBeInTheDocument();
  expect(screen.getByText("Finalidade")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:ui -- freight-origin.test.jsx`

Expected: FAIL because `FreightCteFacts` is not exported yet.

- [ ] **Step 3: Write minimal implementation**

```jsx
export function FreightCteFacts({ detail }) {
  return <><div><span>Competência da NF-e</span><strong>{d(detail.reference_date)}</strong><small>Data exata quando localizada; mês da chave nas pendências</small></div><div><span>Emissão registrada no ERP</span><strong>{d(detail.issue_date)}</strong><small>Data original do CT-e</small></div><div><span>Chave do CT-e</span><strong>{detail.cte.access_key || "Não informada"}</strong></div><FreightOriginFacts senderName={detail.sender_name} senderCnpj={detail.cte.sender_cnpj} origins={detail.origins || []} /><div><span>Finalidade</span><strong>{detail.cte.purpose === 0 ? "Normal" : `Código ${detail.cte.purpose}`}</strong></div></>;
}

<FreightCteFacts detail={detail} />
```

Do not render `detail.cte.cargo_liters` or the copy `Não substitui a NF-e`.

- [ ] **Step 4: Run tests and build**

Run: `npm run test:ui -- freight-origin.test.jsx; npm test; npm run build`

Expected: the focused test, full frontend suite, and Vite build pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/freight-origin.test.jsx docs/superpowers/plans/2026-08-06-ocultar-volume-auxiliar-cte.md
git commit -m "fix(freight): hide auxiliary cargo volume"
```

## Self-review

- Spec coverage: the task removes only the visible field and preserves all technical sources and calculations.
- Placeholder scan: no incomplete requirement or deferred step exists.
- Type consistency: no interface or API type changes are introduced.
