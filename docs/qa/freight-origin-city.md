# QA: origem cadastral de frete

Execute este roteiro somente depois de a migração, os testes do backend e o build do frontend terem passado. Este recurso consulta apenas o cadastro público e grava o registro local de origem; ele não lê nem grava no ERP, não modifica `FreightRate` e não recalcula conciliações, valores ou status.

## Sequência controlada

1. Registre uma fotografia **antes** (contagem e soma) de `freight_rates` e `freight_reconciliations`. Preserve o resultado para comparação:

   ```bash
   docker exec -i contracts_app python - <<'PY'
   from sqlalchemy import func, select
   from app.database import SessionLocal
   from app.models import FreightRate, FreightReconciliation

   with SessionLocal() as db:
       print({
           "freight_rates": db.scalar(select(func.count()).select_from(FreightRate)),
           "reconciliations": db.scalar(select(func.count()).select_from(FreightReconciliation)),
           "expected": str(db.scalar(select(func.coalesce(func.sum(FreightReconciliation.expected_value), 0)))),
           "charged": str(db.scalar(select(func.coalesce(func.sum(FreightReconciliation.charged_value), 0)))),
           "difference": str(db.scalar(select(func.coalesce(func.sum(FreightReconciliation.difference_value), 0)))),
       })
   PY
   ```

2. Aplique a migração e atualize somente os containers da aplicação:

   ```bash
   docker compose -f docker-compose.prod.yml run --rm contracts_app alembic -c alembic.ini upgrade head
   docker compose -f docker-compose.prod.yml up -d --build contracts_app contracts_sync
   ```

3. Execute a primeira carga controlada dentro de `contracts_app`:

   ```bash
   docker exec contracts_app python -m app.scripts.backfill_freight_origins
   ```

   Registre os totais impressos de CNPJs consultados, enriquecidos e pendentes. Há no máximo três consultas públicas por execução. Se ainda houver pendências, aguarde **um minuto** antes de executar novamente; repita somente enquanto houver pendências.

4. Na consulta de fornecedores resolvidos, selecione os cinco CNPJs atuais retornados pela origem de frete e confirme visualmente, em cada um, razão social, cidade e UF cadastradas. Em Tarifas, confira o rótulo `Origem cadastral: Cidade/UF`; abra um CT-e associado e confirme o mesmo rótulo no detalhe. A cidade/UF é referência cadastral, não prova de coleta física.

5. Repita exatamente a fotografia do passo 1 e compare, campo a campo: a quantidade de tarifas, a quantidade de conciliações e as somas `expected`, `charged` e `difference` devem permanecer idênticas. Anexe as duas saídas ao registro de release.

## Exceções e rollback

Uma divergência de dados não-UTC ou uma disputa sobre coleta física deve ser investigada com XML fiscal e evidência operacional; não substitua nem force o resultado pelo registro cadastral. Se for necessário recuar antes de depender dos dados gravados, use o downgrade Alembic aprovado. Depois que os registros forem usados, desabilite a exibição e mantenha o registro local inofensivo; não use rollback para apagar evidência operacional.
