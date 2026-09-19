from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from otp_service import (
    OTPError,
    OTP_PURPOSES,
    OTP_SECURITIES,
    PURPOSE_LABELS,
    EMAIL_RE,
    issue_otp,
    public_settings,
    get_settings_row,
    encrypt_secret,
    verify_otp,
)

router = APIRouter(prefix="/api/otp", tags=["otp"])


# ── Pydantic models ────────────────────────────────────────────────────

class OtpSettingsIn(BaseModel):
    otpEnabled: bool = True
    gmailAddress: str = ""
    gmailAppPassword: str = ""
    smtpHost: str = "smtp.gmail.com"
    smtpPort: int = Field(default=587, ge=1, le=65535)
    smtpSecurity: str = "STARTTLS"
    otpLength: int = Field(default=6, ge=4, le=10)
    otpExpirationSeconds: int = Field(default=300, ge=30, le=7200)
    maxAttempts: int = Field(default=5, ge=1, le=20)
    resendCooldownSeconds: int = Field(default=60, ge=15, le=3600)
    maxResendAttempts: int = Field(default=5, ge=1, le=20)
    otpPurpose: str = "login"


class OtpSendIn(BaseModel):
    email: str
    purpose: str = ""


class OtpVerifyIn(BaseModel):
    email: str
    code: str
    purpose: str = ""


# ── Validation helpers ─────────────────────────────────────────────────

def _normalize_purpose(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in OTP_PURPOSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid OTP purpose. Choose one of: {', '.join(sorted(OTP_PURPOSES))}",
        )
    return value


def _validate_settings(settings: OtpSettingsIn) -> None:
    address = settings.gmailAddress.strip()
    if address and not EMAIL_RE.match(address):
        raise HTTPException(status_code=400, detail="Please enter a valid Gmail address.")
    if settings.smtpSecurity not in OTP_SECURITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid connection security. Choose one of: {', '.join(sorted(OTP_SECURITIES))}",
        )
    _normalize_purpose(settings.otpPurpose)


async def _upsert_settings(conn, settings: OtpSettingsIn, app_password_encrypted: str) -> dict:
    """Persist the singleton config row; returns the updated public view."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO otp_settings (
                id, otp_enabled, delivery_method, email_provider, gmail_address,
                gmail_app_password_encrypted, smtp_host, smtp_port, smtp_security,
                otp_length, otp_expiration_seconds, max_attempts, resend_cooldown_seconds,
                max_resend_attempts, otp_purpose
            ) VALUES (1, %s, 'email', 'gmail', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                otp_enabled = EXCLUDED.otp_enabled,
                gmail_address = EXCLUDED.gmail_address,
                gmail_app_password_encrypted = CASE
                    WHEN EXCLUDED.gmail_app_password_encrypted = ''
                    THEN otp_settings.gmail_app_password_encrypted
                    ELSE EXCLUDED.gmail_app_password_encrypted END,
                smtp_host = EXCLUDED.smtp_host,
                smtp_port = EXCLUDED.smtp_port,
                smtp_security = EXCLUDED.smtp_security,
                otp_length = EXCLUDED.otp_length,
                otp_expiration_seconds = EXCLUDED.otp_expiration_seconds,
                max_attempts = EXCLUDED.max_attempts,
                resend_cooldown_seconds = EXCLUDED.resend_cooldown_seconds,
                max_resend_attempts = EXCLUDED.max_resend_attempts,
                otp_purpose = EXCLUDED.otp_purpose,
                updated_at = now()
            """,
            (
                settings.otpEnabled,
                settings.gmailAddress.strip(),
                app_password_encrypted,
                settings.smtpHost.strip(),
                int(settings.smtpPort),
                settings.smtpSecurity.strip(),
                int(settings.otpLength),
                int(settings.otpExpirationSeconds),
                int(settings.maxAttempts),
                int(settings.resendCooldownSeconds),
                int(settings.maxResendAttempts),
                _normalize_purpose(settings.otpPurpose),
            ),
        )
    row = await get_settings_row(conn)
    return public_settings(row)


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings():
    """Return OTP + Gmail SMTP configuration (the App Password is never returned)."""
    async with get_db() as conn:
        row = await get_settings_row(conn)
    return public_settings(row)


@router.put("/settings")
async def put_settings(settings: OtpSettingsIn):
    """Save OTP + Gmail SMTP configuration.

    Sending an empty gmailAppPassword keeps the already-configured App Password;
    sending a non-empty value replaces (encrypts) it.
    """
    _validate_settings(settings)
    # Gmail shows App Passwords with spaces ("abcd efgh ijkl mnop") — strip
    # all whitespace so SMTP auth works whether the user pastes with spaces or not.
    app_password = "".join((settings.gmailAppPassword or "").split())
    app_password_encrypted = encrypt_secret(app_password) if app_password else ""
    async with get_db() as conn:
        return await _upsert_settings(conn, settings, app_password_encrypted)


@router.post("/test")
async def send_test_otp(req: OtpSendIn):
    """Send a real test OTP using the configured Gmail account (admin testing).

    Returns a structured result instead of raising HTTP errors so the System
    Settings UI can display a clear success/failure status.
    """
    email = (req.email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        return {
            "success": False,
            "recipient": email,
            "purpose": req.purpose or None,
            "message": "Please enter a valid test recipient email address.",
        }

    try:
        async with get_db() as conn:
            result = await issue_otp(conn, email, purpose=req.purpose or None)
    except OTPError as e:
        return {
            "success": False,
            "recipient": email,
            "purpose": _friendly_purpose(req.purpose),
            "message": e.message,
        }
    except Exception:  # noqa: BLE001 — surface a generic message, never internals
        import logging
        logging.getLogger(__name__).exception("Unexpected error in /api/otp/test")
        return {
            "success": False,
            "recipient": email,
            "purpose": _friendly_purpose(req.purpose),
            "message": "An unexpected error occurred while sending the test OTP.",
        }

    return {
        "success": True,
        "recipient": result["recipient"],
        "purpose": result["purpose"],
        "message": "Test OTP sent successfully.",
        "sentAt": result["sentAt"],
        "expiresAt": result["expiresAt"],
        "expiresInSeconds": result["expiresInSeconds"],
    }


@router.post("/send")
async def send_otp(req: OtpSendIn):
    """Issue an OTP (used by login / account verification / password-reset flows)."""
    try:
        async with get_db() as conn:
            result = await issue_otp(conn, req.email, purpose=req.purpose or None)
    except OTPError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_public()) from e
    return result


@router.post("/verify")
async def verify(req: OtpVerifyIn):
    """Verify a submitted code; invalidates it on success and enforces attempt limits."""
    try:
        async with get_db() as conn:
            result = await verify_otp(conn, req.email, req.code, purpose=req.purpose or None)
    except OTPError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_public()) from e
    return result


def _friendly_purpose(purpose: Optional[str]) -> Optional[str]:
    if not purpose:
        return None
    return PURPOSE_LABELS.get(purpose.strip().lower(), purpose)