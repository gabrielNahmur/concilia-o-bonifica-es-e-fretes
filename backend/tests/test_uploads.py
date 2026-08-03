import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile

from app.services.uploads import UploadValidationError, read_validated_upload


def test_limited_upload_sanitizes_name_and_accepts_matching_signature():
    file = UploadFile(filename="../extrato.pdf", file=BytesIO(b"%PDF-valid"))
    result = asyncio.run(
        read_validated_upload(file, max_bytes=32, signatures={".pdf": (b"%PDF-",)})
    )
    assert result.filename == "extrato.pdf"
    assert result.content == b"%PDF-valid"


def test_limited_upload_rejects_oversize_before_reading_entire_file():
    file = UploadFile(filename="extrato.pdf", file=BytesIO(b"%PDF-" + b"x" * 100))
    with pytest.raises(UploadValidationError) as error:
        asyncio.run(
            read_validated_upload(file, max_bytes=10, signatures={".pdf": (b"%PDF-",)})
        )
    assert error.value.status_code == 413


def test_limited_upload_rejects_extension_signature_mismatch():
    file = UploadFile(filename="extrato.xlsx", file=BytesIO(b"%PDF-invalid"))
    with pytest.raises(UploadValidationError, match="assinatura"):
        asyncio.run(
            read_validated_upload(file, max_bytes=32, signatures={".xlsx": (b"PK\x03\x04",)})
        )
