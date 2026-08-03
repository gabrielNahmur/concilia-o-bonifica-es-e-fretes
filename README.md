# Contratos GBI

Aplicação independente para acompanhamento de volumes contratuais e conciliação de bonificações da rede GBI.

## Arquitetura

- `contracts_app`: FastAPI, autenticação própria, SPA React compilada e geração de PDF.
- `contracts_sync`: worker APScheduler em rede do host; único serviço com credenciais do ERP.
- PostgreSQL: banco `contracts_gbi` e usuário `contracts_app` no servidor PostgreSQL já existente.
- ERP: snapshot local em PostgreSQL, com leitura de `MDCHP`, `MDCIP`, `MPreNota`, `MDCDP`, `MLANF`, `MExtratoBancoLanc`, `mlanc` e das evidências de CT-e (`MCTe`, `MCTe_Integracao`, `MCTe_Pessoa`, `MCTe_Docum` e `MCTe_Carga`).

O frontend e a API nunca acessam o SQL Server. A sobreposição incremental é de sete dias; compras canceladas são removidas do snapshot. Se a última sincronização válida tiver mais de 24 horas, a interface exibe alerta e o envio de relatórios é bloqueado.

A conciliação de fretes recarrega o horizonte configurado em `ERP_FREIGHT_SYNC_START_DATE` (padrão `2025-08-01`) para capturar retificações e cancelamentos. O volume financeiro é sempre calculado pelos itens em litros da NF-e; `MCTe_Carga` é preservada apenas como conferência auxiliar. Nenhuma revisão ou tarifa cadastrada na aplicação escreve no ERP.

## Desenvolvimento

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m app.scripts.init_db
uvicorn app.main:app --reload
```

Em outro terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Testes e compilação:

```powershell
cd backend
ruff check app tests
pytest -q
cd ..\frontend
npm run build
```

## Produção

1. Copiar `.env.example` para `.env` e preencher apenas no servidor.
2. Criar o banco dedicado, construir a imagem e executar `alembic -c alembic.ini upgrade head`.
3. Rodar `python -m app.scripts.init_db` para referência e administrador inicial.
4. Subir `contracts_app`, executar o backfill no worker e conferir os totais de controle.
5. Subir `contracts_sync`; ativar `MONTHLY_REPORTS_ENABLED=true` somente após cadastrar destinatários.
6. Usar `deploy/nginx-contracts.http.conf` antes do certificado e `deploy/nginx-contracts.https.conf` após o Certbot.

O login `sa` é temporário e fica somente no `.env` do worker. O script `backend/scripts/create_erp_readonly_login.sql` cria o substituto de leitura restrita.

## Operação

- Healthcheck: `/api/health`.
- Sincronização manual: Administração → Sincronização.
- Relatórios: geração sob demanda ou envio automático no dia 5.
- Backups: banco PostgreSQL e volume de PDFs diariamente às 02:15, com manifesto SHA-256 e retenção local de 14 dias.
- Cópia externa: configure `BACKUP_REMOTE` com um destino `rclone` e `BACKUP_REMOTE_REQUIRED=true` para impedir sucesso sem cópia externa.
- Restauração: use `/usr/local/sbin/restore-contracts-gbi --yes ARQUIVO.dump ARQUIVO-relatorios.tar.gz ARQUIVO.sha256`; o script cria um backup preventivo, valida estrutura e hashes e confirma o healthcheck.
- Logs Docker: rotação de 10 MB, três arquivos por serviço.

## Catálogo completo do ERP (somente leitura)

O agente `app.scripts.map_erp_catalog` inventaria todas as tabelas e views, colunas, PKs, FKs, índices, objetos SQL, dependências e chaves de ligação inferidas. Ele classifica candidatas a pagamentos, lançamentos, recebimentos, depósitos, extratos bancários e áreas correlatas sem ler dados de negócio ou alterar o ERP.

Execute a partir de `backend`, mantendo a senha fora do repositório:

```powershell
$env:ERP_CATALOG_PASSWORD = "<senha-do-erp>"
python -m app.scripts.map_erp_catalog `
  --server '<servidor\instancia>' --database '<banco>' --user '<usuario>' `
  --password-env ERP_CATALOG_PASSWORD
```

Os artefatos ficam em `output/investigation/erp_catalog/`: `catalogo_erp.sqlite` é o inventário completo consultável; `candidatos_financeiros.csv`, `resumo_catalogo_erp.json` e `relatorio_catalogo_erp.md` são as visões de triagem.

Para uma instância nomeada acessada diretamente, o host do agente precisa ter um driver ODBC do SQL Server e o pacote Python `pyodbc`; conexões já expostas como `servidor:porta` continuam usando `pymssql` do projeto.

## Política de conciliação

- A confirmação automática exige igualdade em centavos e uma cadeia documental determinística e exclusiva.
- Desconto em boleto: nota, título único, baixa MDCMP, movimento `D` e 6204 de mesmo valor.
- Depósito: valor exato com identidade da companhia no extrato ou par contábil exato banco/conta a receber.
- Crédito em distribuidora, adicional S10, rateio do guarda-chuva e qualquer aproximação permanecem para revisão humana.
- Evidências são normalizadas e alocadas por item; uma evidência apropriada acima do valor de origem abre exceção crítica.
- Alterações posteriores no ERP invalidam revisões afetadas e reabrem a conferência.
- A Central de exceções separa título sem baixa, pago sem desconto, desconto parcial, origem não comprovada, cadeia incompleta e valor vencido.

### Fretes

- A identidade técnica é `MCTe.Cd_Cte`; o número impresso do CT-e nunca é usado como chave.
- O vínculo direto exige chave da NF-e, unidade e data coerentes, e uma mesma NF-e não pode ser consumida por dois CT-es.
- Chaves ausentes, duplicadas ou de outra unidade geram sugestão, nunca substituição automática. A confirmação administrativa exige observação e `fingerprint` atual.
- A tarifa efetiva segue a precedência transportador + origem + unidade, transportador + unidade, transportador + origem e transportador global.
- Cancelados não integram os totais normais; cancelado com título ou pagamento gera alerta crítico.
