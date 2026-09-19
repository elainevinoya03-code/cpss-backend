from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration loaded from environment variables (.env)."""

    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_SECRET_KEY: str = ""
    DATABASE_URL: str = ""

    APP_NAME: str = "CPSS Backend"
    DEBUG: bool = False

    # Gmail SMTP / OTP email credentials. These may be set in the environment
    # directly, or configured through the System Settings → OTP page (stored
    # encrypted in the otp_settings table). Values set here act as a fallback /
    # override source for the backend and are never returned to the frontend.
    GMAIL_ADDRESS: str = ""
    GMAIL_APP_PASSWORD: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_SECURITY: str = "STARTTLS"

    # Fernet key used to encrypt the Gmail App Password at rest in the DB.
    # If empty, a stable key is derived from DATABASE_URL (dev convenience);
    # production should set a dedicated secret.
    OTP_CREDENTIAL_ENCRYPTION_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


def get_settings() -> Settings:
    return Settings()


settings = get_settings()