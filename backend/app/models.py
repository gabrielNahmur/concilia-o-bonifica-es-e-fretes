from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Company(Base):
    __tablename__ = "companies"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    legal_group: Mapped[str | None] = mapped_column(String(120))


class Unit(Base):
    __tablename__ = "units"
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    cnpj: Mapped[str | None] = mapped_column(String(14), unique=True, index=True)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(2), default="RS")
    brand: Mapped[str | None] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    contracts: Mapped[list[Contract]] = relationship(back_populates="unit")
    bonus_rules: Mapped[list[BonusRule]] = relationship(back_populates="unit")


class SupplierAlias(Base):
    __tablename__ = "supplier_aliases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_code: Mapped[str] = mapped_column(ForeignKey("companies.code"), index=True)
    unit_code: Mapped[str | None] = mapped_column(ForeignKey("units.code"), index=True)
    cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    legal_name_pattern: Mapped[str | None] = mapped_column(String(160))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("units.code"), index=True)
    company_code: Mapped[str] = mapped_column(ForeignKey("companies.code"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    term_months: Mapped[int] = mapped_column(Integer)
    total_liters: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    upfront_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    upfront_per_liter: Mapped[Decimal] = mapped_column(Numeric(12, 8), default=0)
    postpaid_per_liter: Mapped[Decimal] = mapped_column(Numeric(12, 8), default=0)
    umbrella_group: Mapped[str | None] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    unit: Mapped[Unit] = relationship(back_populates="contracts")


class BonusRule(Base):
    __tablename__ = "bonus_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("units.code"), index=True)
    company_code: Mapped[str] = mapped_column(ForeignKey("companies.code"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    rate_per_liter: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    threshold_liters: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    milestone_liters: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    milestone_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    period_months: Mapped[int | None] = mapped_column(Integer)
    due_day: Mapped[int | None] = mapped_column(Integer)
    due_month_offset: Mapped[int] = mapped_column(Integer, default=1)
    applies_to: Mapped[str] = mapped_column(String(30), default="all_fuel")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    unit: Mapped[Unit] = relationship(back_populates="bonus_rules")


class Purchase(Base):
    __tablename__ = "purchases"
    erp_entry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    supplier_person_id: Mapped[int | None] = mapped_column(Integer)
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    supplier_cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    mapped_company_code: Mapped[str | None] = mapped_column(String(20), index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(30))
    invoice_series: Mapped[str | None] = mapped_column(String(12))
    access_key: Mapped[str | None] = mapped_column(String(44))
    purchase_date: Mapped[date] = mapped_column(Date, index=True)
    invoice_issue_date: Mapped[date | None] = mapped_column(Date, index=True)
    total_liters: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=0)
    s10_liters: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=0)
    gross_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    net_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list[PurchaseItem]] = relationship(cascade="all, delete-orphan", back_populates="purchase")
    __table_args__ = (
        Index("ix_purchase_unit_date_company", "unit_code", "purchase_date", "mapped_company_code"),
        Index("ix_purchase_unit_issue_company", "unit_code", "invoice_issue_date", "mapped_company_code"),
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    erp_entry_id: Mapped[int] = mapped_column(ForeignKey("purchases.erp_entry_id", ondelete="CASCADE"), index=True)
    item_code: Mapped[str] = mapped_column(String(15))
    sequence: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit_value: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    purchase: Mapped[Purchase] = relationship(back_populates="items")
    __table_args__ = (UniqueConstraint("erp_entry_id", "item_code", "sequence", name="uq_purchase_item_erp"),)


class PayableDocument(Base):
    __tablename__ = "payable_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    person_id: Mapped[int] = mapped_column(Integer)
    title_type: Mapped[str] = mapped_column(String(2))
    document_id: Mapped[str] = mapped_column(String(60), index=True)
    sequence: Mapped[str] = mapped_column(String(2))
    erp_entry_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    erp_cte_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(30))
    document_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    other_discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date, index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("unit_code", "person_id", "title_type", "document_id", "sequence", name="uq_payable_erp"),)


class PayableMovement(Base):
    """Movimento nativo do título no contas a pagar (ERP MDCMP)."""

    __tablename__ = "payable_movements"
    erp_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    person_id: Mapped[int] = mapped_column(Integer)
    title_type: Mapped[str] = mapped_column(String(2))
    document_id: Mapped[str] = mapped_column(String(60), index=True)
    document_sequence: Mapped[str] = mapped_column(String(2))
    movement_type: Mapped[str] = mapped_column(String(2), index=True)
    movement_date: Mapped[date] = mapped_column(Date, index=True)
    reference_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    payment_sequence: Mapped[int] = mapped_column(Integer, default=0)
    reversal_sequence: Mapped[int] = mapped_column(Integer, default=0)
    financial_launch_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    financial_sequence: Mapped[int | None] = mapped_column(Integer)
    batch_id: Mapped[int | None] = mapped_column(BigInteger)
    payment_method: Mapped[str | None] = mapped_column(String(12))
    operation_id: Mapped[str | None] = mapped_column(String(12))
    center_code: Mapped[str | None] = mapped_column(String(12))
    notes: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        Index(
            "ix_payable_movement_title",
            "unit_code",
            "person_id",
            "title_type",
            "document_id",
            "document_sequence",
        ),
    )


class FinancialEntry(Base):
    __tablename__ = "financial_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    erp_launch_id: Mapped[int] = mapped_column(BigInteger)
    erp_sequence: Mapped[int] = mapped_column(Integer)
    unit_code: Mapped[str | None] = mapped_column(String(3), index=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    current_account: Mapped[int | None] = mapped_column(Integer)
    history_code: Mapped[int | None] = mapped_column(Integer, index=True)
    direction: Mapped[str | None] = mapped_column(String(1))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    document_id: Mapped[str | None] = mapped_column(String(60), index=True)
    history_text: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(String(10))
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("erp_launch_id", "erp_sequence", name="uq_financial_erp"),)


class BankEntry(Base):
    __tablename__ = "bank_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    history_text: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    bank_code: Mapped[int | None] = mapped_column(Integer)
    account_number_masked: Mapped[str | None] = mapped_column(String(24))
    document: Mapped[str | None] = mapped_column(String(100))
    classification_id: Mapped[int | None] = mapped_column(Integer)
    included_at: Mapped[datetime | None] = mapped_column(DateTime)


class AccountingEntry(Base):
    __tablename__ = "accounting_entries"
    erp_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    unit_code: Mapped[str | None] = mapped_column(String(3), index=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    account_code: Mapped[str | None] = mapped_column(String(20))
    history_code: Mapped[int | None] = mapped_column(Integer)
    operation: Mapped[str | None] = mapped_column(String(1))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    document_id: Mapped[str | None] = mapped_column(String(60))
    history_text: Mapped[str | None] = mapped_column(Text)
    person_id: Mapped[int | None] = mapped_column(Integer)
    journal_lot: Mapped[int | None] = mapped_column(BigInteger, index=True)
    journal_entry: Mapped[int | None] = mapped_column(BigInteger, index=True)
    journal_sequence: Mapped[int | None] = mapped_column(Integer)
    source_unit_code: Mapped[str | None] = mapped_column(String(3))
    account_name: Mapped[str | None] = mapped_column(String(180))
    history_name: Mapped[str | None] = mapped_column(String(180))
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_accounting_journal", "journal_lot", "journal_entry", "unit_code"),)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    requested_by: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20), default="incremental")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark_from: Mapped[datetime | None] = mapped_column(DateTime)
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("bonus_rules.id"), index=True)
    reference_month: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date] = mapped_column(Date)
    expected_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    manual_adjustment: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    difference_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), index=True)
    confidence: Mapped[str] = mapped_column(String(20), default="none")
    confirmation_mode: Mapped[str] = mapped_column(String(20), default="none", index=True)
    auto_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    algorithm_version: Mapped[str | None] = mapped_column(String(30))
    evidence_json: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("rule_id", "reference_month", name="uq_reconciliation_rule_month"),)


class ReconciliationItem(Base):
    """Smallest expected bonus component that can be reviewed independently."""

    __tablename__ = "reconciliation_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reconciliation_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )
    item_type: Mapped[str] = mapped_column(String(40), index=True)
    source_key: Mapped[str] = mapped_column(String(180))
    source_date: Mapped[date | None] = mapped_column(Date, index=True)
    source_document: Mapped[str | None] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(String(240))
    expected_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    difference_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), index=True)
    confidence: Mapped[str] = mapped_column(String(20), default="none")
    automatic_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    automatic_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)
    policy_reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("reconciliation_id", "source_key", name="uq_reconciliation_item_source"),
        Index("ix_reconciliation_item_rec_status", "reconciliation_id", "status"),
    )


class ReconciliationEvidence(Base):
    """Normalized, reusable identity of a source-system proof."""

    __tablename__ = "reconciliation_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    source_key: Mapped[str] = mapped_column(String(220))
    evidence_date: Mapped[date | None] = mapped_column(Date, index=True)
    unit_code: Mapped[str | None] = mapped_column(String(3), index=True)
    document_id: Mapped[str | None] = mapped_column(String(80), index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    counted: Mapped[bool] = mapped_column(Boolean, default=True)
    identity_json: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("source_type", "source_key", name="uq_reconciliation_evidence_source"),
    )


class ReconciliationAllocation(Base):
    """Auditable allocation of one evidence value to one expected item."""

    __tablename__ = "reconciliation_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    item_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_items.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_evidence.id", ondelete="CASCADE"), index=True
    )
    allocated_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    match_status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    confidence: Mapped[str] = mapped_column(String(20), default="none")
    match_basis: Mapped[str | None] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(String(30))
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("item_id", "evidence_id", name="uq_reconciliation_allocation"),
    )


class ReconciliationException(Base):
    """Actionable exception shown in the administrative review queue."""

    __tablename__ = "reconciliation_exceptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reconciliation_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("reconciliation_items.id", ondelete="CASCADE"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(220))
    exception_type: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    title: Mapped[str] = mapped_column(String(220))
    description: Mapped[str] = mapped_column(Text)
    expected_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    difference_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    assigned_to: Mapped[str | None] = mapped_column(String(36), index=True)
    resolution_code: Mapped[str | None] = mapped_column(String(50))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(36))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("reconciliation_id", "source_key", name="uq_reconciliation_exception_source"),
        Index("ix_reconciliation_exception_queue", "status", "severity", "exception_type"),
    )


class ManualAdjustment(Base):
    __tablename__ = "manual_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reconciliation_id: Mapped[str] = mapped_column(ForeignKey("reconciliations.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReconciliationReview(Base):
    __tablename__ = "reconciliation_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reconciliation_id: Mapped[str] = mapped_column(ForeignKey("reconciliations.id"), index=True)
    action: Mapped[str] = mapped_column(String(30), index=True)
    expected_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    observed_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    difference_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    confidence: Mapped[str] = mapped_column(String(20))
    snapshot_json: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PortalStatementImport(Base):
    """Arquivo original do portal armazenado somente no banco da aplicação."""

    __tablename__ = "portal_statement_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    company_code: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    client_cnpj: Mapped[str | None] = mapped_column(String(14))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120))
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_file: Mapped[bytes] = mapped_column(LargeBinary)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PortalBonusEvent(Base):
    """Lançamento deduplicado do portal, independente de arquivos sobrepostos."""

    __tablename__ = "portal_bonus_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    company_code: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    portal_date: Mapped[date] = mapped_column(Date, index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    product: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(160))
    reference: Mapped[str | None] = mapped_column(String(160))
    client_cnpj: Mapped[str | None] = mapped_column(String(14))
    business_classification: Mapped[str | None] = mapped_column(String(40), index=True)
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PortalBonusEventSource(Base):
    """Proveniência de um evento; preserva aparições em extratos sobrepostos."""

    __tablename__ = "portal_bonus_event_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    import_id: Mapped[str] = mapped_column(
        ForeignKey("portal_statement_imports.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("portal_bonus_events.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("import_id", "event_id", name="uq_portal_import_event"),
    )


class PortalBonusMatch(Base):
    """Resultado materializado da cadeia portal -> título -> nota -> financeiro."""

    __tablename__ = "portal_bonus_matches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_id: Mapped[str] = mapped_column(
        ForeignKey("portal_bonus_events.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    movement_key: Mapped[str | None] = mapped_column(String(220), index=True)
    payable_document_id: Mapped[int | None] = mapped_column(Integer, index=True)
    purchase_entry_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    financial_entry_id: Mapped[int | None] = mapped_column(Integer, index=True)
    date_difference_days: Mapped[int | None] = mapped_column(Integer)
    match_basis: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(String(30))
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InvoiceBoletoEvidence(Base):
    """Boleto PDF usado como prova primária de desconto por nota fiscal."""

    __tablename__ = "invoice_boleto_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reconciliation_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )
    unit_code: Mapped[str] = mapped_column(String(3), index=True)
    company_code: Mapped[str] = mapped_column(String(20), index=True)
    purchase_entry_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    payable_document_id: Mapped[int | None] = mapped_column(Integer, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120))
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_file: Mapped[bytes] = mapped_column(LargeBinary)
    cedent_name: Mapped[str | None] = mapped_column(String(200))
    payer_name: Mapped[str | None] = mapped_column(String(200))
    payer_cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    boleto_document_number: Mapped[str | None] = mapped_column(String(80), index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(30), index=True)
    title_document_id: Mapped[str | None] = mapped_column(String(60), index=True)
    nosso_numero: Mapped[str | None] = mapped_column(String(40))
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    gross_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    discount_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    net_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    expected_discount_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    match_status: Mapped[str] = mapped_column(String(30), index=True)
    match_basis: Mapped[str] = mapped_column(Text)
    parsed_text: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FreightCte(Base):
    """Read-only snapshot of one CT-e issued by a freight carrier."""

    __tablename__ = "freight_ctes"
    erp_cte_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(24), default="mcte", index=True)
    source_entry_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    cte_number: Mapped[int] = mapped_column(Integer, index=True)
    series: Mapped[str | None] = mapped_column(String(5))
    access_key: Mapped[str | None] = mapped_column(String(44), index=True)
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("units.code"), index=True)
    destination_cnpj: Mapped[str] = mapped_column(String(14), index=True)
    carrier_person_id: Mapped[int | None] = mapped_column(Integer)
    carrier_cnpj: Mapped[str] = mapped_column(String(14), index=True)
    carrier_name: Mapped[str | None] = mapped_column(String(200))
    sender_cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    sender_name: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[int] = mapped_column(Integer, default=0)
    source_status: Mapped[str | None] = mapped_column(String(10))
    is_canceled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    charged_service_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    charged_receivable_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    charged_net_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    cargo_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    cargo_liters: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=0)
    cargo_json: Mapped[str | None] = mapped_column(Text)
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invoices: Mapped[list[FreightCteInvoice]] = relationship(
        cascade="all, delete-orphan", back_populates="cte"
    )
    reconciliation: Mapped[FreightReconciliation | None] = relationship(
        cascade="all, delete-orphan", back_populates="cte", uselist=False
    )
    __table_args__ = (
        Index("ix_freight_cte_month_unit", "issue_date", "unit_code"),
        Index("ix_freight_cte_carrier_date", "carrier_cnpj", "issue_date"),
    )


class FreightCteInvoice(Base):
    """One NF-e reference carried by a CT-e and its local resolution."""

    __tablename__ = "freight_cte_invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    erp_cte_id: Mapped[int] = mapped_column(
        ForeignKey("freight_ctes.erp_cte_id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    reference_access_key: Mapped[str | None] = mapped_column(String(44), index=True)
    reference_invoice_number: Mapped[str | None] = mapped_column(String(30), index=True)
    reference_issue_date: Mapped[date | None] = mapped_column(Date, index=True)
    resolved_purchase_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchases.erp_entry_id", ondelete="SET NULL"), index=True
    )
    candidate_purchase_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchases.erp_entry_id", ondelete="SET NULL"), index=True
    )
    resolution_source: Mapped[str] = mapped_column(String(20), default="none", index=True)
    match_status: Mapped[str] = mapped_column(String(30), default="unmatched", index=True)
    match_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    cte: Mapped[FreightCte] = relationship(back_populates="invoices")
    __table_args__ = (
        UniqueConstraint("erp_cte_id", "sequence", name="uq_freight_cte_invoice_sequence"),
    )


class FreightRate(Base):
    """Independent, effective-dated freight price reference."""

    __tablename__ = "freight_rates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    carrier_cnpj: Mapped[str] = mapped_column(String(14), index=True)
    carrier_name: Mapped[str | None] = mapped_column(String(200))
    origin_cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    unit_code: Mapped[str | None] = mapped_column(ForeignKey("units.code"), index=True)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, index=True)
    rate_per_liter: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(36))
    updated_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        Index("ix_freight_rate_lookup", "carrier_cnpj", "unit_code", "origin_cnpj", "effective_from"),
    )


class FreightReconciliation(Base):
    """Materialized freight verification result for one CT-e."""

    __tablename__ = "freight_reconciliations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    erp_cte_id: Mapped[int] = mapped_column(
        ForeignKey("freight_ctes.erp_cte_id", ondelete="CASCADE"), unique=True, index=True
    )
    rate_id: Mapped[int | None] = mapped_column(ForeignKey("freight_rates.id"), index=True)
    reference_date: Mapped[date] = mapped_column(Date, index=True)
    matched_liters: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=0)
    expected_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    charged_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    payable_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    paid_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    difference_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    payable_difference: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    primary_status: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    review_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    algorithm_version: Mapped[str] = mapped_column(String(30))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    cte: Mapped[FreightCte] = relationship(back_populates="reconciliation")


class FreightReview(Base):
    """Immutable audit record of a human freight decision."""

    __tablename__ = "freight_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reconciliation_id: Mapped[str] = mapped_column(
        ForeignKey("freight_reconciliations.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(30), index=True)
    notes: Mapped[str] = mapped_column(Text)
    candidate_purchase_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    fingerprint: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(80))
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ReportRecipient(Base):
    __tablename__ = "report_recipients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReportRun(Base):
    __tablename__ = "report_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reference_month: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    file_path: Mapped[str | None] = mapped_column(String(500))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportDeliveryAttempt(Base):
    __tablename__ = "report_delivery_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_run_id: Mapped[str] = mapped_column(ForeignKey("report_runs.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    provider_message_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="sent", index=True)
    provider_event: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
