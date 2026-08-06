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


def _locked_cnpj_ws_limiter(db: Session) -> ExternalApiRateLimit:
    """Lock the pre-seeded singleton shared by worker and backfill."""
    row = db.scalar(
        select(ExternalApiRateLimit)
        .where(ExternalApiRateLimit.source == CNPJ_WS_SOURCE)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = ExternalApiRateLimit(source=CNPJ_WS_SOURCE)
        db.add(row)
        db.flush()
    return row


def _recent_cnpj_ws_calls(row: ExternalApiRateLimit, now) -> list:
    cutoff = now - CNPJ_WS_WINDOW
    return sorted(
        _as_utc(value)
        for value in (row.call_1_at, row.call_2_at, row.call_3_at)
        if value is not None and _as_utc(value) > cutoff
    )


def _record_cnpj_ws_call(row: ExternalApiRateLimit, completed_at) -> None:
    calls = (_recent_cnpj_ws_calls(row, completed_at) + [completed_at])[-CNPJ_WS_MAX_CALLS:]
    padded = [None] * (CNPJ_WS_MAX_CALLS - len(calls)) + calls
    row.call_1_at, row.call_2_at, row.call_3_at = padded
    row.updated_at = completed_at


def _origin_is_complete(origin: FreightOrigin | None) -> bool:
    return bool(
        origin
        and _clean_location_text(origin.city)
        and _clean_location_text(origin.state)
    )


def _origin_retry_is_pending(origin: FreightOrigin | None, now) -> bool:
    if origin is None or origin.next_retry_at is None:
        return False
    if origin.next_retry_at.tzinfo is None:
        return origin.next_retry_at > now.replace(tzinfo=None)
    return origin.next_retry_at.astimezone(timezone.utc) > now.astimezone(timezone.utc)


def refresh_freight_origin_under_cnpj_ws_limit(
    db: Session,
    raw_cnpj: str,
    refresh: Callable[[Session, Iterable[str]], int],
) -> tuple[bool, int]:
    """Revalidate and execute one lookup while holding the global API lock.

    The singleton remains locked from the quota decision until the refresh
    returns. The persisted instant is the conservative completion time, so a
    delayed/preempted fetch cannot make its slot expire before the real call.
    """
    cnpj = normalize_cnpj(raw_cnpj)
    if not cnpj:
        return False, 0

    limiter = _locked_cnpj_ws_limiter(db)
    origin = db.scalar(
        select(FreightOrigin)
        .where(FreightOrigin.cnpj == cnpj)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    now = utcnow()
    if _origin_is_complete(origin) or _origin_retry_is_pending(origin, now):
        db.commit()
        return False, 0

    if len(_recent_cnpj_ws_calls(limiter, now)) >= CNPJ_WS_MAX_CALLS:
        db.commit()
        return False, 0

    try:
        enriched = refresh(db, [cnpj])
    except CnpjLookupError:
        _record_cnpj_ws_call(limiter, utcnow())
        db.commit()
        raise

    _record_cnpj_ws_call(limiter, utcnow())
    db.commit()
    return True, enriched


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
        if _origin_is_complete(origin):
            continue

        if origin is None:
            origin = FreightOrigin(cnpj=cnpj)
            db.add(origin)

        now = utcnow()
        if _origin_retry_is_pending(origin, now):
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
