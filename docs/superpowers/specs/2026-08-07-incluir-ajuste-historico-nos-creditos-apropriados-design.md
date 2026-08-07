# Ajustes históricos no total conciliado da rotina mensal

## Objetivo

Remover a inconsistência visual dos cards acumulados de créditos Ipiranga: se
um ajuste histórico auditado fecha parte do saldo, ele deve compor o valor
mostrado como `Créditos apropriados`.

## Regra

Para cada card acumulado, o valor exibido como identificado será:

`créditos apropriados do portal + ajustes históricos aprovados`.

O saldo efetivo continuará sendo calculado contra esse total conciliado. Os
créditos emitidos, alocados e residuais do portal permanecem valores distintos
e auditáveis: nenhum ajuste passa a ser apresentado como crédito da
distribuidora.

## Apresentação

Quando houver ajuste, o card explicará que o total de créditos apropriados o
inclui e exibirá, na mesma nota, quanto veio efetivamente do portal. Assim, a
unidade 001 poderá mostrar o total fechado sem ocultar a regularização
histórica aprovada.

## Escopo e segurança

- Aplicar a mesma regra a todos os cards acumulados com ajuste histórico.
- Não alterar compras, regras financeiras, eventos importados do portal,
  conciliações individuais ou dados do ERP.
- Preservar o ajuste em auditoria e a diferença exclusiva do portal para
  investigação técnica.

## Testes de aceite

- Um card com R$ 200,00 de crédito do portal e R$ 100,00 de ajuste mostra
  R$ 300,00 em `observed_value` e saldo efetivo zero para R$ 300,00 esperado.
- O total bruto e o residual da carteira do portal não se misturam ao ajuste.
- Um card sem ajuste mantém o valor identificado anterior.
