from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


class UploadValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    content: bytes


async def read_validated_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    signatures: dict[str, tuple[bytes, ...]],
) -> ValidatedUpload:
    """Read at most max_bytes and verify extension plus file signature."""
    filename = Path(file.filename or "arquivo").name[:255]
    suffix = Path(filename).suffix.lower()
    if suffix not in signatures:
        allowed = ", ".join(sorted(signatures))
        raise UploadValidationError(f"Formato nao permitido. Envie um arquivo {allowed}")
    try:
        content = await file.read(max_bytes + 1)
    finally:
        await file.close()
    if not content:
        raise UploadValidationError("O arquivo enviado esta vazio")
    if len(content) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise UploadValidationError(f"O arquivo excede o limite de {limit_mb} MB", status_code=413)
    if not any(content.startswith(signature) for signature in signatures[suffix]):
        raise UploadValidationError("A assinatura do arquivo nao corresponde a extensao informada")
    return ValidatedUpload(filename=filename, content=content)
