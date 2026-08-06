from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dependencies import AdminUser, DbSession
from app.models import BonusRule, Company, Contract, FreightCte, FreightOrigin, FreightRate, ReportRecipient, SupplierAlias, SyncRun, Unit, User
from app.security import hash_password
from app.services.audit import audit
from app.services.erp_sync import queue_sync
from app.services.freight_reconciliation import rebuild_freight_reconciliations
from app.services.reconciliation import rebuild_reconciliations
from app.services.rules import add_months


router = APIRouter(prefix="/admin", tags=["administração"])


class UserInput(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: str = "viewer"
    password: str | None = Field(None, min_length=10, max_length=128)
    active: bool = True


class RecipientInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    active: bool = True


class UnitInput(BaseModel):
    display_name: str | None = Field(None, min_length=2, max_length=120)
    cnpj: str | None = Field(None, max_length=18)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, min_length=2, max_length=2)
    brand: str | None = Field(None, max_length=40)
    active: bool | None = None

    @model_validator(mode="after")
    def reject_null_for_required_columns(self):
        for field_name in ("display_name", "state", "active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} não pode ser nulo")
        return self


ContractStatus = Literal["active", "inactive"]


class ContractInput(BaseModel):
    unit_code: str
    company_code: str
    start_date: date
    term_months: int = Field(gt=0, le=240)
    total_liters: Decimal = Field(gt=0)
    upfront_total: Decimal = Decimal("0")
    upfront_per_liter: Decimal = Decimal("0")
    postpaid_per_liter: Decimal = Decimal("0")
    umbrella_group: str | None = None
    status: ContractStatus = "active"


RuleKind = Literal[
    "distributor_credit",
    "milestone_bonus",
    "invoice_discount",
    "s10_excess_credit",
    "bank_deposit",
]


def _valid_applies_to(value: str) -> bool:
    if value in {"all_fuel", "s10"}:
        return True
    if not value.startswith("fuel_codes:"):
        return False
    codes = [item.strip() for item in value.split(":", 1)[1].split(",")]
    return bool(codes) and all(code.isdigit() for code in codes)


class RuleInput(BaseModel):
    unit_code: str
    company_code: str
    kind: RuleKind
    effective_from: date
    effective_to: date | None = None
    rate_per_liter: Decimal = Field(default=Decimal("0"), ge=0)
    threshold_liters: Decimal | None = Field(default=None, ge=0)
    milestone_liters: Decimal | None = Field(default=None, ge=0)
    milestone_amount: Decimal | None = Field(default=None, ge=0)
    period_months: int | None = None
    due_day: int | None = None
    due_month_offset: int = Field(default=1, ge=0)
    applies_to: str = "all_fuel"
    active: bool = True

    @model_validator(mode="after")
    def validate_rule_period(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Fim da vigência não pode ser anterior ao início")
        if self.due_day is not None and not 1 <= self.due_day <= 31:
            raise ValueError("Dia limite deve estar entre 1 e 31")
        if self.period_months is not None and self.period_months <= 0:
            raise ValueError("Período deve ser maior que zero")
        if not _valid_applies_to(self.applies_to):
            raise ValueError("Aplicação da regra inválida")
        if self.kind == "milestone_bonus":
            if not self.milestone_liters or not self.milestone_amount or not self.period_months:
                raise ValueError("Bônus por marco exige volume, valor e período positivos")
        elif self.kind == "s10_excess_credit":
            if not self.rate_per_liter or not self.threshold_liters or self.applies_to != "s10":
                raise ValueError("Crédito S10 exige taxa, limite e aplicação s10")
        elif not self.rate_per_liter:
            raise ValueError("A regra exige uma taxa por litro positiva")
        return self


class AliasInput(BaseModel):
    company_code: str
    unit_code: str | None = None
    cnpj: str | None = None
    legal_name_pattern: str | None = None
    effective_from: date
    effective_to: date | None = None
    active: bool = True

    @model_validator(mode="after")
    def validate_alias(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Fim da vigência não pode ser anterior ao início")
        if not (self.cnpj or "").strip() and not (self.legal_name_pattern or "").strip():
            raise ValueError("Informe um CNPJ ou trecho do nome do fornecedor")
        return self


class FreightRateInput(BaseModel):
    carrier_cnpj: str = Field(min_length=14, max_length=18)
    carrier_name: str | None = Field(None, max_length=200)
    origin_cnpj: str | None = Field(None, max_length=18)
    unit_code: str | None = Field(None, min_length=1, max_length=3)
    effective_from: date
    effective_to: date | None = None
    rate_per_liter: Decimal = Field(gt=0)
    active: bool = True


def _user(row: User):
    return {"id": row.id, "email": row.email, "full_name": row.full_name, "role": row.role, "active": row.active, "must_change_password": row.must_change_password}


def _validated_references(
    db: DbSession,
    *,
    unit_code: str | None,
    company_code: str,
) -> tuple[str | None, str]:
    normalized_unit = unit_code.strip().zfill(3) if unit_code and unit_code.strip() else None
    normalized_company = company_code.strip().upper()
    if normalized_unit and not db.get(Unit, normalized_unit):
        raise HTTPException(status_code=400, detail="Unidade não encontrada")
    if not db.get(Company, normalized_company):
        raise HTTPException(status_code=400, detail="Companhia não encontrada")
    return normalized_unit, normalized_company


@router.get("/users")
def list_users(db: DbSession, _: AdminUser):
    return [_user(row) for row in db.scalars(select(User).order_by(User.full_name)).all()]


@router.post("/users")
def create_user(payload: UserInput, db: DbSession, actor: AdminUser):
    if payload.role not in {"admin", "viewer"}:
        raise HTTPException(status_code=400, detail="Perfil deve ser admin ou viewer")
    email = payload.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    if not payload.password:
        raise HTTPException(status_code=400, detail="Senha inicial obrigatória")
    row = User(
        email=email, full_name=payload.full_name, role=payload.role,
        active=payload.active, password_hash=hash_password(payload.password), must_change_password=True,
    )
    db.add(row)
    try:
        db.flush()
        audit(db, actor, "create", "user", row.id, {"email": row.email, "role": row.role})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="E-mail já cadastrado") from exc
    return _user(row)


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserInput, db: DbSession, actor: AdminUser):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if payload.role not in {"admin", "viewer"}:
        raise HTTPException(status_code=400, detail="Perfil inválido")
    email = payload.email.lower()
    duplicate = db.scalar(select(User).where(func.lower(User.email) == email, User.id != row.id))
    if duplicate:
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    if row.id == actor.id and (not payload.active or payload.role != "admin"):
        raise HTTPException(
            status_code=400,
            detail="Não é possível desativar ou remover o próprio perfil de administrador",
        )
    row.email, row.full_name, row.role, row.active = email, payload.full_name, payload.role, payload.active
    if payload.password:
        row.password_hash = hash_password(payload.password)
        row.must_change_password = True
    audit(db, actor, "update", "user", row.id, {"email": row.email, "role": row.role, "active": row.active})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="E-mail já cadastrado") from exc
    return _user(row)


@router.delete("/users/{user_id}")
def deactivate_user(user_id: str, db: DbSession, actor: AdminUser):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if row.id == actor.id:
        raise HTTPException(status_code=400, detail="Não é possível desativar o próprio usuário")
    row.active = False
    audit(db, actor, "deactivate", "user", row.id, {"email": row.email})
    db.commit()
    return {"ok": True}


@router.get("/recipients")
def list_recipients(db: DbSession, _: AdminUser):
    return [{"id": row.id, "name": row.name, "email": row.email, "active": row.active} for row in db.scalars(select(ReportRecipient).order_by(ReportRecipient.name)).all()]


@router.post("/recipients")
def create_recipient(payload: RecipientInput, db: DbSession, actor: AdminUser):
    values = payload.model_dump()
    values["email"] = payload.email.lower()
    if db.scalar(select(ReportRecipient).where(func.lower(ReportRecipient.email) == values["email"])):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado como destinatário")
    row = ReportRecipient(**values)
    db.add(row)
    try:
        db.flush()
        audit(db, actor, "create", "recipient", row.id, values)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="E-mail já cadastrado como destinatário") from exc
    return {"id": row.id, **values}


@router.patch("/recipients/{recipient_id}")
def update_recipient(recipient_id: int, payload: RecipientInput, db: DbSession, actor: AdminUser):
    row = db.get(ReportRecipient, recipient_id)
    if not row: raise HTTPException(status_code=404, detail="Destinatário não encontrado")
    values = payload.model_dump()
    values["email"] = payload.email.lower()
    duplicate = db.scalar(
        select(ReportRecipient).where(
            func.lower(ReportRecipient.email) == values["email"],
            ReportRecipient.id != row.id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="E-mail já cadastrado como destinatário")
    for key, value in values.items(): setattr(row, key, value)
    audit(db, actor, "update", "recipient", row.id, values)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="E-mail já cadastrado como destinatário") from exc
    return {"id": row.id, **values}


@router.delete("/recipients/{recipient_id}")
def delete_recipient(recipient_id: int, db: DbSession, actor: AdminUser):
    row = db.get(ReportRecipient, recipient_id)
    if not row: raise HTTPException(status_code=404, detail="Destinatário não encontrado")
    audit(db, actor, "delete", "recipient", row.id, {"email": row.email})
    db.delete(row); db.commit()
    return {"ok": True}


@router.patch("/units/{unit_code}")
def update_unit(unit_code: str, payload: UnitInput, db: DbSession, actor: AdminUser):
    row = db.get(Unit, unit_code.zfill(3))
    if not row: raise HTTPException(status_code=404, detail="Unidade não encontrada")
    values = payload.model_dump(exclude_unset=True)
    if "cnpj" in values:
        values["cnpj"] = "".join(character for character in (values["cnpj"] or "") if character.isdigit()) or None
        if values["cnpj"] and len(values["cnpj"]) != 14:
            raise HTTPException(status_code=400, detail="CNPJ da unidade deve conter 14 dígitos")
        duplicate = db.scalar(select(Unit).where(Unit.cnpj == values["cnpj"], Unit.code != row.code)) if values["cnpj"] else None
        if duplicate:
            raise HTTPException(status_code=409, detail="CNPJ já cadastrado em outra unidade")
    for key, value in values.items(): setattr(row, key, value)
    audit(db, actor, "update", "unit", row.code, values)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="CNPJ já cadastrado em outra unidade") from exc
    return {"code": row.code, "display_name": row.display_name, "cnpj": row.cnpj, "city": row.city, "state": row.state, "brand": row.brand, "active": row.active}


def _contract(row: Contract):
    return {"id": row.id, "unit_code": row.unit_code, "company_code": row.company_code, "start_date": row.start_date, "end_date": row.end_date, "term_months": row.term_months, "total_liters": float(row.total_liters), "upfront_total": float(row.upfront_total), "upfront_per_liter": float(row.upfront_per_liter), "postpaid_per_liter": float(row.postpaid_per_liter), "umbrella_group": row.umbrella_group, "status": row.status}


def _ensure_contract_does_not_overlap(
    db: Session,
    values: dict,
    ignore_id: int | None = None,
) -> None:
    if values["status"] != "active":
        return
    statement = select(Contract).where(
        Contract.unit_code == values["unit_code"],
        Contract.company_code == values["company_code"],
        Contract.status == "active",
        Contract.start_date <= values["end_date"],
        Contract.end_date >= values["start_date"],
    )
    if ignore_id is not None:
        statement = statement.where(Contract.id != ignore_id)
    conflict = db.scalar(statement.limit(1))
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=(
                f"O contrato sobrepõe a vigência {conflict.start_date:%d/%m/%Y} a "
                f"{conflict.end_date:%d/%m/%Y} da unidade {conflict.unit_code}."
            ),
        )


@router.get("/contracts")
def list_contracts(db: DbSession, _: AdminUser): return [_contract(row) for row in db.scalars(select(Contract).order_by(Contract.unit_code)).all()]


@router.post("/contracts")
def create_contract(payload: ContractInput, db: DbSession, actor: AdminUser):
    values = payload.model_dump()
    values["unit_code"], values["company_code"] = _validated_references(
        db, unit_code=payload.unit_code, company_code=payload.company_code
    )
    values["end_date"] = add_months(payload.start_date, payload.term_months)
    _ensure_contract_does_not_overlap(db, values)
    row = Contract(**values)
    db.add(row); db.flush(); audit(db, actor, "create", "contract", row.id, values); db.commit()
    return _contract(row)


@router.patch("/contracts/{contract_id}")
def update_contract(contract_id: int, payload: ContractInput, db: DbSession, actor: AdminUser):
    row = db.get(Contract, contract_id)
    if not row: raise HTTPException(status_code=404, detail="Contrato não encontrado")
    values = payload.model_dump()
    values["unit_code"], values["company_code"] = _validated_references(
        db, unit_code=payload.unit_code, company_code=payload.company_code
    )
    values["end_date"] = add_months(payload.start_date, payload.term_months)
    _ensure_contract_does_not_overlap(db, values, ignore_id=row.id)
    for key, value in values.items(): setattr(row, key, value)
    audit(db, actor, "update", "contract", row.id, values); db.commit()
    return _contract(row)


@router.delete("/contracts/{contract_id}")
def deactivate_contract(contract_id: int, db: DbSession, actor: AdminUser):
    row = db.get(Contract, contract_id)
    if not row: raise HTTPException(status_code=404, detail="Contrato não encontrado")
    row.status = "inactive"; audit(db, actor, "deactivate", "contract", row.id); db.commit()
    return {"ok": True}


def _rule(row: BonusRule):
    return {"id": row.id, "unit_code": row.unit_code, "company_code": row.company_code, "kind": row.kind, "effective_from": row.effective_from, "effective_to": row.effective_to, "rate_per_liter": float(row.rate_per_liter), "threshold_liters": float(row.threshold_liters) if row.threshold_liters is not None else None, "milestone_liters": float(row.milestone_liters) if row.milestone_liters is not None else None, "milestone_amount": float(row.milestone_amount) if row.milestone_amount is not None else None, "period_months": row.period_months, "due_day": row.due_day, "due_month_offset": row.due_month_offset, "applies_to": row.applies_to, "active": row.active}


@router.get("/rules")
def list_rules(db: DbSession, _: AdminUser): return [_rule(row) for row in db.scalars(select(BonusRule).order_by(BonusRule.unit_code, BonusRule.id)).all()]


@router.post("/rules")
def create_rule(payload: RuleInput, db: DbSession, actor: AdminUser):
    values = payload.model_dump()
    values["unit_code"], values["company_code"] = _validated_references(
        db, unit_code=payload.unit_code, company_code=payload.company_code
    )
    row = BonusRule(**values)
    db.add(row)
    db.flush()
    audit(db, actor, "create", "rule", row.id, values)
    rebuild_reconciliations(db, commit=False)
    db.commit()
    return _rule(row)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RuleInput, db: DbSession, actor: AdminUser):
    row = db.get(BonusRule, rule_id)
    if not row: raise HTTPException(status_code=404, detail="Regra não encontrada")
    values = payload.model_dump()
    values["unit_code"], values["company_code"] = _validated_references(
        db, unit_code=payload.unit_code, company_code=payload.company_code
    )
    for key, value in values.items(): setattr(row, key, value)
    audit(db, actor, "update", "rule", row.id, values)
    rebuild_reconciliations(db, commit=False)
    db.commit()
    return _rule(row)


@router.delete("/rules/{rule_id}")
def deactivate_rule(rule_id: int, db: DbSession, actor: AdminUser):
    row = db.get(BonusRule, rule_id)
    if not row: raise HTTPException(status_code=404, detail="Regra não encontrada")
    row.active = False
    audit(db, actor, "deactivate", "rule", row.id)
    rebuild_reconciliations(db, commit=False)
    db.commit()
    return {"ok": True}


@router.get("/aliases")
def list_aliases(db: DbSession, _: AdminUser):
    return [{"id": row.id, "company_code": row.company_code, "unit_code": row.unit_code, "cnpj": row.cnpj, "legal_name_pattern": row.legal_name_pattern, "effective_from": row.effective_from, "effective_to": row.effective_to, "active": row.active} for row in db.scalars(select(SupplierAlias).order_by(SupplierAlias.unit_code, SupplierAlias.company_code)).all()]


@router.post("/aliases")
def create_alias(payload: AliasInput, db: DbSession, actor: AdminUser):
    values = payload.model_dump(); values["cnpj"] = "".join(c for c in (values["cnpj"] or "") if c.isdigit()) or None
    values["unit_code"], values["company_code"] = _validated_references(
        db, unit_code=payload.unit_code, company_code=payload.company_code
    )
    if values["cnpj"] and len(values["cnpj"]) != 14:
        raise HTTPException(status_code=400, detail="CNPJ do fornecedor deve conter 14 dígitos")
    if not values["cnpj"] and not (values["legal_name_pattern"] or "").strip():
        raise HTTPException(status_code=400, detail="Informe um CNPJ ou trecho do nome do fornecedor")
    row = SupplierAlias(**values); db.add(row); db.flush(); audit(db, actor, "create", "alias", row.id, values); db.commit(); return {"id": row.id, **values}


@router.patch("/aliases/{alias_id}")
def update_alias(alias_id: int, payload: AliasInput, db: DbSession, actor: AdminUser):
    row = db.get(SupplierAlias, alias_id)
    if not row: raise HTTPException(status_code=404, detail="Alias não encontrado")
    values = payload.model_dump(); values["cnpj"] = "".join(c for c in (values["cnpj"] or "") if c.isdigit()) or None
    values["unit_code"], values["company_code"] = _validated_references(
        db, unit_code=payload.unit_code, company_code=payload.company_code
    )
    if values["cnpj"] and len(values["cnpj"]) != 14:
        raise HTTPException(status_code=400, detail="CNPJ do fornecedor deve conter 14 dígitos")
    if not values["cnpj"] and not (values["legal_name_pattern"] or "").strip():
        raise HTTPException(status_code=400, detail="Informe um CNPJ ou trecho do nome do fornecedor")
    for key, value in values.items(): setattr(row, key, value)
    audit(db, actor, "update", "alias", row.id, values); db.commit(); return {"id": row.id, **values}


@router.delete("/aliases/{alias_id}")
def deactivate_alias(alias_id: int, db: DbSession, actor: AdminUser):
    row = db.get(SupplierAlias, alias_id)
    if not row: raise HTTPException(status_code=404, detail="Alias não encontrado")
    row.active = False; audit(db, actor, "deactivate", "alias", row.id); db.commit()
    return {"ok": True}


@router.post("/sync")
def start_sync(db: DbSession, actor: AdminUser, kind: str = Query("incremental", pattern="^(incremental|full)$")):
    row = queue_sync(db, kind, actor.id)
    audit(db, actor, "queue", "sync", row.id, {"kind": row.kind}); db.commit()
    return {"id": row.id, "status": row.status, "kind": row.kind, "created_at": row.created_at}


@router.get("/sync")
def sync_history(db: DbSession, _: AdminUser):
    rows = db.scalars(select(SyncRun).order_by(SyncRun.created_at.desc()).limit(50)).all()
    return [{"id": row.id, "status": row.status, "kind": row.kind, "created_at": row.created_at, "started_at": row.started_at, "finished_at": row.finished_at, "rows_processed": row.rows_processed, "error_message": row.error_message} for row in rows]


def _normalized_rate_values(payload: FreightRateInput) -> dict:
    values = payload.model_dump()
    values["carrier_cnpj"] = "".join(character for character in values["carrier_cnpj"] if character.isdigit())
    values["origin_cnpj"] = "".join(character for character in (values["origin_cnpj"] or "") if character.isdigit()) or None
    values["unit_code"] = values["unit_code"].zfill(3) if values["unit_code"] else None
    if len(values["carrier_cnpj"]) != 14 or (values["origin_cnpj"] and len(values["origin_cnpj"]) != 14):
        raise HTTPException(status_code=400, detail="CNPJs devem conter 14 dígitos")
    if values["effective_to"] and values["effective_to"] < values["effective_from"]:
        raise HTTPException(status_code=400, detail="Fim da vigência não pode ser anterior ao início")
    return values


def _ensure_rate_does_not_overlap(db: DbSession, values: dict, ignore_id: int | None = None) -> None:
    if not values["active"]:
        return
    statement = select(FreightRate).where(
        FreightRate.active.is_(True),
        FreightRate.carrier_cnpj == values["carrier_cnpj"],
        FreightRate.origin_cnpj == values["origin_cnpj"],
        FreightRate.unit_code == values["unit_code"],
        or_(FreightRate.effective_to.is_(None), FreightRate.effective_to >= values["effective_from"]),
    )
    if values["effective_to"]:
        statement = statement.where(FreightRate.effective_from <= values["effective_to"])
    if ignore_id is not None:
        statement = statement.where(FreightRate.id != ignore_id)
    if db.scalar(statement.limit(1)):
        raise HTTPException(status_code=409, detail="Já existe uma tarifa ativa sobreposta para a mesma combinação")


def _freight_rate(row: FreightRate, origins: dict[str, FreightOrigin]) -> dict:
    origin = origins.get(row.origin_cnpj) if row.origin_cnpj else None
    return {
        "id": row.id,
        "carrier_cnpj": row.carrier_cnpj,
        "carrier_name": row.carrier_name,
        "origin_cnpj": row.origin_cnpj,
        "origin": (
            {
                "cnpj": origin.cnpj,
                "legal_name": origin.legal_name,
                "city": origin.city,
                "state": origin.state,
                "source": origin.source,
            }
            if origin else None
        ),
        "unit_code": row.unit_code,
        "effective_from": row.effective_from,
        "effective_to": row.effective_to,
        "rate_per_liter": float(row.rate_per_liter),
        "active": row.active,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/freight-rates")
def list_freight_rates(db: DbSession, _: AdminUser):
    rows = db.scalars(
        select(FreightRate).order_by(FreightRate.carrier_name, FreightRate.effective_from.desc(), FreightRate.id)
    ).all()
    origin_cnpjs = {row.origin_cnpj for row in rows if row.origin_cnpj}
    origins = {
        origin.cnpj: origin
        for origin in db.scalars(select(FreightOrigin).where(FreightOrigin.cnpj.in_(origin_cnpjs))).all()
    } if origin_cnpjs else {}
    return [_freight_rate(row, origins) for row in rows]


@router.get("/freight-carriers")
def list_freight_carriers(db: DbSession, _: AdminUser):
    rows = db.execute(
        select(
            FreightCte.carrier_cnpj,
            func.max(FreightCte.carrier_name).label("carrier_name"),
            func.count(FreightCte.erp_cte_id).label("cte_count"),
            func.min(FreightCte.issue_date).label("first_issue_date"),
            func.max(FreightCte.issue_date).label("last_issue_date"),
        )
        .where(
            FreightCte.source_active.is_(True),
            FreightCte.is_canceled.is_(False),
        )
        .group_by(FreightCte.carrier_cnpj)
        .order_by(func.max(FreightCte.carrier_name), FreightCte.carrier_cnpj)
    ).all()
    return [
        {
            "carrier_cnpj": row.carrier_cnpj,
            "carrier_name": row.carrier_name,
            "cte_count": row.cte_count,
            "first_issue_date": row.first_issue_date,
            "last_issue_date": row.last_issue_date,
        }
        for row in rows
    ]


@router.post("/freight-rates")
def create_freight_rate(payload: FreightRateInput, db: DbSession, actor: AdminUser):
    values = _normalized_rate_values(payload)
    if values["unit_code"] and not db.get(Unit, values["unit_code"]):
        raise HTTPException(status_code=400, detail="Unidade não encontrada")
    _ensure_rate_does_not_overlap(db, values)
    row = FreightRate(**values, created_by=actor.id, updated_by=actor.id)
    db.add(row)
    db.flush()
    audit(db, actor, "create", "freight_rate", row.id, values)
    recalculated = rebuild_freight_reconciliations(db)
    db.commit()
    result = _freight_rate(row)
    result["reconciliations_rebuilt"] = recalculated
    return result


@router.patch("/freight-rates/{rate_id}")
def update_freight_rate(rate_id: int, payload: FreightRateInput, db: DbSession, actor: AdminUser):
    row = db.get(FreightRate, rate_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tarifa de frete não encontrada")
    values = _normalized_rate_values(payload)
    if values["unit_code"] and not db.get(Unit, values["unit_code"]):
        raise HTTPException(status_code=400, detail="Unidade não encontrada")
    _ensure_rate_does_not_overlap(db, values, rate_id)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = actor.id
    audit(db, actor, "update", "freight_rate", row.id, values)
    recalculated = rebuild_freight_reconciliations(db)
    db.commit()
    result = _freight_rate(row)
    result["reconciliations_rebuilt"] = recalculated
    return result
