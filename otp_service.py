"""OTP email service for CPSS.

Implements OTP generation, secure storage (SHA-256 hash), expiration,
verification with attempt limiting, resend cooldown/resend limits, and the
Gmail SMTP sender used to deliver verification codes.

Security rules honored here (also enforced at the API layer):
  - The Gmail App Password is never logged, never returned through the API,
    and is stored encrypted (Fernet) at rest in otp_settings.
  - Plaintext OTP values are never stored and never logged — only the hash.
"""

import asyncio
import base64
import hashlib
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import get_settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

OTP_PURPOSES = {"login", "account_verification", "password_reset"}
PURPOSE_LABELS = {
    "login": "Login",
    "account_verification": "Account Verification",
    "password_reset": "Password Reset",
}
OTP_SECURITIES = {"STARTTLS", "SSL/TLS", "NONE"}
VALID_OTP_STATUSES = {"active", "verified", "expired", "superseded", "failed"}

# Resend/send limits: at most MAX_SENDS_PER_WINDOW sends (initial + resends)
# per email+purpose within a rolling SEND_WINDOW_SECONDS window. Once the
# limit is reached, the user must wait until the oldest send in the window
# drops out. Also enforced by the resident app (frontend) and the web dashboard.
MAX_SENDS_PER_WINDOW = 3
SEND_WINDOW_SECONDS = 3600  # 60 minutes

SYSTEM_NAME = "CPSS (Culiat Public Safety System)"


class OTPError(Exception):
    """Raised for expected OTP flow failures.

    code: stable machine-readable code used by the API layer.
    status_code: HTTP status appropriate for the failure.
    retry_after: optional number of seconds the client should wait before
        retrying (used by cooldown / limit errors).
    """

    def __init__(self, code: str, message: str, status_code: int = 400, retry_after: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after

    def to_public(self) -> dict:
        """Structured payload safe to expose through the API."""
        public = {"code": self.code, "message": self.message}
        if self.retry_after is not None:
            public["retryAfterSeconds"] = int(self.retry_after)
        return public


# ── Helpers ────────────────────────────────────────────────────────────

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def generate_otp(length: int) -> str:
    """Return a cryptographically random numeric OTP of the given length."""
    return "".join(str(secrets.randbelow(10)) for _ in range(max(1, int(length))))


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _fernet():
    from cryptography.fernet import Fernet

    settings = get_settings()
    key = (settings.OTP_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if key:
        try:
            return Fernet(key.encode("utf-8"))
        except Exception:
            # Invalid configured key — fall back to the derived key below so the
            # stored secrets stay decryptable between deployments.
            pass
    # Derive a stable 32-byte key from DATABASE_URL. This keeps values we have
    # already encrypted readable across restarts even when no dedicated key is
    # configured (local/dev convenience); production can set the env var.
    digest = hashlib.sha256((settings.DATABASE_URL or "cpss").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        return None


# ── Settings helpers ────────────────────────────────────────────────────

async def get_settings_row(conn) -> dict:
    """Return the otp_settings singleton row, seeding defaults if missing."""
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM otp_settings WHERE id = 1")
        row = await cur.fetchone()
    if row is None:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO otp_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
            )
            await cur.execute("SELECT * FROM otp_settings WHERE id = 1")
            row = await cur.fetchone()
    return row or {}


def public_settings(row: dict) -> dict:
    """Return the settings for the frontend — the App Password is NEVER included."""
    return {
        "otpEnabled": bool(row.get("otp_enabled", True)),
        "deliveryMethod": row.get("delivery_method") or "email",
        "emailProvider": row.get("email_provider") or "gmail",
        "gmailAddress": row.get("gmail_address") or "",
        "gmailPasswordSet": bool(row.get("gmail_app_password_encrypted")),
        "smtpHost": row.get("smtp_host") or "smtp.gmail.com",
        "smtpPort": int(row.get("smtp_port") or 587),
        "smtpSecurity": row.get("smtp_security") or "STARTTLS",
        "otpLength": int(row.get("otp_length") or 6),
        "otpExpirationSeconds": int(row.get("otp_expiration_seconds") or 300),
        "maxAttempts": int(row.get("max_attempts") or 5),
        "resendCooldownSeconds": int(row.get("resend_cooldown_seconds") or 60),
        "maxResendAttempts": int(row.get("max_resend_attempts") or 5),
        "otpPurpose": row.get("otp_purpose") or "login",
    }


def _smtp_address(row: dict) -> Optional[str]:
    address = (row.get("gmail_address") or "").strip()
    if not address:
        address = (get_settings().GMAIL_ADDRESS or "").strip()
    return address or None


def _app_password(row: dict) -> Optional[str]:
    stored = (row.get("gmail_app_password_encrypted") or "").strip()
    if stored:
        decrypted = decrypt_secret(stored)
        if decrypted:
            return decrypted
    return (get_settings().GMAIL_APP_PASSWORD or "").strip() or None


def _utc(value) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ── Email sending ──────────────────────────────────────────────────────

def _expires_label(expires_at: datetime) -> str:
    local = _utc(expires_at).astimezone()
    return local.strftime("%B %d, %Y at %I:%M %p")


def build_otp_email(code: str, purpose: str, expires_at: datetime) -> tuple[str, str]:
    """Return (plain_text, html) for the CPSS verification email."""
    expires_str = _expires_label(expires_at)
    purpose_label = PURPOSE_LABELS.get(purpose, "Login")

    text = (
        f"{SYSTEM_NAME}\n\n"
        f"Hello,\n\n"
        f"Your verification code for {purpose_label} is:\n\n"
        f"    {code}\n\n"
        f"This code expires on {expires_str}.\n\n"
        f"For your security, do not share this code with anyone. CPSS personnel will never ask you for it.\n\n"
        f"Regards,\nThe CPSS Team"
    )

    html = f"""\
<html>
  <body style="margin:0;padding:0;background-color:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9;padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="max-width:520px;width:100%;background-color:#ffffff;border-radius:12px;border:1px solid #e7e5e4;overflow:hidden;">
            <tr>
              <td style="background-color:#0038A8;padding:18px 24px;">
                <span style="color:#ffffff;font-size:16px;font-weight:bold;">Culiat Public Safety System (CPSS)</span>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 24px;">
                <p style="margin:0 0 6px;color:#1c1917;font-size:15px;font-weight:600;">Your verification code</p>
                <p style="margin:0 0 18px;color:#78716c;font-size:13px;">Use this code to complete your {purpose_label.lower()} verification.</p>
                <table role="presentation" cellpadding="0" cellspacing="0" style="background-color:#eef2ff;border:1px dashed #c7d2fe;border-radius:10px;padding:0;">
                  <tr>
                    <td align="center" style="padding:20px;">
                      <span style="letter-spacing:8px;font-size:32px;font-weight:bold;color:#0038A8;">{code}</span>
                    </td>
                  </tr>
                </table>
                <p style="margin:18px 0 0;color:#57534e;font-size:13px;">
                  This code expires on <strong>{expires_str}</strong>.
                </p>
                <p style="margin:14px 0 0;color:#a8a29e;font-size:12px;line-height:1.5;">
                  For your security, do not share this code with anyone. CPSS personnel will never ask you for it.
                </p>
              </td>
            </tr>
            <tr>
              <td style="background-color:#f5f5f4;padding:14px 24px;">
                <span style="color:#a8a29e;font-size:11px;">&copy; {datetime.now().year} Culiat Public Safety System</span>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    return text, html


def _smtp_send(
    to_email: str,
    settings_row: dict,
    username: str,
    password: str,
    code: str,
    expires_at: datetime,
    purpose: str,
) -> None:
    """Send the OTP email synchronously via the configured SMTP server."""
    text, html = build_otp_email(code, purpose, expires_at)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your CPSS Verification Code"
    msg["From"] = f"{SYSTEM_NAME} <{username}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    host = (settings_row.get("smtp_host") or "smtp.gmail.com").strip()
    port = int(settings_row.get("smtp_port") or 587)
    security = (settings_row.get("smtp_security") or "STARTTLS").upper()

    if security == "SSL/TLS":
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if security == "STARTTLS":
                server.starttls()
                server.ehlo()
            server.login(username, password)
            server.send_message(msg)


# ── OTP issuance ───────────────────────────────────────────────────────

async def issue_otp(conn, email: str, purpose: Optional[str] = None) -> dict:
    """Create, store, and email a new OTP for the recipient.

    Enforces: valid email, configured+enabled OTP, complete Gmail config,
    a resend cooldown between consecutive sends, and a rolling-window cap of
    MAX_SENDS_PER_WINDOW sends within SEND_WINDOW_SECONDS. Only one active
    code per recipient+purpose is kept (previous active codes are superseded).
    """
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        raise OTPError("INVALID_EMAIL", "Please enter a valid email address.", 400)

    row = await get_settings_row(conn)
    if not row.get("otp_enabled", True):
        raise OTPError("OTP_DISABLED", "OTP email verification is currently disabled.", 403)

    purpose = (purpose or "").strip().lower()
    if not purpose:
        purpose = (row.get("otp_purpose") or "login").lower()
    if purpose not in OTP_PURPOSES:
        raise OTPError(
            "INVALID_PURPOSE",
            "Invalid OTP purpose. Choose one of: " + ", ".join(sorted(OTP_PURPOSES)),
            400,
        )

    address = _smtp_address(row)
    password = _app_password(row)
    if not address or not password:
        raise OTPError(
            "GMAIL_CONFIG_INCOMPLETE",
            "Gmail configuration is incomplete. Set the Gmail address and App Password first.",
            400,
        )

    now = datetime.now(timezone.utc)
    cooldown = int(row.get("resend_cooldown_seconds") or 60)
    window = SEND_WINDOW_SECONDS
    max_sends = MAX_SENDS_PER_WINDOW

    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT * FROM otp_records
            WHERE lower(email) = %s AND purpose = %s
              AND created_at >= %s
            ORDER BY id ASC
            """,
            (email, purpose, now - timedelta(seconds=window)),
        )
        recent = await cur.fetchall()

    recent = [r for r in recent if r.get("status") != "failed"]

    if recent:
        latest_send = _utc(recent[-1].get("last_resend_at") or recent[-1].get("created_at"))
        elapsed = (now - latest_send).total_seconds()
        if elapsed < cooldown:
            retry_after = max(1, int(cooldown - elapsed))
            raise OTPError(
                "RESEND_COOLDOWN",
                f"A code was recently sent. Please wait {retry_after} second(s) before requesting another one.",
                429,
                retry_after=retry_after,
            )

        if len(recent) >= max_sends:
            oldest_send = _utc(recent[0].get("created_at"))
            wait_by = oldest_send + timedelta(seconds=window)
            if wait_by > now:
                retry_after = max(1, int((wait_by - now).total_seconds()))
                minutes, seconds = divmod(retry_after, 60)
                raise OTPError(
                    "SEND_LIMIT_REACHED",
                    f"You've reached the limit of {max_sends} verification codes in the last hour. "
                    f"Please wait {minutes} minute(s) and {seconds} second(s) before requesting another one.",
                    429,
                    retry_after=retry_after,
                )

        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE otp_records SET status = 'superseded', superseded_at = %s WHERE id = ANY(%s)",
                (now, [r["id"] for r in recent]),
            )

    prior_resends = len(recent)

    code = generate_otp(int(row.get("otp_length") or 6))
    expires_at = now + timedelta(seconds=int(row.get("otp_expiration_seconds") or 300))

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO otp_records (otp_hash, email, purpose, status, attempts, resends, expires_at, last_resend_at)
            VALUES (%s, %s, %s, 'active', 0, %s, %s, %s)
            RETURNING id
            """,
            (hash_otp(code), email, purpose, prior_resends, expires_at, now),
        )
        record_id = (await cur.fetchone())["id"]

    try:
        await asyncio.to_thread(
            _smtp_send, email, row, address, password, code, expires_at, purpose
        )
    except Exception as exc:  # noqa: BLE001 — never leak SMTP details to clients
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE otp_records SET status = 'failed' WHERE id = %s", (record_id,)
            )
        raise OTPError(
            "SEND_FAILED",
            "The OTP email could not be sent. Check the Gmail configuration and try again.",
            502,
        ) from exc

    return {
        "recipient": email,
        "purpose": purpose,
        "sentAt": now.isoformat(),
        "expiresAt": expires_at.isoformat(),
        "expiresInSeconds": int(row.get("otp_expiration_seconds") or 300),
    }


# ── OTP verification ───────────────────────────────────────────────────

async def verify_otp(conn, email: str, code: str, purpose: Optional[str] = None) -> dict:
    """Verify a submitted code against the active OTP record.

    Enforces expiry and the maximum verification-attempt limit; invalidates the
    code (status = verified) immediately on success and permanently locks it
    once attempts are exhausted.
    """
    email = normalize_email(email)
    code = (code or "").strip()
    if not email or not code:
        raise OTPError("INVALID_OTP", "Email and verification code are required.", 400)

    purpose = (purpose or "").strip().lower()
    if not purpose:
        purpose = "login"
    if purpose not in OTP_PURPOSES:
        raise OTPError(
            "INVALID_PURPOSE",
            "Invalid OTP purpose. Choose one of: " + ", ".join(sorted(OTP_PURPOSES)),
            400,
        )

    row = await get_settings_row(conn)
    max_attempts = int((row or {}).get("max_attempts") or 5)
    now = datetime.now(timezone.utc)

    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT * FROM otp_records
            WHERE lower(email) = %s AND purpose = %s AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """,
            (email, purpose),
        )
        rec = await cur.fetchone()

    if rec is None:
        raise OTPError("INVALID_OTP", "Invalid or expired verification code.", 400)

    expires_at = _utc(rec.get("expires_at"))
    attempts_used = int(rec.get("attempts") or 0)

    if expires_at < now:
        await _set_status(conn, rec["id"], "expired")
        raise OTPError("EXPIRED_OTP", "This verification code has expired. Please request a new one.", 410)

    if attempts_used >= max_attempts:
        await _set_status(conn, rec["id"], "expired")
        raise OTPError("ATTEMPTS_EXCEEDED", "Too many incorrect attempts. Please request a new code.", 429)

    new_attempt = attempts_used + 1
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE otp_records SET attempts = %s WHERE id = %s", (new_attempt, rec["id"])
        )

    if not secrets.compare_digest(hash_otp(code), rec["otp_hash"]):
        if new_attempt >= max_attempts:
            await _set_status(conn, rec["id"], "expired")
            raise OTPError("ATTEMPTS_EXCEEDED", "Too many incorrect attempts. Please request a new code.", 429)
        remaining = max_attempts - new_attempt
        raise OTPError(
            "INVALID_OTP",
            f"Incorrect verification code. {remaining} attempt(s) remaining."
            if remaining > 0
            else "Incorrect verification code. Please request a new one.",
            400,
        )

    await _set_status(conn, rec["id"], "verified", verified_at=now)
    return {
        "success": True,
        "email": email,
        "purpose": purpose,
        "message": "Code verified successfully.",
    }


async def _set_status(conn, record_id: int, status: str, verified_at: Optional[datetime] = None):
    assert status in VALID_OTP_STATUSES
    async with conn.cursor() as cur:
        if status == "verified":
            await cur.execute(
                "UPDATE otp_records SET status = %s, verified_at = %s WHERE id = %s",
                (status, verified_at, record_id),
            )
        else:
            await cur.execute(
                "UPDATE otp_records SET status = %s WHERE id = %s",
                (status, record_id),
            )