# Solicitações de informação no drawer de conciliação

## Objetivo

Tornar visível, no detalhe de cada conciliação, a solicitação interna registrada por “Pedir informação”, sem enviar e-mails, criar destinatários ou alterar qualquer valor financeiro.

## Decisão de interface

Será incluída uma seção não recolhida chamada **Solicitações de informação**, imediatamente após “Provas vinculadas” e antes das exceções. Ela só será exibida quando houver ao menos uma solicitação registrada para a conciliação aberta.

Cada solicitação exibirá:

- estado: **Aguardando resposta interna**;
- documento ou competência a que se refere;
- quem solicitou e data/hora;
- motivo curto;
- justificativa completa.

O bloco técnico “Dados técnicos e auditoria” continuará disponível apenas para administradores, mas os nomes internos de ações serão traduzidos para texto operacional, por exemplo `item_needs_information` para **Solicitação de informação registrada**.

## Fonte e integridade dos dados

Não haverá nova tabela ou nova chamada de escrita. A interface usará os dados já retornados em `detail.workspace.items`:

- `review_status = needs_information`;
- `reviewed_by`;
- `reviewed_at`;
- `review_notes`, no formato `[motivo] justificativa`.

O histórico existente em `detail.review.history` permanece inalterado e será somente traduzido na apresentação técnica.

## Critérios de aceite

1. Após registrar “Pedir informação” em uma NF, reabrir o drawer mostra uma seção visível com motivo, justificativa, administrador e data.
2. A seção não aparece em conciliações sem solicitação registrada.
3. Nenhum e-mail é disparado pela ação.
4. Valores esperado, identificado e diferença permanecem inalterados.
5. A auditoria não mostra o rótulo cru `item_needs_information`.
