# Ocultar volume auxiliar do CT-e

## Objetivo

Remover do detalhe visual de conciliação de fretes o card **Volume auxiliar da carga**, evitando que a quantidade declarada pelo CT-e seja confundida com os litros comprovados por NF-e.

## Decisão

Será removido exclusivamente o elemento visual no bloco **CT-e e cobrança**. O indicador **Litros comprovados** continuará sendo a única medida de volume exibida para a conferência financeira.

## Preservação técnica

`cargo_liters` e os dados de `MCTe_Carga` continuarão sincronizados e disponíveis ao algoritmo interno de sugestão de NF-e. A API, o banco de dados, as regras de cálculo, os valores esperados e as conciliações existentes não serão alterados.

## Critério de aceite

Ao abrir qualquer detalhe de CT-e, o bloco **CT-e e cobrança** não apresenta o campo **Volume auxiliar da carga**. Os cartões de litros comprovados, NF-es, cobrança e tarifa permanecem inalterados.
