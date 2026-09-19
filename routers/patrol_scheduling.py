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


async def _schedule_exists(conn, schedule_id: str) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM active_patrol_schedules WHERE id = %s", (schedule_id,))
        return await cur.fetchone() is not None


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
    member_ids = list(dict.fromkeys(team.memberIds))
    if leader_id and leader_id not in member_ids:
        member_ids = [leader_id, *member_ids]

    async with get_db() as conn:
        await _validate_team_refs(conn, leader_id, member_ids)
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
        if not await _team_exists(conn, team_id):
            raise HTTPException(status_code=404, detail="Patrol team not found")

        if patch.name is not None:
            if not patch.name.strip():
                raise HTTPException(status_code=400, detail="Team name is required")
            fields.append("name = %s")
            params.append(patch.name.strip())
        if patch.leaderId is not None:
            if patch.leaderId and not await _roster_has(conn, patch.leaderId):
                raise HTTPException(status_code=400, detail=f"Team leader {patch.leaderId} is not on the roster")
            fields.append("leader_id = %s")
            params.append(patch.leaderId or None)
        if patch.memberIds is not None:
            member_ids = list(dict.fromkeys(patch.memberIds))
            await _validate_team_refs(conn, patch.leaderId, member_ids)
            fields.append("member_ids = %s")
            params.append(Jsonb(member_ids))
        if patch.isActive is not None:
            fields.append("is_active = %s")
            params.append(patch.isActive)

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
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO active_patrol_schedules (
                    id, code, plan_id, start_date, end_date, start_time, end_time,
                    frequency, frequency_days, custom_notes, shift_type, team_id,
                    assignment_mode, assignments, ops, status, created_by,
                    submitted_at, decided_by, decided_at, notified_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    code            = EXCLUDED.code,
                    plan_id         = EXCLUDED.plan_id,
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
        if not await _schedule_exists(conn, schedule_id):
            raise HTTPException(status_code=404, detail="Patrol schedule not found")

        if patch.code is not None:
            fields.append("code = %s")
            params.append(patch.code)
        if patch.planId is not None or patch.teamId is not None:
            current = await _schedule_current(conn, schedule_id)
            plan_id = patch.planId or current["plan_id"]
            team_id = patch.teamId or current["team_id"]
            await _validate_schedule_refs(conn, plan_id, team_id)
            if patch.planId is not None:
                fields.append("plan_id = %s")
                params.append(patch.planId)
            if patch.teamId is not None:
                fields.append("team_id = %s")
                params.append(patch.teamId)
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