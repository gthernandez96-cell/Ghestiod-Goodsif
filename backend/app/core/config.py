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
    DATABASE_URL: str = "sqlite+aiosqlite:///./pos.db"
    DATABASE_URL_SYNC: str = "sqlite:///./pos.db"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 2
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 300
    DB_PGBOUNCER: bool = False

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_async_db_url(cls, v: Optional[str]) -> str:
        if not v:
            return "sqlite+aiosqlite:///./pos.db"
        import urllib.parse
        # DigitalOcean y servicios cloud frecuentemente proveen postgres:// o postgresql://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg no acepta 'sslmode', requiere 'ssl'
        if "?" in v:
            parsed = urllib.parse.urlsplit(v)
            query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            new_query = [(('ssl', val) if k == 'sslmode' else (k, val)) for k, val in query_params]
            v = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(new_query), parsed.fragment))
        return v

    @field_validator("DATABASE_URL_SYNC", mode="before")
    @classmethod
    def assemble_sync_db_url(cls, v: Optional[str]) -> str:
        if not v:
            return "sqlite:///./pos.db"
        import urllib.parse
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql+asyncpg://"):
            v = v.replace("postgresql+asyncpg://", "postgresql://", 1)
        # psycopg2 requiere 'sslmode' en lugar de 'ssl'
        if "?" in v:
            parsed = urllib.parse.urlsplit(v)
            query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            new_query = [(('sslmode', val) if k == 'ssl' else (k, val)) for k, val in query_params]
            v = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(new_query), parsed.fragment))
        return v

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Security
    SECRET_KEY: str = "clave-secreta-pos-2024"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # CORS
    ALLOWED_ORIGINS: Union[List[str], str] = [
        "http://localhost:8000",
        "https://productosautomotricesdelsur.com",
        "https://www.productosautomotricesdelsur.com",
    ]

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
    INIT_ADMIN_PASSWORD: str = "admin1234"


settings = Settings()
