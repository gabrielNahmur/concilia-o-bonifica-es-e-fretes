from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import EmailStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ENV = BACKEND_DIR.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", REPOSITORY_ENV),
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Contratos GBI"
    app_url: str = "http://localhost:8000"
    secret_key: str = "development-only-change-me-please"
    access_token_minutes: int = 720
    database_url: str = "sqlite:///./contracts.db"

    erp_db_server: str = "127.0.0.1"
    erp_db_port: int = 11433
    erp_db_name: str = "smb001"
    erp_db_user: str = "sa"
    erp_db_password: str = ""
    erp_sync_start_date: str = "2022-04-29"
    erp_freight_sync_start_date: str = "2025-08-01"
    erp_sync_interval_minutes: int = 60

    resend_api_key: str = ""
    resend_from_email: str = "nao-responda@atendimento-gbi.online"
    report_day: int = 5
    report_time: str = "07:00"
    monthly_reports_enabled: bool = False
    timezone: str = "America/Sao_Paulo"
    reports_dir: str = "./reports"

    bootstrap_admin_email: EmailStr = "admin@gbi.com"
    bootstrap_admin_name: str = "Administrador GBI"
    bootstrap_admin_password: str = ""

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Self:
        if self.app_env.lower() in {"production", "prod"}:
            insecure_values = {
                "development-only-change-me-please",
                "change-me-with-at-least-32-random-characters",
            }
            if len(self.secret_key) < 32 or self.secret_key in insecure_values:
                raise ValueError("SECRET_KEY deve possuir ao menos 32 caracteres aleatorios em producao")
        return self

    @property
    def reports_path(self) -> Path:
        path = Path(self.reports_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def secure_cookies(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
