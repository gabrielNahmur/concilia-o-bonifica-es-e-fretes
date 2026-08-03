import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    "secret",
    [
        "short",
        "development-only-change-me-please",
        "change-me-with-at-least-32-random-characters",
    ],
)
def test_production_rejects_insecure_secret(secret):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key=secret)


def test_production_accepts_strong_secret():
    settings = Settings(app_env="production", secret_key="a-secure-random-secret-with-more-than-32-characters")
    assert settings.secure_cookies is True


def test_bootstrap_admin_email_must_be_usable_by_login():
    with pytest.raises(ValidationError, match="bootstrap_admin_email"):
        Settings(bootstrap_admin_email="admin@gbi.test")
