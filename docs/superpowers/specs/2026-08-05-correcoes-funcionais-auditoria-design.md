# Correções funcionais da auditoria — Design

Data: 05/08/2026

## Contexto

A auditoria funcional de 05/08/2026 confirmou que as rotas principais estão operacionais, mas encontrou falhas que podem produzir cadastros incoerentes, cálculos ou resumos incorretos, funções inacessíveis e telas inutilizáveis em estados de erro ou em dispositivos móveis.

O usuário decidiu priorizar exclusivamente o funcionamento do produto por se tratar de uma ferramenta interna. Este desenho não inclui endurecimento de autenticação, limitação de tentativas de login, documentação OpenAPI, política de sessão ou melhorias que sejam apenas de acessibilidade técnica.

## Objetivo

Corrigir as falhas funcionais confirmadas pela auditoria sem reestruturar o sistema inteiro, preservando o comportamento atual que já foi validado e adicionando testes de regressão para cada mudança.

## Fora do escopo

- Limitação de tentativas no login.
- Bloqueio de APIs enquanto `must_change_password=true`.
- Alterações em cookies, tokens ou política de sessão.
- `securitySchemes`, schemas de resposta ou reorganização geral do OpenAPI.
- Requisitos exclusivamente de leitores de tela, focus trap ou semântica ARIA.
- Correção automática de dados históricos já cadastrados.
- Sincronização ou backfill do ERP em produção.
- Deploy em produção, que dependerá de autorização separada.
- Refatoração geral do arquivo `frontend/src/App.jsx` sem relação direta com os defeitos.

## Estratégia

A implementação será incremental e orientada a testes. Cada grupo começa com uma reprodução automatizada que falha no código atual, recebe a menor correção necessária e termina com a suíte relacionada verde. A ordem será: integridade de cadastros, consistência de consultas, navegação de conciliações, resiliência de carregamento, CRUD administrativo e layout mobile.

As alterações locais já presentes no workspace são consideradas parte do estado atual do produto. Nenhum arquivo será revertido ou substituído em bloco.

## 1. Integridade funcional do backend

### 1.1 Contratos ativos sobrepostos

Antes de criar ou editar um contrato ativo, a API consultará contratos ativos da mesma combinação `unit_code + company_code`. Haverá conflito quando os intervalos forem inclusivos e satisfizerem:

```text
novo_início <= existente_fim AND novo_fim >= existente_início
```

Na edição, o próprio contrato será ignorado. Em conflito, a API responderá HTTP 409 com mensagem que identifique a unidade, a companhia e a vigência conflitante. Contratos inativos não bloqueiam novos cadastros.

O status de contrato será limitado a `active` ou `inactive`. A verificação será transacional na aplicação; não será adicionada extensão ou constraint específica do PostgreSQL nesta fase, para manter a suíte SQLite e o deploy simples.

### 1.2 Regras de bonificação válidas

Os tipos aceitos serão exatamente:

- `distributor_credit`
- `milestone_bonus`
- `invoice_discount`
- `s10_excess_credit`
- `bank_deposit`

Regras comuns:

- `effective_to`, quando informado, não pode ser anterior a `effective_from`.
- Valores monetários, litros e períodos informados não podem ser negativos.
- `due_day`, quando informado, deve estar entre 1 e 31.
- `due_month_offset` não pode ser negativo.
- `applies_to` aceita `all_fuel`, `s10` ou `fuel_codes:<lista de códigos numéricos separados por vírgula>`.

Regras por tipo:

- `milestone_bonus`: exige `milestone_liters > 0`, `milestone_amount > 0` e `period_months > 0`.
- `s10_excess_credit`: exige `rate_per_liter > 0`, `threshold_liters > 0` e `applies_to=s10`.
- `distributor_credit`, `invoice_discount` e `bank_deposit`: exigem `rate_per_liter > 0`.

O reconciliador deixará de tratar um tipo desconhecido como regra mensal genérica. Um tipo fora do domínio será rejeitado antes da persistência.

### 1.3 Alias global de fornecedor

`unit_code=null` continuará sendo um cadastro válido e passará a significar “todas as unidades”. O mapeamento buscará aliases ativos da unidade atual e aliases globais no mesmo conjunto.

A precedência será:

1. alias específico da unidade;
2. alias global;
3. dentro do mesmo nível, vigência mais recente;
4. CNPJ ou trecho do nome, conforme já suportado.

Essa regra evita que um alias global sobrescreva uma exceção deliberada de um posto.

## 2. Consistência das consultas de conciliação

### 2.1 Resumo filtrado

O total `summary.open_exceptions` de `GET /api/reconciliations` reutilizará os filtros aplicados à lista, incluindo unidade, status, competência e tipo de regra. Uma exceção fora do recorte não poderá alterar o resumo exibido.

### 2.2 Prioridade das exceções

A ordenação será explícita e funcional:

```text
critical → high → medium → low
```

Dentro da mesma severidade permanecem os critérios atuais de vencimento, unidade e criação.

## 3. Uma única experiência de Conciliações

A rota `/conciliacoes` continuará sendo o único ponto de entrada. Ela receberá quatro abas internas:

1. **Fila** — experiência atual e aba inicial.
2. **Exceções** — central de exceções e suas ações.
3. **Competências** — listagem consolidada por competência.
4. **Cobertura automática** — indicadores e evidências de cobertura.

O componente legado não voltará como rota separada. Suas partes úteis serão transformadas em painéis usados pela tela atual; código que apenas duplicava cabeçalho, filtros ou navegação será removido.

Trocar de aba não altera dados. Filtros pertencem ao painel correspondente e o retorno à aba Fila preserva o estado da fila durante a sessão da página.

Cada aba terá estado próprio de carregamento, vazio e erro. A falha de uma aba não impedirá o uso das demais.

## 4. Estados de carregamento e erro

### 4.1 Páginas de contratos e compras

`Contracts`, `UnitDetail` e `Purchases` terão os três estados explícitos:

- carregando;
- conteúdo carregado;
- erro com mensagem e botão `Tentar novamente`.

Uma rejeição de API nunca poderá manter o spinner indefinidamente. Compras manterá a proteção contra respostas obsoletas e eliminará a requisição duplicada ao entrar ou voltar para a página 1.

### 4.2 Detalhamento da bonificação

O drawer distinguirá `loading`, `data` e `error`. Um erro não será representado por `null`, pois `null` também é o estado inicial de carregamento. O usuário verá a falha e poderá tentar novamente ou fechar.

### 4.3 Administração

A Administração deixará de carregar todos os recursos em um único `Promise.all`. Serão carregados:

- dados compartilhados mínimos ao abrir a página;
- dados de cada aba somente quando necessários;
- erro e tentativa novamente por aba.

Falha no Portal, nas tarifas ou no histórico de sincronização não bloqueará Usuários, Destinatários, Contratos, Postos, Bonificações ou Fornecedores.

## 5. Ciclos CRUD administrativos

A interface será alinhada às operações já existentes na API:

- **Usuários:** criar, editar nome/e-mail/perfil/estado e inativar.
- **Destinatários:** criar, editar e remover.
- **Contratos:** criar, editar e inativar.
- **Bonificações:** criar, editar e inativar.
- **Fornecedores/aliases:** criar, editar e inativar.
- **Postos:** manter a edição atual.
- **Tarifas:** manter criação/edição/desativação e adicionar cobertura de regressão.

As ações destrutivas usarão confirmação explícita e recarregarão somente o recurso afetado. Erros de validação do backend serão exibidos junto ao formulário correspondente.

## 6. Layout mobile e drawers

### 6.1 Tarifas de frete

Cards e linhas de ações usarão `min-width: 0`, quebra controlada e uma área de botões que permaneça dentro da largura útil. Em 390×844 e 360×800, nenhum botão de editar ou desativar poderá ultrapassar o viewport.

### 6.2 Portal Ipiranga/Texaco

Badges de eventos aceitarão quebra de linha e terão `max-width: 100%` e `overflow-wrap: anywhere`. A apresentação mobile deixará de depender da largura natural da tabela para mostrar status e valores.

### 6.3 Drawers

Os drawers usarão altura dinâmica (`100dvh`) e bloquearão a rolagem da página enquanto estiverem abertos. Somente o corpo do drawer será rolável. Cabeçalho e botão de fechar permanecerão visíveis e o fechamento restaurará a rolagem normal da página.

Não faz parte deste escopo criar uma infraestrutura completa de foco ou acessibilidade; os atributos acessíveis que já existem serão preservados.

## 7. Testes

### Backend

Serão adicionados testes HTTP/serviço para:

- criação e edição de contrato sobreposto;
- contrato inativo fora do bloqueio;
- domínio e requisitos de cada tipo de regra;
- rejeição de tipo e `applies_to` desconhecidos;
- precedência de alias específico sobre global;
- uso de alias global quando não há específico;
- resumo de exceções respeitando todos os filtros;
- ordenação funcional de severidade.

### Frontend

O frontend receberá um script `npm test` e infraestrutura mínima de testes de componentes com ambiente DOM. Os testes cobrirão:

- erro e nova tentativa nas páginas afetadas;
- ausência de requisição duplicada em Compras;
- erro distinto de loading no drawer de bonificação;
- isolamento de falhas entre abas administrativas;
- navegação das quatro abas de Conciliações;
- operações CRUD visíveis e atualização do recurso correto;
- abertura/fechamento do drawer com bloqueio e restauração da rolagem.

### Validação visual

Depois de testes e build verdes, as rotas afetadas serão verificadas localmente em desktop, 390×844 e 360×800. Os critérios são ausência de overflow horizontal, ausência de dupla rolagem e acesso a todos os controles essenciais.

## 8. Critérios de aceite

- A suíte backend completa permanece verde.
- `npm test`, `npm run build` e `npm run audit:prod` terminam com código zero.
- Contratos sobrepostos retornam 409 e não são persistidos.
- Somente os cinco tipos de regra válidos são persistidos, com seus campos obrigatórios.
- Alias global funciona e perde precedência para alias específico.
- Resumo e lista de conciliações apresentam o mesmo recorte.
- Exceções críticas aparecem antes de altas, médias e baixas.
- As quatro capacidades de Conciliações são acessíveis pela rota atual.
- Erros de API não deixam spinners infinitos nem bloqueiam abas não relacionadas.
- Os ciclos CRUD descritos estão disponíveis na interface.
- Tarifas e Portal não geram overflow em 390×844 ou 360×800.
- Drawers não apresentam duas barras de rolagem.
- Nenhum dado de produção é alterado durante a implementação e validação local.

