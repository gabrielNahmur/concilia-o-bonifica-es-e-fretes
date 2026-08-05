# Relatório de auditoria funcional — Contratos GBI

Data: 05/08/2026
Ambientes: workspace local isolado e produção em modo somente leitura
URL validada: `https://contratos.atendimento-gbi.online/`

## Atualização pós-implementação

As correções funcionais autorizadas foram aplicadas no workspace, sem deploy em produção e sem incluir limitação de tentativas no login. O texto da auditoria original abaixo foi preservado como histórico dos defeitos encontrados; o estado atualizado está consolidado na matriz funcional.

Itens corrigidos:

- bloqueio de contratos ativos sobrepostos por unidade e companhia;
- domínio fechado e validação por tipo das regras de bonificação;
- alias global de fornecedor com precedência do alias específico da unidade;
- contagens filtradas e prioridade explícita das exceções de conciliação;
- a integração das quatro visões em `/conciliacoes` foi revertida por decisão de produto; a rota voltou a exibir somente a Fila, preservando os componentes e APIs internos;
- estados de erro recuperáveis em Dashboard, Contratos, detalhe da unidade e Compras;
- carregamento independente das abas administrativas e CRUD de usuários, destinatários, contratos, regras e aliases;
- drawers com scroll lock, restauração da rolagem, fechamento por Escape, altura `100dvh` e semântica de diálogo no detalhe de frete;
- infraestrutura de testes frontend com runner Node e Vitest separados.

Validação pós-correção: Ruff aprovado, 164 testes backend aprovados, 5 testes Node aprovados, 15 testes Vitest aprovados e build Vite aprovado. A navegação automatizada do build local em 390×844 não pôde ser repetida porque o navegador bloqueou endereços privados antes do carregamento. A inicialização SQLite por migrations também encontrou SQL específico de PostgreSQL na revisão `0007`; para o harness isolado, as tabelas foram criadas pelos modelos.

## Resultado executivo

O sistema está operacional nas rotas principais e a suíte atual está verde, mas **nem todas as funções estão corretas**. A auditoria identificou cinco falhas de alta prioridade ligadas a autorização, integridade de contratos, validade das regras, mapeamento de fornecedores e consistência dos resumos. Também foram reproduzidos problemas de ordenação, telas inacessíveis, tratamento de erros, acessibilidade e layout mobile.

Resumo quantitativo:

| Indicador | Resultado |
|---|---:|
| Rotas React navegáveis | 10, mais fallback |
| Abas administrativas | 9 |
| Caminhos OpenAPI | 56 |
| Operações HTTP | 69 |
| Operações chamadas diretamente por testes | 36 |
| Operações sem chamada HTTP direta | 33 |
| Testes backend | 141 aprovados |
| Testes frontend | 5 aprovados |
| Lint backend | aprovado |
| Build frontend | aprovado, 2.152 módulos |
| Auditoria de dependências de produção | aprovada |
| Viewports móveis | 390×844 e 360×800 |
| Defeitos P1 | 5 |
| Defeitos P2 | 8 |
| Riscos/lacunas P3 | 4 |

As operações que gravam dados, importam arquivos, acionam ERP ou enviam e-mail não foram submetidas em produção. Quando havia teste existente, elas foram exercitadas no banco local isolado; quando não havia, foram classificadas como parciais ou não executadas.

## O que funcionou

- Login, sessão, logout, restrição administrativa e leitura por perfil.
- Dashboard, filtros, KPIs, evolução, exceções e composição da bonificação.
- Matriz de contratos, busca por unidade e detalhe da unidade 054.
- Compras: três filtros, busca por NF, ordenação, paginação, detalhe de NF, itens, títulos, baixas e movimentos.
- Exportador XLSX: arquivo ZIP/Office válido, XML escapado, números preservados e download acionado.
- Fila de conciliação, cadeia de evidências, formulários de informação/rejeição/ajuste/confirmação e histórico de respostas.
- Rotina mensal, indicadores, classificações e histórico de arquivos.
- Fretes: filtros históricos, KPIs, 124 CT-es no recorte, detalhe do CT-e 2592, tarifa e dados financeiros.
- Tarifas no desktop: listagem, formulário de criação e formulário de edição.
- Relatórios: histórico e download de PDF existente.
- As nove abas administrativas carregaram no desktop; os formulários principais foram abertos e cancelados sem gravar dados.
- O logo clicável retorna corretamente à Visão geral.
- Os multisseletores móveis de Compras não se sobrepõem mais: cada painel termina antes do próximo filtro e os checkboxes/textos mantêm o mesmo alinhamento.

## Defeitos P1 — corrigir antes de ampliar o uso

### P1-01 — Troca obrigatória de senha pode ser ignorada pela API

- **Reprodução:** autenticar um usuário com `must_change_password=true` e chamar um endpoint protegido diretamente. A interface mostra somente a troca de senha, mas a API aceita a requisição.
- **Causa:** `backend/app/security.py:36` valida token, atividade e perfil, mas não bloqueia o restante da API enquanto a senha não for alterada.
- **Impacto:** a política de primeiro acesso é apenas visual e pode ser contornada por chamada HTTP.
- **Correção recomendada:** dependência central que permita apenas `/auth/me`, `/auth/logout` e `/auth/change-password` durante esse estado.

### P1-02 — Contratos ativos idênticos ou sobrepostos são aceitos

- **Reprodução isolada:** dois `POST /api/admin/contracts` com unidade, companhia e período idênticos retornaram 200 e IDs diferentes.
- **Causa:** `backend/app/api/admin.py:299` e `:310` validam referências, mas não verificam interseção; o modelo em `backend/app/models.py:76` não tem restrição correspondente.
- **Impacto:** metas, saldo e bonificações podem ser duplicados ou atribuídos ao contrato errado.
- **Correção recomendada:** bloquear sobreposição ativa por unidade/companhia com validação transacional e teste de concorrência.

### P1-03 — Regras de bonificação aceitam domínio inválido

- **Reprodução isolada:** a API aceitou `kind`, `applies_to`, status e combinações numéricas arbitrárias.
- **Causa:** `RuleInput` em `backend/app/api/admin.py:54` usa strings livres e valida somente datas/dia/período; `backend/app/services/reconciliation.py:1945` encaminha qualquer tipo desconhecido ao conciliador mensal genérico.
- **Impacto:** uma configuração inválida pode materializar conciliações com regra errada em todo o período.
- **Correção recomendada:** enums fechados, validação discriminada por tipo e rejeição explícita de `kind` desconhecido.

### P1-04 — Alias global de fornecedor é cadastrado, mas nunca aplicado

- **Reprodução isolada:** `unit_code=null` é aceito no cadastro; a compra não é mapeada pelo alias.
- **Causa:** `AliasInput` permite unidade nula, enquanto `backend/app/services/seed.py:294` consulta exclusivamente `SupplierAlias.unit_code == unit_code`.
- **Impacto:** o administrador acredita ter criado uma regra global que não produz efeito.
- **Correção recomendada:** consultar alias da unidade ou global com precedência explícita, ou tornar a unidade obrigatória na API/UI.

### P1-05 — Resumo de conciliações ignora o filtro aplicado

- **Reprodução isolada:** lista filtrada para a unidade 001, com exceção aberta apenas na 003, retornou `summary.open_exceptions=1`.
- **Causa:** `backend/app/api/reconciliations.py:262` conta todas as exceções abertas sem reutilizar os filtros da consulta principal.
- **Impacto:** o resumo contradiz a lista e pode direcionar a operação para uma pendência inexistente no recorte.
- **Correção recomendada:** aplicar os mesmos joins/filtros ao agregado e adicionar teste por unidade, competência, status e tipo de regra.

## Defeitos P2 — correção prioritária

| ID | Falha | Evidência | Impacto |
|---|---|---|---|
| P2-01 | Exceções ordenadas alfabeticamente por severidade | `ReconciliationException.severity.desc()` em `backend/app/api/reconciliations.py:751`; ordem observada `medium, low, high, critical` | Itens críticos aparecem depois dos menos graves. |
| P2-02 | Central legada inacessível | `LegacyReconciliations` existe em `frontend/src/App.jsx:2339`, mas não está nas rotas de `:4547` | Exceções, competências e cobertura automática ficam órfãs na interface. |
| P2-03 | Overflow em Tarifas no mobile | 419 px de conteúdo para 375/345 px úteis; botões de desativar avançam até x=419 | Controle fica fora do viewport e a página rola lateralmente. |
| P2-04 | Overflow no Portal mobile | 472 px de conteúdo; badges e células de valor ultrapassam a largura | Leitura/classificação exige rolagem lateral e parte dos dados fica escondida. |
| P2-05 | Drawers sem contrato acessível completo | Corpo permanece `overflow: visible`; detalhe de frete tem zero elementos `role=dialog`; sem focus trap/Escape/restauração | Duas barras de rolagem, foco pode escapar e leitores de tela não identificam o contexto. |
| P2-06 | Falhas de API podem virar spinner infinito | `Contracts`, `UnitDetail` e carga de `Purchases` não tratam rejeição; o drawer de bônus converte erro em `null` | Usuário não recebe erro nem alternativa de recuperação. |
| P2-07 | Uma API derruba todas as abas administrativas | `Promise.all` em `frontend/src/App.jsx:4104` carrega todos os recursos de uma vez | Falha do Portal, fretes ou qualquer cadastro indisponibiliza toda a Administração. |
| P2-08 | Endpoint `/api` inexistente retorna a SPA com 200 | catch-all em `backend/app/main.py:86` não exclui `/api` | Clientes e monitoramento confundem erro de integração com sucesso HTML. |

## Riscos e lacunas P3

| ID | Lacuna | Consequência |
|---|---|---|
| P3-01 | Ciclos administrativos incompletos na UI | API oferece editar/excluir usuários, editar destinatários e excluir contratos/regras/aliases, mas os controles equivalentes não estão todos disponíveis. |
| P3-02 | Cobertura frontend mínima | Cinco testes de helper; nenhuma cobertura automatizada de React, rotas, formulários, uploads, downloads, permissões, paginação ou estados de erro. |
| P3-03 | Contrato OpenAPI incompleto | Sem `securitySchemes` e várias respostas sem schema; dificulta cliente tipado e teste contratual. |
| P3-04 | Requisições redundantes/obsoletas | Compras pode fazer duas cargas ao entrar/voltar à página 1; outras telas não protegem contra resposta fora de ordem. |

## Evidência de navegador

### Desktop

- Visão geral: composição de bonificação abriu com cinco composições e valores esperados/identificados.
- Contratos: quinze unidades; busca por Santa Maria isolou a 054.
- Compras: 8.936 registros; busca por 4231555 retornou uma NF; detalhe mostrou dois itens.
- Conciliações: 3 para tratar, 1 em análise e 345 confirmados; quatro formulários de decisão foram abertos e cancelados.
- Rotina mensal: 4 fechados, 5 aguardando e R$ 18.349,99 em aberto.
- Fretes: julho + agosto trouxe 124 CT-es, 1.723.000 L, 113 corretos e 11 pendentes; CT-e 2592 aberto.
- Tarifas: 8 ativas, 4 transportadoras e 7 sem data final; formulários de criação e edição conferidos.
- Relatórios: dez itens e download de PDF existente disparado.
- Administração: nove abas, cadastros, Portal e histórico de sincronização carregados; nenhuma escrita foi submetida.

### Mobile

| Cenário | 390×844 | 360×800 |
|---|---|---|
| Dashboard | Sem overflow | Sem overflow |
| Compras e filtros | Sem sobreposição/overflow | Sem overflow |
| Conciliações | Sem overflow; drawer com dupla rolagem | Sem overflow da página |
| Rotina mensal | Sem overflow | Sem overflow |
| Fretes | Sem overflow; drawer sem semântica de diálogo | Sem overflow |
| Tarifas | **419 px de largura** | **419 px de largura** |
| Administração padrão | Sem overflow | Sem overflow |
| Portal Ipiranga | **472 px de largura** | **472 px de largura** |

## Comandos finais executados

```text
backend> ..\.venv\Scripts\python.exe -m ruff check app tests
All checks passed!

backend> ..\.venv\Scripts\python.exe -m pytest -q
141 passed in 24.33s

frontend> node --test src\reconciliation-actions.test.js
5 passed, 0 failed

frontend> npm run build
2152 modules transformed; build concluído

frontend> npm run audit:prod
Production dependency audit passed
```

O exportador Excel também foi executado em harness isolado. Todas as verificações retornaram `true`, o MIME foi `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` e o arquivo de prova teve 3.189 bytes.

## Limites da auditoria

- A produção foi mantida somente leitura. Não foram criados usuários, destinatários, contratos, regras, aliases ou tarifas; não foram anexados boletos; não foram importados extratos; não foi executado sync/backfill; não foi gerado nem enviado relatório.
- Os fluxos cobertos apenas de maneira indireta permanecem como parciais, mesmo quando a suíte completa está verde.
- A árvore de trabalho contém alterações preexistentes e concorrentes. O teste local representa o snapshot disponível no encerramento; o navegador representa a versão publicada naquele momento, que pode não ser idêntica ao workspace.
- A ausência de limitação de tentativas no login não foi classificada como defeito porque o usuário determinou explicitamente que essa melhoria não fosse adicionada.

## Ordem recomendada de correção

1. Fechar os cinco P1 com testes de regressão antes da alteração de código.
2. Corrigir `/api` desconhecida, ordenação por severidade e tratamento de erro/spinner.
3. Resolver os dois overflows móveis e padronizar drawers com `100dvh`, scroll lock, foco e semântica de diálogo.
4. Definir se a central legada deve voltar à navegação ou ser removida com substituição formal das capacidades.
5. Desacoplar a carga das abas administrativas e completar os ciclos CRUD necessários.
6. Adicionar testes React e elevar a cobertura HTTP direta dos 33 endpoints faltantes.

O inventário completo e o estado de cada função estão em `docs/qa/2026-08-05-matriz-funcional.md`.
