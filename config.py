from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration loaded from environment variables (.env)."""

    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_SECRET_KEY: str = ""
    DATABASE_URL: str = ""

    APP_NAME: str = "CPSS Backend"
    DEBUG: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


def get_settings() -> Settings:
    s = Settings()
    # Mag-print tayo sa terminal para makita ang totoong value
    print("--- DEBUG READ FROM SETTINGS ---")
    print("DATABASE_URL:", s.DATABASE_URL)
    print("--------------------------------")
    return s


settings = get_settings()