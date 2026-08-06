# Cidade cadastral no detalhe de CT-e

## Objetivo

Exibir a cidade e a UF cadastradas do fornecedor das NF-es conciliadas no bloco **CT-e e cobrança** do detalhe de frete.

## Decisão

O frontend aproveitará `detail.origins`, já retornado pela API de detalhe do frete. O card hoje chamado **Origem** passará a apresentar, abaixo do fornecedor e CNPJ, a mensagem `Origem cadastral: Cidade/UF`.

Quando um CT-e tiver mais de uma origem de NF-e resolvida, o card exibirá todas as cidades cadastradas, uma por linha, identificadas pelo respectivo CNPJ. Isso evita atribuir uma única cidade a documentos com fornecedores distintos.

## Limites de interpretação

Cidade e UF são dados cadastrais consultados pelo CNPJ do fornecedor. A interface não os apresentará como prova do local físico de carregamento, retirada ou base operacional.

## Fallback

Quando uma NF-e conciliada ainda não possuir cidade cadastrada, será exibido `Cidade cadastral não identificada`; nenhum município será inferido.
