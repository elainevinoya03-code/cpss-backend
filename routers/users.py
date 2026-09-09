import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import get_db

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreate(BaseModel):
    name: str
    email: str
    phone: str = ""
    role: str
    purok: str = ""
    sendInvite: bool = True
    password: str = ""
    birthday: str = ""
    sex: str = ""
    civilStatus: str = ""
    address: str = ""


class UserUpdate(BaseModel):
    name: str
    email: str
    phone: str = ""
    role: str
    purok: str = ""
    birthday: str = ""
    sex: str = ""
    civilStatus: str = ""
    address: str = ""


class UserOut(BaseModel):
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
    sendInvite: bool = False
    birthday: str = ""
    sex: str = ""
    civilStatus: str = ""
    address: str = ""


PRIVILEGED_ROLES = {"Admin", "Captain", "Desk Officer"}

ROLE_ID_PREFIXES = {
    "Super Admin": "SA",
    "Captain": "CA",
    "Desk Officer": "DO",
    "CCTV Operator": "CO",
    "Chief Tanod": "CT",
    "Tanod": "TA",
    "Purok Leader": "PL",
    "Resident": "RE",
}


async def _generate_user_id(role: str, conn) -> str:
    prefix = ROLE_ID_PREFIXES.get(role, "US")
    import random
    while True:
        digits = "".join(str(random.randint(0, 9)) for _ in range(8))
        uid = prefix + digits
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM users WHERE user_id = %s", (uid,))
            if not await cur.fetchone():
                return uid


def _row_to_out(row: dict) -> UserOut:
    birthday = ""
    if row.get("birthday"):
        b = row["birthday"]
        birthday = b.isoformat() if hasattr(b, "isoformat") else str(b)
    return UserOut(
        id=row["id"],
        userId=row["user_id"],
        name=row["name"],
        email=row["email"],
        phone=row["phone"] or "",
        role=row["role"],
        purok=row["purok"] or "",
        active=row["active"],
        twoFactor=row["two_factor"],
        lastLogin=row["last_login"] or "—",
        birthday=birthday,
        sex=row["sex"] or "",
        civilStatus=row["civil_status"] or "",
        address=row["address"] or "",
    )


@router.get("", response_model=list[UserOut])
async def list_users():
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM users ORDER BY id ASC")
            rows = await cur.fetchall()
    return [_row_to_out(r) for r in rows]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(req: UserCreate):
    name = req.name.strip()
    email = req.email.strip().lower()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    needs_purok = req.role in ("Chief Tanod", "Tanod", "Purok Leader", "Resident")
    if needs_purok and not req.purok:
        raise HTTPException(status_code=400, detail="Purok/Zone is required for this role")

    privileged = req.role in PRIVILEGED_ROLES

    # Generate password if not provided
    raw_password = req.password.strip() if req.password else _generate_temp_password()
    pw_hash = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    async with get_db() as conn:
        # Check duplicate email
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,))
            if await cur.fetchone():
                raise HTTPException(status_code=409, detail="A user with this email already exists")

        user_id = await _generate_user_id(req.role, conn)
        two_factor = "pending" if privileged else "none"

        async with conn.cursor() as cur:
            birthday = req.birthday.strip() if req.birthday else None
            await cur.execute(
                """INSERT INTO users (user_id, name, email, phone, password, role, purok, active, two_factor, last_login, birthday, sex, civil_status, address)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, '—', %s, %s, %s, %s)
                   RETURNING *""",
                (user_id, name, email, req.phone.strip(), pw_hash, req.role, req.purok if needs_purok else "", two_factor, birthday, req.sex, req.civilStatus, req.address.strip()),
            )
            row = await cur.fetchone()

    result = _row_to_out(row)
    result.sendInvite = req.sendInvite
    return result


@router.put("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, req: UserUpdate):
    name = req.name.strip()
    email = req.email.strip().lower()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    needs_purok = req.role in ("Chief Tanod", "Tanod", "Purok Leader", "Resident")
    if needs_purok and not req.purok:
        raise HTTPException(status_code=400, detail="Purok/Zone is required for this role")

    async with get_db() as conn:
        # Check user exists
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            existing = await cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="User not found")

        # Check duplicate email (excluding self)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM users WHERE lower(email) = %s AND id != %s",
                (email, user_id),
            )
            if await cur.fetchone():
                raise HTTPException(status_code=409, detail="A user with this email already exists")

        was_privileged = existing["role"] in PRIVILEGED_ROLES
        now_privileged = req.role in PRIVILEGED_ROLES
        two_factor = existing["two_factor"]
        if now_privileged and not was_privileged:
            two_factor = "pending"
        elif not now_privileged and was_privileged:
            two_factor = "none"

        async with conn.cursor() as cur:
            birthday = req.birthday.strip() if req.birthday else None
            await cur.execute(
                """UPDATE users
                   SET name = %s, email = %s, phone = %s, role = %s, purok = %s, two_factor = %s, birthday = %s, sex = %s, civil_status = %s, address = %s, updated_at = now()
                   WHERE id = %s
                   RETURNING *""",
                (name, email, req.phone.strip(), req.role, req.purok if needs_purok else "", two_factor, birthday, req.sex, req.civilStatus, req.address.strip(), user_id),
            )
            row = await cur.fetchone()

    return _row_to_out(row)


@router.delete("/{user_id}")
async def delete_user(user_id: int):
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = await cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            await cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return {"message": "User deleted successfully"}


@router.patch("/{user_id}/toggle-active")
async def toggle_active(user_id: int):
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET active = NOT active, updated_at = now() WHERE id = %s RETURNING *",
                (user_id,),
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return _row_to_out(row)


@router.patch("/{user_id}/reset-password")
async def reset_password(user_id: int):
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = await cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            new_pw = _generate_temp_password()
            pw_hash = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            await cur.execute(
                "UPDATE users SET password = %s, updated_at = now() WHERE id = %s",
                (pw_hash, user_id),
            )
    return {"message": "Password reset successfully", "temporaryPassword": new_pw}


def _generate_temp_password() -> str:
    import random
    import string
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))
