# Filtro detalhado de situações da conciliação

## Objetivo

Substituir o filtro operacional amplo da fila de conciliação por situações financeiras claras, mantendo a seleção múltipla e sem alterar cálculos, evidências ou regras de bonificação.

## Situações visíveis

| Filtro | Critério |
| --- | --- |
| Pago maior | Valor identificado superior ao esperado. |
| Pago menor | Há valor identificado, mas inferior ao esperado. |
| Pendentes | Não há valor identificado ou a baixa/crédito ainda está aguardada. |
| Vencidos | Pendência financeira cujo vencimento já passou. |
| Confirmados | Evidência fecha a competência, incluindo confirmações automáticas e manuais. |
| Pago em atraso | Título baixado após o vencimento; o benefício não é apropriado como bonificação. |
| Em análise | Há prova, exceção ou vínculo que requer decisão humana. |
| Ajuste aprovado | Competência encerrada por ajuste gerencial/histórico auditado, sem crédito da companhia. |

Itens excluídos contratualmente ou sem aplicação financeira não aparecem no filtro operacional.

## Contrato de API

O endpoint `GET /api/reconciliations/work-queue` aceitará valores detalhados no parâmetro repetível `state`. O backend classificará cada linha antes da filtragem, preservando o campo operacional atual para compatibilidade.

## Interface

O seletor "Situações" apresentará os oito valores acima. A seleção padrão continuará mostrando pendências operacionais, equivalente à combinação Pendentes, Pago menor, Pago maior, Vencidos e Em análise. A seleção múltipla continuará permitindo combinar, por exemplo, Confirmados e Pago em atraso.

## Testes

- Cada situação filtra somente as linhas compatíveis.
- Itens com valor maior e menor são separados pelo sinal da diferença.
- Vencidos e pagamentos em atraso não são classificados como pendentes simples.
- Ajustes históricos permanecem auditáveis e aparecem apenas em Ajuste aprovado.
- A seleção padrão mantém a fila operacional atual.
