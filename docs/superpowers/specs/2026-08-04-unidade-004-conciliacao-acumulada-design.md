# Unidade 004 — conciliação acumulada por créditos Ipiranga

## Objetivo

Para a unidade 004, substituir a atribuição automática de crédito a ciclos
individuais de notas por uma conciliação acumulada e auditável:

`bonificação esperada pelo volume elegível - créditos postecipados do portal`

O portal Ipiranga é a fonte primária para os créditos. A NF exibida como
"Nota Fiscal Utilizada" comprova onde o crédito foi consumido, mas não é
tratada como a compra que originou a bonificação.

## Escopo

- Aplicar exclusivamente à regra `004 / IPIRANGA / distributor_credit`.
- Registrar os nove vínculos de uso do portal já comprovados: data do crédito,
  valor, NF utilizada, valor da NF e chave de acesso correspondente do ERP.
- Manter as compras contratuais no cálculo do valor esperado acumulado.
- Mostrar os créditos do portal como evidência primária de valor identificado.
- Retirar NFs sem vínculo de origem declarado do fluxo operacional de cobrança.
- Exibir a diferença acumulada como acompanhamento, não como uma lista de
  pendências por NF.

## Regra de status

- Cada crédito com valor, data e NF utilizada confirmados pelo portal é
  `confirmado`.
- O saldo acumulado é `esperado acumulado - créditos confirmados`.
- O saldo histórico de R$ 69,99, observado no corte de 26/05/2026, permanece
  visível como ajuste/acompanhamento e não gera cobrança automática.
- Compras posteriores ao último corte coberto pelos créditos são `aguardando
  próximo extrato`, nunca divergência.

## Evidência e auditoria

- O registro de portal guarda a NF utilizada e uma transcrição/arquivo de
  origem no banco da aplicação.
- A NF é validada contra unidade 004, fornecedor Ipiranga, valor e chave ERP.
- Nenhuma informação é escrita no ERP.
- A lógica de ciclo anterior não confirma mais NFs individuais; poderá ser
  exibida apenas como cálculo técnico de apoio, sem efeito financeiro.

## Testes de aceite

- Os nove créditos conhecidos totalizam R$ 78.470,01 e aparecem confirmados.
- NF utilizada incorreta, ausente ou de outra unidade não confirma o crédito.
- A soma de créditos não pode exceder a fonte de portal.
- A fila deixa de conter as 104 NFs sem origem explicitada pelo portal.
- A rotina mensal apresenta saldo acumulado e o status de atualização do
  extrato, sem classificar o saldo histórico de R$ 69,99 como cobrança.
