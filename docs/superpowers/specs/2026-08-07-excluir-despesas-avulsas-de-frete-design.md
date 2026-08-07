# Exclusão de despesas avulsas da conciliação de fretes

## Objetivo

Exibir na conciliação de fretes somente documentos que tenham vínculo comprovado com compras de combustível. Despesas de transporte registradas no ERP sem qualquer NF-e de combustível relacionada não devem inflar a fila, os cartões ou os indicadores.

## Decisão

Depois de executar os vínculos determinísticos já existentes, um documento suplementar (`source_kind = purchase_entry`) sem nenhuma compra de combustível resolvida será considerado fora do escopo da conciliação. O registro bruto do CT-e/entrada e suas referências continuarão preservados no banco; apenas sua `FreightReconciliation` será removida.

## Limites

- CT-es normais provenientes de `MCTe` continuam aparecendo mesmo quando houver divergência documental, pois carregam a referência declarada pelo transportador e exigem investigação.
- Registros suplementares que o algoritmo consiga associar a uma compra de combustível continuam aparecendo e sendo conciliados normalmente.
- Não haverá escrita no ERP, alteração de tarifa, nem remoção do documento bruto sincronizado.

## Aceite

- O CT-e 27 da unidade 013, classificado no ERP como despesa de frete sem compra e sem NF-e resolvida, não aparece mais na tela, nos cartões ou nos totais de frete.
- Um frete suplementar com compra de combustível vinculada permanece visível.
- Uma reconciliação antiga desse tipo é removida durante o próximo rebuild, evitando que continue aparecendo por dados materializados antes da regra.
