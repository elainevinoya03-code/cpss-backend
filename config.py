from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration loaded from environment variables (.env)."""

    # Supabase credentials
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_SECRET_KEY: str = ""

    # PostgreSQL (Supabase connection string)
    DATABASE_URL: str = ""

    APP_NAME: str = "CPSS Backend"
    DEBUG: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Ignores unneeded variables in .env
    )


def get_settings() -> Settings:
    # Tinanggal muna ang @lru_cache para agad nitong mabasa ang bawat pagbabago sa .env
    return Settings()


settings = get_settings()