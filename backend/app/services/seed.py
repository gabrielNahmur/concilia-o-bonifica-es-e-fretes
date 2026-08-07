from datetime import date
from decimal import Decimal

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.models import BonusRule, Company, Contract, FreightRate, SupplierAlias, Unit
from app.services.rules import add_months


COMPANIES = [
    ("IPIRANGA", "Ipiranga", "Grupo Ultra"),
    ("BR", "BR", "Vibra Energia"),
    ("SHELL", "Shell", "Raízen"),
    ("TEXACO", "Texaco", "Grupo Ultra / Chevron"),
]

UNITS = [
    ("001", "001 — Bagé", "Bagé", "Ipiranga"),
    ("002", "002 — Bagé", "Bagé", "BR"),
    ("003", "003 — Bagé", "Bagé", "Ipiranga"),
    ("004", "004 — Dom Pedrito", "Dom Pedrito", "Ipiranga"),
    ("005", "005 — São Gabriel", "São Gabriel", "Texaco"),
    ("006", "006 — Bagé", "Bagé", "BR"),
    ("007", "007 — Dom Pedrito", "Dom Pedrito", "Texaco"),
    ("008", "008 — Dom Pedrito", "Dom Pedrito", "Ipiranga"),
    ("012", "012 — Bagé", "Bagé", "Ipiranga"),
    ("013", "013 — Rio Grande", "Rio Grande", "Ipiranga"),
    ("014", "014 — Santa Tecla", None, "Texaco"),
    ("050", "050 — Eldorado", "Eldorado do Sul", "Texaco"),
    ("051", "051 — Canoas", "Canoas", "BR"),
    ("052", "052 — Canoas", "Canoas", "BR"),
    ("054", "054 — Santa Maria", "Santa Maria", "Shell"),
]

UNIT_CNPJS = {
    "001": "90589698000115",
    "002": "90589698000204",
    "003": "90589698000387",
    "004": "90589698000468",
    "005": "90589698000549",
    # The legal branch numbers for operational units 006 and 007 are inverted;
    # never infer this mapping from the last CNPJ digits.
    "006": "90589698000700",
    "007": "90589698000620",
    "008": "90589698000891",
    "012": "90589698000972",
    "013": "90589698001197",
    "014": "90589698001278",
    # Unidades incorporadas possuem outras razoes sociais; os CNPJs foram
    # comprovados pelo destinatario das NF-es de combustivel no ERP.
    "050": "12564276000181",
    "051": "17311148000140",
    "052": "17311148000220",
    "054": "12564276000262",
}

CONTRACTS = [
    ("001", "IPIRANGA", date(2024, 12, 26), 36, "5220000", "125484", "0.024039080459770115", "0.087", None),
    ("002", "BR", date(2025, 2, 1), 24, "2640000", "0", "0", "0.07954545454545454", "BR_002_006"),
    ("003", "IPIRANGA", date(2026, 3, 6), 36, "3600000", "133585", "0.03710694444444444", "0.06", None),
    ("004", "IPIRANGA", date(2025, 8, 28), 36, "4536000", "45360", "0.01", "0.07", None),
    ("006", "BR", date(2025, 2, 1), 24, "2640000", "0", "0", "0.07954545454545454", "BR_002_006"),
    ("008", "IPIRANGA", date(2022, 4, 29), 60, "6000000", "0", "0", "0.05", None),
    ("054", "SHELL", date(2024, 12, 1), 40, "4448524", "55000", "0.012363651404375923", "0.05", None),
]

RULES = [
    ("001", "IPIRANGA", "distributor_credit", date(2024, 12, 26), "0.087", None, None, None, None, None, 1, "all_fuel"),
    ("002", "BR", "milestone_bonus", date(2025, 2, 1), "0", None, "440000", "35000", 4, None, 1, "all_fuel"),
    ("003", "IPIRANGA", "distributor_credit", date(2026, 3, 6), "0.06", None, None, None, None, None, 1, "all_fuel"),
    ("004", "IPIRANGA", "distributor_credit", date(2025, 8, 28), "0.07", None, None, None, None, None, 1, "all_fuel"),
    # A postecipada da 008 foi comprovada no relatório de parcelas emitido
    # pelo portal Ipiranga. Os ciclos próprios começam em 27/06/2022.
    ("008", "IPIRANGA", "distributor_credit", date(2022, 6, 27), "0.05", None, None, None, None, None, 1, "all_fuel"),
    ("005", "TEXACO", "invoice_discount", date(2025, 7, 31), "0.04", None, None, None, None, None, 0, "fuel_codes:1,2,3,4,5,9"),
    ("006", "BR", "milestone_bonus", date(2025, 2, 1), "0", None, "440000", "35000", 4, None, 1, "all_fuel"),
    ("007", "TEXACO", "invoice_discount", date(2025, 9, 10), "0.04", None, None, None, None, None, 0, "fuel_codes:1,2,3,4,5,9"),
    ("014", "TEXACO", "invoice_discount", date(2026, 4, 22), "0.04", None, None, None, None, None, 0, "fuel_codes:1,2,3,4,5,9"),
    # A primeira NF da 050 que recebeu integralmente R$0,04/L foi emitida em
    # 15/05/2026 e quitada com Nota PrÃ³pria no portal em 18/05. Antes disso o
    # portal nÃ£o mostra a rotina regular de desconto.
    ("050", "TEXACO", "invoice_discount", date(2026, 5, 15), "0.04", None, None, None, None, None, 0, "all_fuel"),
    # O adicional S10 foi confirmado pela diretoria a partir de agosto/2026;
    # nÃ£o pode criar pendÃªncia retroativa nos meses anteriores.
    ("050", "TEXACO", "s10_excess_credit", date(2026, 8, 1), "0.10", "150000", None, None, None, None, 1, "s10"),
    ("054", "SHELL", "bank_deposit", date(2024, 12, 1), "0.05", None, None, None, None, 20, 1, "all_fuel"),
]


def seed_reference_data(db: Session) -> None:
    for code, display_name, legal_group in COMPANIES:
        row = db.get(Company, code)
        if not row:
            db.add(Company(code=code, display_name=display_name, legal_group=legal_group))

    for code, display_name, city, brand in UNITS:
        row = db.get(Unit, code)
        if not row:
            db.add(
                Unit(
                    code=code,
                    display_name=display_name,
                    cnpj=UNIT_CNPJS.get(code),
                    city=city,
                    brand=brand,
                    state="RS",
                )
            )
        else:
            # The operating name (usually the street/name of the station) is
            # maintained by the administrators.  Seeding must only provide a
            # fallback for legacy blank records, never overwrite that choice
            # every time the worker starts.
            if not row.display_name:
                row.display_name = display_name
            if not row.city:
                row.city = city
            if not row.brand:
                row.brand = brand
            if code in UNIT_CNPJS:
                row.cnpj = UNIT_CNPJS[code]
    db.flush()

    if not db.scalar(select(Contract.id).limit(1)):
        for unit, company, start, months, total, upfront, upfront_liter, post_liter, umbrella in CONTRACTS:
            db.add(
                Contract(
                    unit_code=unit,
                    company_code=company,
                    start_date=start,
                    end_date=add_months(start, months),
                    term_months=months,
                    total_liters=Decimal(total),
                    upfront_total=Decimal(upfront),
                    upfront_per_liter=Decimal(upfront_liter),
                    postpaid_per_liter=Decimal(post_liter),
                    umbrella_group=umbrella,
                )
            )

    if not db.scalar(select(BonusRule.id).limit(1)):
        for unit, company, kind, start, rate, threshold, milestone_liters, milestone_amount, period, day, offset, applies in RULES:
            db.add(
                BonusRule(
                    unit_code=unit,
                    company_code=company,
                    kind=kind,
                    effective_from=start,
                    rate_per_liter=Decimal(rate),
                    threshold_liters=Decimal(threshold) if threshold else None,
                    milestone_liters=Decimal(milestone_liters) if milestone_liters else None,
                    milestone_amount=Decimal(milestone_amount) if milestone_amount else None,
                    period_months=period,
                    due_day=day,
                    due_month_offset=offset,
                    applies_to=applies,
                )
            )

    # Regras comprovadas no movimento real dos títulos. Mantém instalações
    # existentes alinhadas sem depender apenas da migração de dados.
    for unit_code in ("005", "007", "014"):
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == unit_code,
                BonusRule.kind == "invoice_discount",
            )
        )
        if rule:
            rule.applies_to = "fuel_codes:1,2,3,4,5,9"

    # A vigÃªncia acima foi confirmada depois da criaÃ§Ã£o da base inicial. O
    # ajuste idempotente evita que uma reinstalaÃ§Ã£o volte a cobrar descontos
    # inexistentes entre nov/2025 e mai/2026.
    rule_050 = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == "050",
            BonusRule.company_code == "TEXACO",
            BonusRule.kind == "invoice_discount",
        )
    )
    if rule_050:
        rule_050.effective_from = date(2026, 5, 15)

    rule_050_s10 = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == "050",
            BonusRule.company_code == "TEXACO",
            BonusRule.kind == "s10_excess_credit",
        )
    )
    if rule_050_s10:
        rule_050_s10.effective_from = date(2026, 8, 1)

    # Instalações já existentes foram criadas antes de a bonificação da 008
    # ter sido confirmada. Mantemos o ajuste idempotente para que um backfill
    # ou um script operacional não dependa de recriar todo o banco.
    contract_008 = db.scalar(
        select(Contract).where(
            Contract.unit_code == "008",
            Contract.company_code == "IPIRANGA",
        )
    )
    if contract_008:
        contract_008.postpaid_per_liter = Decimal("0.05")

    rule_008 = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == "008",
            BonusRule.company_code == "IPIRANGA",
            BonusRule.kind == "distributor_credit",
        )
    )
    if not rule_008:
        db.add(
            BonusRule(
                unit_code="008",
                company_code="IPIRANGA",
                kind="distributor_credit",
                effective_from=date(2022, 6, 27),
                effective_to=date(2027, 6, 26),
                rate_per_liter=Decimal("0.05"),
                due_month_offset=1,
                applies_to="all_fuel",
                active=True,
            )
        )
    else:
        rule_008.effective_from = date(2022, 6, 27)
        rule_008.effective_to = date(2027, 6, 26)
        rule_008.rate_per_liter = Decimal("0.05")
        rule_008.due_month_offset = 1
        rule_008.applies_to = "all_fuel"
        rule_008.active = True

    if not db.scalar(select(SupplierAlias.id).limit(1)):
        aliases: list[tuple[str, str | None, str, date, str]] = []
        for unit in ("001", "003", "004", "008", "012"):
            aliases.append(("IPIRANGA", unit, "33337122015906", date(2022, 1, 1), "IPIRANGA PRODUTOS"))
        aliases.extend(
            [
                ("IPIRANGA", "013", "33337122015906", date(2022, 1, 1), "IPIRANGA PRODUTOS"),
                ("IPIRANGA", "013", "33337122009698", date(2022, 1, 1), "IPIRANGA PRODUTOS"),
                ("BR", "002", "34274233006801", date(2022, 1, 1), "VIBRA ENERGIA"),
                ("BR", "006", "34274233006801", date(2022, 1, 1), "VIBRA ENERGIA"),
                ("BR", "051", "34274233006801", date(2022, 1, 1), "VIBRA ENERGIA"),
                ("BR", "052", "34274233006801", date(2022, 1, 1), "VIBRA ENERGIA"),
                ("TEXACO", "005", "33337122015906", date(2025, 7, 31), "IPIRANGA PRODUTOS"),
                ("TEXACO", "007", "33337122015906", date(2025, 9, 10), "IPIRANGA PRODUTOS"),
                ("TEXACO", "014", "33337122015906", date(2026, 4, 22), "IPIRANGA PRODUTOS"),
                ("TEXACO", "050", "33337122015906", date(2025, 11, 21), "IPIRANGA PRODUTOS"),
                ("SHELL", "054", "33453598011400", date(2024, 12, 1), "RAIZEN"),
                ("SHELL", "054", "33453598013705", date(2024, 12, 1), "RAIZEN"),
            ]
        )
        for company, unit, cnpj, effective_from, pattern in aliases:
            db.add(
                SupplierAlias(
                    company_code=company,
                    unit_code=unit,
                    cnpj=cnpj,
                    legal_name_pattern=pattern,
                    effective_from=effective_from,
                )
            )

    # Bootstrap only. Once the first tariff exists, managers own this table
    # entirely through the audited administration screen; application startup
    # must never restore or alter a tariff they edited or deactivated.
    if not db.scalar(select(FreightRate.id).limit(1)):
        db.add_all(
            (
                FreightRate(
                    carrier_cnpj="40080594000102",
                    carrier_name="TRR PAMPA DIESEL LTDA",
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2026, 2, 28),
                    rate_per_liter=Decimal("0.1425"),
                    active=True,
                ),
                FreightRate(
                    carrier_cnpj="40080594000102",
                    carrier_name="TRR PAMPA DIESEL LTDA",
                    effective_from=date(2026, 3, 1),
                    rate_per_liter=Decimal("0.1525"),
                    active=True,
                ),
            )
        )
    db.commit()


def map_supplier(db: Session, unit_code: str, cnpj: str | None, legal_name: str | None, purchase_date: date) -> str | None:
    normalized_cnpj = "".join(character for character in (cnpj or "") if character.isdigit())
    aliases = db.scalars(
        select(SupplierAlias)
        .where(
            SupplierAlias.active.is_(True),
            or_(
                SupplierAlias.unit_code == unit_code,
                SupplierAlias.unit_code.is_(None),
            ),
        )
        .order_by(
            case((SupplierAlias.unit_code == unit_code, 0), else_=1),
            SupplierAlias.effective_from.desc(),
            SupplierAlias.id.desc(),
        )
    ).all()
    upper_name = (legal_name or "").upper()
    for alias in aliases:
        if purchase_date < alias.effective_from or (alias.effective_to and purchase_date > alias.effective_to):
            continue
        cnpj_match = bool(alias.cnpj and normalized_cnpj == alias.cnpj)
        name_match = bool(alias.legal_name_pattern and alias.legal_name_pattern.upper() in upper_name)
        if cnpj_match or name_match:
            return alias.company_code
    return None
