# Desconto identificado em títulos pagos com atraso

## Objetivo

Exibir o desconto nativo encontrado no ERP para notas cujo título foi baixado após o vencimento, sem classificá-lo como bonificação contratual aplicável.

## Decisão

O valor contratual continuará inelegível quando a data de baixa registrada no ERP for posterior ao vencimento. A conciliação manterá `expected_value`, `observed_value` e `difference_value` em zero para esses casos, evitando que um desconto tardio seja contado como benefício válido.

O detalhe e a fila receberão um campo apenas informativo, `identified_discount_value`, obtido de `details.raw_discount_value`. A interface o exibirá como **Desconto identificado no ERP** junto da mensagem de atraso. O texto explicará que o valor foi lançado, mas não foi apropriado como bonificação por causa da data de baixa registrada.

## Restrições

- Não alterar dados do ERP nem regras de elegibilidade por vencimento.
- Não incluir o desconto tardio em totais de bonificação confirmada ou de valor identificado contratual.
- Preservar a prova MDCMP e a corroborção MLANF/6204 já existentes.
- Aplicar somente a itens com status `late_payment`.

## Verificação

- Um item atrasado com `raw_discount_value = 920` expõe `identified_discount_value = 920` na API.
- O item continua com status `late_payment`, valor observado contratual zero e diferença zero.
- A tela mostra o desconto como evidência informativa e o motivo da não apropriação.
