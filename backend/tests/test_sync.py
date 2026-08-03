from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import FreightCte, FreightCteInvoice, PayableDocument, PayableMovement, Purchase, PurchaseItem, SyncRun
from app.services.erp_sync import queue_sync, sync_freights, sync_payable_movements, sync_payables, sync_purchases
from app.services.seed import seed_reference_data


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return self.rows


class FreightCursor:
    def __init__(self, headers, documents, cargo, supplemental=None, supplemental_documents=None):
        self.headers = headers
        self.documents = documents
        self.cargo = cargo
        self.supplemental = supplemental or []
        self.supplemental_documents = supplemental_documents or []
        self.rows = []

    def execute(self, query, *_args, **_kwargs):
        if "JOIN MDFE_NFREF r" in query:
            self.rows = self.supplemental_documents
        elif "FROM MDCHP h WITH" in query and "i.Cd_Item='12303'" in query:
            self.rows = self.supplemental
        elif "FROM MCTe_Docum d" in query:
            self.rows = self.documents
        elif "FROM MCTe_Carga g" in query:
            self.rows = self.cargo
        elif "FROM MCTe c WITH" in query:
            self.rows = self.headers

    def fetchall(self):
        return self.rows


def purchase_row(**overrides):
    row = {
        "Cd_Entrada": 987,
        "Cd_Estab": "001",
        "Cd_Pessoa": 44,
        "Cd_Nota": "12345",
        "Serie_Nota": "1",
        "Dt_Emis": datetime(2026, 6, 30),
        "Dt_Entr": datetime(2026, 7, 1),
        "Dt_Canc": None,
        "Tot_VlrBru": Decimal("65000"),
        "Tot_VlrLiq": Decimal("65000"),
        "Cd_ChaveAcesso": "1" * 44,
        "VSINC_DH": datetime(2026, 7, 1, 12),
        "Razao_XML": "IPIRANGA PRODUTOS DE PETROLEO S.A.",
        "CPF_CNPJ_XML": "33.337.122/0159-06",
        "PreNotaChave": None,
        "Cd_Item": "1",
        "Seq_Item": 1,
        "Descricao": "GASOLINA",
        "Unidade": "L",
        "Quant": Decimal("10000"),
        "Vlr_Unit": Decimal("6.5"),
        "Tot_VlrItem": Decimal("65000"),
    }
    row.update(overrides)
    return row


def test_purchase_sync_is_idempotent_and_maps_supplier():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        cursor = FakeCursor([purchase_row()])
        sync_purchases(db, cursor, datetime(2026, 6, 1)); db.commit()
        sync_purchases(db, cursor, datetime(2026, 6, 1)); db.commit()
        assert db.scalar(select(func.count()).select_from(Purchase)) == 1
        assert db.scalar(select(func.count()).select_from(PurchaseItem)) == 1
        purchase = db.get(Purchase, 987)
        assert purchase.mapped_company_code == "IPIRANGA"
        assert purchase.total_liters == Decimal("10000.000")
        assert purchase.invoice_issue_date == date(2026, 6, 30)
        assert purchase.purchase_date == date(2026, 7, 1)


def test_cancelled_purchase_is_removed_on_overlap_sync():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        sync_purchases(db, FakeCursor([purchase_row()]), datetime(2026, 6, 1)); db.commit()
        sync_purchases(db, FakeCursor([purchase_row(Dt_Canc=datetime(2026, 7, 4))]), datetime(2026, 6, 1)); db.commit()
        assert db.get(Purchase, 987) is None


def test_unmapped_supplier_is_kept_as_exception_but_not_contractual():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        row = purchase_row(CPF_CNPJ_XML="00000000000000", Razao_XML="FORNECEDOR DIVERSO")
        sync_purchases(db, FakeCursor([row]), datetime(2026, 6, 1)); db.commit()
        assert db.get(Purchase, 987).mapped_company_code is None


def test_entry_without_registered_supplier_is_not_imported_and_removes_old_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        sync_purchases(db, FakeCursor([purchase_row()]), datetime(2026, 6, 1)); db.commit()
        row = purchase_row(Razao_XML=None, CPF_CNPJ_XML=None)
        sync_purchases(db, FakeCursor([row]), datetime(2026, 6, 1)); db.commit()
        assert db.get(Purchase, 987) is None
        assert db.scalar(select(func.count()).select_from(PurchaseItem)) == 0


def test_payable_movement_refresh_is_idempotent_and_captures_discount_identity():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    movement = {
        "Cd_Entrada": 987,
        "Cd_Estab": "005",
        "Cd_Pessoa": 99,
        "Cd_TipTit": "NF",
        "Id_Docum": "123456",
        "Sq_Docum": "01",
        "Tipo": "D",
        "Data": datetime(2026, 6, 20),
        "DataMov": datetime(2026, 6, 20),
        "Dt_Referencia": datetime(2026, 6, 20),
        "Valor": Decimal("400"),
        "Sq_Baixa": 1,
        "Sq_Estorno": 0,
        "Cd_LancFin": 7001,
        "Cd_Lancto": 2,
        "Cd_Lote": 8001,
        "Cd_ModPag": "806",
        "ID_Operacao": "BT",
        "Cd_Centro": "005",
        "Obs": None,
    }
    with Session(engine) as db:
        sync_payable_movements(db, FakeCursor([movement]), datetime(2026, 6, 1)); db.commit()
        sync_payable_movements(db, FakeCursor([movement]), datetime(2026, 6, 1)); db.commit()
        assert db.scalar(select(func.count()).select_from(PayableMovement)) == 1
        row = db.scalar(select(PayableMovement))
        assert row.movement_type == "D"
        assert row.financial_launch_id == 7001
        assert row.financial_sequence == 2


def test_freight_sync_uses_explicit_unit_cnpj_and_preserves_internal_cte_identity():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    header = {
        "Cd_Cte": 2509,
        "Cd_Nota": 2426,
        "Serie_Cte": "1",
        "Dt_emis": datetime(2026, 6, 12),
        "Sit_Nota": "N",
        "Tp_Finalidade": 0,
        "Tot_VlrServ": Decimal("1525"),
        "Tot_VlrReceber": Decimal("1525"),
        "Tot_VlrLiq": Decimal("1525"),
        "Tot_VlrCarga": Decimal("60000"),
        "VSINC_DH": datetime(2026, 6, 12, 18),
        "IsCanceled": 0,
        "Cd_ChaveAcesso": "9" * 44,
        "CarrierPersonId": 77,
        "CarrierCnpj": "40.080.594/0001-02",
        "CarrierName": "TRR PAMPA DIESEL LTDA",
        "SenderCnpj": "11.111.111/0001-11",
        "SenderName": "DISTRIBUIDORA",
        "TakerCnpj": "90.589.698/0002-04",
        "DestinationCnpj": None,
    }
    documents = [{"Cd_Cte": 2509, "Cd_ChaveAcesso": "7" * 44, "Cd_Nota": 3075300, "Serie_Docum": "1", "Dt_Emis": datetime(2026, 6, 12), "Sq_DocumVinculado": 1}]
    cargo = [{"Cd_Cte": 2509, "Sq_Carga": 1, "Tp_Unidade": "4", "Descricao": "GASOLINA", "Qtd_Carga": Decimal("10000")}]
    with Session(local_engine) as db:
        seed_reference_data(db)
        loaded = sync_freights(db, FreightCursor([header], documents, cargo), datetime(2026, 7, 1))
        db.commit()
        assert loaded == 1
        cte = db.get(FreightCte, 2509)
        assert cte.cte_number == 2426
        assert cte.unit_code == "002"
        assert cte.destination_cnpj == "90589698000204"
        assert cte.carrier_cnpj == "40080594000102"
        assert cte.cargo_liters == Decimal("10000.000")
        reference = db.scalar(select(FreightCteInvoice).where(FreightCteInvoice.erp_cte_id == 2509))
        assert reference.reference_access_key == "7" * 44
        assert reference.reference_invoice_number == "3075300"
        assert reference.reference_issue_date == date(2026, 6, 12)


def test_payable_sync_stores_direct_cd_cte_link():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    payable = {
        "Cd_Estab": "002", "Cd_Pessoa": 77, "Cd_TipTit": "CT", "Id_Docum": "002426", "Sq_Docum": "01",
        "Cd_Entrada": None, "Cd_CTe": 2509, "Cd_Nota": 2426, "Vl_Docum": Decimal("1525"), "Vlr_OutAbt": 0,
        "Dt_Emissao": datetime(2026, 6, 12), "Dt_Vencimento": datetime(2026, 6, 20), "Dt_Pagamento": None,
        "Sld_Docum": Decimal("1525"), "VSINC_DH": datetime(2026, 6, 12, 18),
    }
    with Session(local_engine) as db:
        sync_payables(db, FakeCursor([payable]), datetime(2026, 7, 1))
        db.commit()
        assert db.scalar(select(PayableDocument)).erp_cte_id == 2509


def test_freight_sync_maps_model_57_purchase_entry_without_inventing_invoice_link():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    supplemental = {
        "Cd_Entrada": 900095664,
        "Cd_Estab": "054",
        "Cd_Pessoa": 17690,
        "Cd_Nota": 1458,
        "Serie_Nota": "1",
        "Dt_Emis": datetime(2026, 6, 5),
        "Sit_Nota": "N",
        "Dt_Canc": None,
        "Tot_VlrBru": Decimal("2595"),
        "Tot_VlrLiq": Decimal("2595"),
        "Cd_ChaveAcesso": "43260652935802000197570010000014581000014570",
        "VSINC_DH": datetime(2026, 6, 5, 18),
        "CarrierCnpj": "52.935.802/0001-97",
        "CarrierName": "PAULO BRONDANI TRANSPORTES",
    }
    with Session(local_engine) as db:
        seed_reference_data(db)
        loaded = sync_freights(
            db,
            FreightCursor([], [], [], supplemental=[supplemental]),
            datetime(2026, 7, 1),
        )
        db.commit()
        assert loaded == 1
        cte = db.get(FreightCte, -900095664)
        assert cte.source_kind == "purchase_entry"
        assert cte.source_entry_id == 900095664
        assert cte.unit_code == "054"
        assert cte.destination_cnpj == "12564276000262"
        assert cte.carrier_cnpj == "52935802000197"
        assert cte.charged_receivable_value == Decimal("2595.00")
        reference = db.scalar(
            select(FreightCteInvoice).where(FreightCteInvoice.erp_cte_id == -900095664)
        )
        assert reference.reference_access_key is None


def test_full_sync_upgrades_a_queued_incremental_request():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        incremental = queue_sync(db, "incremental", "first-user")
        full = queue_sync(db, "full", "manager-user")
        assert full.id == incremental.id
        assert full.kind == "full"
        assert full.requested_by == "manager-user"


def test_full_sync_is_queued_behind_a_running_incremental_request():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        running = SyncRun(kind="incremental", status="running", started_at=datetime(2026, 7, 29, 12))
        db.add(running)
        db.commit()
        full = queue_sync(db, "full", "manager-user")
        assert full.id != running.id
        assert full.kind == "full"
        assert full.status == "queued"
