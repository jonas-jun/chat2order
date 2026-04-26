from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str

    juso_api_key: str = ""

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    cookie_secure: bool = True
    cookie_samesite: str = "lax"

    cors_origins: list[str] = []

    prompt_order_extraction: str = ""
    prompt_address_to_search: str = ""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
