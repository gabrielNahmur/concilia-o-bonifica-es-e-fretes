# Correções Funcionais da Auditoria Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir as falhas funcionais confirmadas na auditoria sem incluir endurecimento de segurança ou deploy em produção.

**Architecture:** A correção será incremental e orientada a testes. Validações de domínio ficarão nas entradas administrativas e serviços de seleção; consultas reutilizarão um único conjunto de filtros; a interface ganhará estados de erro explícitos, painéis de Conciliações integrados e componentes de drawer/layout reutilizáveis somente onde o defeito exige.

**Tech Stack:** Python 3.12, FastAPI 0.140, Pydantic 2, SQLAlchemy 2, pytest, React 19, React Router 7, Vite 8, Vitest 4, Testing Library 16, jsdom 26 e CSS responsivo.

## Global Constraints

- Não adicionar limitação de tentativas no login.
- Não alterar troca obrigatória de senha, cookies, tokens, sessão ou OpenAPI.
- Não corrigir dados históricos automaticamente.
- Não executar sincronização, backfill, upload ou envio de e-mail em produção.
- Não implantar em produção sem autorização separada.
- Preservar todas as alterações locais preexistentes; não reverter nem substituir arquivos em bloco.
- O checkout contém diffs anteriores nos mesmos arquivos. Não criar commits de implementação que incluam alterações cuja autoria não possa ser isolada; usar checkpoints de teste e deixar a integração Git para o handoff final.
- Cada mudança de produção deve ser precedida por um teste que falhe pelo motivo funcional esperado.

---

### Task 1: Integridade de contratos e regras de bonificação

**Files:**
- Create: `backend/tests/test_admin_functional_integrity.py`
- Modify: `backend/app/api/admin.py:45-120`
- Modify: `backend/app/api/admin.py:291-370`

**Interfaces:**
- Consumes: `ContractInput`, `RuleInput`, `DbSession`, `AdminUser`, modelos `Contract` e `BonusRule`.
- Produces: `_ensure_contract_does_not_overlap(db, values, ignore_id=None) -> None`; domínio fechado para `status`, `kind` e `applies_to`.

- [ ] **Step 1: Criar fixture HTTP administrativa isolada no arquivo de teste**

O novo arquivo criará schema, seed, unidade `990`, companhia `QA` e administrador próprios antes de abrir o `TestClient`:

```python
@pytest.fixture
def admin_client():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        if not db.get(Company, "QA"):
            db.add(Company(code="QA", display_name="Companhia QA"))
        if not db.get(Unit, "990"):
            db.add(Unit(code="990", display_name="Unidade QA", state="RS", active=True))
        user = db.scalar(select(User).where(User.email == "functional.qa@gbi.com"))
        if not user:
            db.add(User(
                email="functional.qa@gbi.com", full_name="QA Funcional", role="admin",
                active=True, must_change_password=False, password_hash=hash_password(PASSWORD),
            ))
        db.commit()
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={
            "email": "functional.qa@gbi.com", "password": PASSWORD,
        }).status_code == 200
        yield client
```

- [ ] **Step 2: Escrever testes HTTP de contratos sobrepostos**

Adicionar casos independentes que criem um contrato ativo e confirmem:

```python
def test_create_contract_rejects_overlapping_active_period(admin_client):
    first = admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 12))
    assert first.status_code == 200
    conflict = admin_client.post("/api/admin/contracts", json=contract_payload("2026-06-01", 12))
    assert conflict.status_code == 409
    assert "sobrepõe" in conflict.json()["detail"]


def test_create_contract_allows_period_after_existing_contract(admin_client):
    assert admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 6)).status_code == 200
    assert admin_client.post("/api/admin/contracts", json=contract_payload("2026-07-01", 6)).status_code == 200


def test_inactive_contract_does_not_block_new_active_contract(admin_client):
    inactive = contract_payload("2026-01-01", 12) | {"status": "inactive"}
    assert admin_client.post("/api/admin/contracts", json=inactive).status_code == 200
    assert admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 12)).status_code == 200
```

Incluir edição que ignore o próprio ID e rejeite sobreposição com outro contrato.

- [ ] **Step 3: Executar os testes de contrato e confirmar RED**

Run from `backend`:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests\test_admin_functional_integrity.py -k contract
```

Expected: os casos de conflito falham porque POST/PATCH retornam 200.

- [ ] **Step 4: Implementar validação mínima de sobreposição**

Em `admin.py`, tipar o status e consultar intervalos inclusivos:

```python
ContractStatus = Literal["active", "inactive"]


def _ensure_contract_does_not_overlap(db: Session, values: dict, ignore_id: int | None = None) -> None:
    if values["status"] != "active":
        return
    statement = select(Contract).where(
        Contract.unit_code == values["unit_code"],
        Contract.company_code == values["company_code"],
        Contract.status == "active",
        Contract.start_date <= values["end_date"],
        Contract.end_date >= values["start_date"],
    )
    if ignore_id is not None:
        statement = statement.where(Contract.id != ignore_id)
    conflict = db.scalar(statement.limit(1))
    if conflict:
        raise HTTPException(status_code=409, detail=(
            f"O contrato sobrepõe a vigência {conflict.start_date:%d/%m/%Y} a "
            f"{conflict.end_date:%d/%m/%Y} da unidade {conflict.unit_code}."
        ))
```

Calcular `end_date` antes da validação e chamar a função em POST e PATCH.

- [ ] **Step 5: Executar testes de contrato e confirmar GREEN**

Run: `..\.venv\Scripts\python.exe -m pytest -q tests\test_admin_functional_integrity.py -k contract`

Expected: todos os casos de contrato passam.

- [ ] **Step 6: Escrever testes RED para o domínio das regras**

Adicionar tabela de payloads inválidos com expectativas literais:

```python
@pytest.mark.parametrize("change", [
    {"kind": "unknown"},
    {"applies_to": "qualquer_coisa"},
    {"kind": "milestone_bonus", "milestone_liters": None, "milestone_amount": 35000, "period_months": 4},
    {"kind": "s10_excess_credit", "rate_per_liter": 0.10, "threshold_liters": None, "applies_to": "s10"},
    {"kind": "invoice_discount", "rate_per_liter": 0},
    {"due_month_offset": -1},
])
def test_rule_rejects_invalid_domain(admin_client, change):
    response = admin_client.post("/api/admin/rules", json=valid_rule_payload() | change)
    assert response.status_code == 422
```

Adicionar um caso válido para cada um dos cinco tipos aceitos.

- [ ] **Step 7: Executar testes de regra e confirmar RED**

Run: `..\.venv\Scripts\python.exe -m pytest -q tests\test_admin_functional_integrity.py -k rule`

Expected: tipos/aplicações desconhecidos e combinações incompletas ainda são aceitos.

- [ ] **Step 8: Implementar validação discriminada mínima**

Usar `Literal` e um único `model_validator`:

```python
RuleKind = Literal[
    "distributor_credit", "milestone_bonus", "invoice_discount",
    "s10_excess_credit", "bank_deposit",
]


def _valid_applies_to(value: str) -> bool:
    if value in {"all_fuel", "s10"}:
        return True
    if not value.startswith("fuel_codes:"):
        return False
    codes = [item.strip() for item in value.split(":", 1)[1].split(",")]
    return bool(codes) and all(item.isdigit() for item in codes)
```

No validator, exigir os campos descritos na especificação para cada tipo e rejeitar valores negativos.

- [ ] **Step 9: Executar testes de integridade administrativa**

Run:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests\test_admin_functional_integrity.py tests\test_admin_edge_cases.py
..\.venv\Scripts\python.exe -m ruff check app\api\admin.py tests\test_admin_functional_integrity.py
```

Expected: testes e Ruff passam.

- [ ] **Step 10: Registrar checkpoint**

Run: `git diff -- backend/app/api/admin.py backend/tests/test_admin_functional_integrity.py`

Confirmar que o diff contém apenas validação de contratos/regras e seus testes; não commitar o arquivo sobreposto automaticamente.

---

### Task 2: Alias global com precedência específica

**Files:**
- Create: `backend/tests/test_supplier_alias_mapping.py`
- Modify: `backend/app/services/seed.py:294-310`

**Interfaces:**
- Consumes: `map_supplier(db, unit_code, cnpj, legal_name, purchase_date)`.
- Produces: seleção que considera `unit_code == atual OR unit_code IS NULL` e prioriza a unidade.

- [ ] **Step 1: Criar fixture de sessão real no arquivo de teste**

```python
@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        if not session.get(Company, "QAGLOBAL"):
            session.add(Company(code="QAGLOBAL", display_name="Global QA"))
        if not session.get(Company, "QALOCAL"):
            session.add(Company(code="QALOCAL", display_name="Local QA"))
        if not session.get(Unit, "991"):
            session.add(Unit(code="991", display_name="Unidade Alias QA", state="RS", active=True))
        session.commit()
        yield session
    finally:
        session.close()
```

- [ ] **Step 2: Escrever testes de comportamento real**

```python
def test_map_supplier_uses_global_alias_when_unit_has_no_specific_alias(db):
    db.add(SupplierAlias(company_code="QAGLOBAL", unit_code=None, cnpj="55111111000101", effective_from=date(2026, 1, 1), active=True))
    db.commit()
    assert map_supplier(db, "991", "55.111.111/0001-01", None, date(2026, 8, 1)) == "QAGLOBAL"


def test_map_supplier_prefers_unit_alias_over_global_alias(db):
    db.add_all([
        SupplierAlias(company_code="QAGLOBAL", unit_code=None, cnpj="55222222000102", effective_from=date(2026, 1, 1), active=True),
        SupplierAlias(company_code="QALOCAL", unit_code="991", cnpj="55222222000102", effective_from=date(2026, 1, 1), active=True),
    ])
    db.commit()
    assert map_supplier(db, "991", "55.222.222/0001-02", None, date(2026, 8, 1)) == "QALOCAL"
```

- [ ] **Step 3: Executar e confirmar RED**

Run: `..\.venv\Scripts\python.exe -m pytest -q tests\test_supplier_alias_mapping.py`

Expected: alias global retorna `None` e/ou ganha precedência incorreta.

- [ ] **Step 4: Implementar consulta ordenada**

```python
aliases = db.scalars(
    select(SupplierAlias)
    .where(
        SupplierAlias.active.is_(True),
        or_(SupplierAlias.unit_code == unit_code, SupplierAlias.unit_code.is_(None)),
    )
    .order_by(
        case((SupplierAlias.unit_code == unit_code, 0), else_=1),
        SupplierAlias.effective_from.desc(),
        SupplierAlias.id.desc(),
    )
).all()
```

- [ ] **Step 5: Confirmar GREEN e regressões de seed**

Run:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests\test_supplier_alias_mapping.py tests\test_purchases.py tests\test_api.py
..\.venv\Scripts\python.exe -m ruff check app\services\seed.py tests\test_supplier_alias_mapping.py
```

- [ ] **Step 6: Registrar checkpoint**

Inspecionar o diff de `seed.py` e do novo teste; não alterar os aliases de seed existentes.

---

### Task 3: Resumos filtrados e ordem de severidade

**Files:**
- Create: `backend/tests/test_reconciliation_filters.py`
- Modify: `backend/app/api/reconciliations.py:214-270`
- Modify: `backend/app/api/reconciliations.py:721-810`

**Interfaces:**
- Consumes: filtros `unit`, `status`, `reference_month`, `rule_kind`, `severity` e `exception_type`.
- Produces: `_filtered_open_exception_count(db, filters, rule_kind) -> int`; `SEVERITY_ORDER` usado na consulta de exceções.

- [ ] **Step 1: Criar cliente autenticado e dados exclusivos no arquivo de teste**

Usar `Base.metadata.create_all`, `SessionLocal` e `TestClient(app)` como no teste administrativo, com usuário `reconciliation.filters.qa@gbi.com`, unidades `992` e `993` e regras exclusivas. A fixture deve limpar apenas reconciliações dessas duas unidades no início, inserir os dados do caso, autenticar e entregar o cliente real; nenhuma chamada ao ERP será mockada.

- [ ] **Step 2: Escrever teste RED do resumo filtrado**

Criar reconciliações para 992 e 993, exceção aberta somente na 993 e chamar:

```python
response = client.get("/api/reconciliations", params={"unit": "992"})
assert response.status_code == 200
assert response.json()["summary"]["open_exceptions"] == 0
```

Repetir um caso por competência e tipo de regra para provar que o agregado reutiliza o recorte.

- [ ] **Step 3: Escrever teste RED da prioridade**

Persistir `low`, `medium`, `high` e `critical` em ordem reversa e afirmar:

```python
payload = client.get("/api/reconciliations/exceptions").json()
assert [item["severity"] for item in payload["items"][:4]] == ["critical", "high", "medium", "low"]
```

- [ ] **Step 4: Executar e confirmar RED**

Run: `..\.venv\Scripts\python.exe -m pytest -q tests\test_reconciliation_filters.py`

Expected: resumo retorna a exceção global e ordem é textual.

- [ ] **Step 5: Implementar o agregado com os mesmos joins/filtros**

Construir a consulta de contagem a partir de `ReconciliationException JOIN Reconciliation`, aplicando os predicados equivalentes da lista e join em `BonusRule` somente quando `rule_kind` estiver presente.

- [ ] **Step 6: Implementar ordem explícita com SQLAlchemy `case`**

```python
severity_rank = case(
    (ReconciliationException.severity == "critical", 0),
    (ReconciliationException.severity == "high", 1),
    (ReconciliationException.severity == "medium", 2),
    (ReconciliationException.severity == "low", 3),
    else_=4,
)
```

Ordenar por `severity_rank`, vencimento, unidade e criação.

- [ ] **Step 7: Confirmar GREEN e regressões da API**

Run:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests\test_reconciliation_filters.py tests\test_reconciliation_queue.py tests\test_api.py
..\.venv\Scripts\python.exe -m ruff check app\api\reconciliations.py tests\test_reconciliation_filters.py
```

- [ ] **Step 8: Registrar checkpoint**

Inspecionar o diff e confirmar que paginação e payloads não mudaram.

---

### Task 4: Infraestrutura frontend e estados de erro recuperáveis

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/vitest.config.js`
- Create: `frontend/src/test/setup.js`
- Create: `frontend/src/loading-pages.test.jsx`
- Modify: `frontend/src/App.jsx:381-950`

**Interfaces:**
- Consumes: funções `get` e `post` de `frontend/src/api.js`.
- Produces: script `npm test`; estados `loading`, `error`, `data`; botões `Tentar novamente`.

- [ ] **Step 1: Instalar dependências de teste compatíveis com Node 20**

Run from `frontend`:

```powershell
npm install --save-dev vitest@4.1.10 jsdom@26.1.0 @testing-library/react@16.3.2 @testing-library/dom@10.4.1 @testing-library/user-event@14.6.3 @testing-library/jest-dom@6.9.1
```

Adicionar scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 2: Configurar Vitest/jsdom**

```js
// vitest.config.js
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.js"], clearMocks: true },
});
```

O setup importará `@testing-library/jest-dom/vitest`, executará `cleanup()` após cada teste e restaurará mocks:

```js
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
```

- [ ] **Step 3: Escrever testes RED para Contracts e UnitDetail**

Mockar apenas o limite HTTP e renderizar os componentes reais exportados de `App.jsx`:

```jsx
it("shows the contracts load error and retries", async () => {
  get.mockRejectedValueOnce(new Error("ERP indisponível")).mockResolvedValueOnce([]);
  render(<MemoryRouter><Contracts /></MemoryRouter>);
  expect(await screen.findByText("ERP indisponível")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
});
```

Criar caso equivalente para `UnitDetail` usando rota `/unidades/054`.

- [ ] **Step 4: Escrever testes RED para Compras e bônus**

- Compras: a entrada inicial realiza uma única chamada `/purchases?...page=1`.
- Compras: erro de listagem mostra mensagem e retry.
- Drawer de bônus: erro mostra mensagem, não spinner, e retry executa nova carga.

- [ ] **Step 5: Executar e confirmar RED**

Run: `npm test -- src/loading-pages.test.jsx`

Expected: não há estados de erro/retry e Compras chama página 1 duas vezes.

- [ ] **Step 6: Implementar estados explícitos e uma única carga de Compras**

Para cada página, usar uma função `load` com `setLoading(true)`, `setError("")`, `try/catch/finally`. Em Compras, substituir os dois efeitos de carga por um efeito único dependente de `page` e filtros; ao mudar filtro, ajustar página para 1 sem disparar uma carga paralela obsoleta.

O drawer de bônus receberá `error` e `onRetry` separados de `data`.

- [ ] **Step 7: Confirmar GREEN**

Run:

```powershell
npm test -- src/loading-pages.test.jsx
npm run build
```

- [ ] **Step 8: Registrar checkpoint**

Inspecionar `package.json`, lockfile, configuração, teste e somente as regiões afetadas de `App.jsx`.

---

### Task 5: Integrar Exceções, Competências e Cobertura em Conciliações

**Files:**
- Create: `frontend/src/reconciliation-tabs.test.jsx`
- Modify: `frontend/src/App.jsx:2070-2990`
- Modify: `frontend/src/styles.css` nas regras de navegação de conciliação

**Interfaces:**
- Consumes: `LegacyExceptions`, `LegacyCoverage` e listagem legada atualmente dentro de `LegacyReconciliations`.
- Produces: `ReconciliationTabs`; painéis `QueuePanel`, `ExceptionsPanel`, `CompetencesPanel`, `CoveragePanel` usados por `/conciliacoes`.

- [ ] **Step 1: Escrever teste RED da navegação única**

Renderizar `Reconciliations` e afirmar as quatro abas:

```jsx
expect(screen.getByRole("button", { name: "Fila" })).toHaveAttribute("aria-pressed", "true");
await user.click(screen.getByRole("button", { name: "Exceções" }));
expect(await screen.findByRole("heading", { name: "Central de exceções" })).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "Competências" }));
expect(await screen.findByRole("heading", { name: "Competências conciliadas" })).toBeInTheDocument();
await user.click(screen.getByRole("button", { name: "Cobertura automática" }));
expect(await screen.findByRole("heading", { name: "Cobertura automática" })).toBeInTheDocument();
```

Adicionar teste em que a API de Exceções falha e Fila continua utilizável.

- [ ] **Step 2: Executar e confirmar RED**

Run: `npm test -- src/reconciliation-tabs.test.jsx`

Expected: somente a fila atual está acessível.

- [ ] **Step 3: Extrair painéis sem criar nova rota**

Mover apenas o corpo útil de `LegacyReconciliations` para componentes internos. Manter `Reconciliations` como dona de `activeTab="queue"` e renderizar um painel por vez. Não duplicar `PageHeader`.

- [ ] **Step 4: Implementar navegação e estados independentes**

Usar botões com estado visual, carregar cada painel ao abrir e manter erro/retry local ao painel.

- [ ] **Step 5: Confirmar GREEN e build**

Run:

```powershell
npm test -- src/reconciliation-tabs.test.jsx
npm run build
```

- [ ] **Step 6: Registrar checkpoint**

Confirmar no diff que `/conciliacoes` continua a única rota e que `LegacyReconciliations` não permanece como componente órfão completo.

---

### Task 6: Administração isolada por aba e CRUD completo

**Files:**
- Create: `frontend/src/administration.test.jsx`
- Modify: `frontend/src/App.jsx:3300-4510`
- Modify: `frontend/src/api.js` somente se faltar helper HTTP já exposto
- Modify: `frontend/src/styles.css` para ações/formulários administrativos

**Interfaces:**
- Consumes: endpoints `/admin/users`, `/admin/recipients`, `/admin/contracts`, `/admin/rules`, `/admin/aliases`, `/admin/freight-rates`, `/units`, `/portal-statements/*` e `/admin/sync`.
- Produces: `loadAdminResource(key)`, cache `resourceState[key] = {data, loading, error, loaded}`; criar/editar/inativar por recurso.

- [ ] **Step 1: Escrever teste RED de isolamento**

Simular falha no Portal e sucesso em Usuários. Abrir Administração e afirmar que Usuários aparece; clicar Portal e afirmar erro com retry; voltar a Usuários e confirmar que os dados permanecem.

- [ ] **Step 2: Escrever testes RED dos ciclos CRUD**

Para cada recurso, testar comportamento observável:

- Usuários: botão Editar preenche formulário; salvar chama PATCH; Inativar confirma e chama DELETE.
- Destinatários: editar chama PATCH; remover chama DELETE.
- Contratos, Bonificações e Fornecedores: editar preenche valores; salvar chama PATCH; inativar chama DELETE.
- Após sucesso, somente o endpoint GET do recurso afetado é recarregado.
- Erro 409/422 permanece visível junto ao formulário e não fecha a edição.

- [ ] **Step 3: Executar e confirmar RED**

Run: `npm test -- src/administration.test.jsx`

Expected: `Promise.all` bloqueia tudo e faltam controles de edição/inativação.

- [ ] **Step 4: Substituir carga global por estado por recurso**

Implementar mapa de loaders:

```js
const adminLoaders = {
  users: () => get("/admin/users"),
  recipients: () => get("/admin/recipients"),
  contracts: () => get("/admin/contracts"),
  rules: () => get("/admin/rules"),
  aliases: () => get("/admin/aliases"),
  units: () => get("/units"),
  freightRates: () => Promise.all([get("/admin/freight-rates"), get("/admin/freight-carriers")]),
  portal: () => get("/portal-statements/ipiranga/001"),
  portalTexaco: () => get("/portal-statements/texaco/050"),
  sync: () => get("/admin/sync"),
};
```

Carregar o recurso ativo sob demanda e expor retry por chave.

- [ ] **Step 5: Implementar edição/inativação usando formulários existentes**

Reutilizar `configSets` para contratos/regras/aliases, mantendo `editing` com ID. No submit, usar POST quando não houver ID e PATCH quando houver. Adicionar confirmação antes de DELETE e recarregar a chave correspondente.

- [ ] **Step 6: Confirmar GREEN**

Run:

```powershell
npm test -- src/administration.test.jsx
npm run build
```

- [ ] **Step 7: Registrar checkpoint**

Inspecionar diffs por região e confirmar que Portal e Sincronização não são acionados ao abrir Usuários.

---

### Task 7: Drawers e layout mobile sem overflow

**Files:**
- Create: `frontend/src/drawer.test.jsx`
- Modify: `frontend/src/App.jsx` nos drawers existentes
- Modify: `frontend/src/styles.css:1705-1765`
- Modify: `frontend/src/styles.css:2934-3030`
- Modify: `frontend/src/styles.css` nas regras mobile de tarifas

**Interfaces:**
- Consumes: `onClose` de cada drawer.
- Produces: `usePageScrollLock(isOpen)` ou componente `DrawerShell`; CSS com `100dvh`, corpo rolável e largura limitada.

- [ ] **Step 1: Escrever teste RED de bloqueio de rolagem**

```jsx
it("locks and restores page scrolling while a drawer is open", () => {
  const { unmount } = render(<PurchaseDetail entryId="1" onClose={() => {}} />);
  expect(document.body.style.overflow).toBe("hidden");
  unmount();
  expect(document.body.style.overflow).toBe("");
});
```

Adicionar teste de fechamento que restaura o valor anterior de `overflow`.

- [ ] **Step 2: Executar e confirmar RED**

Run: `npm test -- src/drawer.test.jsx`

Expected: `body.style.overflow` não muda.

- [ ] **Step 3: Implementar lifecycle mínimo compartilhado**

```js
function usePageScrollLock() {
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, []);
}
```

Aplicar uma vez por drawer aberto e preservar os atributos atuais.

- [ ] **Step 4: Corrigir CSS de drawers**

Usar overlay `height: 100dvh; overflow: hidden`, drawer `max-width: 100%; min-width: 0; height: 100dvh` e `.drawer-body { min-height: 0; overflow-y: auto; }`.

- [ ] **Step 5: Corrigir tarifas e Portal no mobile**

- Ações de tarifa: `min-width: 0`, quebra/coluna mobile e botões sempre dentro do card.
- `.portal-event-badges` e `.badge`: `max-width: 100%`, `min-width: 0`, `white-space: normal`, `overflow-wrap: anywhere`.
- Cards/células mobile: largura máxima de 100%, sem largura intrínseca herdada da tabela.

- [ ] **Step 6: Confirmar testes e build**

Run:

```powershell
npm test -- src/drawer.test.jsx
npm run build
```

- [ ] **Step 7: Validar visualmente em navegador local**

Executar Vite/API local e medir `scrollWidth === clientWidth` em `/tarifas-frete` e nas abas Portal Ipiranga/Texaco da Administração para 390×844 e 360×800. Abrir drawers de compra, conciliação e frete e confirmar uma única área rolável.

- [ ] **Step 8: Registrar checkpoint**

Salvar no relatório de execução as quatro medidas de viewport e os drawers validados.

---

### Task 8: Verificação integrada e atualização da auditoria

**Files:**
- Modify: `docs/qa/2026-08-05-matriz-funcional.md`
- Modify: `docs/qa/2026-08-05-relatorio-auditoria-funcional.md`

**Interfaces:**
- Consumes: resultados das Tasks 1–7.
- Produces: estado final rastreável dos achados funcionais corrigidos e das exclusões de segurança mantidas.

- [ ] **Step 1: Executar suíte completa backend**

Run from `backend`:

```powershell
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m pytest -q
```

Expected: zero erros e todos os testes aprovados.

- [ ] **Step 2: Executar suíte completa frontend**

Run from `frontend`:

```powershell
npm test
node --test src\reconciliation-actions.test.js
npm run build
npm run audit:prod
```

Expected: zero falhas, build e auditoria aprovados.

- [ ] **Step 3: Executar smoke funcional local**

Validar Dashboard, Contratos, unidade 054, Compras, as quatro abas de Conciliações, Rotina mensal, Fretes, Tarifas, Relatórios e as nove abas administrativas sem gravar em produção.

- [ ] **Step 4: Atualizar matriz e relatório**

Para cada achado no escopo, registrar teste de regressão, arquivo corrigido e status `Corrigida localmente`. Manter itens de segurança como `Fora do escopo por decisão do usuário`, sem descrevê-los como corrigidos.

- [ ] **Step 5: Auditar requisitos**

Comparar os critérios de aceite da especificação com resultados concretos. Não declarar correção de produção; a conclusão válida é somente do workspace/branch local.

- [ ] **Step 6: Preparar handoff Git**

Executar `git status --short`, separar arquivos tocados pela implementação de alterações preexistentes e apresentar ao usuário opções de integração sem commitar mudanças cuja origem não possa ser isolada.

## Self-Review

- Cobertura da especificação: Tasks 1–3 cobrem integridade e consultas; Tasks 4–6 cobrem resiliência, Conciliações e CRUD; Task 7 cobre mobile/drawers; Task 8 cobre validação e documentação.
- Escopo: autenticação, rate limiting, sessão, OpenAPI, migração de dados e produção aparecem apenas como exclusões.
- Tipos e interfaces: `RuleKind`, `ContractStatus`, `_ensure_contract_does_not_overlap`, `loadAdminResource` e `usePageScrollLock` têm uma definição única e consumidores explícitos.
- TDD: cada mudança de produção possui teste RED, implementação mínima e comando GREEN antes do checkpoint.
- Git: o plano reconhece os diffs preexistentes e proíbe commits automáticos que misturem autoria.
