import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    id: int
    userId: str
    name: str
    email: str
    phone: str
    role: str
    purok: str
    active: bool
    twoFactor: str
    lastLogin: str


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM users WHERE lower(email) = %s",
                (email,),
            )
            user = await cur.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    stored_hash = user["password"]
    if isinstance(stored_hash, str):
        stored_hash = stored_hash.encode("utf-8")

    if not bcrypt.checkpw(req.password.encode("utf-8"), stored_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user["active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # Update last_login
    async with get_db() as conn:
        async with conn.cursor() as cur:
            from datetime import datetime, timezone
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            await cur.execute(
                "UPDATE users SET last_login = %s WHERE id = %s",
                (now_str, user["id"]),
            )

    return LoginResponse(
        id=user["id"],
        userId=user["user_id"],
        name=user["name"],
        email=user["email"],
        phone=user["phone"] or "",
        role=user["role"],
        purok=user["purok"] or "",
        active=user["active"],
        twoFactor=user["two_factor"],
        lastLogin=user["last_login"] or "—",
    )
