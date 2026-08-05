# Matriz funcional — Contratos GBI

Data da execução: 05/08/2026

## Escopo e legenda

Esta matriz cobre a versão local presente no workspace e a versão publicada em `https://contratos.atendimento-gbi.online/`. A suíte local usa banco isolado; na produção foram executadas apenas ações de leitura e abertura/cancelamento de formulários, sem gravar cadastros, importar arquivos, sincronizar o ERP ou enviar relatórios.

Status usados:

- **Aprovada:** o comportamento principal foi confirmado por teste automatizado ou navegação real.
- **Falhou:** existe reprodução objetiva de comportamento incorreto.
- **Parcial:** parte do fluxo foi validada, mas falta um caminho importante ou teste HTTP direto.
- **Não executada em produção:** operação destrutiva, envio externo ou alteração de dados; o contrato visual foi inspecionado e a cobertura local é informada.

Evidências:

- **E1:** `ruff check app tests` — aprovado.
- **E2:** `pytest -q` — 164 testes aprovados após as correções.
- **E3:** `npm test` — 5 testes Node e 15 testes Vitest aprovados.
- **E4:** `npm run build` e `npm run audit:prod` — aprovados.
- **E5:** exploração desktop autenticada em produção.
- **E6:** exploração mobile em 390×844 e 360×800.
- **E7:** probes locais com FastAPI/TestClient e SQLite isolado.
- **E8:** inspeção estática de rotas, handlers, permissões e chamadas HTTP.
- **E9:** validação isolada do exportador XLSX: ZIP válido, planilha, XML escapado, célula numérica, nome, clique e revogação da URL aprovados.
- **E10:** testes de regressão adicionados para integridade administrativa, aliases, filtros de conciliação, estados de erro, abas, CRUD administrativo e drawers.
- **E11:** a tentativa de abrir o build local em 390×844 foi bloqueada pelo navegador antes do carregamento para `localhost`, `127.0.0.1` e IP privado; não é evidência de falha da aplicação.

## Interface e fluxos do usuário

| ID | Área | Funcionalidade | Resultado | Evidência e observação |
|---|---|---|---|---|
| QA-AUTH-001 | Autenticação | Login, restauração de sessão, logout e bloqueio de usuário inativo | Aprovada | E2, E5. Sessão autenticada real e testes básicos de login/logout/me. |
| QA-AUTH-002 | Autenticação | Obrigar troca de senha inicial | **Falhou** | E7, E8. A interface bloqueia a navegação, mas o backend aceita chamadas autenticadas enquanto `must_change_password=true`. |
| QA-AUTH-003 | Autorização | Restringir Administração e Tarifas a administradores | Aprovada | E2, E8. Rotas redirecionam viewer e dependências administrativas retornam 403. |
| QA-NAV-001 | Navegação | Menu lateral, grupos expansíveis e rotas principais | Aprovada com ressalva | E5, E6. Navegação funciona; o menu fechado continua no DOM sem `aria-hidden`/`inert`. |
| QA-NAV-002 | Navegação | Logo central clicável retorna à Visão geral | Aprovada | E6. Testado a partir de Compras: link `Ir para visão geral` levou a `/`. |
| QA-NAV-003 | Navegação | Rota de interface inexistente redireciona para `/` | Aprovada | E5. O React Router normalizou a rota para a Visão geral. |
| QA-DASH-001 | Visão geral | Carregar KPIs, evolução, exceções e situação dos contratos | Aprovada | E2, E5, E6. |
| QA-DASH-002 | Visão geral | Filtrar competência, unidades e bandeiras | Aprovada | E5, E6. |
| QA-DASH-003 | Visão geral | Abrir detalhamento da bonificação esperada | Aprovada | E5, E10. O drawer agora apresenta erro recuperável e ação de tentar novamente. |
| QA-CON-001 | Contratos | Listar matriz de contratos por unidade | Aprovada | E2, E5, E6. Quinze unidades visíveis na produção. |
| QA-CON-002 | Contratos | Buscar unidade | Aprovada | E5. Busca por Santa Maria retornou somente a unidade 054. |
| QA-CON-003 | Contratos | Abrir detalhe da unidade e acessar suas notas | Aprovada | E5, E6. Unidade 054 e link `Ver notas` confirmados. |
| QA-CON-004 | Contratos | Tratar falha ao carregar matriz/detalhe | Aprovada localmente | E10. Matriz e detalhe exibem falha recuperável sem spinner infinito. |
| QA-PUR-001 | Compras | Listar, paginar e ordenar notas do ERP | Aprovada | E2, E5, E6. 8.936 registros e 179 páginas observados. |
| QA-PUR-002 | Compras | Filtrar por unidade, companhia e competência | Aprovada | E5, E6. Os três multisseletores móveis abrem em fluxo, sem sobreposição; opções alinhadas em grid. |
| QA-PUR-003 | Compras | Buscar por NF, fornecedor, CNPJ ou chave | Aprovada | E5. NF 4231555 retornou um registro. |
| QA-PUR-004 | Compras | Abrir nota, itens, títulos, baixas e movimentos | Aprovada | E2, E5. NF 4231555 conferida com dois itens e dados financeiros. |
| QA-PUR-005 | Compras | Exportar listagem e itens para Excel | Aprovada | E9. O clique por `blob:` não foi observável pelo evento de download do navegador, mas o gerador foi validado isoladamente como XLSX real. |
| QA-PUR-006 | Compras | Tratar falha de carregamento | Aprovada localmente | E10. A carga principal apresenta erro e permite tentar novamente; a requisição duplicada da primeira página foi removida. |
| QA-REC-001 | Conciliações | Fila por estado, filtros e histórico de confirmados | Aprovada | E2, E5, E6. Produção mostrou 3 para tratar, 1 em análise e 345 confirmados. |
| QA-REC-002 | Conciliações | Abrir cadeia de conferência e evidências | Aprovada | E2, E5, E6. NF 2958247 conferida. |
| QA-REC-003 | Conciliações | Pedir informação, responder e manter histórico de encerramento | Aprovada localmente | E2, E3, E5. Formulários abertos/cancelados em produção; mutações cobertas localmente. |
| QA-REC-004 | Conciliações | Rejeitar vínculo, ajustar e confirmar competência | Aprovada localmente | E2, E5. Contratos e campos conferidos em produção sem submissão. |
| QA-REC-005 | Conciliações | Anexar e baixar boleto/evidência | Parcial | E5, E8. Controle de upload existe; POST e download não possuem teste HTTP direto suficiente. |
| QA-REC-006 | Conciliações | Central de exceções, competências e cobertura automática | Revertida por decisão de produto | E10. `/conciliacoes` voltou a exibir somente a Fila; os componentes e as APIs permanecem internos. |
| QA-REC-007 | Conciliações | Resumo respeitar os mesmos filtros da lista | Aprovada localmente | E2, E10. A contagem de exceções reutiliza unidade, competência, status e tipo de regra. |
| QA-REC-008 | Conciliações | Ordenar exceções por criticidade | Aprovada localmente | E2, E10. Ordenação explícita `critical`, `high`, `medium`, `low`. |
| QA-MON-001 | Rotina mensal | Exibir fechados, aguardando, valores e histórico de importações | Aprovada | E2, E5, E6. Produção: 4 fechados, 5 aguardando e R$ 18.349,99 em aberto. |
| QA-MON-002 | Rotina mensal | Importar extrato Ipiranga | Parcial | E2, E5. Permissão e formulário validados; caminho feliz do upload não tem teste HTTP direto e não foi enviado em produção. |
| QA-MON-003 | Rotina mensal | Importar comprovantes Raízen da unidade 054 | Parcial | E2, E5. Mesmo limite do upload Ipiranga. |
| QA-MON-004 | Rotina mensal | Ratear TED Raízen entre competências | Parcial | E2, E8. Regra e UI existem, mas falta teste HTTP direto do caminho feliz. |
| QA-FRT-001 | Fretes | Filtrar competências, unidades, transportadora, status e CT-e/NF-e | Aprovada | E2, E5, E6. Julho + agosto retornou 124 CT-es. |
| QA-FRT-002 | Fretes | KPIs e detalhamento por indicador | Aprovada | E2, E5. 1.723.000 L, 113 corretos e 11 pendentes no recorte testado. |
| QA-FRT-003 | Fretes | Abrir CT-e, notas vinculadas, tarifa, título e pagamento | Aprovada | E2, E5, E6. CT-e 2592 validado. |
| QA-FRT-004 | Fretes | Confirmar, contestar, justificar ou reabrir revisão | Parcial | E2, E5. Confirmação, autorização e fingerprint obsoleto têm cobertura; contestar/reabrir não têm cobertura equivalente. |
| QA-RATE-001 | Tarifas | Listar tarifas, transportadoras cobertas e vigências | Aprovada no desktop | E2, E5. Oito tarifas ativas, quatro transportadoras e sete vigências abertas. |
| QA-RATE-002 | Tarifas | Cadastrar tarifa e bloquear sobreposição do mesmo escopo | Aprovada localmente | E2, E5. Formulário inspecionado; validação de sobreposição existe no backend. |
| QA-RATE-003 | Tarifas | Editar ou desativar tarifa | Parcial | E5, E8. Formulário de edição conferido; PATCH não tem teste HTTP direto suficiente. |
| QA-RATE-004 | Tarifas | Layout mobile da lista | **Falhou** | E6. Documento mede 419 px para 375/345 px úteis; botões `.icon-button.danger` avançam até x=419. |
| QA-REP-001 | Relatórios | Listar histórico e status de tentativas | Aprovada | E5, E6. Dez registros observados. |
| QA-REP-002 | Relatórios | Baixar PDF existente | Aprovada | E5. Evento de download disparou para relatório de julho/2026. |
| QA-REP-003 | Relatórios | Exportar histórico para Excel | Aprovada | E9. Mesmo exportador XLSX validado isoladamente. |
| QA-REP-004 | Relatórios | Gerar PDF | Não executada em produção | E5, E8. Botão e payload existem; endpoint não tem teste HTTP direto. |
| QA-REP-005 | Relatórios | Gerar e enviar por e-mail | Não executada em produção | E5, E8. Evitado para não enviar mensagem real; serviço possui cobertura indireta. |
| QA-ADM-001 | Administração | Criar, editar, inativar e excluir usuários | Aprovada localmente | E2, E10. A UI agora expõe edição e inativação, com recarga restrita a Usuários. |
| QA-ADM-002 | Administração | Destinatários de relatórios | Aprovada localmente | E2, E10. Cadastro, edição e remoção estão disponíveis e cobertos por teste de componente. |
| QA-ADM-003 | Administração | Contratos: criar, editar e inativar | Aprovada localmente | E2, E10. Criação e edição bloqueiam sobreposição de períodos ativos por unidade e companhia. |
| QA-ADM-004 | Administração | Postos: editar nome, cidade e bandeira | Aprovada com ressalva | E2, E5, E6. Formulário existe e valida referências; gravação positiva não foi feita em produção. |
| QA-ADM-005 | Administração | Regras de bonificação | Aprovada localmente | E2, E10. Domínios e campos obrigatórios por tipo são validados; combinações inválidas retornam 422. |
| QA-ADM-006 | Administração | Alias de fornecedores/CNPJ | Aprovada localmente | E2, E10. Alias global é aplicado e o alias específico da unidade tem precedência. |
| QA-ADM-007 | Administração | Tarifas dentro da área administrativa | **Falhou no mobile** | E5, E6. Funciona no desktop; reproduz o mesmo overflow de 419 px no mobile. |
| QA-ADM-008 | Administração | Portal Ipiranga/Texaco: consultar extratos e classificar eventos | Aprovada no desktop | E2, E5. Dados, classificação e histórico visíveis. |
| QA-ADM-009 | Administração | Portal: importar arquivo e baixar originais/CSV/Excel | Parcial | E5. Controles e links existem; upload não foi submetido e a maioria dos endpoints não tem teste HTTP direto. |
| QA-ADM-010 | Administração | Portal no mobile | **Falhou** | E6. Documento chega a 472 px; badges de eventos e colunas ultrapassam o viewport. |
| QA-ADM-011 | Administração | Sincronização incremental e backfill completo | Não executada em produção | E5, E8. Botões e histórico carregam; execução foi evitada por atingir ERP/processamento real. |
| QA-ADM-012 | Administração | Isolamento de falhas entre as nove abas | Aprovada localmente | E10. Cada aba carrega e tenta novamente seu próprio recurso; falha do Portal não bloqueia Usuários. |
| QA-MOB-001 | Mobile | Rotas principais em 390×844 | Parcial | E6. 8 de 10 rotas sem overflow; Tarifas e Portal falham. |
| QA-MOB-002 | Mobile | Pontos críticos em 360×800 | Parcial | E6. Mesmos dois overflows reproduzidos. |
| QA-MOB-003 | Mobile | Drawer de conciliação | Aprovada localmente | E10. O corpo da página é travado enquanto o drawer está aberto e restaurado no fechamento. |
| QA-MOB-004 | Mobile | Drawer de frete acessível | Parcialmente aprovada | E10. Possui diálogo nomeado e fecha por Escape; focus trap e restauração explícita de foco continuam fora deste escopo funcional. |
| QA-MOB-005 | Mobile | Sidebar acessível | **Falhou parcialmente** | E6, E8. Botões têm 44 px e menu funciona, mas a sidebar fechada continua potencialmente focável e o gatilho não informa `aria-expanded`. |

## Inventário completo da API

O OpenAPI atual expõe **56 caminhos e 69 operações HTTP**. A suíte chama diretamente 36 operações; 33 dependem de cobertura indireta, inspeção ou ainda não têm teste HTTP específico.

| Domínio | Operação | Finalidade | Resultado |
|---|---|---|---|
| Infra | `GET /api/health` | Saúde da aplicação e banco | Aprovada |
| Auth | `POST /api/auth/login` | Criar sessão | Aprovada |
| Auth | `POST /api/auth/logout` | Encerrar sessão | Aprovada |
| Auth | `GET /api/auth/me` | Usuário atual | Aprovada |
| Auth | `POST /api/auth/change-password` | Trocar senha obrigatória | Parcial; sem teste HTTP direto e com bypass de outras APIs |
| Dashboard | `GET /api/dashboard` | KPIs e contratos | Aprovada |
| Dashboard | `GET /api/dashboard/bonus-details` | Composição da bonificação | Aprovada com falha de estado no frontend quando a API erra |
| Unidades | `GET /api/units` | Listar unidades/contratos | Aprovada pela UI; sem teste HTTP dedicado |
| Unidades | `GET /api/units/{code}` | Detalhar unidade | Aprovada |
| Compras | `GET /api/purchases` | Listar/filtrar/paginar/ordenar | Aprovada |
| Compras | `GET /api/purchases/{erp_entry_id}` | NF, itens, títulos, baixas | Aprovada |
| Conciliação | `GET /api/reconciliations` | Listagem legada e resumo | Aprovada localmente; resumo usa os filtros da consulta |
| Conciliação | `GET /api/reconciliations/coverage` | Cobertura automática | Aprovada localmente; sem acesso direto na navegação após reversão de produto |
| Conciliação | `GET /api/reconciliations/work-queue` | Fila operacional | Aprovada |
| Conciliação | `GET /api/reconciliations/exceptions` | Central de exceções | Aprovada localmente; prioridade explícita por severidade |
| Conciliação | `POST /api/reconciliations/items/{item_id}/review` | Solicitar informação/rejeitar/aceitar | Aprovada |
| Conciliação | `POST /api/reconciliations/information-requests/{request_id}/respond` | Responder solicitação | Aprovada |
| Conciliação | `POST /api/reconciliations/exceptions/{exception_id}/action` | Agir sobre exceção | Parcial; sem teste HTTP direto |
| Conciliação | `POST /api/reconciliations/{reconciliation_id}/boleto-evidence` | Anexar boleto | Parcial; sem caminho feliz HTTP direto |
| Conciliação | `GET /api/reconciliations/boleto-evidence/{evidence_id}/file` | Baixar boleto | Parcial; sem teste HTTP direto |
| Conciliação | `GET /api/reconciliations/{reconciliation_id}/detail` | Detalhe e workspace | Aprovada |
| Conciliação | `GET /api/reconciliations/{reconciliation_id}/reviews/{review_id}` | Snapshot de revisão | Aprovada |
| Conciliação | `POST /api/reconciliations/{reconciliation_id}/confirm` | Confirmar competência | Aprovada localmente |
| Conciliação | `POST /api/reconciliations/{reconciliation_id}/adjust` | Registrar ajuste | Aprovada localmente |
| Rotina mensal | `GET /api/monthly-routine` | Painel mensal | Aprovada |
| Rotina mensal | `POST /api/monthly-routine/imports/ipiranga` | Importar extrato Ipiranga | Parcial |
| Rotina mensal | `POST /api/monthly-routine/imports/raizen/054` | Importar Raízen 054 | Parcial |
| Rotina mensal | `POST /api/monthly-routine/raizen/054/receipts/{event_id}/allocations` | Ratear comprovante | Parcial |
| Fretes | `GET /api/freights/summary` | KPIs de frete | Aprovada |
| Fretes | `GET /api/freights/card-details` | Detalhar indicador | Aprovada |
| Fretes | `GET /api/freights` | Listar CT-es | Aprovada |
| Fretes | `GET /api/freights/{reconciliation_id}` | Detalhar CT-e | Aprovada |
| Fretes | `POST /api/freights/{reconciliation_id}/review` | Confirmar/contestar/justificar/reabrir | Parcial; ações não têm cobertura uniforme |
| Portal | `GET /api/portal-statements/ipiranga/001` | Extrato Ipiranga 001 | Aprovada pela UI |
| Portal | `GET /api/portal-statements/texaco/050` | Extrato Texaco 050 | Aprovada pela UI |
| Portal | `POST /api/portal-statements/ipiranga/events/{event_id}/classify` | Classificar evento | Aprovada localmente |
| Portal | `POST /api/portal-statements/ipiranga` | Importar extrato | Parcial |
| Portal | `GET /api/portal-statements/ipiranga/001/supplemental.csv` | Exportar CSV suplementar | Parcial |
| Portal | `GET /api/portal-statements/imports/{import_id}/file` | Baixar original | Parcial |
| Relatórios | `GET /api/reports/monthly` | Listar histórico | Aprovada pela UI |
| Relatórios | `POST /api/reports/monthly` | Gerar/gerar e enviar | Não executada em produção; cobertura apenas indireta |
| Relatórios | `GET /api/reports/{report_id}/download` | Baixar PDF | Aprovada em produção |
| Admin usuários | `GET /api/admin/users` | Listar | Aprovada |
| Admin usuários | `POST /api/admin/users` | Criar | Parcial; sem teste HTTP direto suficiente |
| Admin usuários | `PATCH /api/admin/users/{user_id}` | Editar, resetar senha, ativar/inativar | Aprovada localmente; sem UI completa |
| Admin usuários | `DELETE /api/admin/users/{user_id}` | Inativar | Parcial; sem UI e sem teste HTTP direto |
| Admin destinatários | `GET /api/admin/recipients` | Listar | Aprovada pela UI |
| Admin destinatários | `POST /api/admin/recipients` | Criar | Aprovada em cenários de borda; positivo não foi gravado em produção |
| Admin destinatários | `PATCH /api/admin/recipients/{recipient_id}` | Editar | Parcial; sem UI e sem teste direto |
| Admin destinatários | `DELETE /api/admin/recipients/{recipient_id}` | Remover | Parcial; UI existe, sem submissão em produção |
| Admin unidades | `PATCH /api/admin/units/{unit_code}` | Editar posto | Aprovada em validações; positivo não foi gravado em produção |
| Admin contratos | `GET /api/admin/contracts` | Listar | Aprovada |
| Admin contratos | `POST /api/admin/contracts` | Criar | Aprovada localmente; sobreposição ativa retorna 409 |
| Admin contratos | `PATCH /api/admin/contracts/{contract_id}` | Editar | Aprovada localmente; ignora o próprio registro e bloqueia outro conflito |
| Admin contratos | `DELETE /api/admin/contracts/{contract_id}` | Inativar | Aprovada localmente e disponível na UI |
| Admin regras | `GET /api/admin/rules` | Listar | Aprovada |
| Admin regras | `POST /api/admin/rules` | Criar | Aprovada localmente; domínio e combinação de campos validados |
| Admin regras | `PATCH /api/admin/rules/{rule_id}` | Editar | Aprovada localmente; domínio e combinação de campos validados |
| Admin regras | `DELETE /api/admin/rules/{rule_id}` | Inativar | Aprovada localmente e disponível na UI |
| Admin aliases | `GET /api/admin/aliases` | Listar | Aprovada |
| Admin aliases | `POST /api/admin/aliases` | Criar | Aprovada localmente; alias global é aplicado |
| Admin aliases | `PATCH /api/admin/aliases/{alias_id}` | Editar | Aprovada localmente; alias específico mantém precedência |
| Admin aliases | `DELETE /api/admin/aliases/{alias_id}` | Inativar | Aprovada localmente e disponível na UI |
| Admin sync | `GET /api/admin/sync` | Histórico | Aprovada pela UI |
| Admin sync | `POST /api/admin/sync` | Incremental/backfill | Não executada em produção |
| Admin fretes | `GET /api/admin/freight-rates` | Listar tarifas | Aprovada |
| Admin fretes | `GET /api/admin/freight-carriers` | Listar transportadoras | Aprovada |
| Admin fretes | `POST /api/admin/freight-rates` | Criar tarifa | Aprovada localmente |
| Admin fretes | `PATCH /api/admin/freight-rates/{rate_id}` | Editar/desativar | Parcial; sem teste HTTP direto suficiente |

## Lacunas de cobertura automatizada

- O frontend agora executa 5 testes Node e 15 testes Vitest, incluindo componentes React, estados de erro, abas, CRUD administrativo e drawers; uploads/downloads e permissões ainda precisam de cobertura adicional.
- `npm test` executa os dois runners sem coletar o arquivo Node pelo Vitest.
- 33 das 69 operações HTTP não são chamadas diretamente pela suíte atual. As maiores lacunas estão em relatórios, uploads/downloads, mudança de senha, ação de exceção, sincronização, CRUDs administrativos e variantes de revisão de frete.
- O OpenAPI não declara esquema de autenticação e várias respostas não têm schema; isso reduz a capacidade de gerar testes contratuais automaticamente.
- A migration `0007_ipiranga_supplemental_review` contém `ALTER COLUMN` específico de PostgreSQL e não sobe em SQLite; o QA mobile isolado precisou criar as tabelas diretamente pelos modelos.
