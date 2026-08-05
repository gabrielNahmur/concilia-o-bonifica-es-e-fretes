# Auditoria Funcional Completa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inventariar e validar todas as funcionalidades expostas pelo sistema Contratos GBI, com cobertura rastreável entre interface, API, regras de negócio e testes.

**Architecture:** A auditoria usa quatro camadas de evidência: inspeção estática das rotas e handlers, testes automatizados em banco local isolado, navegação exploratória em produção sem alterar dados e revalidação independente dos achados. O resultado final é uma matriz por funcionalidade com status `Aprovada`, `Falhou`, `Parcialmente testada` ou `Não testável com segurança`, sempre acompanhada da evidência observada.

**Tech Stack:** React 19, React Router 7, Vite 8, Node test runner, FastAPI, SQLAlchemy, pytest, Ruff, Docker Compose e navegador Chrome/in-app.

## Global Constraints

- Não alterar nem excluir dados de produção durante a auditoria.
- Operações de escrita devem ser exercitadas pelos testes locais com banco isolado; em produção, apenas abrir e validar formulários sem submetê-los.
- Não acessar nem exibir o conteúdo de `.env`, cookies, credenciais ou dados sensíveis.
- Preservar todas as alterações já existentes no worktree.
- Não implementar correções nesta fase; cada defeito deve ser reproduzido e documentado antes de qualquer autorização de correção.
- O login permanece sem limitação de tentativas, conforme restrição explícita do usuário.

---

### Task 1: Inventário rastreável da plataforma

**Files:**
- Inspect: `frontend/src/App.jsx`
- Inspect: `frontend/src/api.js`
- Inspect: `frontend/src/ui.jsx`
- Inspect: `backend/app/main.py`
- Inspect: `backend/app/api/*.py`
- Create: `docs/qa/2026-08-05-matriz-funcional.md`

**Interfaces:**
- Consumes: rotas React, chamadas do cliente HTTP e decorators FastAPI.
- Produces: identificadores estáveis `QA-<MODULO>-NNN` usados em todos os resultados posteriores.

- [ ] **Step 1: Listar as nove rotas navegáveis**

Registrar `/`, `/contratos`, `/unidades/:code`, `/compras`, `/conciliacoes`, `/rotina-mensal`, `/fretes`, `/tarifas-frete`, `/relatorios` e `/administracao`, incluindo redirecionamentos e restrições por perfil.

- [ ] **Step 2: Listar os endpoints por domínio**

Run: `rg -n "@(router|app)\\.(get|post|put|patch|delete)\\(" backend/app`

Expected: endpoints de infraestrutura, autenticação, dashboard, unidades, compras, conciliações, rotina mensal, fretes, extratos de portal, relatórios e administração.

- [ ] **Step 3: Relacionar cada controle da interface à chamada HTTP**

Run: `rg -n "\\b(get|post|patch|del|postFile)\\(" frontend/src/App.jsx frontend/src/api.js`

Expected: cada botão, formulário, upload, download, filtro e exportação possui handler ou é registrado como lacuna.

### Task 2: Qualidade automatizada e dependências

**Files:**
- Inspect: `backend/tests/*.py`
- Inspect: `frontend/src/reconciliation-actions.test.js`
- Inspect: `.github/workflows/ci.yml`
- Inspect: `frontend/scripts/audit-prod.mjs`

**Interfaces:**
- Consumes: suíte atual e dependências bloqueadas no lockfile.
- Produces: resultados reproduzíveis de lint, testes, build e auditoria de dependências.

- [ ] **Step 1: Executar lint e suíte completa do backend**

Run from repository root: `.venv\\Scripts\\python.exe -m ruff check backend/app backend/tests`

Run from `backend`: `..\\.venv\\Scripts\\python.exe -m pytest -q`

Expected: ambos terminam com código `0`; qualquer falha é registrada com teste e traceback.

- [ ] **Step 2: Executar os testes unitários do frontend**

Run from `frontend`: `node --test src/*.test.js`

Expected: todos os testes terminam como `pass`.

- [ ] **Step 3: Compilar e auditar dependências de produção**

Run from `frontend`: `npm run build`

Run from `frontend`: `npm run audit:prod`

Expected: build Vite e auditoria terminam com código `0`; avisos de tamanho permanecem registrados como risco não funcional.

### Task 3: Validação dos fluxos de escrita em ambiente isolado

**Files:**
- Inspect: `backend/tests/conftest.py`
- Inspect: `backend/tests/test_api.py`
- Inspect: `backend/tests/test_admin_edge_cases.py`
- Inspect: `backend/tests/test_freights.py`
- Inspect: `backend/tests/test_monthly_routine.py`
- Inspect: `backend/tests/test_reconciliation_*.py`
- Inspect: `backend/tests/test_uploads.py`

**Interfaces:**
- Consumes: `TestClient`, banco temporário e fixtures autenticadas.
- Produces: prova de criar/editar/inativar usuários, destinatários, contratos, regras, aliases, unidades e tarifas; revisar fretes/conciliações; importar arquivos; gerar relatórios; trocar senha e sincronizar sem atingir ERP real.

- [ ] **Step 1: Mapear cada teste existente ao endpoint exercitado**

Classificar cada endpoint como `coberto`, `cobertura indireta` ou `sem teste`.

- [ ] **Step 2: Executar arquivos críticos individualmente**

Run from `backend`: `..\\.venv\\Scripts\\python.exe -m pytest -q tests/test_api.py tests/test_admin_edge_cases.py tests/test_freights.py tests/test_monthly_routine.py tests/test_purchases.py tests/test_reconciliation_detail.py tests/test_reconciliation_queue.py tests/test_reconciliation_workspace.py tests/test_reporting.py tests/test_uploads.py`

Expected: operações usam apenas o banco temporário configurado em `tests/conftest.py`.

- [ ] **Step 3: Registrar lacunas sem inventar cobertura**

Para cada endpoint sem teste direto, registrar método, URL, risco e cenário mínimo necessário.

### Task 4: Teste exploratório desktop em produção

**Files:**
- Read only: aplicação publicada em `https://contratos.atendimento-gbi.online/`
- Update: `docs/qa/2026-08-05-matriz-funcional.md`

**Interfaces:**
- Consumes: sessão autenticada existente e DOM visível.
- Produces: evidência por rota de carregamento, navegação, filtros, drawers, paginação, exportações e formulários.

- [ ] **Step 1: Validar navegação e autorização**

Abrir todas as rotas, confirmar título principal, ausência de fallback inesperado e visibilidade correta das áreas administrativas.

- [ ] **Step 2: Validar operações somente leitura**

Testar filtros, busca, paginação, ordenação, abertura/fechamento de detalhes, abas internas, downloads seguros e exportações que não alterem dados.

- [ ] **Step 3: Inspecionar formulários de escrita sem submissão**

Confirmar campos, rótulos, validações locais, botões e estados desabilitados para cadastro de tarifas, notas/ajustes, regras, usuários, destinatários e uploads; não enviar dados em produção.

- [ ] **Step 4: Verificar console e rede visível**

Registrar erros de console, respostas HTTP falhas e estados de carregamento sem tratamento.

### Task 5: Teste exploratório mobile e acessibilidade básica

**Files:**
- Inspect: `frontend/src/styles.css`
- Update: `docs/qa/2026-08-05-matriz-funcional.md`

**Interfaces:**
- Consumes: mesmas rotas da Task 4 nos viewports 390×844 e 360×800.
- Produces: resultado de menu, filtros, tabelas/cards, drawers, modais, formulários, overflow e foco/nomes acessíveis.

- [ ] **Step 1: Repetir navegação principal nos dois viewports**

Expected: nenhum overflow horizontal do documento e nenhum controle essencial fora do viewport.

- [ ] **Step 2: Abrir filtros, drawers e formulários**

Expected: componentes empurram ou sobrepõem conteúdo de forma intencional, sem colisões ou perda de botões.

- [ ] **Step 3: Validar nomes acessíveis e teclado**

Confirmar nome acessível dos botões principais, `aria-expanded` nos menus e fechamento por Escape nos componentes que oferecem esse contrato.

### Task 6: Consolidação e auditoria de completude

**Files:**
- Create: `docs/qa/2026-08-05-relatorio-auditoria-funcional.md`
- Update: `docs/qa/2026-08-05-matriz-funcional.md`

**Interfaces:**
- Consumes: resultados dos agentes, comandos locais e observações do navegador.
- Produces: inventário final, cobertura medida, falhas reproduzidas, riscos e recomendações priorizadas.

- [ ] **Step 1: Revalidar achados classificados como defeito**

Exigir segunda evidência independente: teste automatizado + código, ou reprodução no navegador + resposta/API.

- [ ] **Step 2: Auditar a matriz contra rotas e endpoints**

Nenhuma rota, endpoint ou ação identificada na Task 1 pode ficar sem estado e evidência.

- [ ] **Step 3: Publicar o relatório local**

O relatório deve separar claramente `Aprovada`, `Falhou`, `Parcialmente testada` e `Não testável com segurança`, sem afirmar cobertura total quando houver lacunas.

## Self-Review

- Cobertura da solicitação: inventário completo, testes automatizados, exploração desktop/mobile, permissões, CRUDs, uploads, downloads, exportações e relatório estão associados a tarefas específicas.
- Placeholder scan: o plano não contém `TBD`, `TODO` ou etapas vagas sem comando/evidência.
- Consistência: a matriz criada na Task 1 é o artefato atualizado nas Tasks 4 e 5 e auditado na Task 6.
