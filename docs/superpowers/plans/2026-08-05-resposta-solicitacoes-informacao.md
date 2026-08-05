# Resposta a Solicitações de Informação — Plano de Implementação

> **Para agentes:** execute com testes antes do código e preserve o diretório de trabalho atual, pois ele contém alterações de produção ainda não versionadas.

**Objetivo:** permitir que qualquer administrador responda e encerre uma solicitação interna sem encerrar ou alterar a pendência financeira da conciliação.

**Arquitetura:** criar uma entidade própria para a solicitação, vinculada ao item conciliável, com autor, motivo, resposta, responsáveis e datas. O detalhe da conciliação retornará solicitações abertas e encerradas; o frontend recarregará o detalhe depois de cada gravação para evitar estado visual desatualizado.

**Tecnologias:** FastAPI, SQLAlchemy/Alembic, React/Vite e testes Pytest/node:test.

## Restrições globais

- Não escrever no ERP; persistir somente no banco da aplicação.
- Apenas administradores podem abrir, responder ou encerrar solicitações.
- Encerrar uma solicitação não confirma o vínculo, não ajusta valores e não resolve a exceção financeira.
- Preservar solicitações já abertas antes desta mudança.

---

### Tarefa 1: Persistência e API auditável

**Arquivos:**

- Modificar: `backend/app/models.py`
- Criar: `backend/migrations/versions/0012_reconciliation_information_requests.py`
- Modificar: `backend/app/api/reconciliations.py`
- Modificar: `backend/app/services/reconciliation_detail.py`
- Testar: `backend/tests/test_api.py`

- [ ] Escrever teste que abre uma solicitação, retorna-a no detalhe, responde e encerra-a sem zerar `difference_value` nem resolver a exceção financeira.
- [ ] Executar o teste e confirmar que falha porque a API/campo ainda não existe.
- [ ] Criar tabela `reconciliation_information_requests` e migração Alembic, com dados de abertura, resposta e encerramento.
- [ ] Criar endpoint administrativo `POST /reconciliations/information-requests/{id}/respond` com justificativa obrigatória.
- [ ] Incluir dados de solicitações no detalhe e compatibilidade de leitura para pedidos legados armazenados em `review_notes`.
- [ ] Executar o teste novamente e confirmar a passagem.

### Tarefa 2: Atualização imediata e interface de resposta

**Arquivos:**

- Modificar: `frontend/src/App.jsx`
- Modificar: `frontend/src/reconciliation-actions.js`
- Modificar: `frontend/src/reconciliation-actions.test.js`
- Modificar: `frontend/src/styles.css`

- [ ] Escrever teste da normalização de solicitação aberta/encerrada recebida pelo detalhe.
- [ ] Executar o teste e confirmar que falha antes da nova estrutura.
- [ ] Após qualquer ação por item, recarregar `GET /reconciliations/{id}/detail` antes de atualizar a fila.
- [ ] Exibir pedido, motivo, solicitante, resposta, respondente e data; administradores recebem formulário “Responder e encerrar”.
- [ ] Executar testes frontend e build Vite.

### Tarefa 3: Verificação e publicação

**Arquivos:**

- Validar as alterações dos arquivos acima sem reverter mudanças preexistentes.

- [ ] Rodar testes backend completos, lint e build frontend.
- [ ] Aplicar migração pelo deploy de produção e verificar `/api/health`.
- [ ] Verificar no navegador: novo pedido aparece imediatamente, resposta encerra apenas a solicitação e a pendência financeira permanece visível.
