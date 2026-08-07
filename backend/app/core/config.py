from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator
from typing import List, Optional, Union
import json


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_NAME: str = "POS Empresarial"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # CORS
    ALLOWED_ORIGINS: Union[List[str], str] = ["http://localhost:8000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return [v]
        return v

    # FEL
    FEL_PROVIDER: str = "generic"
    FEL_API_URL: Optional[str] = None
    FEL_API_KEY: Optional[str] = None
    FEL_CERTIFICATE_ID: Optional[str] = None
    FEL_SIGNER_USER: Optional[str] = None
    FEL_SIGNER_PASS: Optional[str] = None

    # Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAILS_FROM: str = "noreply@pos.com"
    EMAILS_FROM_NAME: str = "POS Empresarial"

    # Initial Company
    INIT_COMPANY_NAME: str = "Mi Empresa S.A."
    INIT_COMPANY_NIT: str = "123456789"
    INIT_ADMIN_EMAIL: str = "admin@empresa.com"
    INIT_ADMIN_PASSWORD: str = "Admin2024!"


settings = Settings()
