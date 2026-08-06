# Cidade cadastral das origens de frete

## Objetivo

Exibir a cidade e a UF cadastrais do CNPJ de origem da mercadoria para tornar a conferência das tarifas de frete mais auditável. A informação é uma referência cadastral: não prova, por si só, o local físico de carregamento e não altera nenhum cálculo financeiro.

## Escopo

- Aplicar a consulta aos CNPJs de origem resolvidos pelas NF-es vinculadas aos CT-es, não aos CNPJs das transportadoras.
- Criar um cadastro local de origem, identificado pelo CNPJ normalizado, contendo razão social, cidade, UF, fonte, data da consulta e mensagem de falha mais recente, se houver.
- Usar uma consulta pública no backend para preencher somente CNPJs que ainda não existirem no cadastro local.
- Popular inicialmente os CNPJs de origem já presentes nos CT-es e reutilizar o cache nos próximos ciclos de sincronização.
- Exibir a cidade/UF cadastradas na administração de tarifas e no detalhe/lista da conciliação de fretes quando houver CNPJ de origem resolvido.

## Fora de escopo

- Consultar a API a cada carregamento de página ou para cada CT-e.
- Alterar tarifas existentes, critérios de busca de tarifa, conciliações, valores esperados ou status financeiros.
- Inferir que a cidade cadastral é a base física que carregou a mercadoria.
- Coletar ou depender dos XMLs de CT-e/NF-e nesta entrega.

## Arquitetura

Uma tabela local `freight_origins` será a fonte de leitura da aplicação. Ela armazenará o CNPJ sem pontuação, nome retornado, cidade, UF, identificador da fonte e instantes de consulta e atualização. O backend terá um cliente isolado de consulta de CNPJ, com timeout curto, tratamento de erro e normalização de dados.

Durante a sincronização de fretes, a aplicação reunirá os CNPJs de fornecedores das NF-es efetivamente resolvidas. Para cada CNPJ ausente no cadastro local, solicitará o preenchimento uma única vez. Falhas não interrompem a sincronização do ERP nem a reconciliação: o cadastro fica pendente e poderá ser tentado de novo em execução posterior.

As telas continuam usando `FreightRate.origin_cnpj` para definir o escopo contratual da tarifa. A cidade é resolvida pelo novo cadastro e exibida como `Origem cadastral: Cidade/UF`. Quando o CNPJ não estiver registrado na tarifa, mas for conhecido no CT-e, a cidade aparecerá apenas como contexto documental, sem escolher ou sugerir tarifa.

## Fonte externa e segurança

A primeira fonte será a API pública CNPJ.ws, chamada apenas pelo backend. Não será exposta ao navegador, não exigirá credencial e respeitará o limite público de consultas. O resultado será armazenado no PostgreSQL da aplicação; nenhum dado será escrito no ERP.

A origem da informação será visível na interface como dado cadastral automático. Uma futura conferência por XML poderá acrescentar uma origem comprovada, sem substituir ou alterar o cadastro obtido agora.

## Critérios de aceite

- Um CNPJ de fornecedor de NF-e resolvida recebe cidade/UF automaticamente uma vez e é reutilizado em novos CT-es.
- CNPJ formatado ou não formatado gera o mesmo cadastro.
- Falha, dado incompleto ou indisponibilidade da API não interrompem a sincronização nem mudam a tarifa aplicada.
- As telas mostram cidade/UF apenas como origem cadastral e não como prova de carregamento.
- Nenhuma tarifa, valor esperado, diferença ou status histórico muda em consequência da nova informação.
- Testes cobrem sucesso, cache, normalização e falha da fonte externa.
