from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from db import get_db

router = APIRouter(prefix="/api/roster-members", tags=["roster-members"])


# ── Pydantic models ─────────────────────────────────────────────────────
# Field names are camelCase on purpose: they mirror the frontend types
# RosterMember / SkillsInventory in
# cpss-frontend/src/chief_tanod/patrolScheduleShared.ts so the API payload
# round-trips without transformation.


class TrainingCertification(BaseModel):
    hasTraining: bool = False
    certificateDate: Optional[str] = None
    trainingProvider: Optional[str] = None
    certificateNumber: Optional[str] = None
    proofOfTraining: Optional[str] = None


class SkillsInventory(BaseModel):
    existingTanodExperience: str = ""
    basicPatrolExperience: str = ""
    firstAidTraining: TrainingCertification = TrainingCertification()
    selfDefenseTraining: bool = False
    disasterResponseTraining: bool = False
    crowdControlTraining: bool = False
    radioCommunicationSkills: bool = False
    humanRightsOrientation: bool = False
    otherRelevantSkills: str = ""
    certificateNumbers: str = ""


class RosterMemberCreate(BaseModel):
    id: str
    name: str
    purok: str = ""
    skills: List[str] = []
    experienceYears: int = 0
    performance: int = 0
    available: bool = True
    lastDutyAt: str = ""
    skillsInventory: Optional[SkillsInventory] = None
    userId: Optional[int] = None


class RosterMemberUpdate(BaseModel):
    name: Optional[str] = None
    purok: Optional[str] = None
    skills: Optional[List[str]] = None
    experienceYears: Optional[int] = None
    performance: Optional[int] = None
    available: Optional[bool] = None
    lastDutyAt: Optional[str] = None
    skillsInventory: Optional[SkillsInventory] = None
    userId: Optional[int] = None


class RosterMemberOut(BaseModel):
    id: str
    name: str
    purok: str
    skills: List[str]
    experienceYears: int
    performance: int
    available: bool
    lastDutyAt: str
    skillsInventory: Optional[dict] = None
    userId: Optional[int] = None
    createdAt: str
    updatedAt: str


# ── Helpers ─────────────────────────────────────────────────────────────


def _iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _norm_json(raw, default):
    """psycopg3 can return JSONB as a Python object or as a JSON string —
    normalize both so the response always contains a real list/dict."""
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw)
        except Exception:
            return default
    if isinstance(raw, dict) and raw == {} and default == []:
        return default
    if raw is None:
        return default
    return raw


def _norm_skills(raw) -> list:
    value = _norm_json(raw, [])
    return value if isinstance(value, list) else []


def _norm_skills_inventory(raw) -> dict:
    value = _norm_json(raw, {})
    return value if isinstance(value, dict) else {}


def _row_to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "purok": row.get("purok") or "",
        "skills": _norm_skills(row.get("skills")),
        "experienceYears": row.get("experience_years") or 0,
        "performance": row.get("performance") or 0,
        "available": bool(row["available"]) if row.get("available") is not None else True,
        "lastDutyAt": row.get("last_duty_at") or "",
        "skillsInventory": _norm_skills_inventory(row.get("skills_inventory")),
        "userId": row.get("user_id"),
        "createdAt": _iso(row.get("created_at")),
        "updatedAt": _iso(row.get("updated_at")),
    }


async def _fetch_member(conn, tanod_id: str) -> Optional[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT * FROM roster_members WHERE id = %s", (tanod_id,)
        )
        row = await cur.fetchone()
    return _row_to_out(row) if row else None


async def _sync_registered_tanods(conn) -> None:
    """Materialize every registered Tanod account (users.role = 'Tanod')
    into roster_members so the roster is the union of registered accounts and
    any manually maintained rows.

    Mirrors database/013_link_registered_tanods_to_roster.sql and acts as a
    runtime safety net for deployments where the SQL trigger is not installed
    yet. Idempotent: skills_inventory is never overwritten.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO roster_members (id, user_id, name, purok)
            SELECT 'tn-' || u.id, u.id, u.name, u.purok
              FROM users u
             WHERE u.role = 'Tanod'
               AND NOT EXISTS (
                   SELECT 1 FROM roster_members rm
                    WHERE rm.user_id = u.id OR rm.id = 'tn-' || u.id
               )
            ON CONFLICT (id) DO UPDATE SET
                user_id    = EXCLUDED.user_id,
                name       = EXCLUDED.name,
                purok      = EXCLUDED.purok,
                updated_at = now()
            """
        )


async def _validate_user(conn, user_id: Optional[int]) -> None:
    if user_id is None:
        return
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
        if await cur.fetchone() is None:
            raise HTTPException(
                status_code=400,
                detail=f"User id {user_id} does not exist in public.users",
            )


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("", response_model=List[RosterMemberOut])
async def list_roster():
    """Return every registered Tanod with their full skills inventory."""
    async with get_db() as conn:
        await _sync_registered_tanods(conn)
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM roster_members ORDER BY id ASC")
            rows = await cur.fetchall()
    return [_row_to_out(r) for r in rows]


@router.get("/{tanod_id}", response_model=RosterMemberOut)
async def get_roster_member(tanod_id: str):
    """Get a single Tanod roster member."""
    async with get_db() as conn:
        member = await _fetch_member(conn, tanod_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Tanod not found")
    return member


@router.post("", response_model=RosterMemberOut, status_code=201)
async def upsert_roster_member(member: RosterMemberCreate):
    """Create a Tanod roster member, or update it if the id already exists.

    Mirrors the frontend upsertRosterMember() used by skillsInventory.tsx,
    so saving a skills inventory never requires a separate existence check.
    """
    name = member.name.strip()
    tanod_id = member.id.strip()
    if not tanod_id or not name:
        raise HTTPException(status_code=400, detail="Tanod id and name are required")

    async with get_db() as conn:
        await _validate_user(conn, member.userId)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO roster_members (
                    id, user_id, name, purok, skills, experience_years,
                    performance, available, last_duty_at, skills_inventory
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    user_id           = COALESCE(EXCLUDED.user_id, roster_members.user_id),
                    name              = EXCLUDED.name,
                    purok             = EXCLUDED.purok,
                    skills            = EXCLUDED.skills,
                    experience_years  = EXCLUDED.experience_years,
                    performance       = EXCLUDED.performance,
                    available         = EXCLUDED.available,
                    last_duty_at      = EXCLUDED.last_duty_at,
                    skills_inventory  = EXCLUDED.skills_inventory,
                    updated_at        = now()
                """,
                (
                    tanod_id,
                    member.userId,
                    name,
                    member.purok,
                    Jsonb(member.skills),
                    member.experienceYears,
                    member.performance,
                    member.available,
                    member.lastDutyAt,
                    Jsonb(member.skillsInventory.model_dump() if member.skillsInventory else {}),
                ),
            )
        member_out = await _fetch_member(conn, tanod_id)

    if member_out is None:
        raise HTTPException(status_code=500, detail="Failed to save Tanod roster member")
    return member_out


@router.put("/{tanod_id}", response_model=RosterMemberOut)
async def update_roster_member(tanod_id: str, patch: RosterMemberUpdate):
    """Update a Tanod roster member (partial update)."""
    fields = []
    params = []

    if patch.name is not None:
        if not patch.name.strip():
            raise HTTPException(status_code=400, detail="Name is required")
        fields.append("name = %s")
        params.append(patch.name.strip())
    if patch.purok is not None:
        fields.append("purok = %s")
        params.append(patch.purok)
    if patch.skills is not None:
        fields.append("skills = %s")
        params.append(Jsonb(patch.skills))
    if patch.experienceYears is not None:
        fields.append("experience_years = %s")
        params.append(patch.experienceYears)
    if patch.performance is not None:
        fields.append("performance = %s")
        params.append(patch.performance)
    if patch.available is not None:
        fields.append("available = %s")
        params.append(patch.available)
    if patch.lastDutyAt is not None:
        fields.append("last_duty_at = %s")
        params.append(patch.lastDutyAt)
    if patch.skillsInventory is not None:
        fields.append("skills_inventory = %s")
        params.append(Jsonb(patch.skillsInventory.model_dump()))
    if patch.userId is not None:
        fields.append("user_id = %s")
        params.append(patch.userId)

    async with get_db() as conn:
        await _validate_user(conn, patch.userId)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM roster_members WHERE id = %s", (tanod_id,)
            )
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Tanod not found")

            if fields:
                fields.append("updated_at = now()")
                params.append(tanod_id)
                await cur.execute(
                    f"UPDATE roster_members SET {', '.join(fields)} WHERE id = %s",
                    params,
                )
        member = await _fetch_member(conn, tanod_id)

    return member


@router.patch("/{tanod_id}/skills", response_model=RosterMemberOut)
async def update_skills_inventory(tanod_id: str, inventory: SkillsInventory):
    """Replace only the skills inventory of a Tanod."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE roster_members
                SET skills_inventory = %s, updated_at = now()
                WHERE id = %s
                """,
                (Jsonb(inventory.model_dump()), tanod_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Tanod not found")
        member = await _fetch_member(conn, tanod_id)

    return member


@router.delete("/{tanod_id}")
async def delete_roster_member(tanod_id: str):
    """Delete a Tanod roster member."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM roster_members WHERE id = %s", (tanod_id,)
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Tanod not found")
    return {"message": f"Tanod {tanod_id} deleted successfully"}