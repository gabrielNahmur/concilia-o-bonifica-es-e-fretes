from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalApiRateLimit, FreightOrigin, utcnow


class CnpjLookupError(Exception):
    """The public CNPJ registry could not provide a usable location."""


@dataclass(frozen=True)
class CnpjLocation:
    cnpj: str
    legal_name: str | None
    city: str | None
    state: str | None
    source: str


CNPJ_WS_SOURCE = "cnpj_ws"
CNPJ_WS_WINDOW = timedelta(seconds=60)
CNPJ_WS_MAX_CALLS = 3


def normalize_cnpj(value: str) -> str | None:
    cnpj = "".join(character for character in value if character.isdigit())
    return cnpj if len(cnpj) == 14 else None


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def reserve_cnpj_ws_call(db: Session) -> bool:
    """Persist one API call immediately before the outbound request.

    PostgreSQL serializes concurrent worker/backfill reservations through the
    pre-seeded singleton row. Committing each reservation separately keeps its
    real call instant in the rolling window, even if later work is rolled back.
    """
    row = db.scalar(
        select(ExternalApiRateLimit)
        .where(ExternalApiRateLimit.source == CNPJ_WS_SOURCE)
        .with_for_update()
    )
    if row is None:
        row = ExternalApiRateLimit(source=CNPJ_WS_SOURCE)
        db.add(row)
        db.flush()

    now = utcnow()
    cutoff = now - CNPJ_WS_WINDOW
    recent = sorted(
        _as_utc(value)
        for value in (row.call_1_at, row.call_2_at, row.call_3_at)
        if value is not None and _as_utc(value) > cutoff
    )
    if len(recent) >= CNPJ_WS_MAX_CALLS:
        db.commit()
        return False

    reservations = (recent + [now])[-CNPJ_WS_MAX_CALLS:]
    padded = [None] * (CNPJ_WS_MAX_CALLS - len(reservations)) + reservations
    row.call_1_at, row.call_2_at, row.call_3_at = padded
    row.updated_at = now
    db.commit()
    return True


def fetch_cnpj_location(cnpj: str) -> CnpjLocation | None:
    try:
        response = httpx.get(
            f"https://publica.cnpj.ws/cnpj/{cnpj}",
            timeout=httpx.Timeout(8.0, connect=3.0),
            headers={"User-Agent": "GBI-Contratos/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        establishment = payload["estabelecimento"]
        return CnpjLocation(
            cnpj=cnpj,
            legal_name=payload["razao_social"],
            city=establishment["cidade"]["nome"],
            state=establishment["estado"]["sigla"],
            source="cnpj_ws",
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise CnpjLookupError(str(error)) from error


def refresh_freight_origins(
    db: Session,
    cnpjs: Iterable[str],
    fetch: Callable[[str], CnpjLocation | None] = fetch_cnpj_location,
) -> int:
    refreshed = 0
    for raw_cnpj in cnpjs:
        cnpj = normalize_cnpj(raw_cnpj)
        if not cnpj:
            continue

        origin = db.get(FreightOrigin, cnpj)
        if origin and _clean_location_text(origin.city) and _clean_location_text(origin.state):
            continue

        if origin is None:
            origin = FreightOrigin(cnpj=cnpj)
            db.add(origin)

        now = utcnow()
        if origin.next_retry_at:
            if origin.next_retry_at.tzinfo is None:
                retry_pending = origin.next_retry_at > now.replace(tzinfo=None)
            else:
                retry_pending = origin.next_retry_at.astimezone(timezone.utc) > now.astimezone(timezone.utc)
            if retry_pending:
                continue
        origin.last_lookup_at = now
        try:
            location = fetch(cnpj)
        except CnpjLookupError as error:
            origin.last_error = str(error)
            origin.next_retry_at = now + timedelta(hours=24)
            continue

        if location is None:
            origin.last_error = "CNPJ não encontrado"
            origin.next_retry_at = now + timedelta(hours=24)
            continue

        origin.legal_name = location.legal_name
        origin.city = _clean_location_text(location.city)
        origin.state = _clean_location_text(location.state, uppercase=True)
        origin.source = location.source
        if not origin.city or not origin.state:
            origin.last_error = "Resposta incompleta: cidade e UF são obrigatórias"
            origin.next_retry_at = now + timedelta(hours=24)
            continue
        origin.last_success_at = now
        origin.last_error = None
        origin.next_retry_at = None
        refreshed += 1

    return refreshed


def _clean_location_text(value: str | None, *, uppercase: bool = False) -> str | None:
    cleaned = value.strip() if value else ""
    if not cleaned:
        return None
    return cleaned.upper() if uppercase else cleaned
