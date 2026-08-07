# Créditos Ipiranga no mês seguinte - Unidades 003 e 004

## Objetivo

Substituir a atribuição por valor exato, carteira cronológica ou ciclo de NFs
das regras `003 / Ipiranga / crédito na distribuidora` e
`004 / Ipiranga / crédito na distribuidora` por uma competência mensal simples
e auditável.

## Regra aprovada

Para cada competência `M`, o valor identificado é a soma integral dos eventos
de `Bonificação Postecipada` emitidos no extrato Ipiranga durante o mês
calendário `M + 1`.

Exemplo: as compras elegíveis de junho de 2026 geram o valor esperado de
junho; os créditos postecipados registrados entre 01 e 31 de julho compõem o
valor identificado de junho. Não há rateio, FIFO, aproximação de valor ou
inferência de NF originadora.

## Escopo e evidência

- A regra vale somente para 003 e 004, ambas Ipiranga e crédito na
  distribuidora.
- O extrato Ipiranga continua sendo a única prova financeira primária.
- Cada crédito exibido preserva data, valor, identificador do evento e o
  motivo explícito da associação mensal.
- Um crédito maior ou menor que o esperado permanece integralmente mostrado;
  a diferença apurada é real e não é ajustada automaticamente.
- As evidências históricas de uso de crédito da 004 e quaisquer ajustes
  auditados permanecem armazenados, mas deixam de definir a competência
  operacional mensal.
- Não há escrita no ERP.

## Situações

- Valor do extrato igual ao esperado: confirmado automaticamente por extrato
  Ipiranga.
- Valor menor ou maior: divergente (ou pendente antes do vencimento), com a
  diferença financeira real e os créditos do mês seguinte visíveis.
- Sem crédito no mês seguinte: aguarda o extrato se a competência ainda está
  no prazo; depois do prazo, permanece pendente de comprovação externa.

## Testes de aceite

- 003, abril/2026: R$ 5.100,00 esperado e crédito de maio de R$ 5.580,00
  resultam em R$ 5.580,00 identificado e diferença de -R$ 480,00.
- 003, junho/2026: R$ 5.400,00 esperado e crédito de julho de R$ 5.400,00
  fecham automaticamente.
- 004, junho/2026: R$ 7.700,00 esperado e crédito de julho de R$ 7.350,00
  mostram diferença de R$ 350,00 sem tentar redistribuir o crédito.
- Um crédito de julho não pode entrar na competência de julho nem ser usado
  novamente pela competência de maio.
