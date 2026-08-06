# Task 1 report — cidade no detalhe do CT-e

## Status

DONE

## Arquivos alterados

- `frontend/src/freight-origin.test.jsx`
- `frontend/src/App.jsx`
- `.superpowers/sdd/2026-08-06-cidade-no-detalhe-cte/task-1-report.md`

## Implementação

- Exportado `FreightOriginFacts({ senderName, senderCnpj, origins })`.
- O card de origem no bloco `CT-e e cobrança` agora consome exclusivamente `detail.sender_name`, `detail.cte.sender_cnpj` e `detail.origins || []`.
- Cada origem cadastrada exibe seu CNPJ e usa `FreightOriginSummary`; portanto, cidade ausente continua exibindo `Cidade cadastral não identificada`, sem inferência.

## Evidência TDD

### RED

Comando:

```powershell
npm run test:ui -- freight-origin.test.jsx
```

Resultado: exit code 1, 1 teste falhou e 2 passaram. A falha esperada foi `Element type is invalid ... got: undefined`, porque `FreightOriginFacts` ainda não era exportado.

### GREEN

Comando:

```powershell
npm run test:ui -- freight-origin.test.jsx
```

Resultado: exit code 0, `1 passed` e `3 passed`.

## Verificação final

```powershell
npm run test:ui -- freight-origin.test.jsx
npm run build
```

Resultado: ambos exit code 0. O build Vite concluiu com `built in 1.01s`.

## Commit

`feat(freight): show registered origin city in CT-e detail`

## Preocupações

- A verificação automatizada ficou limitada ao teste de UI específico solicitado e ao build; a suíte completa não foi executada.
- Não houve alterações em backend, ERP, dados de origem, tarifas, sincronização ou regras de conciliação.
