from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ExpenseLens"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://expenselens:change-me@db:5432/expenselens"
    secret_key: str = "change-this-to-a-random-secret-of-at-least-32-characters"
    secure_cookies: bool = True
    allow_registration: bool = True
    frontend_origin: str = "http://localhost:5173"
    storage_dir: str = "./data/documents"
    max_upload_bytes: int = 12 * 1024 * 1024
    ocr_provider: str = "tesseract"
    vision_api_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_key: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    webauthn_rp_id: str = ""
    webauthn_rp_name: str = "ExpenseLens"
    rates_provider: str = ""
    telegram_notify_hour: int = 8

    @field_validator("secret_key")
    @classmethod
    def require_secret_in_production(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
