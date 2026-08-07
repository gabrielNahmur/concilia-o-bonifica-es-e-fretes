# Competência Ipiranga por emissão da NF

## Objetivo

Usar a data de emissão da NF (`MDCHP.Dt_Emis`) como competência contratual
para regras Ipiranga de crédito na distribuidora. A data de entrada no ERP
(`MDCHP.Dt_Entr`) continua registrada e exibida como informação operacional.

## Regra aprovada

- A compra pertence ao mês da emissão da NF; na ausência de emissão, usa-se a
  data de entrada como contingência.
- Para as unidades 003 e 004, o crédito identificado permanece sendo a soma
  das bonificações postecipadas emitidas no portal no mês calendário seguinte.
- A data em que o portal debita uma NF comprova uso de crédito e não altera a
  competência da compra que gerou a bonificação.

## Escopo

- Aplicar a base de emissão somente às regras `distributor_credit` da Ipiranga.
- Recalcular histórico sem escrever no ERP.
- Alinhar as notas exibidas no detalhe/auditoria da conciliação à mesma base
  usada pelo cálculo financeiro.

## Critérios de aceite

- NF emitida em 30/04 e entrada em 01/05, com 8.000 L e regra de R$ 0,06/L,
  gera R$ 480,00 em abril, não em maio.
- Crédito postecipado de R$ 480,00 emitido no portal em maio confirma abril.
- NF 3081382 da unidade 004, emitida em 29/06 e entrada em 30/06, continua em
  junho mesmo tendo débito exibido pelo portal em 02/07.
