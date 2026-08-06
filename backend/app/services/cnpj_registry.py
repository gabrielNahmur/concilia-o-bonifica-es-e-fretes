from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import FreightOrigin, utcnow


class CnpjLookupError(Exception):
    """The public CNPJ registry could not provide a usable location."""


@dataclass(frozen=True)
class CnpjLocation:
    cnpj: str
    legal_name: str | None
    city: str | None
    state: str | None
    source: str


def normalize_cnpj(value: str) -> str | None:
    cnpj = "".join(character for character in value if character.isdigit())
    return cnpj if len(cnpj) == 14 else None


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
        if origin and origin.city and origin.state:
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
        origin.city = location.city
        origin.state = location.state
        origin.source = location.source
        origin.last_success_at = now
        origin.last_error = None
        origin.next_retry_at = None
        refreshed += 1

    return refreshed
