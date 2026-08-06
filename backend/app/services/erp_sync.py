from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pymssql
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AccountingEntry,
    BankEntry,
    FinancialEntry,
    FreightCte,
    FreightCteInvoice,
    FreightOrigin,
    PayableDocument,
    PayableMovement,
    Purchase,
    PurchaseItem,
    SyncRun,
    Unit,
)
from app.services.seed import map_supplier
from app.services.cnpj_registry import (
    CnpjLookupError,
    normalize_cnpj,
    refresh_freight_origins,
    reserve_cnpj_ws_batch,
)


logger = logging.getLogger(__name__)
UNIT_CODES = ("001", "002", "003", "004", "005", "006", "007", "008", "012", "013", "014", "050", "051", "052", "054")
UNIT_SQL = ",".join(f"'{code}'" for code in UNIT_CODES)


def _date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return value.date()


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _digits(value: str | None) -> str | None:
    normalized = "".join(char for char in (value or "") if char.isdigit())
    return normalized or None


def erp_connection():
    settings = get_settings()
    if not settings.erp_db_password:
        raise RuntimeError("ERP_DB_PASSWORD não configurada")
    connection_options = dict(
        server=settings.erp_db_server,
        user=settings.erp_db_user,
        password=settings.erp_db_password,
        database=settings.erp_db_name,
        login_timeout=10,
        timeout=120,
        as_dict=True,
        charset="UTF-8",
    )
    if settings.erp_db_port > 0:
        connection_options["port"] = settings.erp_db_port
    return pymssql.connect(**connection_options)


def _sync_start(db: Session, run: SyncRun) -> datetime:
    settings = get_settings()
    if run.kind == "full":
        return datetime.combine(date.fromisoformat(settings.erp_sync_start_date), time.min)
    previous = db.scalar(
        select(SyncRun)
        .where(SyncRun.status == "success", SyncRun.id != run.id)
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )
    if previous and previous.finished_at:
        value = previous.finished_at
        if value.tzinfo:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value - timedelta(days=7)
    return datetime.combine(date.fromisoformat(settings.erp_sync_start_date), time.min)


def _fetch(cursor, query: str, params: tuple) -> list[dict]:
    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED; SET LOCK_TIMEOUT 5000;")
    cursor.execute(query, params)
    return list(cursor.fetchall())


def sync_purchases(db: Session, cursor, since: datetime) -> int:
    rows = _fetch(
        cursor,
        f"""
        SELECT h.Cd_Entrada,h.Cd_Estab,h.Cd_Pessoa,h.Cd_Nota,h.Serie_Nota,h.Dt_Emis,h.Dt_Entr,h.Dt_Canc,
               h.Tot_VlrBru,h.Tot_VlrLiq,h.Cd_ChaveAcesso,h.VSINC_DH,
               p.Razao_XML,p.CPF_CNPJ_XML,p.Cd_ChaveAcesso AS PreNotaChave,
               i.Cd_Item,i.Seq_Item,i.Descricao,i.Unidade,i.Quant,i.Vlr_Unit,i.Tot_VlrItem
        FROM MDCHP h
        JOIN MDCIP i ON i.Cd_Entrada=h.Cd_Entrada AND i.Cd_Estab=h.Cd_Estab
        LEFT JOIN MPreNota p ON p.Id_PreNota=h.Id_PreNota
        WHERE h.Cd_Estab IN ({UNIT_SQL})
          AND i.Unidade='L' AND i.Cd_Item IN ('1','2','3','4','5','9')
          AND (ISNULL(h.VSINC_DH,h.Dt_Entr) >= %s OR h.Dt_Entr >= %s)
        ORDER BY h.Cd_Entrada,i.Seq_Item
        """,
        (since, since),
    )
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["Cd_Entrada"])].append(row)

    for entry_id, items in grouped.items():
        first = items[0]
        if first.get("Dt_Canc") is not None:
            existing = db.get(Purchase, entry_id)
            if existing:
                db.delete(existing)
            continue
        # Entradas sem emitente identificado no XML não são compras
        # contratáveis. Na operação atual elas correspondem a devoluções de
        # venda (MDCHP.Cd_VendaDevolvida); removê-las também protege contra um
        # registro antigo reaparecer na sobreposição da sincronização.
        supplier_name = str(first.get("Razao_XML") or "").strip()
        cnpj = _digits(first.get("CPF_CNPJ_XML"))
        if not supplier_name and not cnpj:
            existing = db.get(Purchase, entry_id)
            if existing:
                db.delete(existing)
            continue
        purchase_date = _date(first["Dt_Entr"])
        total_liters = sum((_decimal(item["Quant"]) for item in items), Decimal("0"))
        s10_liters = sum(
            (_decimal(item["Quant"]) for item in items if str(item["Cd_Item"]).strip() in {"5", "9"}),
            Decimal("0"),
        )
        mapped = map_supplier(db, str(first["Cd_Estab"]), cnpj, supplier_name, purchase_date)
        purchase = db.get(Purchase, entry_id)
        if not purchase:
            purchase = Purchase(erp_entry_id=entry_id, unit_code=str(first["Cd_Estab"]), purchase_date=purchase_date)
            db.add(purchase)
        purchase.unit_code = str(first["Cd_Estab"])
        purchase.supplier_person_id = first.get("Cd_Pessoa")
        purchase.supplier_name = supplier_name
        purchase.supplier_cnpj = cnpj
        purchase.mapped_company_code = mapped
        purchase.invoice_number = str(first.get("Cd_Nota") or "")
        purchase.invoice_series = str(first.get("Serie_Nota") or "")
        purchase.access_key = first.get("Cd_ChaveAcesso") or first.get("PreNotaChave")
        purchase.purchase_date = purchase_date
        # Dt_Entr continua sendo a data operacional. A competência contratual
        # das distribuidoras usa a emissão da NF, comprovada pelo portal Ipiranga.
        purchase.invoice_issue_date = _date(first.get("Dt_Emis")) or purchase_date
        purchase.total_liters = total_liters
        purchase.s10_liters = s10_liters
        purchase.gross_value = _decimal(first.get("Tot_VlrBru"))
        purchase.net_value = _decimal(first.get("Tot_VlrLiq"))
        purchase.erp_updated_at = first.get("VSINC_DH")
        purchase.synced_at = datetime.now(timezone.utc)
        db.flush()
        db.execute(delete(PurchaseItem).where(PurchaseItem.erp_entry_id == entry_id))
        for item in items:
            db.add(
                PurchaseItem(
                    erp_entry_id=entry_id,
                    item_code=str(item["Cd_Item"]).strip(),
                    sequence=int(item.get("Seq_Item") or 0),
                    description=item.get("Descricao"),
                    unit=str(item.get("Unidade") or "L"),
                    quantity=_decimal(item.get("Quant")),
                    unit_value=_decimal(item.get("Vlr_Unit")),
                    total_value=_decimal(item.get("Tot_VlrItem")),
                )
            )
    db.flush()
    return len(rows)


def sync_payables(db: Session, cursor, since: datetime) -> int:
    freight_start = datetime.combine(date.fromisoformat(get_settings().erp_freight_sync_start_date), time.min)
    rows = _fetch(
        cursor,
        f"""
        SELECT Cd_Estab,Cd_Pessoa,Cd_TipTit,Id_Docum,Sq_Docum,Cd_Entrada,Cd_CTe,Cd_Nota,
               Vl_Docum,Vlr_OutAbt,Dt_Emissao,Dt_Vencimento,Dt_Pagamento,Sld_Docum,VSINC_DH
        FROM MDCDP
        WHERE Cd_Estab IN ({UNIT_SQL})
          AND (ISNULL(VSINC_DH,Dt_Emissao) >= %s OR Dt_Pagamento >= %s
               OR (Cd_CTe IS NOT NULL AND Dt_Emissao >= %s))
        """,
        (since, since, freight_start),
    )
    loaded: dict[tuple[str, int, str, str, str], PayableDocument] = {}
    for row in rows:
        key = (
            str(row["Cd_Estab"]),
            int(row["Cd_Pessoa"]),
            str(row["Cd_TipTit"]),
            str(row["Id_Docum"]),
            str(row["Sq_Docum"]),
        )
        statement = select(PayableDocument).where(
            PayableDocument.unit_code == key[0],
            PayableDocument.person_id == key[1],
            PayableDocument.title_type == key[2],
            PayableDocument.document_id == key[3],
            PayableDocument.sequence == key[4],
        )
        document = loaded.get(key) or db.scalar(statement)
        if not document:
            document = PayableDocument(
                unit_code=key[0],
                person_id=key[1],
                title_type=key[2],
                document_id=key[3],
                sequence=key[4],
            )
            db.add(document)
        loaded[key] = document
        document.erp_entry_id = row.get("Cd_Entrada")
        document.erp_cte_id = row.get("Cd_CTe")
        document.invoice_number = str(row.get("Cd_Nota") or "")
        document.document_value = _decimal(row.get("Vl_Docum"))
        document.other_discount = _decimal(row.get("Vlr_OutAbt"))
        document.issue_date = _date(row.get("Dt_Emissao"))
        document.due_date = _date(row.get("Dt_Vencimento"))
        document.payment_date = _date(row.get("Dt_Pagamento"))
        document.balance = _decimal(row.get("Sld_Docum"))
        document.erp_updated_at = row.get("VSINC_DH")
    db.flush()
    return len(rows)


def sync_freights(db: Session, cursor, since: datetime) -> int:
    """Refresh the complete freight horizon from read-only ERP evidence.

    CT-e corrections and cancellations are not safely represented by a single
    watermark, so the bounded 12-competence horizon is deliberately re-read.
    The PostgreSQL review history remains immutable.
    """
    del since
    start = datetime.combine(date.fromisoformat(get_settings().erp_freight_sync_start_date), time.min)
    headers = _fetch(
        cursor,
        """
        SELECT c.Cd_Cte,c.Cd_Nota,c.Serie_Cte,c.Dt_emis,c.Sit_Nota,c.Tp_Finalidade,
               c.Tot_VlrServ,c.Tot_VlrReceber,c.Tot_VlrLiq,c.Tot_VlrCarga,c.VSINC_DH,
               CASE WHEN c.XML_CancCTe IS NOT NULL OR c.Sit_Nota='C' THEN 1 ELSE 0 END AS IsCanceled,
               integration.Cd_ChaveAcesso,
               emitter.Cd_Pessoa AS CarrierPersonId,emitter.CpfCnpj AS CarrierCnpj,emitter.Nome AS CarrierName,
               sender.CpfCnpj AS SenderCnpj,sender.Nome AS SenderName,
               taker.CpfCnpj AS TakerCnpj,destination.CpfCnpj AS DestinationCnpj
        FROM MCTe c WITH (NOLOCK)
        OUTER APPLY (
            SELECT TOP 1 i.Cd_ChaveAcesso
            FROM MCTe_Integracao i WITH (NOLOCK)
            WHERE i.Cd_CTe=c.Cd_Cte
            ORDER BY i.Dt_Criacao DESC
        ) integration
        OUTER APPLY (
            SELECT TOP 1 p.Cd_Pessoa,p.CpfCnpj,p.Nome
            FROM MCTe_Pessoa p WITH (NOLOCK)
            WHERE p.Cd_CTe=c.Cd_Cte AND p.Tp_Pessoa='EM'
        ) emitter
        OUTER APPLY (
            SELECT TOP 1 p.CpfCnpj,p.Nome
            FROM MCTe_Pessoa p WITH (NOLOCK)
            WHERE p.Cd_CTe=c.Cd_Cte AND p.Tp_Pessoa='RE'
        ) sender
        OUTER APPLY (
            SELECT TOP 1 p.CpfCnpj
            FROM MCTe_Pessoa p WITH (NOLOCK)
            WHERE p.Cd_CTe=c.Cd_Cte AND p.Tp_Pessoa='TO'
        ) taker
        OUTER APPLY (
            SELECT TOP 1 p.CpfCnpj
            FROM MCTe_Pessoa p WITH (NOLOCK)
            WHERE p.Cd_CTe=c.Cd_Cte AND p.Tp_Pessoa='DE'
        ) destination
        WHERE c.Dt_emis >= %s
        ORDER BY c.Cd_Cte
        """,
        (start,),
    )
    documents = _fetch(
        cursor,
        """
        SELECT d.Cd_Cte,d.Cd_ChaveAcesso,d.Cd_Nota,d.Serie_Docum,d.Dt_Emis,d.Sq_DocumVinculado
        FROM MCTe_Docum d WITH (NOLOCK)
        JOIN MCTe c WITH (NOLOCK) ON c.Cd_Cte=d.Cd_Cte
        WHERE c.Dt_emis >= %s
        ORDER BY d.Cd_Cte,ISNULL(d.Sq_DocumVinculado,0),d.Cd_Nota,d.Cd_ChaveAcesso
        """,
        (start,),
    )
    cargo_rows = _fetch(
        cursor,
        """
        SELECT g.Cd_Cte,g.Sq_Carga,g.Tp_Unidade,g.Descricao,g.Qtd_Carga
        FROM MCTe_Carga g WITH (NOLOCK)
        JOIN MCTe c WITH (NOLOCK) ON c.Cd_Cte=g.Cd_Cte
        WHERE c.Dt_emis >= %s
        ORDER BY g.Cd_Cte,g.Sq_Carga
        """,
        (start,),
    )
    supplemental_headers = _fetch(
        cursor,
        f"""
        SELECT h.Cd_Entrada,h.Cd_Estab,h.Cd_Pessoa,h.Cd_Nota,h.Serie_Nota,
               h.Dt_Emis,h.Sit_Nota,h.Dt_Canc,h.Tot_VlrBru,h.Tot_VlrLiq,
               h.Cd_ChaveAcesso,h.VSINC_DH,
               t.CGC AS CarrierCnpj,t.Nome AS CarrierName
        FROM MDCHP h WITH (NOLOCK)
        LEFT JOIN TTRAN t WITH (NOLOCK) ON t.Cd_Pessoa=h.Cd_Pessoa
        WHERE h.Cd_Estab IN ({UNIT_SQL})
          AND h.Dt_Emis >= %s
          AND SUBSTRING(REPLACE(ISNULL(h.Cd_ChaveAcesso,''),' ',''),21,2)='57'
          AND EXISTS (
              SELECT 1 FROM MDCIP i WITH (NOLOCK)
              WHERE i.Cd_Entrada=h.Cd_Entrada AND i.Cd_Estab=h.Cd_Estab
                AND i.Cd_Item='12303'
          )
          AND NOT EXISTS (
              SELECT 1 FROM MCTe_Integracao ci WITH (NOLOCK)
              WHERE ci.Cd_ChaveAcesso=h.Cd_ChaveAcesso
          )
        ORDER BY h.Cd_Entrada
        """,
        (start,),
    )
    supplemental_documents = _fetch(
        cursor,
        f"""
        SELECT h.Cd_Entrada,r.Cd_ChaveAcessoRef,p.Cd_Nota,p.Serie_Nota,p.Dt_Emis,
               r.Cd_EntSaiNFRef
        FROM MDCHP h WITH (NOLOCK)
        JOIN MDFE_NFREF r WITH (NOLOCK) ON r.Cd_EntSai=h.Cd_Entrada
        LEFT JOIN MDCHP p WITH (NOLOCK)
          ON p.Cd_Entrada=r.Cd_EntSaiNFRef AND p.Cd_ChaveAcesso=r.Cd_ChaveAcessoRef
        WHERE h.Cd_Estab IN ({UNIT_SQL})
          AND h.Dt_Emis >= %s
          AND SUBSTRING(REPLACE(ISNULL(h.Cd_ChaveAcesso,''),' ',''),21,2)='57'
        ORDER BY h.Cd_Entrada,r.Cd_EntSaiNFRef,r.Cd_ChaveAcessoRef
        """,
        (start,),
    )
    documents_by_cte: dict[int, list[dict]] = defaultdict(list)
    for row in documents:
        documents_by_cte[int(row["Cd_Cte"])].append(row)
    cargo_by_cte: dict[int, list[dict]] = defaultdict(list)
    for row in cargo_rows:
        cargo_by_cte[int(row["Cd_Cte"])].append(row)
    supplemental_documents_by_entry: dict[int, list[dict]] = defaultdict(list)
    for row in supplemental_documents:
        supplemental_documents_by_entry[int(row["Cd_Entrada"])].append(row)

    units = db.scalars(select(Unit).where(Unit.active.is_(True), Unit.cnpj.is_not(None))).all()
    units_by_cnpj = {unit.cnpj: unit.code for unit in units if unit.cnpj}
    units_by_code = {unit.code: unit for unit in units}
    db.execute(
        update(FreightCte)
        .where(FreightCte.issue_date >= start.date())
        .values(source_active=False)
    )
    loaded = 0
    for row in headers:
        taker_cnpj = _digits(row.get("TakerCnpj"))
        destination_cnpj = _digits(row.get("DestinationCnpj"))
        unit_cnpj = taker_cnpj if taker_cnpj in units_by_cnpj else destination_cnpj
        unit_code = units_by_cnpj.get(unit_cnpj)
        carrier_cnpj = _digits(row.get("CarrierCnpj"))
        if not unit_code or not unit_cnpj or not carrier_cnpj:
            continue
        erp_cte_id = int(row["Cd_Cte"])
        cte = db.get(FreightCte, erp_cte_id)
        if not cte:
            cte = FreightCte(
                erp_cte_id=erp_cte_id,
                cte_number=int(row.get("Cd_Nota") or 0),
                issue_date=_date(row["Dt_emis"]),
                unit_code=unit_code,
                destination_cnpj=unit_cnpj,
                carrier_cnpj=carrier_cnpj,
            )
            db.add(cte)
        cte.source_kind = "mcte"
        cte.source_entry_id = None
        cte.cte_number = int(row.get("Cd_Nota") or 0)
        cte.series = str(row.get("Serie_Cte") or "") or None
        cte.access_key = _digits(row.get("Cd_ChaveAcesso")) or None
        cte.issue_date = _date(row["Dt_emis"])
        cte.unit_code = unit_code
        cte.destination_cnpj = unit_cnpj
        cte.carrier_person_id = row.get("CarrierPersonId")
        cte.carrier_cnpj = carrier_cnpj
        cte.carrier_name = str(row.get("CarrierName") or "").strip() or None
        cte.sender_cnpj = _digits(row.get("SenderCnpj"))
        cte.sender_name = str(row.get("SenderName") or "").strip() or None
        cte.purpose = int(row.get("Tp_Finalidade") or 0)
        cte.source_status = str(row.get("Sit_Nota") or "") or None
        cte.is_canceled = bool(row.get("IsCanceled"))
        cte.source_active = True
        cte.charged_service_value = _decimal(row.get("Tot_VlrServ"))
        cte.charged_receivable_value = _decimal(row.get("Tot_VlrReceber"))
        cte.charged_net_value = _decimal(row.get("Tot_VlrLiq"))
        cte.cargo_value = _decimal(row.get("Tot_VlrCarga"))
        cargo = cargo_by_cte.get(erp_cte_id, [])
        cte.cargo_liters = sum((_decimal(item.get("Qtd_Carga")) for item in cargo), Decimal("0"))
        cte.cargo_json = json.dumps(cargo, default=str, ensure_ascii=False)
        cte.erp_updated_at = row.get("VSINC_DH")
        cte.synced_at = datetime.now(timezone.utc)
        db.flush()

        source_documents = documents_by_cte.get(erp_cte_id, []) or [{"Cd_ChaveAcesso": None}]
        current_by_sequence = {reference.sequence: reference for reference in cte.invoices}
        retained_sequences: set[int] = set()
        for sequence, source in enumerate(source_documents, start=1):
            retained_sequences.add(sequence)
            key = _digits(source.get("Cd_ChaveAcesso")) or None
            invoice_number = str(source.get("Cd_Nota") or "").strip() or None
            reference_issue_date = _date(source["Dt_Emis"]) if source.get("Dt_Emis") else None
            reference = current_by_sequence.get(sequence)
            if not reference:
                reference = FreightCteInvoice(erp_cte_id=erp_cte_id, sequence=sequence)
                db.add(reference)
            if reference.reference_access_key != key:
                reference.resolved_purchase_entry_id = None
                reference.candidate_purchase_entry_id = None
                reference.resolution_source = "none"
                reference.match_status = "unmatched"
                reference.match_reason = None
            reference.reference_access_key = key
            reference.reference_invoice_number = invoice_number
            reference.reference_issue_date = reference_issue_date
        for sequence, reference in current_by_sequence.items():
            if sequence not in retained_sequences:
                db.delete(reference)
        loaded += 1

    # Some carriers are received by the ERP as model-57 purchase entries and
    # never materialized in MCTe. The negative key keeps Cd_Entrada as a stable,
    # collision-free technical identity without pretending it is MCTe.Cd_Cte.
    for row in supplemental_headers:
        entry_id = int(row["Cd_Entrada"])
        unit_code = str(row.get("Cd_Estab") or "")
        unit = units_by_code.get(unit_code)
        carrier_cnpj = _digits(row.get("CarrierCnpj"))
        if not unit or not unit.cnpj or not carrier_cnpj:
            continue
        erp_cte_id = -entry_id
        cte = db.get(FreightCte, erp_cte_id)
        if not cte:
            cte = FreightCte(
                erp_cte_id=erp_cte_id,
                cte_number=int(row.get("Cd_Nota") or 0),
                issue_date=_date(row["Dt_Emis"]),
                unit_code=unit_code,
                destination_cnpj=unit.cnpj,
                carrier_cnpj=carrier_cnpj,
            )
            db.add(cte)
        cte.source_kind = "purchase_entry"
        cte.source_entry_id = entry_id
        cte.cte_number = int(row.get("Cd_Nota") or 0)
        cte.series = str(row.get("Serie_Nota") or "") or None
        cte.access_key = _digits(row.get("Cd_ChaveAcesso")) or None
        cte.issue_date = _date(row["Dt_Emis"])
        cte.unit_code = unit_code
        cte.destination_cnpj = unit.cnpj
        cte.carrier_person_id = row.get("Cd_Pessoa")
        cte.carrier_cnpj = carrier_cnpj
        cte.carrier_name = str(row.get("CarrierName") or "").strip() or None
        cte.sender_cnpj = None
        cte.sender_name = None
        cte.purpose = 0
        cte.source_status = str(row.get("Sit_Nota") or "") or None
        cte.is_canceled = row.get("Dt_Canc") is not None
        cte.source_active = True
        cte.charged_service_value = _decimal(row.get("Tot_VlrBru"))
        cte.charged_receivable_value = _decimal(row.get("Tot_VlrLiq"))
        cte.charged_net_value = _decimal(row.get("Tot_VlrLiq"))
        cte.cargo_value = Decimal("0")
        cte.cargo_liters = Decimal("0")
        cte.cargo_json = json.dumps(
            {"source": "MDCHP", "erp_entry_id": entry_id, "item_code": "12303"},
            ensure_ascii=False,
        )
        cte.erp_updated_at = row.get("VSINC_DH")
        cte.synced_at = datetime.now(timezone.utc)
        db.flush()

        source_documents = supplemental_documents_by_entry.get(entry_id, []) or [
            {"Cd_ChaveAcessoRef": None}
        ]
        current_by_sequence = {reference.sequence: reference for reference in cte.invoices}
        retained_sequences: set[int] = set()
        for sequence, source in enumerate(source_documents, start=1):
            retained_sequences.add(sequence)
            key = _digits(source.get("Cd_ChaveAcessoRef")) or None
            invoice_number = str(source.get("Cd_Nota") or "").strip() or None
            reference_issue_date = _date(source["Dt_Emis"]) if source.get("Dt_Emis") else None
            reference = current_by_sequence.get(sequence)
            if not reference:
                reference = FreightCteInvoice(erp_cte_id=erp_cte_id, sequence=sequence)
                db.add(reference)
            if reference.reference_access_key != key:
                reference.resolved_purchase_entry_id = None
                reference.candidate_purchase_entry_id = None
                reference.resolution_source = "none"
                reference.match_status = "unmatched"
                reference.match_reason = None
            reference.reference_access_key = key
            reference.reference_invoice_number = invoice_number
            reference.reference_issue_date = reference_issue_date
        for sequence, reference in current_by_sequence.items():
            if sequence not in retained_sequences:
                db.delete(reference)
        loaded += 1
    db.flush()
    return loaded


def sync_payable_movements(db: Session, cursor, since: datetime) -> int:
    """Refresh the native accounts-payable movement chain for fuel purchases.

    MDCMP has no reliable update watermark. The relevant contractual data set is
    small, so every run replaces the complete period atomically. This captures
    late corrections and deleted/reversed discounts instead of trusting 6204.
    """
    scan_start = max(date.fromisoformat(get_settings().erp_sync_start_date), date(2024, 12, 1))
    rows = _fetch(
        cursor,
        f"""
        SELECT d.Cd_Entrada,m.Cd_Estab,m.Cd_Pessoa,m.Cd_TipTit,m.Id_Docum,m.Sq_Docum,
               m.Tipo,m.Data,m.DataMov,m.Dt_Referencia,m.Valor,m.Sq_Baixa,m.Sq_Estorno,
               m.Cd_LancFin,m.Cd_Lancto,m.Cd_Lote,m.Cd_ModPag,m.ID_Operacao,m.Cd_Centro,m.Obs
        FROM MDCDP d WITH (NOLOCK)
        JOIN MDCMP m WITH (NOLOCK) ON m.Cd_Estab=d.Cd_Estab AND m.Cd_Pessoa=d.Cd_Pessoa
          AND m.Cd_TipTit=d.Cd_TipTit AND m.Id_Docum=d.Id_Docum AND m.Sq_Docum=d.Sq_Docum
        WHERE d.Cd_Estab IN ({UNIT_SQL}) AND d.Cd_Entrada IS NOT NULL
          AND d.Dt_Emissao >= %s
          AND EXISTS (
              SELECT 1 FROM MDCIP i WITH (NOLOCK)
              WHERE i.Cd_Estab=d.Cd_Estab AND i.Cd_Entrada=d.Cd_Entrada
                AND i.Unidade='L' AND i.Cd_Item IN ('1','2','3','4','5','9')
          )
        ORDER BY m.Cd_Estab,m.Cd_Pessoa,m.Cd_TipTit,m.Id_Docum,m.Sq_Docum,
                 m.Sq_Baixa,m.Tipo,m.Cd_Lancto
        """,
        (scan_start,),
    )
    db.execute(delete(PayableMovement).where(PayableMovement.movement_date >= scan_start))
    now = datetime.now(timezone.utc)
    for row in rows:
        movement_date = _date(row.get("DataMov") or row.get("Data"))
        key_parts = (
            row.get("Cd_Estab"), row.get("Cd_Pessoa"), row.get("Cd_TipTit"), row.get("Id_Docum"),
            row.get("Sq_Docum"), row.get("Tipo"), row.get("Sq_Baixa"), row.get("Sq_Estorno"),
            row.get("Cd_LancFin"), row.get("Cd_Lancto"), movement_date,
        )
        key = ":".join(str(value or "") for value in key_parts)
        db.add(
            PayableMovement(
                erp_key=key,
                unit_code=str(row.get("Cd_Estab") or ""),
                person_id=int(row.get("Cd_Pessoa") or 0),
                title_type=str(row.get("Cd_TipTit") or ""),
                document_id=str(row.get("Id_Docum") or ""),
                document_sequence=str(row.get("Sq_Docum") or ""),
                movement_type=str(row.get("Tipo") or "").strip().upper(),
                movement_date=movement_date,
                reference_date=_date(row.get("Dt_Referencia")),
                amount=_decimal(row.get("Valor")),
                payment_sequence=int(row.get("Sq_Baixa") or 0),
                reversal_sequence=int(row.get("Sq_Estorno") or 0),
                financial_launch_id=row.get("Cd_LancFin") or None,
                financial_sequence=row.get("Cd_Lancto") or None,
                batch_id=row.get("Cd_Lote") or None,
                payment_method=str(row.get("Cd_ModPag") or "").strip() or None,
                operation_id=str(row.get("ID_Operacao") or "").strip() or None,
                center_code=str(row.get("Cd_Centro") or "").strip() or None,
                notes=row.get("Obs"),
                synced_at=now,
            )
        )
    db.flush()
    return len(rows)


def _resolved_freight_origin_cnpjs(
    db: Session,
    *,
    eligible_now: bool,
    limit: int | None = None,
) -> list[str]:
    incomplete_origin = or_(
        FreightOrigin.cnpj.is_(None),
        FreightOrigin.city.is_(None),
        FreightOrigin.state.is_(None),
    )
    if eligible_now:
        now = datetime.now(timezone.utc)
        incomplete_origin = and_(
            incomplete_origin,
            or_(FreightOrigin.next_retry_at.is_(None), FreightOrigin.next_retry_at <= now),
        )
    statement = (
        select(Purchase.supplier_cnpj)
        .join(FreightCteInvoice, FreightCteInvoice.resolved_purchase_entry_id == Purchase.erp_entry_id)
        .outerjoin(FreightOrigin, FreightOrigin.cnpj == Purchase.supplier_cnpj)
        .where(Purchase.supplier_cnpj.is_not(None), incomplete_origin)
        .distinct()
        .order_by(Purchase.supplier_cnpj)
    )
    cnpjs = []
    seen = set()
    for raw_cnpj in db.scalars(statement):
        cnpj = normalize_cnpj(raw_cnpj)
        if not cnpj or cnpj in seen:
            continue
        seen.add(cnpj)
        cnpjs.append(cnpj)
        if limit is not None and len(cnpjs) >= limit:
            break
    return cnpjs


def pending_resolved_freight_origin_cnpjs(db: Session, limit: int | None = None) -> list[str]:
    """Return unresolved origins eligible for an external lookup now."""
    return _resolved_freight_origin_cnpjs(db, eligible_now=True, limit=limit)


def unresolved_resolved_freight_origin_cnpjs(db: Session) -> list[str]:
    """Return every unresolved origin, including records in retry cooldown."""
    return _resolved_freight_origin_cnpjs(db, eligible_now=False)


def refresh_selected_freight_origins(db: Session, cnpjs: list[str]) -> tuple[int, int]:
    batch = reserve_cnpj_ws_batch(db, cnpjs)
    if not batch:
        return 0, 0
    try:
        return len(batch), refresh_freight_origins(db, batch)
    except CnpjLookupError as error:
        logger.warning("Freight origin refresh failed without interrupting sync: %s", error)
        return len(batch), 0


def refresh_origins_from_resolved_freight_invoices(db: Session, limit: int = 3) -> int:
    cnpjs = pending_resolved_freight_origin_cnpjs(db, limit=limit)
    _, enriched = refresh_selected_freight_origins(db, cnpjs)
    return enriched


def sync_financial(db: Session, cursor, since: datetime) -> int:
    since = max(since, datetime(2024, 12, 1))
    rows = _fetch(
        cursor,
        f"""
        SELECT Cd_LancFin,Sq_LancFin,Cd_Estab,Data,Cd_CtaCor,Cd_Historico,Ent_Sai,
               Valor,Documento,txt_Historico,Origem,VSINC_DH
        FROM MLANF
        WHERE Cd_Estab IN ({UNIT_SQL}) AND ISNULL(VSINC_DH,Data) >= %s
          AND (Cd_Historico IN (51,6198,6204)
               OR UPPER(ISNULL(txt_Historico,'')) LIKE '%IPIRANGA%'
               OR UPPER(ISNULL(txt_Historico,'')) LIKE '%VIBRA%'
               OR UPPER(ISNULL(txt_Historico,'')) LIKE '%RAIZEN%'
               OR UPPER(ISNULL(txt_Historico,'')) LIKE '%SHELL%')
        """,
        (since,),
    )
    for row in rows:
        statement = select(FinancialEntry).where(
            FinancialEntry.erp_launch_id == int(row["Cd_LancFin"]),
            FinancialEntry.erp_sequence == int(row["Sq_LancFin"]),
        )
        entry = db.scalar(statement)
        if not entry:
            entry = FinancialEntry(
                erp_launch_id=int(row["Cd_LancFin"]),
                erp_sequence=int(row["Sq_LancFin"]),
                entry_date=_date(row["Data"]),
                value=_decimal(row["Valor"]),
            )
            db.add(entry)
        entry.unit_code = str(row.get("Cd_Estab") or "") or None
        entry.entry_date = _date(row["Data"])
        entry.current_account = row.get("Cd_CtaCor")
        entry.history_code = row.get("Cd_Historico")
        entry.direction = row.get("Ent_Sai")
        entry.value = _decimal(row["Valor"])
        entry.document_id = str(row.get("Documento") or "") or None
        entry.history_text = row.get("txt_Historico")
        entry.origin = row.get("Origem")
        entry.erp_updated_at = row.get("VSINC_DH")
    db.flush()
    return len(rows)


def sync_bank(db: Session, cursor, since: datetime) -> int:
    rows = _fetch(
        cursor,
        """
        SELECT Id,Data,Historico,Valor,Cd_Banco,Cd_ContaCorrente,Documento,ConcClassifId,Dt_Inclusao
        FROM MExtratoBancoLanc WHERE ISNULL(Dt_Inclusao,CAST(Data AS datetime)) >= %s
        """,
        (since,),
    )
    for row in rows:
        entry_id = str(row["Id"])
        entry = db.get(BankEntry, entry_id)
        if not entry:
            entry = BankEntry(id=entry_id, entry_date=_date(row["Data"]), value=_decimal(row["Valor"]))
            db.add(entry)
        account = str(row.get("Cd_ContaCorrente") or "")
        entry.entry_date = _date(row["Data"])
        entry.history_text = row.get("Historico")
        entry.value = _decimal(row["Valor"])
        entry.bank_code = row.get("Cd_Banco")
        entry.account_number_masked = f"***{account[-4:]}" if account else None
        entry.document = row.get("Documento")
        entry.classification_id = row.get("ConcClassifId")
        entry.included_at = row.get("Dt_Inclusao")
    db.flush()
    return len(rows)


def sync_accounting(db: Session, cursor, since: datetime) -> int:
    since = max(since, datetime(2024, 12, 1))
    rows = _fetch(
        cursor,
        f"""
        WITH target AS (
            SELECT DISTINCT CD_LOTE,CD_LANCTO,DATA
            FROM mlanc WITH (NOLOCK)
            WHERE DATA >= %s AND (
                CD_CONTA IN ('1170810','3111001000031','3111001000037','3111001000038')
                OR CD_HISTOR IN (62,6513)
                OR UPPER(ISNULL(HISTORICO,'')) LIKE '%BONIF%'
                OR UPPER(ISNULL(COMPLEMENTOS,'')) LIKE '%BONIF%'
                OR UPPER(ISNULL(HISTORICO,'')) LIKE '%IPIRANGA%'
                OR UPPER(ISNULL(HISTORICO,'')) LIKE '%VIBRA%'
                OR UPPER(ISNULL(HISTORICO,'')) LIKE '%RAIZEN%'
            )
        )
        SELECT m.CD_LANCTO,m.CD_LOTE,m.Sq_Lancto,m.Cd_Estab,m.CD_CENTRO,m.DATA,m.CD_CONTA,m.CD_HISTOR,
               m.OPERACAO,m.VALOR,m.Id_Docum,m.HISTORICO,m.COMPLEMENTOS,m.Cd_Pessoa,m.VSINC_DH
        FROM mlanc m WITH (NOLOCK)
        JOIN target t ON t.CD_LOTE=m.CD_LOTE AND t.CD_LANCTO=m.CD_LANCTO AND t.DATA=m.DATA
        WHERE m.Cd_Estab IN ({UNIT_SQL}) OR m.CD_CENTRO IN ({UNIT_SQL})
        """,
        (since,),
    )
    for row in rows:
        key = f"{row.get('CD_LOTE')}:{row.get('CD_LANCTO')}:{row.get('Sq_Lancto')}:{row.get('Cd_Estab') or row.get('CD_CENTRO')}"
        entry = db.get(AccountingEntry, key)
        if not entry:
            entry = AccountingEntry(erp_key=key, entry_date=_date(row["DATA"]), value=_decimal(row["VALOR"]))
            db.add(entry)
        entry.unit_code = str(row.get("Cd_Estab") or row.get("CD_CENTRO") or "") or None
        entry.entry_date = _date(row["DATA"])
        entry.account_code = row.get("CD_CONTA")
        entry.history_code = row.get("CD_HISTOR")
        entry.operation = row.get("OPERACAO")
        entry.value = _decimal(row["VALOR"])
        entry.document_id = str(row.get("Id_Docum") or "") or None
        entry.history_text = " ".join(filter(None, [row.get("HISTORICO"), row.get("COMPLEMENTOS")]))
        entry.person_id = row.get("Cd_Pessoa")
        entry.journal_lot = row.get("CD_LOTE")
        entry.journal_entry = row.get("CD_LANCTO")
        entry.journal_sequence = row.get("Sq_Lancto")
        entry.source_unit_code = str(row.get("Cd_Estab") or "") or None
        entry.erp_updated_at = row.get("VSINC_DH")
    db.flush()
    return len(rows)


def process_sync_run(db: Session, run: SyncRun) -> SyncRun:
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    since = _sync_start(db, run)
    run.watermark_from = since
    db.commit()
    processed = 0
    try:
        # O túnel pode encerrar conexões longas entre duas consultas. Cada
        # etapa usa uma conexão curta e independente, mantendo os upserts
        # idempotentes e permitindo repetir uma execução interrompida.
        for sync_step in (
            sync_purchases,
            sync_payables,
            sync_freights,
            sync_payable_movements,
            sync_financial,
            sync_bank,
            sync_accounting,
        ):
            logger.info("Starting ERP sync step: %s", sync_step.__name__)
            with erp_connection() as connection:
                cursor = connection.cursor()
                processed += sync_step(db, cursor, since)
            db.commit()
            logger.info("Finished ERP sync step: %s", sync_step.__name__)
        refresh_origins_from_resolved_freight_invoices(db)
        from app.services.freight_reconciliation import rebuild_freight_reconciliations
        from app.services.reconciliation import rebuild_reconciliations

        rebuild_reconciliations(db)
        rebuild_freight_reconciliations(db)
        run.status = "success"
        run.rows_processed = processed
    except Exception as exc:
        logger.exception("ERP synchronization failed")
        db.rollback()
        run = db.get(SyncRun, run.id)
        run.status = "failed"
        run.error_message = str(exc)[:4000]
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return run


def queue_sync(db: Session, kind: str = "incremental", requested_by: str | None = None) -> SyncRun:
    queued = db.scalar(
        select(SyncRun).where(SyncRun.status == "queued").order_by(SyncRun.created_at).limit(1)
    )
    if queued:
        if kind == "full" and queued.kind != "full":
            queued.kind = "full"
            queued.requested_by = requested_by or queued.requested_by
            db.commit()
            db.refresh(queued)
        return queued

    running = db.scalar(
        select(SyncRun).where(SyncRun.status == "running").order_by(SyncRun.started_at).limit(1)
    )
    if running and (kind != "full" or running.kind == "full"):
        return running

    run = SyncRun(kind=kind, requested_by=requested_by, status="queued")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
