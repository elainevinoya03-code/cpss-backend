import json as _json
from datetime import date, datetime, time
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from db import get_db

router = APIRouter(prefix="/api/patrol-scheduling", tags=["patrol-scheduling"])

# ── Enums (mirror patrolScheduleShared.ts) ──────────────────────────────
VALID_SCHEDULE_STATUS = {"draft", "pending_approval", "scheduled", "active", "completed"}
VALID_FREQUENCY = {"one_time", "daily", "nightly", "specific_days", "date_range", "custom"}
VALID_SHIFT = {"day", "night", "graveyard"}
VALID_ASSIGNMENT_MODE = {"whole_team", "per_checkpoint"}
VALID_DUTY_LOG_STATUS = {"pending", "on_duty", "completed"}
VALID_CHECK_IN_STATUS = {"checked_in", "checked_out"}

_OPS_DEFAULTS = {
    "assemblyPoint": "",
    "equipment": "",
    "instructions": "",
    "pulisCoordination": "",
    "emergencyProcedure": "",
}


# ── Pydantic models ─────────────────────────────────────────────────────
# camelCase fields on purpose: they mirror the frontend types PatrolTeam,
# PatrolSchedule, DutyLog and CheckInOutRecord in
# cpss-frontend/src/chief_tanod/patrolScheduleShared.ts so API payloads
# round-trip without transformation (same convention as roster_members.py).


class PatrolTeamCreate(BaseModel):
    id: str
    name: str
    leaderId: str = ""
    memberIds: List[str] = []
    isActive: bool = True


class PatrolTeamUpdate(BaseModel):
    name: Optional[str] = None
    leaderId: Optional[str] = None
    memberIds: Optional[List[str]] = None
    isActive: Optional[bool] = None


class CheckpointAssignment(BaseModel):
    pointId: str
    tanodIds: List[str] = []


class PatrolOpsNotes(BaseModel):
    assemblyPoint: str = ""
    equipment: str = ""
    instructions: str = ""
    pulisCoordination: str = ""
    emergencyProcedure: str = ""


class PatrolScheduleCreate(BaseModel):
    id: str
    code: str
    planId: str
    operationalScheduleId: str = ""
    startDate: str
    endDate: str = ""
    startTime: str
    endTime: str
    frequency: str = "one_time"
    frequencyDays: List[str] = []
    customNotes: str = ""
    shiftType: str = "day"
    teamId: str
    assignmentMode: str = "whole_team"
    assignments: List[CheckpointAssignment] = []
    ops: PatrolOpsNotes = PatrolOpsNotes()
    status: str = "draft"
    createdBy: str = "Chief Tanod"
    submittedAt: Optional[str] = None
    decidedBy: Optional[str] = None
    decidedAt: Optional[str] = None
    notifiedAt: Optional[str] = None


class PatrolScheduleUpdate(BaseModel):
    code: Optional[str] = None
    planId: Optional[str] = None
    operationalScheduleId: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    frequency: Optional[str] = None
    frequencyDays: Optional[List[str]] = None
    customNotes: Optional[str] = None
    shiftType: Optional[str] = None
    teamId: Optional[str] = None
    assignmentMode: Optional[str] = None
    assignments: Optional[List[CheckpointAssignment]] = None
    ops: Optional[PatrolOpsNotes] = None
    status: Optional[str] = None
    submittedAt: Optional[str] = None
    decidedBy: Optional[str] = None
    decidedAt: Optional[str] = None
    notifiedAt: Optional[str] = None


class DutyLogCreate(BaseModel):
    id: str
    scheduleId: str
    tanodId: str
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None
    observations: str = ""
    linkedBlotter: bool = False
    linkedBpops: bool = False
    confirmedBy: Optional[str] = None
    photoEvidence: Optional[str] = None
    checkInNotes: Optional[str] = None
    checkOutNotes: Optional[str] = None
    status: str = "pending"


class DutyLogUpdate(BaseModel):
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None
    observations: Optional[str] = None
    linkedBlotter: Optional[bool] = None
    linkedBpops: Optional[bool] = None
    confirmedBy: Optional[str] = None
    photoEvidence: Optional[str] = None
    checkInNotes: Optional[str] = None
    checkOutNotes: Optional[str] = None
    status: Optional[str] = None


class CheckInOutCreate(BaseModel):
    id: str
    scheduleId: str
    tanodId: str
    teamId: str
    checkInTime: Optional[str] = None
    checkOutTime: Optional[str] = None
    confirmedBy: str = ""
    photoEvidence: Optional[str] = None
    checkInNotes: Optional[str] = None
    checkOutNotes: Optional[str] = None
    checkpointPlanId: Optional[str] = None
    status: str = "checked_in"


class CheckInOutUpdate(BaseModel):
    checkInTime: Optional[str] = None
    checkOutTime: Optional[str] = None
    confirmedBy: Optional[str] = None
    photoEvidence: Optional[str] = None
    checkInNotes: Optional[str] = None
    checkOutNotes: Optional[str] = None
    checkpointPlanId: Optional[str] = None
    status: Optional[str] = None


# ── Helpers ─────────────────────────────────────────────────────────────


def _iso(value):
    """Normalize a DB value into the ISO string the frontend expects.

    TIMESTAMPTZ → 'YYYY-MM-DDTHH:MM:SS', DATE → 'YYYY-MM-DD',
    TIME → 'HH:MM' (mirrors the frontend 'HH:MM' start/end times).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)


def _norm_json(raw, default):
    """psycopg3 can return JSONB as a Python object or a JSON string — normalize."""
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except Exception:
            return default
    if raw is None:
        return default
    return raw


def _team_row_to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "leaderId": row.get("leader_id") or "",
        "memberIds": _norm_json(row.get("member_ids"), []),
        "isActive": bool(row["is_active"]) if row.get("is_active") is not None else True,
        "createdAt": _iso(row.get("created_at")),
    }


def _schedule_row_to_out(row: dict) -> dict:
    ops = _norm_json(row.get("ops"), {})
    if not isinstance(ops, dict):
        ops = {}
    return {
        "id": row["id"],
        "code": row["code"],
        "planId": row["plan_id"],
        "operationalScheduleId": str(row.get("operational_schedule_id") or ""),
        "startDate": _iso(row.get("start_date")),
        "endDate": _iso(row.get("end_date")),
        "startTime": _iso(row.get("start_time")),
        "endTime": _iso(row.get("end_time")),
        "frequency": row.get("frequency") or "one_time",
        "frequencyDays": _norm_json(row.get("frequency_days"), []),
        "customNotes": row.get("custom_notes") or "",
        "shiftType": row.get("shift_type") or "day",
        "teamId": row["team_id"],
        "assignmentMode": row.get("assignment_mode") or "whole_team",
        "assignments": _norm_json(row.get("assignments"), []),
        "ops": {**_OPS_DEFAULTS, **ops},
        "status": row.get("status") or "draft",
        "createdBy": row.get("created_by") or "",
        "createdAt": _iso(row.get("created_at")),
        "submittedAt": _iso(row.get("submitted_at")),
        "decidedBy": row.get("decided_by"),
        "decidedAt": _iso(row.get("decided_at")),
        "notifiedAt": _iso(row.get("notified_at")),
    }


def _duty_log_row_to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "scheduleId": row["schedule_id"],
        "tanodId": row["tanod_id"],
        "startedAt": _iso(row.get("started_at")),
        "endedAt": _iso(row.get("ended_at")),
        "observations": row.get("observations") or "",
        "linkedBlotter": bool(row["linked_blotter"]) if row.get("linked_blotter") is not None else False,
        "linkedBpops": bool(row["linked_bpops"]) if row.get("linked_bpops") is not None else False,
        "confirmedBy": row.get("confirmed_by"),
        "photoEvidence": row.get("photo_evidence"),
        "checkInNotes": row.get("check_in_notes"),
        "checkOutNotes": row.get("check_out_notes"),
        "status": row.get("status") or "pending",
    }


def _check_in_row_to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "scheduleId": row["schedule_id"],
        "tanodId": row["tanod_id"],
        "teamId": row["team_id"],
        "checkInTime": _iso(row.get("check_in_time")),
        "checkOutTime": _iso(row.get("check_out_time")),
        "confirmedBy": row.get("confirmed_by") or "",
        "photoEvidence": row.get("photo_evidence"),
        "checkInNotes": row.get("check_in_notes"),
        "checkOutNotes": row.get("check_out_notes"),
        "checkpointPlanId": row.get("checkpoint_plan_id"),
        "status": row.get("status") or "checked_in",
        "createdAt": _iso(row.get("created_at")),
    }


async def _sync_team_in_members(conn, team_id: str) -> None:
    """Create missing roster_members rows so the patrol_teams FK holds.

    The frontend can reference any roster id; when teams are created before a
    roster row exists (e.g. an unregistered 'tn-XX' member id), the FK would
    reject the insert. Mirror _sync_registered_tanods in roster_members.py.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO roster_members (id, name)
            SELECT member_id, member_id
            FROM jsonb_array_elements_text(
                COALESCE((SELECT member_ids FROM patrol_teams WHERE id = %s), '[]'::jsonb)
            ) AS member_id
            WHERE member_id <> '' AND member_id IS NOT NULL
            ON CONFLICT (id) DO NOTHING
            """,
            (team_id,),
        )


async def _validate_team_refs(conn, leader_id: str, member_ids: List[str]) -> None:
    if leader_id:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM roster_members WHERE id = %s", (leader_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=400, detail=f"Team leader {leader_id} is not on the roster")
    if member_ids:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM roster_members WHERE id = ANY(%s)",
                (member_ids,),
            )
            found = {r["id"] for r in await cur.fetchall()}
            missing = [m for m in member_ids if m not in found]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Roster members not found: {', '.join(missing)}",
                )


async def _validate_schedule_refs(conn, plan_id: str, team_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM checkpoint_plans WHERE id = %s", (plan_id,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=400, detail=f"Checkpoint plan {plan_id} does not exist")
        await cur.execute("SELECT 1 FROM patrol_teams WHERE id = %s", (team_id,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=400, detail=f"Patrol team {team_id} does not exist")


async def _team_exists(conn, team_id: str) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM patrol_teams WHERE id = %s", (team_id,))
        return await cur.fetchone() is not None


async def _active_team_member_conflicts(
    conn, candidate_ids: List[str], exclude_team_id: str
) -> List[str]:
    """Member ids already taken by another ACTIVE team (excluding `exclude_team_id`).

    Deleting/deactivating a team (or removing the member from it) frees the
    member again because inactive/deleted rows are not considered.
    """
    ids = [m for m in dict.fromkeys(candidate_ids) if m]
    if not ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT member_ids, leader_id FROM patrol_teams WHERE is_active = TRUE AND id <> %s",
            (exclude_team_id,),
        )
        rows = await cur.fetchall()
    taken = set()
    for r in rows:
        for mid in _norm_json(r.get("member_ids"), []) or []:
            if mid:
                taken.add(mid)
        if r.get("leader_id"):
            taken.add(r["leader_id"])
    return [m for m in ids if m in taken]


async def _roster_names(conn, ids: List[str]) -> dict:
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, name FROM roster_members WHERE id = ANY(%s)", (ids,))
        rows = await cur.fetchall()
    return {r["id"]: r.get("name") or r["id"] for r in rows}


async def _reject_team_member_conflicts(conn, candidate_ids: List[str], exclude_team_id: str) -> None:
    """409 when any candidate is already in another active team (no bypass)."""
    conflicts = await _active_team_member_conflicts(conn, candidate_ids, exclude_team_id)
    if conflicts:
        names = await _roster_names(conn, conflicts)
        labels = [names.get(m, m) for m in conflicts]
        raise HTTPException(
            status_code=409,
            detail=f"Already assigned to another active team: {', '.join(labels)}",
        )


async def _schedule_exists(conn, schedule_id: str) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
        return await cur.fetchone() is not None


# ── Operational Schedule linkage ──────────────────────────────────────
# A patrol schedule must be created from an existing Operational Schedule
# (the patrol_schedules row owned by its checkpoint plan) and stay inside
# that schedule's date/time range and valid recurring dates.

_PY_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_hhmm(value) -> Optional[int]:
    import re as _re

    m = _re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", str(value or "").strip())
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2))
    if h < 0 or h > 23 or minute < 0 or minute > 59:
        return None
    return h * 60 + minute


def _split_window(start_min: int, end_min: int) -> list:
    if start_min == end_min:
        return []
    return [[start_min, end_min]] if end_min > start_min else [[start_min, 1440], [0, end_min]]


def _time_within(t: str, window_start: str, window_end: str) -> bool:
    tm = _parse_hhmm(t)
    sm = _parse_hhmm(window_start)
    em = _parse_hhmm(window_end)
    if tm is None or sm is None or em is None:
        return False
    if sm == em:
        return False
    for a0, a1 in _split_window(sm, em):
        if a0 <= tm <= a1:
            return True
    return False


def _dates_in_range(start: str, end: str, limit: int = 370) -> List[str]:
    from datetime import date as _date

    try:
        y1, m1, d1 = [int(x) for x in str(start).split("-")]
        y2, m2, d2 = [int(x) for x in str(end).split("-")]
        d_start, d_end = _date(y1, m1, d1), _date(y2, m2, d2)
    except Exception:
        return []
    if d_end < d_start or (d_end - d_start).days > limit:
        return []
    return [(d_start + __import__("datetime").timedelta(days=i)).isoformat() for i in range((d_end - d_start).days + 1)]


def _op_occurrences(op_start: str, op_end: str, recurring: str, recurring_days) -> List[str]:
    """Valid recurring dates generated by the Operational Schedule."""
    days = _dates_in_range(op_start, op_end)
    if recurring == "daily":
        return days
    if recurring == "specific_days":
        want = {d for d in (recurring_days or []) if d}
        if not want:
            return []
        from datetime import date as _date

        out = []
        for ds in days:
            y, m, d = [int(x) for x in ds.split("-")]
            if _PY_WEEKDAYS[_date(y, m, d).weekday()] in want:
                out.append(ds)
        return out
    return [op_start] if op_start else []


def _patrol_dates(start: str, end: str, frequency: str, frequency_days) -> List[str]:
    end = end or start
    days = _dates_in_range(start, end)
    if frequency == "specific_days":
        want = {d for d in (frequency_days or []) if d}
        if not want:
            return []
        from datetime import date as _date

        return [ds for ds in days if _PY_WEEKDAYS[_date(*[int(x) for x in ds.split("-")]).weekday()] in want]
    if frequency in ("one_time", "custom"):
        return [start] if start else []
    return days


async def _fetch_operational_schedule(conn, op_id: int):
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM patrol_schedules WHERE id = %s", (op_id,))
        return await cur.fetchone()


async def _validate_operational_link(
    conn,
    *,
    op_id_raw: str,
    plan_id: str,
    start_date: str,
    end_date: str,
    start_time: str,
    end_time: str,
    frequency: str,
    frequency_days,
    require_link: bool,
    enforce_window: bool,
) -> None:
    """Enforce the Operational Schedule linkage for a patrol schedule.

    - `require_link`: reject when no Operational Schedule is selected
      (creating/activating a patrol schedule always requires one).
    - existence of the linked row is always checked when an id is given, so
      a deleted Operational Schedule blocks saves with a clear message.
    - `enforce_window`: reject dates/times outside the Operational
      Schedule's range and dates outside its valid recurring dates.
    """
    op_id_raw = (op_id_raw or "").strip()
    if not op_id_raw:
        if require_link:
            raise HTTPException(
                status_code=400,
                detail="Select an existing Operational Schedule — a patrol schedule cannot be created without one",
            )
        return
    try:
        op_pk = int(op_id_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Operational Schedule reference")
    op = await _fetch_operational_schedule(conn, op_pk)
    if op is None:
        raise HTTPException(
            status_code=400,
            detail="The linked Operational Schedule is no longer available — it was deleted or is invalid. Select a current Operational Schedule.",
        )
    if str(op.get("plan_id") or "") != str(plan_id or ""):
        raise HTTPException(
            status_code=400,
            detail="The linked Operational Schedule does not belong to the selected checkpoint plan",
        )
    op_start = _iso(op.get("operation_date")) or ""
    op_end = _iso(op.get("end_date")) or op_start
    op_start_time = str(op.get("start_time") or "")
    op_end_time = str(op.get("end_time") or "")
    if not op_start or not op_start_time or not op_end_time:
        raise HTTPException(
            status_code=400,
            detail="The linked Operational Schedule is no longer valid — update it in Patrol Configuration first",
        )
    if not enforce_window:
        return
    if frequency == "custom":
        raise HTTPException(
            status_code=400,
            detail="Custom cadence cannot be verified against the Operational Schedule — pick a listed frequency within its dates",
        )
    if not start_date or not start_time or not end_time:
        raise HTTPException(status_code=400, detail="Start date and start/end times are required")
    end_date = end_date or start_date
    if start_date < op_start or end_date > op_end or end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail=f"Patrol dates must stay inside the Operational Schedule range {op_start} → {op_end}",
        )
    if not _time_within(start_time, op_start_time, op_end_time) or not _time_within(end_time, op_start_time, op_end_time):
        raise HTTPException(
            status_code=400,
            detail=f"Patrol times must stay inside the Operational Schedule window {op_start_time}–{op_end_time}",
        )
    occurrences = set(_op_occurrences(op_start, op_end, op.get("recurring") or "none", _norm_json(op.get("recurring_days"), [])))
    for ds in _patrol_dates(start_date, end_date, frequency, frequency_days or []):
        if ds not in occurrences:
            raise HTTPException(
                status_code=400,
                detail=f"Patrol date {ds} is not a valid recurring date of the Operational Schedule",
            )


# ── Teams ───────────────────────────────────────────────────────────────


@router.get("/teams")
async def list_teams():
    """Return all patrol teams, newest first (mirrors store upsertTeam)."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_teams ORDER BY created_at DESC, id DESC")
            rows = await cur.fetchall()
    return [_team_row_to_out(r) for r in rows]


@router.post("/teams", status_code=201)
async def upsert_team(team: PatrolTeamCreate):
    """Create a patrol team, or update it if the id already exists.

    Mirrors the frontend upsertTeam() from patrolScheduleStore.ts.
    """
    team_id = team.id.strip()
    if not team_id or not team.name.strip():
        raise HTTPException(status_code=400, detail="Team id and name are required")

    leader_id = team.leaderId or None  # '' → NULL; patrol_teams.leader_id is nullable
    raw_ids = ([leader_id] if leader_id else []) + list(team.memberIds or [])
    if len(raw_ids) != len(set(raw_ids)):
        raise HTTPException(status_code=400, detail="Duplicate members selected — each tanod can only appear once per team")
    member_ids = list(dict.fromkeys(team.memberIds))
    if leader_id and leader_id not in member_ids:
        member_ids = [leader_id, *member_ids]

    async with get_db() as conn:
        await _validate_team_refs(conn, leader_id, member_ids)
        if team.isActive:
            # Re-check at submit time against current DB state so a member who
            # joined another active team concurrently (or a crafted payload
            # bypassing the disabled UI) cannot be double-assigned.
            await _reject_team_member_conflicts(conn, member_ids, team_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM patrol_teams WHERE LOWER(name) = LOWER(%s) AND id <> %s",
                (team.name.strip(), team_id),
            )
            if await cur.fetchone():
                raise HTTPException(status_code=400, detail="A team with this name already exists")
            await cur.execute(
                """
                INSERT INTO patrol_teams (
                    id, name, leader_id, member_ids, is_active
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name       = EXCLUDED.name,
                    leader_id  = EXCLUDED.leader_id,
                    member_ids = EXCLUDED.member_ids,
                    is_active  = EXCLUDED.is_active,
                    updated_at = now()
                """,
                (team_id, team.name.strip(), leader_id, Jsonb(member_ids), team.isActive),
            )
        await _sync_team_in_members(conn, team_id)
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_teams WHERE id = %s", (team_id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save patrol team")
    return _team_row_to_out(row)


@router.put("/teams/{team_id}")
async def update_team(team_id: str, patch: PatrolTeamUpdate):
    """Partially update a patrol team."""
    fields = []
    params = []

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_teams WHERE id = %s", (team_id,))
            current = await cur.fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Patrol team not found")

        current_member_ids = _norm_json(current.get("member_ids"), []) or []
        resulting_leader = patch.leaderId if patch.leaderId is not None else (current.get("leader_id") or "")
        resulting_members = list(dict.fromkeys(patch.memberIds)) if patch.memberIds is not None else list(current_member_ids)
        resulting_active = patch.isActive if patch.isActive is not None else bool(current.get("is_active"))
        if resulting_leader and resulting_leader not in resulting_members:
            resulting_members = [resulting_leader, *resulting_members]

        if patch.name is not None:
            if not patch.name.strip():
                raise HTTPException(status_code=400, detail="Team name is required")
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM patrol_teams WHERE LOWER(name) = LOWER(%s) AND id <> %s",
                    (patch.name.strip(), team_id),
                )
                if await cur.fetchone():
                    raise HTTPException(status_code=400, detail="A team with this name already exists")
            fields.append("name = %s")
            params.append(patch.name.strip())
        if patch.leaderId is not None:
            if patch.leaderId and not await _roster_has(conn, patch.leaderId):
                raise HTTPException(status_code=400, detail=f"Team leader {patch.leaderId} is not on the roster")
            fields.append("leader_id = %s")
            params.append(patch.leaderId or None)
        if patch.memberIds is not None:
            raw_ids = ([resulting_leader] if resulting_leader else []) + list(patch.memberIds or [])
            if len(raw_ids) != len(set(raw_ids)):
                raise HTTPException(status_code=400, detail="Duplicate members selected — each tanod can only appear once per team")
            await _validate_team_refs(conn, resulting_leader or None, resulting_members)
            fields.append("member_ids = %s")
            params.append(Jsonb(resulting_members))
        if patch.isActive is not None:
            fields.append("is_active = %s")
            params.append(patch.isActive)

        # Enforce single-active-team membership on any change that leaves the
        # team active (member add, leader change, or reactivation).
        if resulting_active and (patch.memberIds is not None or patch.leaderId is not None or patch.isActive is True):
            await _validate_team_refs(conn, resulting_leader or None, resulting_members)
            await _reject_team_member_conflicts(conn, resulting_members, team_id)

        if fields:
            fields.append("updated_at = now()")
            params.append(team_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE patrol_teams SET {', '.join(fields)} WHERE id = %s",
                    params,
                )
        await _sync_team_in_members(conn, team_id)
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_teams WHERE id = %s", (team_id,))
            row = await cur.fetchone()

    return _team_row_to_out(row)


@router.delete("/teams/{team_id}")
async def delete_team(team_id: str):
    """Delete a patrol team. Blocked while schedules still reference it."""
    async with get_db() as conn:
        if not await _team_exists(conn, team_id):
            raise HTTPException(status_code=404, detail="Patrol team not found")
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM active_patrol_schedules WHERE team_id = %s LIMIT 1",
                (team_id,),
            )
            if await cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Team is referenced by one or more patrol schedules — reassign or delete those schedules first",
                )
            await cur.execute("DELETE FROM patrol_teams WHERE id = %s", (team_id,))
    return {"message": f"Patrol team {team_id} deleted successfully"}


# ── Schedules ───────────────────────────────────────────────────────────


@router.get("/schedules")
async def list_schedules(status: Optional[str] = None):
    """Return all patrol schedules. Optional ?status= filter."""
    sql = "SELECT * FROM active_patrol_schedules"
    params: List[str] = []
    if status:
        if status not in VALID_SCHEDULE_STATUS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Choose one of: {', '.join(sorted(VALID_SCHEDULE_STATUS))}",
            )
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC"

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
    return [_schedule_row_to_out(r) for r in rows]


@router.post("/schedules", status_code=201)
async def upsert_schedule(schedule: PatrolScheduleCreate):
    """Create a patrol schedule, or update it if the id already exists.

    Mirrors the frontend upsertSchedule() from patrolScheduleStore.ts, used
    by both "Save draft" and "Done" (activate) in the scheduling wizard.
    """
    schedule_id = schedule.id.strip()
    if not schedule_id or not schedule.code.strip():
        raise HTTPException(status_code=400, detail="Schedule id and code are required")
    if schedule.status not in VALID_SCHEDULE_STATUS:
        raise HTTPException(status_code=400, detail="Invalid schedule status")
    if schedule.frequency not in VALID_FREQUENCY:
        raise HTTPException(status_code=400, detail="Invalid frequency")
    if schedule.shiftType not in VALID_SHIFT:
        raise HTTPException(status_code=400, detail="Invalid shift type")
    if schedule.assignmentMode not in VALID_ASSIGNMENT_MODE:
        raise HTTPException(status_code=400, detail="Invalid assignment mode")

    async with get_db() as conn:
        await _validate_schedule_refs(conn, schedule.planId, schedule.teamId)
        # The Schedule section is driven by the existing Operational Schedule:
        # link is required except for drafts, existence is always checked so a
        # deleted/invalid Operational Schedule blocks the save, and the
        # date/time window is enforced whenever the schedule is activated.
        await _validate_operational_link(
            conn,
            op_id_raw=schedule.operationalScheduleId,
            plan_id=schedule.planId,
            start_date=schedule.startDate,
            end_date=schedule.endDate or schedule.startDate,
            start_time=schedule.startTime,
            end_time=schedule.endTime,
            frequency=schedule.frequency,
            frequency_days=schedule.frequencyDays,
            require_link=schedule.status != "draft",
            enforce_window=schedule.status != "draft",
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO active_patrol_schedules (
                    id, code, plan_id, operational_schedule_id, start_date, end_date, start_time, end_time,
                    frequency, frequency_days, custom_notes, shift_type, team_id,
                    assignment_mode, assignments, ops, status, created_by,
                    submitted_at, decided_by, decided_at, notified_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    code            = EXCLUDED.code,
                    plan_id         = EXCLUDED.plan_id,
                    operational_schedule_id = EXCLUDED.operational_schedule_id,
                    start_date      = EXCLUDED.start_date,
                    end_date        = EXCLUDED.end_date,
                    start_time      = EXCLUDED.start_time,
                    end_time        = EXCLUDED.end_time,
                    frequency       = EXCLUDED.frequency,
                    frequency_days  = EXCLUDED.frequency_days,
                    custom_notes    = EXCLUDED.custom_notes,
                    shift_type      = EXCLUDED.shift_type,
                    team_id         = EXCLUDED.team_id,
                    assignment_mode = EXCLUDED.assignment_mode,
                    assignments     = EXCLUDED.assignments,
                    ops             = EXCLUDED.ops,
                    status          = EXCLUDED.status,
                    submitted_at    = EXCLUDED.submitted_at,
                    decided_by      = EXCLUDED.decided_by,
                    decided_at      = EXCLUDED.decided_at,
                    notified_at     = EXCLUDED.notified_at,
                    updated_at      = now()
                """,
                (
                    schedule_id,
                    schedule.code.strip(),
                    schedule.planId,
                    (schedule.operationalScheduleId or "").strip(),
                    schedule.startDate,
                    schedule.endDate or schedule.startDate,
                    schedule.startTime,
                    schedule.endTime,
                    schedule.frequency,
                    Jsonb(schedule.frequencyDays),
                    schedule.customNotes,
                    schedule.shiftType,
                    schedule.teamId,
                    schedule.assignmentMode,
                    Jsonb([a.model_dump() for a in schedule.assignments]),
                    Jsonb(schedule.ops.model_dump()),
                    schedule.status,
                    schedule.createdBy,
                    schedule.submittedAt,
                    schedule.decidedBy,
                    schedule.decidedAt,
                    schedule.notifiedAt,
                ),
            )
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save patrol schedule")
    return _schedule_row_to_out(row)


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, patch: PatrolScheduleUpdate):
    """Partially update a patrol schedule (status transitions, edits, etc.)."""
    fields = []
    params = []

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
            current = await cur.fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Patrol schedule not found")

        current_op_id = str(current.get("operational_schedule_id") or "")
        if patch.operationalScheduleId is not None:
            new_op_id = (patch.operationalScheduleId or "").strip()
            if current_op_id and new_op_id != current_op_id:
                # The linked Operational Schedule is read-only after creation.
                raise HTTPException(
                    status_code=400,
                    detail="The linked Operational Schedule cannot be changed after the patrol schedule has been created — edit it in Patrol Configuration instead",
                )
        resulting_op_id = (
            (patch.operationalScheduleId or "").strip()
            if patch.operationalScheduleId is not None
            else current_op_id
        )
        resulting_plan_id = patch.planId if patch.planId is not None else str(current.get("plan_id") or "")
        resulting_status = patch.status if patch.status is not None else str(current.get("status") or "draft")
        resulting_start = patch.startDate if patch.startDate is not None else (_iso(current.get("start_date")) or "")
        resulting_end = (
            patch.endDate
            if patch.endDate is not None
            else (_iso(current.get("end_date")) or resulting_start)
        )
        resulting_start_time = patch.startTime if patch.startTime is not None else (_iso(current.get("start_time")) or "")
        resulting_end_time = patch.endTime if patch.endTime is not None else (_iso(current.get("end_time")) or "")
        resulting_freq = patch.frequency if patch.frequency is not None else str(current.get("frequency") or "one_time")
        resulting_days = (
            patch.frequencyDays
            if patch.frequencyDays is not None
            else _norm_json(current.get("frequency_days"), [])
        )
        # Re-validate the linkage on every update: a deleted/invalid
        # Operational Schedule blocks the save, and the date/time window is
        # enforced whenever the schedule is (or stays) activated.
        await _validate_operational_link(
            conn,
            op_id_raw=resulting_op_id,
            plan_id=resulting_plan_id,
            start_date=resulting_start,
            end_date=resulting_end,
            start_time=resulting_start_time,
            end_time=resulting_end_time,
            frequency=resulting_freq,
            frequency_days=resulting_days,
            require_link=resulting_status != "draft",
            enforce_window=resulting_status != "draft",
        )

        if patch.code is not None:
            fields.append("code = %s")
            params.append(patch.code)
        if patch.planId is not None or patch.teamId is not None:
            plan_id = patch.planId or str(current.get("plan_id") or "")
            team_id = patch.teamId or str(current.get("team_id") or "")
            await _validate_schedule_refs(conn, plan_id, team_id)
            if patch.planId is not None:
                fields.append("plan_id = %s")
                params.append(patch.planId)
            if patch.teamId is not None:
                fields.append("team_id = %s")
                params.append(patch.teamId)
        if patch.operationalScheduleId is not None:
            fields.append("operational_schedule_id = %s")
            params.append((patch.operationalScheduleId or "").strip())
        if patch.startDate is not None:
            fields.append("start_date = %s")
            params.append(patch.startDate)
        if patch.endDate is not None:
            fields.append("end_date = %s")
            params.append(patch.endDate)
        if patch.startTime is not None:
            fields.append("start_time = %s")
            params.append(patch.startTime)
        if patch.endTime is not None:
            fields.append("end_time = %s")
            params.append(patch.endTime)
        if patch.frequency is not None:
            if patch.frequency not in VALID_FREQUENCY:
                raise HTTPException(status_code=400, detail="Invalid frequency")
            fields.append("frequency = %s")
            params.append(patch.frequency)
        if patch.frequencyDays is not None:
            fields.append("frequency_days = %s")
            params.append(Jsonb(patch.frequencyDays))
        if patch.customNotes is not None:
            fields.append("custom_notes = %s")
            params.append(patch.customNotes)
        if patch.shiftType is not None:
            if patch.shiftType not in VALID_SHIFT:
                raise HTTPException(status_code=400, detail="Invalid shift type")
            fields.append("shift_type = %s")
            params.append(patch.shiftType)
        if patch.assignmentMode is not None:
            if patch.assignmentMode not in VALID_ASSIGNMENT_MODE:
                raise HTTPException(status_code=400, detail="Invalid assignment mode")
            fields.append("assignment_mode = %s")
            params.append(patch.assignmentMode)
        if patch.assignments is not None:
            fields.append("assignments = %s")
            params.append(Jsonb([a.model_dump() for a in patch.assignments]))
        if patch.ops is not None:
            fields.append("ops = %s")
            params.append(Jsonb(patch.ops.model_dump()))
        if patch.status is not None:
            if patch.status not in VALID_SCHEDULE_STATUS:
                raise HTTPException(status_code=400, detail="Invalid schedule status")
            fields.append("status = %s")
            params.append(patch.status)
        if patch.submittedAt is not None:
            fields.append("submitted_at = %s")
            params.append(patch.submittedAt)
        if patch.decidedBy is not None:
            fields.append("decided_by = %s")
            params.append(patch.decidedBy)
        if patch.decidedAt is not None:
            fields.append("decided_at = %s")
            params.append(patch.decidedAt)
        if patch.notifiedAt is not None:
            fields.append("notified_at = %s")
            params.append(patch.notifiedAt)

        if fields:
            fields.append("updated_at = now()")
            params.append(schedule_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE active_patrol_schedules SET {', '.join(fields)} WHERE id = %s",
                    params,
                )
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
            row = await cur.fetchone()

    return _schedule_row_to_out(row)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Delete a patrol schedule (draft cleanup). Cascades to duty logs / check-ins."""
    async with get_db() as conn:
        if not await _schedule_exists(conn, schedule_id):
            raise HTTPException(status_code=404, detail="Patrol schedule not found")
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
    return {"message": f"Patrol schedule {schedule_id} deleted successfully"}


# ── Duty logs ───────────────────────────────────────────────────────────


@router.get("/duty-logs")
async def list_duty_logs(schedule_id: Optional[str] = None):
    """Return duty logs, newest first. Optional ?schedule_id= filter."""
    sql = "SELECT * FROM patrol_duty_logs"
    params: List[str] = []
    if schedule_id:
        sql += " WHERE schedule_id = %s"
        params.append(schedule_id)
    sql += " ORDER BY created_at DESC, id DESC"

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
    return [_duty_log_row_to_out(r) for r in rows]


@router.post("/duty-logs", status_code=201)
async def create_duty_log(log: DutyLogCreate):
    """Create a duty log for a tanod against a patrol schedule."""
    log_id = log.id.strip()
    if not log_id:
        raise HTTPException(status_code=400, detail="Duty log id is required")
    if log.status not in VALID_DUTY_LOG_STATUS:
        raise HTTPException(status_code=400, detail="Invalid duty log status")

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO patrol_duty_logs (
                    id, schedule_id, tanod_id, started_at, ended_at, observations,
                    linked_blotter, linked_bpops, confirmed_by, photo_evidence,
                    check_in_notes, check_out_notes, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    started_at      = EXCLUDED.started_at,
                    ended_at        = EXCLUDED.ended_at,
                    observations    = EXCLUDED.observations,
                    linked_blotter  = EXCLUDED.linked_blotter,
                    linked_bpops    = EXCLUDED.linked_bpops,
                    confirmed_by    = EXCLUDED.confirmed_by,
                    photo_evidence  = EXCLUDED.photo_evidence,
                    check_in_notes  = EXCLUDED.check_in_notes,
                    check_out_notes = EXCLUDED.check_out_notes,
                    status          = EXCLUDED.status,
                    updated_at      = now()
                """,
                (
                    log_id,
                    log.scheduleId,
                    log.tanodId,
                    log.startedAt,
                    log.endedAt,
                    log.observations,
                    log.linkedBlotter,
                    log.linkedBpops,
                    log.confirmedBy,
                    log.photoEvidence,
                    log.checkInNotes,
                    log.checkOutNotes,
                    log.status,
                ),
            )
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_duty_logs WHERE id = %s", (log_id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save duty log")
    return _duty_log_row_to_out(row)


@router.patch("/duty-logs/{log_id}")
async def update_duty_log(log_id: str, patch: DutyLogUpdate):
    """Partially update a duty log."""
    fields = []
    params = []

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM patrol_duty_logs WHERE id = %s", (log_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Duty log not found")

        if patch.startedAt is not None:
            fields.append("started_at = %s")
            params.append(patch.startedAt)
        if patch.endedAt is not None:
            fields.append("ended_at = %s")
            params.append(patch.endedAt)
        if patch.observations is not None:
            fields.append("observations = %s")
            params.append(patch.observations)
        if patch.linkedBlotter is not None:
            fields.append("linked_blotter = %s")
            params.append(patch.linkedBlotter)
        if patch.linkedBpops is not None:
            fields.append("linked_bpops = %s")
            params.append(patch.linkedBpops)
        if patch.confirmedBy is not None:
            fields.append("confirmed_by = %s")
            params.append(patch.confirmedBy)
        if patch.photoEvidence is not None:
            fields.append("photo_evidence = %s")
            params.append(patch.photoEvidence)
        if patch.checkInNotes is not None:
            fields.append("check_in_notes = %s")
            params.append(patch.checkInNotes)
        if patch.checkOutNotes is not None:
            fields.append("check_out_notes = %s")
            params.append(patch.checkOutNotes)
        if patch.status is not None:
            if patch.status not in VALID_DUTY_LOG_STATUS:
                raise HTTPException(status_code=400, detail="Invalid duty log status")
            fields.append("status = %s")
            params.append(patch.status)

        if fields:
            fields.append("updated_at = now()")
            params.append(log_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE patrol_duty_logs SET {', '.join(fields)} WHERE id = %s",
                    params,
                )
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_duty_logs WHERE id = %s", (log_id,))
            row = await cur.fetchone()

    return _duty_log_row_to_out(row)


# ── Check-in / Check-out ────────────────────────────────────────────────


@router.get("/check-ins")
async def list_check_ins(schedule_id: Optional[str] = None):
    """Return check-in/check-out records. Optional ?schedule_id= filter."""
    sql = "SELECT * FROM patrol_check_in_out"
    params: List[str] = []
    if schedule_id:
        sql += " WHERE schedule_id = %s"
        params.append(schedule_id)
    sql += " ORDER BY created_at DESC, id DESC, check_in_time DESC"

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
    return [_check_in_row_to_out(r) for r in rows]


@router.post("/check-ins", status_code=201)
async def create_check_in(record: CheckInOutCreate):
    """Create (or update) a check-in/check-out record. Mirrors addCheckInOutRecord()."""
    record_id = record.id.strip()
    if not record_id:
        raise HTTPException(status_code=400, detail="Check-in record id is required")
    if record.status not in VALID_CHECK_IN_STATUS:
        raise HTTPException(status_code=400, detail="Invalid check-in status")

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO patrol_check_in_out (
                    id, schedule_id, tanod_id, team_id, check_in_time, check_out_time,
                    confirmed_by, photo_evidence, check_in_notes, check_out_notes,
                    checkpoint_plan_id, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    check_in_time      = EXCLUDED.check_in_time,
                    check_out_time     = EXCLUDED.check_out_time,
                    confirmed_by       = EXCLUDED.confirmed_by,
                    photo_evidence     = EXCLUDED.photo_evidence,
                    check_in_notes     = EXCLUDED.check_in_notes,
                    check_out_notes    = EXCLUDED.check_out_notes,
                    checkpoint_plan_id = EXCLUDED.checkpoint_plan_id,
                    status             = EXCLUDED.status,
                    updated_at         = now()
                """,
                (
                    record_id,
                    record.scheduleId,
                    record.tanodId,
                    record.teamId,
                    record.checkInTime,
                    record.checkOutTime,
                    record.confirmedBy,
                    record.photoEvidence,
                    record.checkInNotes,
                    record.checkOutNotes,
                    record.checkpointPlanId,
                    record.status,
                ),
            )
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_check_in_out WHERE id = %s", (record_id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save check-in record")
    return _check_in_row_to_out(row)


@router.patch("/check-ins/{record_id}")
async def update_check_in(record_id: str, patch: CheckInOutUpdate):
    """Partially update a check-in/check-out record (e.g. add checkout time)."""
    fields = []
    params = []

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM patrol_check_in_out WHERE id = %s", (record_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Check-in record not found")

        if patch.checkInTime is not None:
            fields.append("check_in_time = %s")
            params.append(patch.checkInTime)
        if patch.checkOutTime is not None:
            fields.append("check_out_time = %s")
            params.append(patch.checkOutTime)
        if patch.confirmedBy is not None:
            fields.append("confirmed_by = %s")
            params.append(patch.confirmedBy)
        if patch.photoEvidence is not None:
            fields.append("photo_evidence = %s")
            params.append(patch.photoEvidence)
        if patch.checkInNotes is not None:
            fields.append("check_in_notes = %s")
            params.append(patch.checkInNotes)
        if patch.checkOutNotes is not None:
            fields.append("check_out_notes = %s")
            params.append(patch.checkOutNotes)
        if patch.checkpointPlanId is not None:
            fields.append("checkpoint_plan_id = %s")
            params.append(patch.checkpointPlanId)
        if patch.status is not None:
            if patch.status not in VALID_CHECK_IN_STATUS:
                raise HTTPException(status_code=400, detail="Invalid check-in status")
            fields.append("status = %s")
            params.append(patch.status)

        if fields:
            fields.append("updated_at = now()")
            params.append(record_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE patrol_check_in_out SET {', '.join(fields)} WHERE id = %s",
                    params,
                )
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM patrol_check_in_out WHERE id = %s", (record_id,))
            row = await cur.fetchone()

    return _check_in_row_to_out(row)


# ── Internal lookups used by the update handlers ───────────────────────


async def _roster_has(conn, tanod_id: str) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM roster_members WHERE id = %s", (tanod_id,))
        return await cur.fetchone() is not None


async def _schedule_current(conn, schedule_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute("SELECT plan_id, team_id FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Patrol schedule not found")
    return row