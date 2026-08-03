"""Build a complete, read-only SQL Server catalog for the ERP.

The mapper reads only SQL Server ``sys`` catalogs and metadata DMVs. It never
queries business rows, writes to the ERP, or stores credentials in its output.
The normalized SQLite result is deliberately used instead of a huge flat JSON:
this database contains more than 25,000 tables and 700,000 columns.

From ``backend`` in PowerShell::

    $env:ERP_CATALOG_PASSWORD = "<senha>"
    python -m app.scripts.map_erp_catalog --server 'host\\instancia' \
      --database smb001 --user sa --password-env ERP_CATALOG_PASSWORD
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.config import get_settings


CATALOG_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
CREATE TABLE database_info (
 captured_at TEXT NOT NULL, database_name TEXT, server_name TEXT, sql_version TEXT,
 login_name TEXT, collation_name TEXT, recovery_model TEXT, compatibility_level INTEGER
);
CREATE TABLE tables (
 object_id INTEGER PRIMARY KEY, schema_name TEXT NOT NULL, table_name TEXT NOT NULL,
 create_date TEXT, modify_date TEXT, approximate_rows INTEGER NOT NULL DEFAULT 0,
 reserved_mb REAL NOT NULL DEFAULT 0, column_count INTEGER NOT NULL DEFAULT 0,
 index_count INTEGER NOT NULL DEFAULT 0, foreign_key_count INTEGER NOT NULL DEFAULT 0,
 user_reads INTEGER NOT NULL DEFAULT 0, user_writes INTEGER NOT NULL DEFAULT 0,
 last_user_read TEXT, last_user_write TEXT, activity_status TEXT NOT NULL,
 lifecycle_hint TEXT NOT NULL
);
CREATE INDEX idx_tables_name ON tables(schema_name, table_name);
CREATE INDEX idx_tables_rows ON tables(approximate_rows DESC);
CREATE INDEX idx_tables_activity ON tables(activity_status);
CREATE TABLE columns (
 object_id INTEGER NOT NULL, schema_name TEXT NOT NULL, object_name TEXT NOT NULL,
 object_type TEXT NOT NULL, column_id INTEGER NOT NULL, column_name TEXT NOT NULL,
 data_type TEXT NOT NULL, max_length INTEGER, numeric_precision INTEGER,
 numeric_scale INTEGER, is_nullable INTEGER NOT NULL, is_identity INTEGER NOT NULL,
 identity_seed TEXT, identity_increment TEXT, is_computed INTEGER NOT NULL,
 computed_definition TEXT, default_definition TEXT, collation_name TEXT,
 PRIMARY KEY (object_id, column_id)
);
CREATE INDEX idx_columns_name ON columns(column_name);
CREATE INDEX idx_columns_object ON columns(object_id);
CREATE TABLE objects (
 object_id INTEGER PRIMARY KEY, schema_name TEXT NOT NULL, object_name TEXT NOT NULL,
 object_type TEXT NOT NULL, object_type_desc TEXT NOT NULL, create_date TEXT,
 modify_date TEXT, definition_length INTEGER, is_encrypted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_objects_name ON objects(schema_name, object_name);
CREATE TABLE key_constraints (
 object_id INTEGER NOT NULL, constraint_name TEXT NOT NULL, constraint_type TEXT NOT NULL,
 index_id INTEGER, key_ordinal INTEGER NOT NULL, column_name TEXT NOT NULL,
 is_descending INTEGER NOT NULL, PRIMARY KEY (object_id, constraint_name, key_ordinal)
);
CREATE TABLE foreign_keys (
 foreign_key_name TEXT NOT NULL, parent_object_id INTEGER NOT NULL, parent_schema TEXT NOT NULL,
 parent_table TEXT NOT NULL, parent_column TEXT NOT NULL, referenced_object_id INTEGER NOT NULL,
 referenced_schema TEXT NOT NULL, referenced_table TEXT NOT NULL, referenced_column TEXT NOT NULL,
 constraint_column_id INTEGER NOT NULL, is_disabled INTEGER NOT NULL, is_not_trusted INTEGER NOT NULL,
 delete_action TEXT, update_action TEXT, PRIMARY KEY (foreign_key_name, constraint_column_id)
);
CREATE INDEX idx_foreign_keys_parent ON foreign_keys(parent_object_id);
CREATE INDEX idx_foreign_keys_reference ON foreign_keys(referenced_object_id);
CREATE TABLE indexes (
 object_id INTEGER NOT NULL, index_id INTEGER NOT NULL, index_name TEXT NOT NULL,
 index_type TEXT NOT NULL, is_unique INTEGER NOT NULL, is_primary_key INTEGER NOT NULL,
 is_unique_constraint INTEGER NOT NULL, is_disabled INTEGER NOT NULL, has_filter INTEGER NOT NULL,
 filter_definition TEXT, key_ordinal INTEGER NOT NULL, is_included_column INTEGER NOT NULL,
 is_descending INTEGER NOT NULL, column_name TEXT NOT NULL,
 PRIMARY KEY (object_id, index_id, key_ordinal, column_name)
);
CREATE INDEX idx_indexes_object ON indexes(object_id);
CREATE TABLE dependencies (
 referencing_object_id INTEGER NOT NULL, referencing_schema TEXT, referencing_object TEXT,
 referencing_type TEXT, referenced_server TEXT, referenced_database TEXT,
 referenced_schema TEXT, referenced_entity TEXT, referenced_id INTEGER,
 is_ambiguous INTEGER NOT NULL,
 PRIMARY KEY (referencing_object_id, referenced_server, referenced_database,
              referenced_schema, referenced_entity, referenced_id)
);
CREATE INDEX idx_dependencies_reference ON dependencies(referenced_entity);
CREATE TABLE inferred_keys (
 column_name TEXT PRIMARY KEY, table_count INTEGER NOT NULL,
 non_empty_table_count INTEGER NOT NULL, financial_candidate_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE classifications (
 object_id INTEGER NOT NULL, schema_name TEXT NOT NULL, table_name TEXT NOT NULL,
 category TEXT NOT NULL, score INTEGER NOT NULL, evidence TEXT NOT NULL,
 bridge_keys TEXT NOT NULL, PRIMARY KEY (object_id, category)
);
CREATE INDEX idx_classifications_category ON classifications(category, score DESC);
CREATE INDEX idx_classifications_object ON classifications(object_id);
"""

# These seeds improve ranking for the data paths already used by this project.
# All other tables are classified from their names and full column definitions.
SEED_TABLES: dict[str, tuple[str, ...]] = {
    "MDCHP": ("compras_e_fornecedores", "notas_fiscais"),
    "MDCIP": ("compras_e_fornecedores",),
    "MPRENOTA": ("compras_e_fornecedores", "notas_fiscais"),
    "MDCDP": ("contas_a_pagar", "pagamentos"),
    "MDCMP": ("contas_a_pagar", "pagamentos", "lancamentos_financeiros"),
    "MLANF": ("lancamentos_financeiros",),
    "MLANC": ("lancamentos_financeiros",),
    "MEXTRATOBANCOLANC": ("extratos_bancarios", "depositos"),
}
CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "pagamentos": ("PAGAMENTO", "PAGTO", "BAIXA", "LIQUIDAC", "QUITAC", "REMESSA", "RETORNO", "BOLETO", "CHEQUE"),
    "contas_a_pagar": ("CONTAPAGAR", "CONTASAPAGAR", "CREDOR", "FORNECEDOR", "TITPAG", "OBRIGAC", "DUPLICPAG", "VENCIMENTO"),
    "recebimentos": ("RECEBIMENTO", "RECEBER", "COBRANCA", "ARRECADACAO", "BAIXAREC", "LIQUIDREC", "TITREC", "DUPLICREC"),
    "contas_a_receber": ("CONTARECEBER", "CONTASARECEBER", "DEVEDOR", "COBRANCA", "TITREC", "FATURA", "DUPLICATA", "RECEBIMENTO"),
    "lancamentos_financeiros": ("LANCAMENTO", "LANCFIN", "CONTABIL", "DIARIO", "PARTIDA", "DEBITO", "CREDITO", "HISTORICO"),
    "depositos": ("DEPOSITO", "TRANSFERENCIA", "COMPENSACAO", "NUMDEPOSITO", "COMPROVANTE", "PIX", "TED"),
    "extratos_bancarios": ("EXTRATO", "BANCO", "AGENCIA", "CONCILIACAO", "SALDOBANCARIO", "MOVBANCARIO"),
    "cartoes_e_tef": ("CARTAO", "TEF", "ADQUIRENTE", "NSU", "AUTORIZACAO", "BANDEIRA", "VOUCHER"),
    "notas_fiscais": ("NOTAFISCAL", "NFE", "CHAVEACESSO", "DANFE", "CTE", "MDFE"),
    "compras_e_fornecedores": ("COMPRA", "FORNECEDOR", "PEDIDO", "COTACAO", "PRENOTA"),
    "contratos_e_bonificacoes": ("CONTRATO", "BONIFIC", "BONUS", "VERBA", "PREMIO", "INCENT", "REBATE", "ACORDO"),
}
BRIDGE_COLUMN_PATTERN = re.compile(
    r"^(CD|ID|SQ|NR|NUM|COD)[A-Z0-9_]*$|(DOCUMENT|TITUL|NOTA|LANC|PESSOA|ESTAB|CONTA|BANCO|AGENC|PEDIDO|ENTRADA|BAIXA|OPERAC|CHAVE|NOSSONUMERO|NSU)"
)
TEMPORARY_PATTERN = re.compile(r"^(TMP|TEMP|#)|(_TMP|_TEMP|_AUX|_BACKUP|_BK)$")
HISTORY_PATTERN = re.compile(r"(HIST|LOG|AUDIT|AUDI|ARQ|BACKUP|BKP|RESTORE|TAREFA|COPIA|_BK$)")


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _norm(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(item for item in value if not unicodedata.combining(item))
    return re.sub(r"[^A-Z0-9_]", "", value.upper())


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _connect(server: str, database: str, user: str, password: str):
    """Use ODBC for a named instance; retain pymssql for existing host:port use."""
    errors: list[str] = []
    if "\\" in server:
        try:
            import pyodbc  # type: ignore[import-not-found]

            driver = next((item for item in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server") if item in pyodbc.drivers()), None)
            if not driver:
                raise RuntimeError("driver ODBC do SQL Server ausente")
            return pyodbc.connect(
                f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};UID={user};PWD={password};"
                "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=20;Application Name=ContratosCatalogMapper",
                autocommit=False,
            )
        except Exception as error:  # fallback is host-specific
            errors.append(f"ODBC: {error}")
    try:
        import pymssql

        host, separator, port = server.rpartition(":")
        options: dict[str, Any] = {"server": host if separator else server, "user": user, "password": password, "database": database, "login_timeout": 20, "timeout": 120, "as_dict": True, "charset": "UTF-8"}
        if separator and port.isdecimal():
            options["port"] = int(port)
        return pymssql.connect(**options)
    except Exception as error:
        errors.append(f"pymssql: {error}")
        raise RuntimeError("Falha ao conectar ao SQL Server. " + " | ".join(errors)) from error


def _rows(cursor, query: str, batch_size: int = 5_000) -> Iterator[dict[str, Any]]:
    cursor.execute(query)
    names = [item[0] for item in cursor.description]
    while batch := cursor.fetchmany(batch_size):
        for row in batch:
            if isinstance(row, dict):
                yield {key: _plain(value) for key, value in row.items()}
            else:
                yield {names[index]: _plain(value) for index, value in enumerate(row)}


def _insert(db: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> int:
    statement = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})"
    batch: list[tuple[Any, ...]] = []
    count = 0
    for row in rows:
        batch.append(tuple(row.get(field) for field in fields))
        if len(batch) >= 5_000:
            db.executemany(statement, batch)
            count += len(batch)
            batch.clear()
    if batch:
        db.executemany(statement, batch)
        count += len(batch)
    return count


def _lifecycle(name: str) -> str:
    normalized = _norm(name)
    if TEMPORARY_PATTERN.search(normalized):
        return "temporaria_ou_staging"
    if HISTORY_PATTERN.search(normalized):
        return "historico_ou_auditoria"
    return "operacional_ou_cadastro"


def _tables(cursor) -> Iterator[dict[str, Any]]:
    query = """
    WITH row_counts AS (
      SELECT object_id, SUM(row_count) AS approximate_rows, SUM(reserved_page_count)*8.0/1024 AS reserved_mb
      FROM sys.dm_db_partition_stats WHERE index_id IN (0,1) GROUP BY object_id
    ), index_counts AS (
      SELECT object_id, COUNT(*) AS index_count FROM sys.indexes WHERE index_id>0 GROUP BY object_id
    ), fk_counts AS (
      SELECT object_id, COUNT(*) AS foreign_key_count FROM (
        SELECT parent_object_id AS object_id FROM sys.foreign_keys
        UNION ALL SELECT referenced_object_id FROM sys.foreign_keys
      ) x GROUP BY object_id
    ), usage_counts AS (
      SELECT object_id, SUM(user_seeks+user_scans+user_lookups) AS user_reads, SUM(user_updates) AS user_writes,
             MAX(COALESCE(last_user_seek,last_user_scan,last_user_lookup)) AS last_user_read, MAX(last_user_update) AS last_user_write
      FROM sys.dm_db_index_usage_stats WHERE database_id=DB_ID() GROUP BY object_id
    )
    SELECT t.object_id, s.name AS schema_name, t.name AS table_name, t.create_date, t.modify_date,
           COALESCE(rc.approximate_rows,0) AS approximate_rows, COALESCE(rc.reserved_mb,0) AS reserved_mb,
           (SELECT COUNT(*) FROM sys.columns c WHERE c.object_id=t.object_id) AS column_count,
           COALESCE(ic.index_count,0) AS index_count, COALESCE(fc.foreign_key_count,0) AS foreign_key_count,
           COALESCE(uc.user_reads,0) AS user_reads, COALESCE(uc.user_writes,0) AS user_writes,
           uc.last_user_read, uc.last_user_write
    FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
    LEFT JOIN row_counts rc ON rc.object_id=t.object_id LEFT JOIN index_counts ic ON ic.object_id=t.object_id
    LEFT JOIN fk_counts fc ON fc.object_id=t.object_id LEFT JOIN usage_counts uc ON uc.object_id=t.object_id
    WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name
    """
    for row in _rows(cursor, query):
        if _number(row["approximate_rows"]) == 0:
            row["activity_status"] = "vazia"
        elif _number(row["user_reads"]) or _number(row["user_writes"]):
            row["activity_status"] = "usada_desde_inicio_sqlserver"
        else:
            row["activity_status"] = "preenchida_sem_contador_de_uso"
        row["lifecycle_hint"] = _lifecycle(str(row["table_name"]))
        yield row


def _classify(table_name: str, columns: list[str]) -> list[tuple[str, int, str, str]]:
    name = _norm(table_name)
    all_columns = " ".join(_norm(column) for column in columns)
    seeds = set(SEED_TABLES.get(name, ()))
    bridges = ", ".join(sorted(column for column in columns if BRIDGE_COLUMN_PATTERN.search(_norm(column)))[:15])
    output: list[tuple[str, int, str, str]] = []
    for category, terms in CATEGORY_TERMS.items():
        name_hits = [term for term in terms if term in name]
        column_hits = [term for term in terms if term in all_columns]
        # A lone column named, for example, Cd_Entrada or Documento is common
        # across the ERP and is not enough to identify a business family.
        if not name_hits and len(column_hits) < 2 and category not in seeds:
            continue
        score = min(36, 12 * len(name_hits) + 3 * len(column_hits))
        evidence: list[str] = []
        if name_hits:
            evidence.append("nome: " + ", ".join(name_hits[:4]))
        if column_hits:
            evidence.append("colunas: " + ", ".join(column_hits[:6]))
        if category in seeds:
            score = max(score, 30)
            evidence.append("tabela ERP conhecida")
        if score:
            output.append((category, score, "; ".join(evidence), bridges))
    return output


def _infer_and_classify(db: sqlite3.Connection) -> int:
    db.execute("""
      INSERT INTO inferred_keys (column_name, table_count, non_empty_table_count)
      SELECT c.column_name, COUNT(DISTINCT c.object_id),
             COUNT(DISTINCT CASE WHEN t.approximate_rows>0 THEN c.object_id END)
      FROM columns c JOIN tables t ON t.object_id=c.object_id
      WHERE c.object_type='U' AND (
        c.column_name LIKE 'Cd[_]%' ESCAPE '\\' OR c.column_name LIKE 'Id[_]%' ESCAPE '\\' OR c.column_name LIKE 'Sq[_]%' ESCAPE '\\'
        OR c.column_name LIKE '%Docum%' OR c.column_name LIKE '%Tit%' OR c.column_name LIKE '%Lanc%'
        OR c.column_name LIKE '%Nota%' OR c.column_name LIKE '%Pessoa%' OR c.column_name LIKE '%Conta%'
        OR c.column_name LIKE '%Banco%' OR c.column_name LIKE '%Estab%'
      ) GROUP BY c.column_name HAVING COUNT(DISTINCT c.object_id)>=2
    """)
    output: list[tuple[Any, ...]] = []
    for row in db.execute("""
      SELECT t.object_id,t.schema_name,t.table_name,GROUP_CONCAT(c.column_name,char(31)) AS cols
      FROM tables t LEFT JOIN columns c ON c.object_id=t.object_id
      GROUP BY t.object_id,t.schema_name,t.table_name ORDER BY t.object_id
    """):
        columns = (row[3] or "").split(chr(31)) if row[3] else []
        for category, score, evidence, bridges in _classify(row[2], columns):
            output.append((row[0], row[1], row[2], category, score, evidence, bridges))
    db.executemany("INSERT INTO classifications (object_id,schema_name,table_name,category,score,evidence,bridge_keys) VALUES (?,?,?,?,?,?,?)", output)
    db.execute("""
      UPDATE inferred_keys SET financial_candidate_count=(
        SELECT COUNT(DISTINCT c.object_id) FROM columns c JOIN classifications f ON f.object_id=c.object_id
        WHERE c.column_name=inferred_keys.column_name
      )
    """)
    return len(output)


def _capture(cursor, catalog_path: Path, server: str) -> tuple[dict[str, int], str]:
    if catalog_path.exists():
        catalog_path.unlink()
    db = sqlite3.connect(catalog_path)
    db.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        db.executescript(CATALOG_SCHEMA)
        info = next(_rows(cursor, """
          SELECT DB_NAME() AS database_name, @@VERSION AS sql_version, SUSER_SNAME() AS login_name,
                 DATABASEPROPERTYEX(DB_NAME(),'Collation') AS collation_name,
                 recovery_model_desc AS recovery_model, compatibility_level
          FROM sys.databases WHERE database_id=DB_ID()
        """))
        info.update(captured_at=captured_at, server_name=server)
        counts["database_info"] = _insert(db, "database_info", [info], ("captured_at","database_name","server_name","sql_version","login_name","collation_name","recovery_model","compatibility_level"))
        counts["tables"] = _insert(db, "tables", _tables(cursor), ("object_id","schema_name","table_name","create_date","modify_date","approximate_rows","reserved_mb","column_count","index_count","foreign_key_count","user_reads","user_writes","last_user_read","last_user_write","activity_status","lifecycle_hint"))
        counts["columns"] = _insert(db, "columns", _rows(cursor, """
          SELECT o.object_id,s.name AS schema_name,o.name AS object_name,RTRIM(o.type) AS object_type,c.column_id,c.name AS column_name,
                 ty.name AS data_type,c.max_length,c.precision AS numeric_precision,c.scale AS numeric_scale,c.is_nullable,c.is_identity,
                 CONVERT(varchar(100),ic.seed_value) AS identity_seed,CONVERT(varchar(100),ic.increment_value) AS identity_increment,
                 c.is_computed,cc.definition AS computed_definition,dc.definition AS default_definition,c.collation_name
          FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id JOIN sys.columns c ON c.object_id=o.object_id
          JOIN sys.types ty ON ty.user_type_id=c.user_type_id LEFT JOIN sys.identity_columns ic ON ic.object_id=c.object_id AND ic.column_id=c.column_id
          LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
          LEFT JOIN sys.default_constraints dc ON dc.parent_object_id=c.object_id AND dc.parent_column_id=c.column_id
          WHERE o.is_ms_shipped=0 AND o.type IN ('U','V') ORDER BY o.object_id,c.column_id
        """), ("object_id","schema_name","object_name","object_type","column_id","column_name","data_type","max_length","numeric_precision","numeric_scale","is_nullable","is_identity","identity_seed","identity_increment","is_computed","computed_definition","default_definition","collation_name"))
        counts["objects"] = _insert(db, "objects", _rows(cursor, """
          SELECT o.object_id,s.name AS schema_name,o.name AS object_name,RTRIM(o.type) AS object_type,o.type_desc AS object_type_desc,
                 o.create_date,o.modify_date,LEN(m.definition) AS definition_length,
                 CASE WHEN m.definition IS NULL AND o.type IN ('P','V','FN','IF','TF','TR') THEN 1 ELSE 0 END AS is_encrypted
          FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id
          WHERE o.is_ms_shipped=0 ORDER BY o.type_desc,s.name,o.name
        """), ("object_id","schema_name","object_name","object_type","object_type_desc","create_date","modify_date","definition_length","is_encrypted"))
        counts["key_constraints"] = _insert(db, "key_constraints", _rows(cursor, """
          SELECT kc.parent_object_id AS object_id,kc.name AS constraint_name,kc.type_desc AS constraint_type,kc.unique_index_id AS index_id,
                 ic.key_ordinal,c.name AS column_name,ic.is_descending_key AS is_descending
          FROM sys.key_constraints kc JOIN sys.index_columns ic ON ic.object_id=kc.parent_object_id AND ic.index_id=kc.unique_index_id
          JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE ic.key_ordinal>0
          ORDER BY kc.parent_object_id,kc.name,ic.key_ordinal
        """), ("object_id","constraint_name","constraint_type","index_id","key_ordinal","column_name","is_descending"))
        counts["foreign_keys"] = _insert(db, "foreign_keys", _rows(cursor, """
          SELECT fk.name AS foreign_key_name,fk.parent_object_id,ps.name AS parent_schema,pt.name AS parent_table,pc.name AS parent_column,
                 fk.referenced_object_id,rs.name AS referenced_schema,rt.name AS referenced_table,rc.name AS referenced_column,
                 fkc.constraint_column_id,fk.is_disabled,fk.is_not_trusted,fk.delete_referential_action_desc AS delete_action,fk.update_referential_action_desc AS update_action
          FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
          JOIN sys.tables pt ON pt.object_id=fk.parent_object_id JOIN sys.schemas ps ON ps.schema_id=pt.schema_id
          JOIN sys.columns pc ON pc.object_id=fkc.parent_object_id AND pc.column_id=fkc.parent_column_id
          JOIN sys.tables rt ON rt.object_id=fk.referenced_object_id JOIN sys.schemas rs ON rs.schema_id=rt.schema_id
          JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id AND rc.column_id=fkc.referenced_column_id
          ORDER BY fk.name,fkc.constraint_column_id
        """), ("foreign_key_name","parent_object_id","parent_schema","parent_table","parent_column","referenced_object_id","referenced_schema","referenced_table","referenced_column","constraint_column_id","is_disabled","is_not_trusted","delete_action","update_action"))
        counts["indexes"] = _insert(db, "indexes", _rows(cursor, """
          SELECT i.object_id,i.index_id,i.name AS index_name,i.type_desc AS index_type,i.is_unique,i.is_primary_key,i.is_unique_constraint,i.is_disabled,
                 i.has_filter,i.filter_definition,ic.key_ordinal,ic.is_included_column,ic.is_descending_key AS is_descending,c.name AS column_name
          FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
          JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id JOIN sys.objects o ON o.object_id=i.object_id
          WHERE o.is_ms_shipped=0 AND o.type='U' AND i.index_id>0 ORDER BY i.object_id,i.index_id,ic.key_ordinal,ic.index_column_id
        """), ("object_id","index_id","index_name","index_type","is_unique","is_primary_key","is_unique_constraint","is_disabled","has_filter","filter_definition","key_ordinal","is_included_column","is_descending","column_name"))
        counts["dependencies"] = _insert(db, "dependencies", _rows(cursor, """
          SELECT sed.referencing_id AS referencing_object_id,OBJECT_SCHEMA_NAME(sed.referencing_id) AS referencing_schema,
                 OBJECT_NAME(sed.referencing_id) AS referencing_object,o.type_desc AS referencing_type,sed.referenced_server_name AS referenced_server,
                 sed.referenced_database_name AS referenced_database,sed.referenced_schema_name AS referenced_schema,
                 sed.referenced_entity_name AS referenced_entity,sed.referenced_id,sed.is_ambiguous
          FROM sys.sql_expression_dependencies sed JOIN sys.objects o ON o.object_id=sed.referencing_id
          WHERE o.is_ms_shipped=0 ORDER BY sed.referencing_id,sed.referenced_entity_name
        """), ("referencing_object_id","referencing_schema","referencing_object","referencing_type","referenced_server","referenced_database","referenced_schema","referenced_entity","referenced_id","is_ambiguous"))
        counts["classifications"] = _infer_and_classify(db)
        db.commit()
    finally:
        db.close()
    return counts, captured_at


def _reports(catalog_path: Path, output_dir: Path, captured_at: str) -> tuple[dict[str, Any], int]:
    with sqlite3.connect(catalog_path) as db:
        db.row_factory = sqlite3.Row
        overview = dict(db.execute("""
          SELECT COUNT(*) AS tables_count,SUM(CASE WHEN approximate_rows>0 THEN 1 ELSE 0 END) AS populated_tables,
                 SUM(CASE WHEN activity_status='usada_desde_inicio_sqlserver' THEN 1 ELSE 0 END) AS used_tables,
                 SUM(approximate_rows) AS approximate_rows_total,ROUND(SUM(reserved_mb),2) AS reserved_mb_total FROM tables
        """).fetchone())
        categories = [dict(row) for row in db.execute("""
          SELECT c.category,COUNT(*) AS table_matches,SUM(CASE WHEN t.approximate_rows>0 THEN 1 ELSE 0 END) AS populated_matches,
                 SUM(t.approximate_rows) AS approximate_rows FROM classifications c JOIN tables t ON t.object_id=c.object_id
          GROUP BY c.category ORDER BY table_matches DESC,c.category
        """)]
        top = [dict(row) for row in db.execute("""
          SELECT c.category,c.score,t.schema_name||'.'||t.table_name AS table_name,t.approximate_rows,t.activity_status,c.evidence,c.bridge_keys
          FROM classifications c JOIN tables t ON t.object_id=c.object_id
          WHERE t.approximate_rows>0 AND t.lifecycle_hint='operacional_ou_cadastro'
          ORDER BY c.score DESC,t.approximate_rows DESC,c.category,table_name LIMIT 250
        """)]
        summary = {
            "captured_at": captured_at,
            "scope": "Metadados completos de sys.* e DMVs; nenhuma linha de negocio foi lida.",
            "activity_definition": {
                "usada_desde_inicio_sqlserver": "Tabela preenchida com leitura ou escrita desde o ultimo inicio do SQL Server.",
                "preenchida_sem_contador_de_uso": "Tabela preenchida sem contador DMV no periodo atual; nao prova obsolescencia.",
                "vazia": "Sem linhas estimadas em sys.dm_db_partition_stats.",
            },
            "overview": overview,
            "category_counts": categories,
            "top_candidates": top,
            "artifacts": {
                "catalog_sqlite": "catalogo_erp.sqlite",
                "financial_candidates_csv": "candidatos_financeiros.csv",
                "report_markdown": "relatorio_catalogo_erp.md",
                "integration_plan_markdown": "plano_integracao_contratos.md",
            },
        }
        (output_dir / "resumo_catalogo_erp.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        count = 0
        with (output_dir / "candidatos_financeiros.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(("categoria","score","tabela","linhas_aproximadas","espaco_mb","atividade","ciclo","evidencia","chaves_de_ligacao"))
            for row in db.execute("""
              SELECT c.category,c.score,t.schema_name||'.'||t.table_name,t.approximate_rows,t.reserved_mb,t.activity_status,t.lifecycle_hint,c.evidence,c.bridge_keys
              FROM classifications c JOIN tables t ON t.object_id=c.object_id ORDER BY c.category,c.score DESC,t.approximate_rows DESC,t.table_name
            """):
                writer.writerow(row)
                count += 1
        info = db.execute("SELECT * FROM database_info LIMIT 1").fetchone()
        integration_tables = {
            row["table_name"].upper(): dict(row)
            for row in db.execute(
                """
                SELECT table_name, approximate_rows, activity_status
                FROM tables
                WHERE UPPER(table_name) IN (
                    'MDCDP','MDCMP','MLANF','MLANC','MEXTRATOBANCOLANC',
                    'MDCDR','MDCMR','MACCX','MACCXPARCELA','MTRCX'
                )
                """
            )
        }
        master_objects = {
            row["object_name"].upper(): row["object_type_desc"]
            for row in db.execute(
                """
                SELECT object_name, object_type_desc FROM objects
                WHERE UPPER(object_name) IN ('TCONT','THIST','TBANC','TAGEN','TCENTROCONTAB')
                """
            )
        }
    lines = [
        "# Catálogo completo do ERP", "", f"Capturado em: `{captured_at}`", f"Banco: `{info['database_name']}` | Servidor: `{info['server_name']}` | Login: `{info['login_name']}`", "",
        "## Escopo e segurança", "", "Este inventário leu somente os catálogos `sys.*` e DMVs de estatística. Não consultou dados de negócio, não criou objetos e não alterou registros no ERP.", "",
        "## Cobertura", "", "| Métrica | Quantidade |", "|---|---:|",
        f"| Tabelas inventariadas | {overview['tables_count']:,} |", f"| Tabelas preenchidas | {overview['populated_tables']:,} |", f"| Tabelas usadas desde o início do SQL Server | {overview['used_tables']:,} |", f"| Linhas estimadas totais | {overview['approximate_rows_total']:,} |", f"| Espaço reservado estimado | {overview['reserved_mb_total']:,.2f} MB |", "",
        "O inventário completo está em `catalogo_erp.sqlite`: tabelas, colunas, índices, PKs, FKs, objetos SQL, dependências, chaves de ligação inferidas e classificações.", "",
        "## Famílias financeiras encontradas", "", "| Família | Tabelas sinalizadas | Com linhas | Linhas estimadas |", "|---|---:|---:|---:|",
    ]
    for item in categories:
        lines.append(f"| {item['category']} | {item['table_matches']:,} | {item['populated_matches']:,} | {item['approximate_rows']:,} |")
    lines.extend(["", "## Candidatas prioritárias", "", "| Família | Tabela | Linhas estimadas | Evidência |", "|---|---|---:|---|"])
    per_category: dict[str, int] = {}
    for item in top:
        if per_category.get(item["category"], 0) >= 12:
            continue
        per_category[item["category"]] = per_category.get(item["category"], 0) + 1
        lines.append(f"| {item['category']} | `{item['table_name']}` | {item['approximate_rows']:,} | {str(item['evidence']).replace('|','/')} |")
    lines.extend(["", "## Como usar o mapa", "", "1. Abra `candidatos_financeiros.csv` para a triagem por família, atividade e volume.", "2. Consulte `catalogo_erp.sqlite` com DB Browser for SQLite ou `sqlite3`; a tabela `classifications` guarda a evidência e as chaves de ligação.", "3. Use `foreign_keys` para relações declaradas e `inferred_keys` para possíveis relações históricas não declaradas.", "4. Valide cada integração com consultas pequenas e somente leitura. A classificação é priorização de investigação, não uma afirmação de regra de negócio.", "", "### Definição de atividade", "", "- `usada_desde_inicio_sqlserver`: há contador de leitura/escrita da DMV no ciclo atual do serviço.", "- `preenchida_sem_contador_de_uso`: há linhas, mas nenhuma atividade no ciclo atual; ainda pode ser histórica ou relevante.", "- `vazia`: não há linhas estimadas na partição principal."])
    (output_dir / "relatorio_catalogo_erp.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    def present(names: tuple[str, ...]) -> str:
        parts = []
        for name in names:
            table = integration_tables.get(name.upper())
            if table:
                parts.append(f"`{table['table_name']}` ({table['approximate_rows']:,} linhas estimadas; {table['activity_status']})")
            else:
                parts.append(f"`{name}` (não encontrada)")
        return ", ".join(parts)

    master = ", ".join(f"`{name}` ({kind})" for name, kind in sorted(master_objects.items()))
    plan_lines = [
        "# Plano de integração para Acompanhamento de Contratos", "",
        f"Gerado a partir do catálogo de `{info['database_name']}` em `{captured_at}`. As sugestões abaixo se baseiam apenas em estrutura e metadados; a regra de negócio deve ser validada com consultas pequenas, somente leitura.", "",
        "## 1. Cadeia de contas a pagar e bonificações", "",
        f"Tabelas encontradas: {present(('MDCDP','MDCMP','MLANF','MLANC'))}.", "",
        "- `MDCDP` contém o título a pagar: estabelecimento, pessoa, tipo/documento/sequência, emissão, vencimento, pagamento, saldo e valores de abatimento/acréscimo.",
        "- `MDCMP` é a movimentação/baixa candidata: preserva a chave do título e adiciona valor, data, modalidade, cheque, centro, histórico, `Cd_LancFin` e `Cd_Lancto`.",
        "- `MLANF` e `mlanc` permitem descer ao lançamento financeiro/contábil por `Cd_LancFin`, `Cd_Lancto`, data, documento, valor, centro e histórico.",
        "- Integração indicada: ampliar a conciliação atual com o status de baixa, valor de abatimento e lançamento contábil, mantendo a chave composta `Cd_Estab + Cd_Pessoa + Cd_TipTit + Id_Docum + Sq_Docum` como hipótese inicial a validar.",
        "",
        "## 2. Recebimentos, cartões e depósitos", "",
        f"Tabelas encontradas: {present(('MDCDR','MDCMR','MACCX','MACCXPARCELA','MTRCX'))}.", "",
        "- `MDcDR` e `MDcMR` têm uma estrutura espelhada de títulos e movimentos, com documento, pessoa, vencimento/pagamento, baixa, valor e lançamento financeiro; são candidatas fortes ao fluxo de contas a receber e devem ser confirmadas contra o dicionário funcional do ERP.",
        "- `MACCX` e `MTRCX` registram transações de caixa/cartão e trazem valor pago/transacionado, modalidade, baixa, lançamento financeiro, TEF, NSU, depósito e comprovante.",
        "- `MAcCxParcela` acrescenta parcela, taxa, líquido, previsão/efetivação de pagamento, conta bancária e identificadores TEF/adquirente. É a melhor candidata para conciliar crédito de cartão e atraso de recebimento.",
        "",
        "## 3. Extrato e conciliação bancária", "",
        f"Tabela encontrada: {present(('MEXTRATOBANCOLANC',))}.", "",
        "- `MExtratoBancoLanc` tem data, hora, histórico, valor, banco, agência, conta corrente, documento, estabelecimento de referência/depósito e identificadores de conciliação/importação.",
        "- Integração indicada: usar `ConciliacaoId`, banco/conta, data, valor e documento como evidências na central de exceções; o vínculo com os movimentos financeiros deve ser confirmado por amostra antes de automatizar qualquer baixa.",
        "",
        "## 4. Dimensões para legibilidade e auditoria", "",
        f"Objetos encontrados: {master or 'nenhum dos cadastros esperados foi localizado'}.", "",
        "- `TCONT` oferece plano/descrição de conta; `THIST`, históricos financeiros; `TBANC` e `TAGEN`, dados bancários; `TCentroContab`, mapeamento de centros quando estiver preenchida.",
        "- Inclua essas dimensões no snapshot local para que relatórios e exceções exibam descrições, não apenas códigos ERP.",
        "",
        "## Ordem segura de implementação", "",
        "1. Validar o encadeamento MDCDP → MDCMP → MLANF/mlanc com uma única compra já conciliada.",
        "2. Criar snapshot incremental somente leitura das colunas necessárias, com marca de sincronização e métricas de controle.",
        "3. Incluir extrato e cartões como evidências de conciliação, sem qualquer atualização no ERP.",
        "4. Só depois transformar relações confirmadas em regras automáticas; ambiguidades devem permanecer na revisão humana.",
    ]
    (output_dir / "plano_integracao_contratos.md").write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
    return summary, count


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Mapeia todo o catálogo ERP em modo somente leitura.")
    parser.add_argument("--server", default=settings.erp_db_server, help="SQL Server ou servidor\\instância")
    parser.add_argument("--database", default=settings.erp_db_name)
    parser.add_argument("--user", default=settings.erp_db_user)
    parser.add_argument("--password-env", default="ERP_CATALOG_PASSWORD", help="Variável de ambiente com a senha; se ausente usa ERP_DB_PASSWORD do .env.")
    parser.add_argument("--output-dir", type=Path, default=Path("../output/investigation/erp_catalog"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = os.getenv(args.password_env) or get_settings().erp_db_password
    if not password:
        raise RuntimeError(f"Defina a variável de ambiente {args.password_env} antes de executar o mapeador.")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "catalogo_erp.sqlite"
    with closing(_connect(args.server, args.database, args.user, password)) as connection:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED; SET LOCK_TIMEOUT 10000; SET NOCOUNT ON;")
        try:
            counts, captured_at = _capture(cursor, catalog_path, args.server)
            connection.rollback()
        finally:
            cursor.close()
    summary, candidates = _reports(catalog_path, output_dir, captured_at)
    print(json.dumps({"status":"ok","output_dir":str(output_dir),"metadata_rows":counts,"financial_candidates":candidates,"tables":summary["overview"]["tables_count"],"populated_tables":summary["overview"]["populated_tables"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
