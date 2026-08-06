# Cidade no detalhe de CT-e Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exibir cidade/UF cadastrais da origem das NF-es conciliadas no bloco CT-e e cobrança.

**Architecture:** A API já devolve `detail.origins` com `cnpj`, `city` e `state` apenas para NF-es resolvidas. O componente React usará essa coleção diretamente no card de origem, mantendo o fornecedor do CT-e e sem afirmar que a cidade é o local físico de carregamento.

**Tech Stack:** React 19, Vitest, Testing Library, Vite.

## Global Constraints

- Reutilizar exclusivamente `detail.origins`; não consultar ou escrever no ERP.
- Rotular a informação como `Origem cadastral`.
- Não inventar cidade quando ela não estiver cadastrada.
- Manter compatibilidade com CT-es sem NF-e resolvida ou sem cidade cadastrada.

---

### Task 1: Exibir a origem cadastral na seção CT-e e cobrança

**Files:**
- Modify: `frontend/src/freight-origin.test.jsx`
- Modify: `frontend/src/App.jsx:1039-1043,1124-1131`

**Interfaces:**
- Consumes: `detail.origins: Array<{cnpj: string, legal_name: string | null, city: string | null, state: string | null}>`.
- Produces: `FreightOriginFacts({ senderName, senderCnpj, origins })`, o card de origem com `Origem cadastral: Cidade/UF` ou fallback explícito.

- [ ] **Step 1: Write the failing test**

```jsx
it("shows each resolved origin city in the CT-e billing summary", () => {
  render(<FreightOriginFacts senderName="IPIRANGA PRODUTOS DE PETROLEO" senderCnpj="33337122015906" origins={[{ cnpj: "33337122015906", city: "Canoas", state: "RS" }]} />);

  expect(screen.getByText("Origem cadastral: Canoas/RS")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:ui -- freight-origin.test.jsx`

Expected: FAIL because `FreightOriginFacts` is not exported yet.

- [ ] **Step 3: Write minimal implementation**

```jsx
export function FreightOriginFacts({ senderName, senderCnpj, origins = [] }) {
  return <div><span>Origem</span><strong>{senderName || "Não informada"}</strong><small>{senderCnpj || "—"}</small>{origins.map((origin) => <small key={origin.cnpj}>{origin.cnpj} • <FreightOriginSummary origin={origin} /></small>)}</div>;
}

<FreightOriginFacts senderName={detail.sender_name} senderCnpj={detail.cte.sender_cnpj} origins={detail.origins || []} />
```

- [ ] **Step 4: Run tests and build**

Run: `npm run test:ui -- freight-origin.test.jsx; npm run build`

Expected: test passes and Vite emits `dist` successfully.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/freight-origin.test.jsx docs/superpowers/specs/2026-08-06-cidade-no-detalhe-cte-design.md docs/superpowers/plans/2026-08-06-cidade-no-detalhe-cte.md
git commit -m "feat(freight): show registered origin city in CT-e detail"
```

## Self-review

- Spec coverage: the single task covers city/UF in CT-e and cobrança, multiple origins, and missing-city fallback.
- Placeholder scan: no TBD, TODO, or unspecified test steps remain.
- Type consistency: the UI consumes the existing `detail.origins` object shape returned by `_detail_payload`.
