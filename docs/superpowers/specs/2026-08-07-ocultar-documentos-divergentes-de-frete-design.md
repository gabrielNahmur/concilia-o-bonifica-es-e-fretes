# Ocultação de documentos divergentes na conciliação de fretes

## Objetivo

Retirar da operação diária os CT-es com status `document_mismatch`, pois são inconsistências de lançamento que podem ser ajustadas posteriormente e hoje geram ruído nos indicadores de frete.

## Decisão

O seletor compartilhado de reconciliações de frete excluirá `document_mismatch`. Assim, lista, busca, cards, totais, paginação e exportações que utilizam o seletor deixam de incluir esses casos.

Os registros materializados, CT-es, referências de NF-e e auditoria não serão apagados. Se uma sincronização posterior resolver a documentação e alterar o status para um caso comparável, o registro reaparecerá automaticamente.

## Limites

- A regra aplica-se apenas ao módulo de fretes.
- Não modifica regras de tarifa, valores cobrados, compras de combustível ou o ERP.
- Mantém o acesso técnico/auditável ao registro pelo identificador, caso seja necessário investigá-lo posteriormente.

## Aceite

- Nenhum `document_mismatch` aparece na tabela, nos cards ou no resumo de fretes.
- O total de fretes e valores do painel considera apenas registros operacionais.
- Um registro `correct` continua aparecendo normalmente.
- O registro divergente continua existente no banco e volta ao painel se seu status mudar após uma sincronização.
