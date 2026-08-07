from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    FreightCte,
    FreightCteInvoice,
    FreightRate,
    FreightReconciliation,
    PayableDocument,
    Purchase,
)


ALGORITHM_VERSION = "freight-v5"
CENT = Decimal("0.01")
LITER = Decimal("0.001")
ZERO = Decimal("0")

# Evidence profiles describe how supplemental carriers batch CT-es in the ERP.
# They are not contractual rates: financial pricing still comes exclusively
# from FreightRate and remains "unpriced" until the tariff is confirmed.
VKL_CNPJ = "26313088000195"
BRONDANI_CNPJ = "52935802000197"
TALISMA_CNPJ = "08825400000148"
TALISMA_IPIRANGA_ORIGIN = "33337122009698"
TALISMA_ALLOCATION_RATE = Decimal("0.04")


def _money(value: Decimal | int | float | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _liters(value: Decimal | int | float | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(LITER, rounding=ROUND_HALF_UP)


def _digits(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


def find_freight_rate(
    db: Session,
    cte: FreightCte,
    reference_date: date | None = None,
    origin_cnpj: str | None = None,
) -> FreightRate | None:
    rate_date = reference_date or cte.issue_date
    effective_origin = _digits(origin_cnpj) or _digits(cte.sender_cnpj)
    rates = db.scalars(
        select(FreightRate).where(
            FreightRate.active.is_(True),
            FreightRate.carrier_cnpj == cte.carrier_cnpj,
            FreightRate.effective_from <= rate_date,
        )
    ).all()
    eligible = [
        rate
        for rate in rates
        if (rate.effective_to is None or rate.effective_to >= rate_date)
        and (rate.unit_code is None or rate.unit_code == cte.unit_code)
        and (rate.origin_cnpj is None or rate.origin_cnpj == effective_origin)
    ]
    if not eligible:
        return None
    # Exact origin+unit, unit, origin, then global. Newest validity wins ties.
    return max(
        eligible,
        key=lambda rate: (
            int(rate.origin_cnpj is not None) + int(rate.unit_code is not None),
            int(rate.unit_code is not None),
            int(rate.origin_cnpj is not None),
            rate.effective_from,
            rate.id or 0,
        ),
    )


def _issue(code: str, detail: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "detail": detail, "severity": severity}


def _purchase_date(purchase: Purchase):
    return purchase.invoice_issue_date or purchase.purchase_date


def _access_key_month(value: str | None) -> date | None:
    """Read the AAMM segment embedded in a 44-digit NF-e access key."""
    key = _digits(value)
    if len(key) != 44:
        return None
    try:
        year = 2000 + int(key[2:4])
        month = int(key[4:6])
        return date(year, month, 1)
    except ValueError:
        return None


def _source_reference_date(cte: FreightCte) -> date:
    dates = [reference.reference_issue_date for reference in cte.invoices if reference.reference_issue_date]
    if dates:
        return min(dates)
    key_months = [month for reference in cte.invoices if (month := _access_key_month(reference.reference_access_key))]
    if not key_months:
        return cte.issue_date
    key_month = min(key_months)
    if (cte.issue_date.year, cte.issue_date.month) == (key_month.year, key_month.month):
        return cte.issue_date
    # MCTe_Docum frequently omits the exact issue date, but the official NF-e
    # key still proves its competence (AAMM). Day 1 is only a month marker.
    return key_month


def _reconciliation_reference_date(
    cte: FreightCte, purchase_ids: list[int], purchases_by_id: dict[int, Purchase]
) -> date:
    dates = [_purchase_date(purchases_by_id[purchase_id]) for purchase_id in purchase_ids]
    return min(dates) if dates else _source_reference_date(cte)


def _date_is_coherent(
    cte: FreightCte, purchase: Purchase, reference: FreightCteInvoice | None = None
) -> bool:
    """A keyed NF may precede a consolidated CT-e by up to one month."""
    if reference and reference.reference_issue_date:
        return _purchase_date(purchase) == reference.reference_issue_date
    delta = (cte.issue_date - _purchase_date(purchase)).days
    return 0 <= delta <= 31


def _automatic_document_links(
    db: Session,
    ctes: list[FreightCte],
    purchases: list[Purchase],
    used: dict[int, set[int]],
    globally_used: set[int],
) -> None:
    """Rebuild deterministic NF-e links for supplemental carrier batches."""
    supplemental = [
        cte
        for cte in ctes
        if cte.source_kind == "purchase_entry" and not cte.is_canceled
    ]
    eligible_ids = {
        cte.erp_cte_id
        for cte in supplemental
        if not any(
            reference.resolution_source == "manual" and reference.resolved_purchase_entry_id
            for reference in cte.invoices
        )
        and not any(reference.reference_access_key for reference in cte.invoices)
    }
    links: dict[int, list[tuple[Purchase, str]]] = defaultdict(list)

    def available(purchase: Purchase) -> bool:
        return purchase.erp_entry_id not in globally_used

    def allocate(cte: FreightCte, purchase: Purchase, reason: str) -> None:
        if cte.erp_cte_id not in eligible_ids or not available(purchase):
            return
        links[cte.erp_cte_id].append((purchase, reason))
        used[cte.erp_cte_id].add(purchase.erp_entry_id)
        globally_used.add(purchase.erp_entry_id)

    # VKL issues one consolidated CT-e at month-end for each unit. Direct and
    # manual links from other carriers are reserved before this closed batch.
    vkl_groups: dict[tuple[str, int, int], list[FreightCte]] = defaultdict(list)
    for cte in supplemental:
        if cte.carrier_cnpj == VKL_CNPJ and cte.erp_cte_id in eligible_ids:
            vkl_groups[(cte.unit_code, cte.issue_date.year, cte.issue_date.month)].append(cte)
    for (unit_code, year, month), group in vkl_groups.items():
        if len(group) != 1 or group[0].issue_date.day < 25:
            continue
        cte = group[0]
        candidates = sorted(
            (
                purchase
                for purchase in purchases
                if available(purchase)
                and purchase.unit_code == unit_code
                and (_purchase_date(purchase).year, _purchase_date(purchase).month) == (year, month)
            ),
            key=lambda purchase: (_purchase_date(purchase), purchase.erp_entry_id),
        )
        for purchase in candidates:
            allocate(
                cte,
                purchase,
                "Vínculo automático: lote mensal exclusivo da VKL, mesma unidade e competência; "
                "NF-e ainda não utilizada por outro CT-e.",
            )

    # Brondani follows the dispatch calendar of each unit. Same-day NF-es are
    # reserved first; remaining purchases go to the next dispatch in 8 days.
    brondani_by_unit: dict[str, list[FreightCte]] = defaultdict(list)
    for cte in supplemental:
        if cte.carrier_cnpj == BRONDANI_CNPJ and cte.erp_cte_id in eligible_ids:
            brondani_by_unit[cte.unit_code].append(cte)
    for unit_code, unit_ctes in brondani_by_unit.items():
        unit_ctes.sort(key=lambda cte: (cte.issue_date, cte.cte_number, cte.erp_cte_id))
        route_purchases = sorted(
            (
                purchase
                for purchase in purchases
                if available(purchase) and purchase.unit_code == unit_code
            ),
            key=lambda purchase: (_purchase_date(purchase), purchase.erp_entry_id),
        )
        ctes_by_date: dict[date, list[FreightCte]] = defaultdict(list)
        for cte in unit_ctes:
            ctes_by_date[cte.issue_date].append(cte)
        for purchase in route_purchases:
            same_day = ctes_by_date.get(_purchase_date(purchase), [])
            if len(same_day) == 1 and available(purchase):
                allocate(
                    same_day[0],
                    purchase,
                    "Vínculo automático: NF-e da mesma unidade e data do CT-e de Brondani.",
                )
        for purchase in route_purchases:
            if not available(purchase):
                continue
            purchase_day = _purchase_date(purchase)
            future = [
                cte
                for cte in unit_ctes
                if 0 <= (cte.issue_date - purchase_day).days <= 8
            ]
            if not future:
                continue
            selected = future[0]
            assigned_suppliers = {
                linked_purchase.supplier_cnpj
                for linked_purchase, _ in links.get(selected.erp_cte_id, [])
            }
            if assigned_suppliers and purchase.supplier_cnpj not in assigned_suppliers:
                following_empty = next(
                    (
                        cte
                        for cte in future[1:]
                        if (cte.issue_date - selected.issue_date).days <= 1
                        and not links.get(cte.erp_cte_id)
                    ),
                    None,
                )
                if following_empty:
                    selected = following_empty
            lag = (selected.issue_date - purchase_day).days
            allocate(
                selected,
                purchase,
                f"Vínculo automático: próxima remessa de Brondani na unidade, {lag} dia(s) após a NF-e.",
            )

    # Talismã began with one backlog and later issued batches. In those batches
    # the charges encode nominal loads at R$0.04/L (400=10,000 L, 340=8,500 L,
    # etc.). This rate is used only as allocation evidence, never as FreightRate.
    talisma_by_unit: dict[str, list[FreightCte]] = defaultdict(list)
    for cte in supplemental:
        if cte.carrier_cnpj == TALISMA_CNPJ and cte.erp_cte_id in eligible_ids:
            talisma_by_unit[cte.unit_code].append(cte)
    for unit_code, unit_ctes in talisma_by_unit.items():
        groups: dict[date, list[FreightCte]] = defaultdict(list)
        for cte in unit_ctes:
            groups[cte.issue_date].append(cte)
        covered_until: date | None = None
        for batch_date in sorted(groups):
            batch_ctes = sorted(
                groups[batch_date],
                key=lambda cte: (cte.cte_number, cte.erp_cte_id),
            )
            if covered_until is None and len(batch_ctes) == 1:
                window_start = batch_date - timedelta(days=31)
                candidates = sorted(
                    (
                        purchase
                        for purchase in purchases
                        if available(purchase)
                        and purchase.unit_code == unit_code
                        and purchase.supplier_cnpj == TALISMA_IPIRANGA_ORIGIN
                        and window_start <= _purchase_date(purchase) <= batch_date
                    ),
                    key=lambda purchase: (_purchase_date(purchase), purchase.erp_entry_id),
                )
                for purchase in candidates:
                    allocate(
                        batch_ctes[0],
                        purchase,
                        "Vínculo automático: lote inicial consolidado da Talismã, mesma unidade, origem "
                        "Ipiranga e janela de 31 dias anterior ao CT-e.",
                    )
                covered_until = batch_date
                continue

            batch_end = batch_date.replace(day=1) - timedelta(days=1)
            batch_start = (covered_until + timedelta(days=1)) if covered_until else batch_end.replace(day=1)
            candidates = sorted(
                (
                    purchase
                    for purchase in purchases
                    if available(purchase)
                    and purchase.unit_code == unit_code
                    and purchase.supplier_cnpj == TALISMA_IPIRANGA_ORIGIN
                    and batch_start <= _purchase_date(purchase) <= batch_end
                ),
                key=lambda purchase: (_purchase_date(purchase), purchase.erp_entry_id),
            )
            for cte in batch_ctes:
                target_liters = _liters(
                    Decimal(str(cte.charged_receivable_value or 0)) / TALISMA_ALLOCATION_RATE
                )
                tolerance = max(Decimal("20.000"), target_liters * Decimal("0.002"))
                compatible = sorted(
                    (
                        purchase
                        for purchase in candidates
                        if available(purchase)
                        and abs(_liters(purchase.total_liters) - target_liters) <= tolerance
                    ),
                    key=lambda purchase: (
                        abs(_liters(purchase.total_liters) - target_liters),
                        _purchase_date(purchase),
                        purchase.erp_entry_id,
                    ),
                )
                if not compatible:
                    continue
                purchase = compatible[0]
                allocate(
                    cte,
                    purchase,
                    "Vínculo automático: lote da Talismã, origem Ipiranga, competência anterior e "
                    f"{_liters(purchase.total_liters)} L compatíveis com {target_liters} L nominais "
                    "da cobrança a R$ 0,04/L.",
                )
            covered_until = batch_end

    # Materialize one reproducible evidence row per NF-e. Supplemental records
    # do not carry original NF references, so those fields remain empty.
    for cte in supplemental:
        if cte.erp_cte_id not in eligible_ids:
            continue
        desired = links.get(cte.erp_cte_id, [])
        references = sorted(cte.invoices, key=lambda reference: reference.sequence)
        needed = max(1, len(desired))
        while len(references) < needed:
            reference = FreightCteInvoice(
                erp_cte_id=cte.erp_cte_id,
                sequence=len(references) + 1,
            )
            cte.invoices.append(reference)
            references.append(reference)
        for reference in references[needed:]:
            cte.invoices.remove(reference)
        references = references[:needed]
        for index, reference in enumerate(references):
            reference.sequence = index + 1
            reference.reference_access_key = None
            reference.reference_invoice_number = None
            reference.reference_issue_date = None
            reference.candidate_purchase_entry_id = None
            if index < len(desired):
                purchase, reason = desired[index]
                reference.resolved_purchase_entry_id = purchase.erp_entry_id
                reference.resolution_source = "automatic"
                reference.match_status = "matched"
                reference.match_reason = reason
            else:
                reference.resolved_purchase_entry_id = None
                reference.resolution_source = "none"
                reference.match_status = "missing_reference"
                reference.match_reason = (
                    "O ERP recebeu este CT-e pela entrada de compras, mas as evidências disponíveis "
                    "não formam um vínculo automático seguro com uma NF-e."
                )


def _document_links(
    db: Session, ctes: list[FreightCte], purchases: list[Purchase]
) -> dict[int, set[int]]:
    """Resolve direct evidence and prepare unique, non-binding suggestions."""
    purchases_by_id = {purchase.erp_entry_id: purchase for purchase in purchases}
    purchases_by_key: dict[str, list[Purchase]] = defaultdict(list)
    purchases_by_unit_date: dict[tuple[str, object], list[Purchase]] = defaultdict(list)
    for purchase in purchases:
        if purchase.access_key:
            purchases_by_key[_digits(purchase.access_key)].append(purchase)
        purchases_by_unit_date[(purchase.unit_code, _purchase_date(purchase))].append(purchase)

    direct_claims: dict[int, list[FreightCteInvoice]] = defaultdict(list)
    cte_by_ref: dict[int, FreightCte] = {}
    for cte in ctes:
        for reference in cte.invoices:
            cte_by_ref[reference.id] = cte
            reference.candidate_purchase_entry_id = None
            if reference.resolution_source == "manual" and reference.resolved_purchase_entry_id:
                continue
            reference.resolution_source = "none"
            reference.resolved_purchase_entry_id = None
            key = _digits(reference.reference_access_key)
            if not key:
                reference.match_status = "missing_reference"
                reference.match_reason = (
                    "O ERP recebeu este CT-e pela entrada de compras, mas não materializou "
                    "as NF-es transportadas na tabela padrão."
                    if cte.source_kind == "purchase_entry"
                    else "O CT-e não informa uma chave de NF-e."
                )
                continue
            key_matches = purchases_by_key.get(key, [])
            coherent = [
                purchase
                for purchase in key_matches
                if purchase.unit_code == cte.unit_code and _date_is_coherent(cte, purchase, reference)
            ]
            if len(coherent) == 1:
                direct_claims[coherent[0].erp_entry_id].append(reference)
            elif len(coherent) > 1:
                reference.match_status = "ambiguous_reference"
                reference.match_reason = "A chave encontra mais de uma entrada coerente no ERP."
            elif key_matches and all(purchase.unit_code != cte.unit_code for purchase in key_matches):
                reference.match_status = "wrong_unit"
                reference.match_reason = "A chave informada pertence a outra unidade."
            elif key_matches:
                reference.match_status = "date_mismatch"
                reference.match_reason = "A data da NF-e não é coerente com a emissão do CT-e."
            else:
                reference.match_status = "not_found"
                reference.match_reason = "A chave informada não foi encontrada nas compras sincronizadas."

    used: dict[int, set[int]] = defaultdict(set)
    globally_used: set[int] = set()
    for purchase_id, references in direct_claims.items():
        preferred = [reference for reference in references if not cte_by_ref[reference.id].is_canceled]
        allocatable = preferred if len(preferred) == 1 else references
        if len(allocatable) != 1:
            for reference in references:
                reference.match_status = "duplicate_allocation"
                reference.match_reason = "A mesma NF-e foi referenciada por mais de um CT-e."
            continue
        reference = allocatable[0]
        for duplicate in references:
            if duplicate is not reference:
                duplicate.match_status = "duplicate_allocation"
                duplicate.match_reason = "A NF-e foi priorizada para o CT-e ativo e não pode ser consumida novamente."
        cte = cte_by_ref[reference.id]
        reference.resolved_purchase_entry_id = purchase_id
        reference.resolution_source = "direct"
        reference.match_status = "matched"
        reference.match_reason = "Chave, unidade e datas de emissão coerentes no ERP."
        used[cte.erp_cte_id].add(purchase_id)
        globally_used.add(purchase_id)

    # A manual decision is preserved, but can never consume a purchase already
    # claimed by stronger direct evidence.
    for cte in ctes:
        for reference in cte.invoices:
            if reference.resolution_source != "manual" or not reference.resolved_purchase_entry_id:
                continue
            purchase = purchases_by_id.get(reference.resolved_purchase_entry_id)
            coherent = bool(
                purchase
                and purchase.unit_code == cte.unit_code
                and _date_is_coherent(cte, purchase, reference)
                and purchase.erp_entry_id not in globally_used
            )
            if coherent:
                reference.match_status = "matched"
                reference.match_reason = "Vínculo confirmado por revisão administrativa."
                used[cte.erp_cte_id].add(purchase.erp_entry_id)
                globally_used.add(purchase.erp_entry_id)
            else:
                reference.resolved_purchase_entry_id = None
                reference.resolution_source = "none"
                reference.match_status = "manual_invalidated"
                reference.match_reason = "O vínculo manual deixou de ser coerente após nova sincronização."

    _automatic_document_links(db, ctes, purchases, used, globally_used)

    proposals: dict[int, list[tuple[FreightCteInvoice, Purchase]]] = defaultdict(list)
    for cte in ctes:
        unresolved = [reference for reference in cte.invoices if not reference.resolved_purchase_entry_id]
        if len(unresolved) != 1:
            continue
        reference_date = _source_reference_date(cte)
        rate = find_freight_rate(db, cte, reference_date)
        proven = sum(
            (_liters(purchases_by_id[purchase_id].total_liters) for purchase_id in used[cte.erp_cte_id]),
            ZERO,
        )
        if rate and Decimal(str(rate.rate_per_liter or 0)) > 0:
            target = _liters(Decimal(str(cte.charged_receivable_value or 0)) / Decimal(str(rate.rate_per_liter)))
        else:
            target = _liters(cte.cargo_liters)
        remaining = _liters(target - proven)
        candidates = [
            purchase
            for purchase in purchases_by_unit_date.get((cte.unit_code, reference_date), [])
            if purchase.erp_entry_id not in globally_used and _liters(purchase.total_liters) == remaining
        ]
        origin_candidates = [p for p in candidates if cte.sender_cnpj and p.supplier_cnpj == cte.sender_cnpj]
        if origin_candidates:
            candidates = origin_candidates
        if len(candidates) == 1:
            proposals[candidates[0].erp_entry_id].append((unresolved[0], candidates[0]))

    for candidate_id, candidate_claims in proposals.items():
        if len(candidate_claims) != 1:
            continue
        reference, purchase = candidate_claims[0]
        reference.candidate_purchase_entry_id = candidate_id
        reference.resolution_source = "suggested"
        reference.match_status = "suggested"
        reference.match_reason = (
            f"Candidata única da unidade e data, com {str(_liters(purchase.total_liters))} L compatíveis."
        )
    return used


def rebuild_freight_reconciliations(db: Session) -> int:
    ctes = db.scalars(
        select(FreightCte)
        .where(FreightCte.source_active.is_(True))
        .options(selectinload(FreightCte.invoices))
        .order_by(FreightCte.issue_date, FreightCte.erp_cte_id)
    ).all()
    if not ctes:
        return 0
    min_date = min(cte.issue_date for cte in ctes)
    max_date = max(cte.issue_date for cte in ctes)
    purchases = db.scalars(
        select(Purchase).where(
            (Purchase.invoice_issue_date.between(min_date - timedelta(days=31), max_date))
            | (Purchase.purchase_date.between(min_date - timedelta(days=31), max_date))
        )
    ).all()
    purchases_by_id = {purchase.erp_entry_id: purchase for purchase in purchases}
    used = _document_links(db, ctes, purchases)
    db.flush()

    # Model-57 entries may be received as isolated "freight expense without
    # purchases" documents.  They remain in the raw ERP snapshot, but are
    # outside the fuel-freight scope unless the deterministic matcher proved a
    # link to at least one fuel purchase.  Deleting any old materialization is
    # important: otherwise a historical expense would remain visible forever.
    out_of_scope_ids = {
        cte.erp_cte_id
        for cte in ctes
        if cte.source_kind == "purchase_entry" and not used.get(cte.erp_cte_id)
    }
    if out_of_scope_ids:
        db.execute(
            delete(FreightReconciliation).where(
                FreightReconciliation.erp_cte_id.in_(out_of_scope_ids)
            )
        )
        ctes = [cte for cte in ctes if cte.erp_cte_id not in out_of_scope_ids]
    if not ctes:
        db.flush()
        return 0

    mcte_ids = [cte.erp_cte_id for cte in ctes if cte.source_kind == "mcte"]
    entry_ids = [cte.source_entry_id for cte in ctes if cte.source_entry_id is not None]
    payable_filters = []
    if mcte_ids:
        payable_filters.append(PayableDocument.erp_cte_id.in_(mcte_ids))
    if entry_ids:
        payable_filters.append(PayableDocument.erp_entry_id.in_(entry_ids))
    payable_rows = (
        db.scalars(select(PayableDocument).where(or_(*payable_filters))).all()
        if payable_filters
        else []
    )
    payables: dict[int, list[PayableDocument]] = defaultdict(list)
    cte_by_mcte_id = {
        cte.erp_cte_id: cte.erp_cte_id for cte in ctes if cte.source_kind == "mcte"
    }
    cte_by_entry_id = {
        cte.source_entry_id: cte.erp_cte_id for cte in ctes if cte.source_entry_id is not None
    }
    for payable in payable_rows:
        reconciliation_ids: set[int] = set()
        if payable.erp_cte_id in cte_by_mcte_id:
            reconciliation_ids.add(cte_by_mcte_id[int(payable.erp_cte_id)])
        if payable.erp_entry_id in cte_by_entry_id:
            reconciliation_ids.add(cte_by_entry_id[int(payable.erp_entry_id)])
        for reconciliation_id in reconciliation_ids:
            payables[reconciliation_id].append(payable)

    now = datetime.now(timezone.utc)
    for cte in ctes:
        issues: list[dict[str, str]] = []
        purchase_ids = sorted(used.get(cte.erp_cte_id, set()))
        reference_date = _reconciliation_reference_date(cte, purchase_ids, purchases_by_id)
        purchase_origins = {
            _digits(purchases_by_id[purchase_id].supplier_cnpj)
            for purchase_id in purchase_ids
            if purchases_by_id[purchase_id].supplier_cnpj
        }
        resolved_origin = next(iter(purchase_origins)) if len(purchase_origins) == 1 else None
        rate = find_freight_rate(db, cte, reference_date, resolved_origin)
        matched_liters = _liters(sum((Decimal(str(purchases_by_id[item].total_liters or 0)) for item in purchase_ids), ZERO))
        charged = _money(cte.charged_receivable_value)
        comparison_available = rate is not None and matched_liters > ZERO
        expected = (
            _money(matched_liters * Decimal(str(rate.rate_per_liter)))
            if comparison_available
            else ZERO.quantize(CENT)
        )
        # A CT-e with no proven NF-e volume has no independently verifiable
        # expected freight.  It must be reviewed, but the charged amount is
        # never a "difference" from zero.  This avoids false overcharges such
        # as the historical unit 002 rows with zero matched litres.
        difference = _money(charged - expected) if comparison_available else ZERO.quantize(CENT)
        title_rows = payables.get(cte.erp_cte_id, [])
        payable_value = _money(sum((Decimal(str(row.document_value or 0)) for row in title_rows), ZERO))
        paid_value = _money(
            sum(
                (
                    max(ZERO, Decimal(str(row.document_value or 0)) - Decimal(str(row.balance or 0)))
                    for row in title_rows
                    if row.payment_date is not None or Decimal(str(row.balance or 0)) < Decimal(str(row.document_value or 0))
                ),
                ZERO,
            )
        )
        payable_difference = _money(payable_value - charged)

        unresolved = [reference for reference in cte.invoices if not reference.resolved_purchase_entry_id]
        if not cte.invoices:
            issues.append(_issue("missing_invoice_reference", "O CT-e não possui NF-e transportada informada.", "critical"))
        for reference in unresolved:
            issues.append(
                _issue(
                    reference.match_status or "document_mismatch",
                    reference.match_reason or "A referência da NF-e exige revisão.",
                    "critical" if reference.candidate_purchase_entry_id else "warning",
                )
            )
        if cte.purpose != 0:
            issues.append(_issue("non_normal_purpose", "A finalidade do CT-e não é normal e exige revisão humana.", "critical"))
        if not purchase_ids and not any(reference.reference_issue_date for reference in cte.invoices):
            key_months = [_access_key_month(reference.reference_access_key) for reference in cte.invoices]
            if any(key_months) and reference_date.day == 1:
                issues.append(
                    _issue(
                        "reference_month_from_access_key",
                        "A competência foi obtida do AAMM da chave da NF-e; o ERP não informou o dia exato.",
                        "info",
                    )
                )
        if rate is None:
            issues.append(
                _issue(
                    "missing_rate",
                    "Sem histórico de tarifa para esta transportadora, origem e unidade na competência. "
                    "Somente o valor cobrado pode ser exibido.",
                    "warning",
                )
            )
        elif not matched_liters:
            issues.append(
                _issue(
                    "missing_verified_liters",
                    "Há tarifa cadastrada, mas nenhuma litragem foi comprovada por NF-e; "
                    "não existe base para calcular frete esperado ou diferença.",
                    "warning",
                )
            )
        if cte.cargo_liters and matched_liters and _liters(cte.cargo_liters) != matched_liters:
            issues.append(
                _issue(
                    "cargo_volume_mismatch",
                    f"Carga declara {_liters(cte.cargo_liters)} L; NF-e comprova {matched_liters} L. A NF-e prevalece.",
                    "info",
                )
            )
        if not title_rows:
            linkage = "Cd_CTe" if cte.source_kind == "mcte" else "Cd_Entrada"
            issues.append(_issue("missing_payable", f"Não foi encontrado título vinculado pelo {linkage}.", "warning"))
        elif payable_difference != ZERO:
            issues.append(_issue("payable_mismatch", f"O título diverge do CT-e em R$ {payable_difference}.", "critical"))
        elif any(Decimal(str(row.balance or 0)) > ZERO for row in title_rows):
            issues.append(_issue("payment_pending", "O título está lançado, mas ainda possui saldo em aberto.", "info"))
        if cte.is_canceled and (payable_value > 0 or paid_value > 0):
            issues.append(_issue("canceled_with_payable", "CT-e cancelado possui título lançado ou pagamento.", "critical"))

        if cte.is_canceled:
            primary_status = "canceled"
        elif unresolved or not cte.invoices or cte.purpose != 0:
            primary_status = "document_mismatch"
        elif rate is None:
            primary_status = "unpriced"
        elif not matched_liters:
            primary_status = "document_mismatch"
        elif not title_rows:
            primary_status = "missing_payable"
        elif payable_difference != ZERO:
            primary_status = "payable_mismatch"
        elif difference > ZERO:
            primary_status = "overcharged"
        elif difference < ZERO:
            primary_status = "undercharged"
        else:
            primary_status = "correct"

        severity = "critical" if any(issue["severity"] == "critical" for issue in issues) else (
            "warning" if any(issue["severity"] == "warning" for issue in issues) else "info"
        )
        fingerprint_source = {
            "algorithm": ALGORITHM_VERSION,
            "cte": [
                cte.erp_cte_id,
                cte.source_kind,
                cte.source_entry_id,
                str(charged),
                cte.source_status,
                cte.is_canceled,
                cte.purpose,
            ],
            "references": [
                [
                    reference.sequence,
                    reference.reference_access_key,
                    reference.reference_invoice_number,
                    str(reference.reference_issue_date),
                    reference.resolved_purchase_entry_id,
                    reference.candidate_purchase_entry_id,
                    reference.resolution_source,
                    reference.match_status,
                ]
                for reference in sorted(cte.invoices, key=lambda item: item.sequence)
            ],
            "purchases": [[purchase_id, str(_liters(purchases_by_id[purchase_id].total_liters))] for purchase_id in purchase_ids],
            "rate": [rate.id, str(rate.rate_per_liter)] if rate else None,
            "reference_date": str(reference_date),
            "payables": [[row.id, str(row.document_value), str(row.balance), str(row.payment_date)] for row in title_rows],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_source, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        reconciliation = db.scalar(
            select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == cte.erp_cte_id)
        )
        if not reconciliation:
            reconciliation = FreightReconciliation(erp_cte_id=cte.erp_cte_id, primary_status=primary_status)
            db.add(reconciliation)
        changed = reconciliation.fingerprint != fingerprint
        reconciliation.rate_id = rate.id if rate else None
        reconciliation.reference_date = reference_date
        reconciliation.matched_liters = matched_liters
        reconciliation.expected_value = expected
        reconciliation.charged_value = charged
        reconciliation.payable_value = payable_value
        reconciliation.paid_value = paid_value
        reconciliation.difference_value = difference
        reconciliation.payable_difference = payable_difference
        reconciliation.primary_status = primary_status
        reconciliation.severity = severity
        reconciliation.issues_json = json.dumps(issues, ensure_ascii=False)
        reconciliation.algorithm_version = ALGORITHM_VERSION
        reconciliation.fingerprint = fingerprint
        reconciliation.updated_at = now
        if primary_status == "correct":
            reconciliation.review_status = "not_required"
        elif changed:
            reconciliation.review_status = "pending"
    db.flush()
    return len(ctes)
