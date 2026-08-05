# Créditos apropriados em cartões cumulativos

## Objetivo

Fazer com que o cartão cumulativo de créditos Ipiranga concilie a bonificação
contratual contra o valor do crédito efetivamente apropriado às competências,
sem transformar saldo residual do portal em divergência financeira.

## Decisão aprovada

Para regras de crédito Ipiranga que usam o extrato como fonte primária, o
saldo efetivo será calculado por `crédito apropriado + ajuste histórico
aprovado`. Crédito bruto é a soma dos lançamentos do portal; crédito
apropriado é a soma das alocações auditáveis de evidências do portal para as
competências da própria regra. A diferença entre ambos é um saldo residual
informativo e não altera a situação da conciliação.

## Limites

- A regra é genérica para cartões cumulativos Ipiranga, não uma exceção da
  unidade 001.
- A unidade 004 mantém a política própria de crédito utilizado no portal.
- Ajustes históricos continuam separados dos créditos e não são criados nem
  modificados por este cálculo.
- Nenhum dado do ERP é escrito ou reclassificado.

## Resultado esperado

Um crédito de portal pode fechar uma ou mais competências e ainda deixar
saldo residual. Quando o total apropriado mais ajustes aprovados fechar o
esperado, o cartão fica automático. O residual permanece visível para
acompanhamento, mas não gera cobrança ou status “Em análise”.
