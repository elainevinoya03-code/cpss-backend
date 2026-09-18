from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from db import get_db

router = APIRouter(prefix="/api/patrol-configuration", tags=["patrol-configuration"])

# Valid statuses matching the frontend
VALID_STATUSES = {"draft", "pending_approval", "approved", "revision_required", "rejected"}
VALID_TYPES = {"fixed", "route"}
VALID_RECURRING = {"none", "daily", "specific_days"}

# Complex query to fetch full checkpoint plan with all related data
_PLAN_SELECT = """
    SELECT 
        cp.*,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'id', pt.id,
                        'kind', pt.kind,
                        'label', pt.label,
                        'name', pt.name,
                        'address', pt.address,
                        'landmark', pt.landmark,
                        'description', pt.description,
                        'remarks', pt.remarks,
                        'lat', pt.lat,
                        'lng', pt.lng,
                        'route_id', pt.route_id
                    ) ORDER BY 
                        CASE pt.kind
                            WHEN 'start' THEN 1
                            WHEN 'intermediate' THEN 2
                            WHEN 'end' THEN 3
                            WHEN 'fixed' THEN 4
                            ELSE 5
                        END
                )
                FROM checkpoint_points pt
                WHERE pt.plan_id = cp.id AND pt.route_id IS NULL
            ),
            '[]'::json
        ) AS points,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'id', r.id,
                        'label', r.label,
                        'title', r.title,
                        'role', r.role,
                        'color', r.color,
                        'points', (
                            SELECT json_agg(
                                json_build_object(
                                    'id', rpt.id,
                                    'kind', rpt.kind,
                                    'label', rpt.label,
                                    'name', rpt.name,
                                    'address', rpt.address,
                                    'landmark', rpt.landmark,
                                    'description', rpt.description,
                                    'remarks', rpt.remarks,
                                    'lat', rpt.lat,
                                    'lng', rpt.lng
                                ) ORDER BY rpt.id
                            )
                            FROM checkpoint_points rpt
                            WHERE rpt.route_id = r.id
                        )
                    )
                )
                FROM checkpoint_routes r
                WHERE r.plan_id = cp.id
            ),
            '[]'::json
        ) AS routes,
        COALESCE(
            (
                SELECT json_build_object(
                    'operationDate', ps.operation_date::text,
                    'endDate', ps.end_date::text,
                    'startTime', ps.start_time,
                    'endTime', ps.end_time,
                    'recurring', ps.recurring,
                    'recurringDays', CASE WHEN jsonb_typeof(ps.recurring_days) = 'object' THEN '[]'::jsonb ELSE ps.recurring_days END,
                    'expectedDuration', ps.expected_duration
                )
                FROM patrol_schedules ps
                WHERE ps.plan_id = cp.id
                LIMIT 1
            ),
            json_build_object(
                'operationDate', '',
                'endDate', '',
                'startTime', '',
                'endTime', '',
                'recurring', 'none',
                'recurringDays', '[]'::json,
                'expectedDuration', ''
            )
        ) AS schedule,
        COALESCE(
            (
                SELECT json_build_object(
                    'general', onotes.general,
                    'safety', onotes.safety,
                    'equipment', onotes.equipment,
                    'coordination', onotes.coordination,
                    'special', onotes.special,
                    'other', onotes.other
                )
                FROM operational_notes onotes
                WHERE onotes.plan_id = cp.id
                LIMIT 1
            ),
            json_build_object(
                'general', '',
                'safety', '',
                'equipment', '',
                'coordination', '',
                'special', '',
                'other', ''
            )
        ) AS notes,
    json_build_object(
            'pct', cp.coverage_pct,
            'covered', cp.coverage_covered,
            'total', cp.coverage_total,
            'window', cp.coverage_window
        ) AS coverage
    FROM checkpoint_plans cp
    WHERE cp.id = %s
"""

# Simplified select for listing all plans
_PLANS_LIST_SELECT = """
    SELECT 
        cp.*,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'id', pt.id,
                        'kind', pt.kind,
                        'label', pt.label,
                        'name', pt.name,
                        'address', pt.address,
                        'landmark', pt.landmark,
                        'description', pt.description,
                        'remarks', pt.remarks,
                        'lat', pt.lat,
                        'lng', pt.lng,
                        'route_id', pt.route_id
                    ) ORDER BY 
                        CASE pt.kind
                            WHEN 'start' THEN 1
                            WHEN 'intermediate' THEN 2
                            WHEN 'end' THEN 3
                            WHEN 'fixed' THEN 4
                            ELSE 5
                        END
                )
                FROM checkpoint_points pt
                WHERE pt.plan_id = cp.id AND pt.route_id IS NULL
            ),
            '[]'::json
        ) AS points,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'id', r.id,
                        'label', r.label,
                        'title', r.title,
                        'role', r.role,
                        'color', r.color,
                        'points', (
                            SELECT json_agg(
                                json_build_object(
                                    'id', rpt.id,
                                    'kind', rpt.kind,
                                    'label', rpt.label,
                                    'name', rpt.name,
                                    'address', rpt.address,
                                    'landmark', rpt.landmark,
                                    'description', rpt.description,
                                    'remarks', rpt.remarks,
                                    'lat', rpt.lat,
                                    'lng', rpt.lng
                                ) ORDER BY rpt.id
                            )
                            FROM checkpoint_points rpt
                            WHERE rpt.route_id = r.id
                        )
                    )
                )
                FROM checkpoint_routes r
                WHERE r.plan_id = cp.id
            ),
            '[]'::json
        ) AS routes,
        COALESCE(
            (
                SELECT json_build_object(
                    'operationDate', ps.operation_date::text,
                    'endDate', ps.end_date::text,
                    'startTime', ps.start_time,
                    'endTime', ps.end_time,
                    'recurring', ps.recurring,
                    'recurringDays', CASE WHEN jsonb_typeof(ps.recurring_days) = 'object' THEN '[]'::jsonb ELSE ps.recurring_days END,
                    'expectedDuration', ps.expected_duration
                )
                FROM patrol_schedules ps
                WHERE ps.plan_id = cp.id
                LIMIT 1
            ),
            json_build_object(
                'operationDate', '',
                'endDate', '',
                'startTime', '',
                'endTime', '',
                'recurring', 'none',
                'recurringDays', '[]'::json,
                'expectedDuration', ''
            )
        ) AS schedule,
        COALESCE(
            (
                SELECT json_build_object(
                    'general', onotes.general,
                    'safety', onotes.safety,
                    'equipment', onotes.equipment,
                    'coordination', onotes.coordination,
                    'special', onotes.special,
                    'other', onotes.other
                )
                FROM operational_notes onotes
                WHERE onotes.plan_id = cp.id
                LIMIT 1
            ),
            json_build_object(
                'general', '',
                'safety', '',
                'equipment', '',
                'coordination', '',
                'special', '',
                'other', ''
            )
        ) AS notes,
    json_build_object(
            'pct', cp.coverage_pct,
            'covered', cp.coverage_covered,
            'total', cp.coverage_total,
            'window', cp.coverage_window
        ) AS coverage
    FROM checkpoint_plans cp
    ORDER BY cp.created_at DESC, cp.id DESC
"""


# Pydantic models for request/response
class CpPoint(BaseModel):
    id: str
    kind: str
    label: str
    name: str
    address: str = ""
    landmark: str = ""
    description: str = ""
    remarks: str = ""
    lat: float
    lng: float
    route_id: Optional[str] = None


class CpRoute(BaseModel):
    id: str
    label: str
    title: str = ""
    role: str = "support"
    color: str = "#0d9488"
    points: List[CpPoint] = []


class ScheduleForm(BaseModel):
    operationDate: str
    endDate: Optional[str] = ""
    startTime: str
    endTime: str
    recurring: str = "none"
    recurringDays: List[str] = []
    expectedDuration: str = ""

    @field_validator("recurringDays", mode="before")
    @classmethod
    def coerce_recurring_days(cls, v):
        # DB jsonb can come back as {} for an empty array — normalize it.
        if v is None or v == {}:
            return []
        return v


class OperNotes(BaseModel):
    general: str = ""
    safety: str = ""
    equipment: str = ""
    coordination: str = ""
    special: str = ""
    other: str = ""


class CoverageResult(BaseModel):
    pct: int = 0
    covered: int = 0
    total: int = 0
    window: str = ""


class CheckpointPlanCreate(BaseModel):
    id: str
    code: str
    name: str
    type: str
    purpose: str
    objective: str
    rationale: str
    target_area: str
    remarks: str = ""
    points: List[CpPoint] = []
    routes: List[CpRoute] = []
    linked_incident_ids: List[str] = []
    schedule: ScheduleForm
    notes: OperNotes
    coverage: CoverageResult
    status: str = "draft"
    submitted_by: Optional[str] = None
    submitted_at: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    revision_comment: Optional[str] = None
    rejection_reason: Optional[str] = None
    approval_comments: Optional[str] = None

    @field_validator("linked_incident_ids", mode="before")
    @classmethod
    def coerce_linked_ids(cls, v):
        if v is None or v == {}:
            return []
        return v


class CheckpointPlanUpdate(BaseModel):
    name: Optional[str] = None
    purpose: Optional[str] = None
    objective: Optional[str] = None
    rationale: Optional[str] = None
    target_area: Optional[str] = None
    remarks: Optional[str] = None
    points: Optional[List[CpPoint]] = None
    routes: Optional[List[CpRoute]] = None
    linked_incident_ids: Optional[List[str]] = None
    schedule: Optional[ScheduleForm] = None
    notes: Optional[OperNotes] = None
    coverage: Optional[CoverageResult] = None
    status: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_at: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    revision_comment: Optional[str] = None
    rejection_reason: Optional[str] = None
    approval_comments: Optional[str] = None

    @field_validator("linked_incident_ids", mode="before")
    @classmethod
    def coerce_linked_ids(cls, v):
        if v is None or v == {}:
            return None if v is None else []
        return v


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_plan(row: dict) -> dict:
    out = dict(row)
    for key in ("created_at", "updated_at", "submitted_at", "decided_at"):
        out[key] = _iso(row.get(key))
    # DB jsonb can store {} for an empty array — always return a real array.
    if not isinstance(out.get("linked_incident_ids"), list):
        out["linked_incident_ids"] = []
    return out


async def _fetch_plan(conn, plan_id: str) -> Optional[dict]:
    async with conn.cursor() as cur:
        await cur.execute(_PLAN_SELECT, (plan_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_plan(row)


def _parse_date(date_str: str) -> Optional[str]:
    """Parse date string or return None if empty"""
    if not date_str or date_str.strip() == "":
        return None
    return date_str


@router.get("")
async def list_plans():
    """Return all checkpoint plans with full details, newest first."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_PLANS_LIST_SELECT)
            rows = await cur.fetchall()
    return [_row_to_plan(r) for r in rows]


@router.get("/{plan_id}")
async def get_plan(plan_id: str):
    """Get a specific checkpoint plan by ID with all related data."""
    async with get_db() as conn:
        row = await _fetch_plan(conn, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Checkpoint plan not found")
    return row


@router.post("")
async def create_plan(plan: CheckpointPlanCreate):
    """Create a new checkpoint plan with all related data."""
    # Validate inputs
    if plan.type not in VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type. Choose one of: {', '.join(sorted(VALID_TYPES))}"
        )
    if plan.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Choose one of: {', '.join(sorted(VALID_STATUSES))}"
        )
    if plan.schedule.recurring not in VALID_RECURRING:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid recurring value. Choose one of: {', '.join(sorted(VALID_RECURRING))}"
        )

    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Insert main plan
            await cur.execute(
                """
                INSERT INTO checkpoint_plans (
                    id, code, name, type, purpose, objective, rationale, target_area, remarks,
                    linked_incident_ids, status, submitted_by, submitted_at, decided_by, decided_at,
                    revision_comment, rejection_reason, approval_comments,
                    coverage_pct, coverage_covered, coverage_total, coverage_window
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    type = EXCLUDED.type,
                    purpose = EXCLUDED.purpose,
                    objective = EXCLUDED.objective,
                    rationale = EXCLUDED.rationale,
                    target_area = EXCLUDED.target_area,
                    remarks = EXCLUDED.remarks,
                    linked_incident_ids = EXCLUDED.linked_incident_ids,
                    status = EXCLUDED.status,
                    submitted_by = EXCLUDED.submitted_by,
                    submitted_at = EXCLUDED.submitted_at,
                    decided_by = EXCLUDED.decided_by,
                    decided_at = EXCLUDED.decided_at,
                    revision_comment = EXCLUDED.revision_comment,
                    rejection_reason = EXCLUDED.rejection_reason,
                    approval_comments = EXCLUDED.approval_comments,
                    coverage_pct = EXCLUDED.coverage_pct,
                    coverage_covered = EXCLUDED.coverage_covered,
                    coverage_total = EXCLUDED.coverage_total,
                    coverage_window = EXCLUDED.coverage_window,
                    updated_at = now()
                """,
                (
                    plan.id, plan.code, plan.name, plan.type, plan.purpose, plan.objective,
                    plan.rationale, plan.target_area, plan.remarks,
                    plan.linked_incident_ids, plan.status, plan.submitted_by, plan.submitted_at,
                    plan.decided_by, plan.decided_at, plan.revision_comment, plan.rejection_reason,
                    plan.approval_comments, plan.coverage.pct, plan.coverage.covered,
                    plan.coverage.total, plan.coverage.window
                )
            )

            # Delete existing points and routes for this plan (for updates)
            await cur.execute("DELETE FROM checkpoint_points WHERE plan_id = %s", (plan.id,))
            await cur.execute("DELETE FROM checkpoint_routes WHERE plan_id = %s", (plan.id,))
            await cur.execute("DELETE FROM patrol_schedules WHERE plan_id = %s", (plan.id,))
            await cur.execute("DELETE FROM operational_notes WHERE plan_id = %s", (plan.id,))

            # Insert points (primary route points only)
            for point in plan.points:
                if point.id:  # Only insert if point has valid data
                    await cur.execute(
                        """
                        INSERT INTO checkpoint_points (
                            id, plan_id, kind, label, name, address, landmark, description, remarks, lat, lng, route_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (point.id, plan.id, point.kind, point.label, point.name, point.address,
                         point.landmark, point.description, point.remarks, point.lat, point.lng, None)
                    )

            # Insert routes and their points
            for route in plan.routes:
                if route.id:  # Only insert if route has valid data
                    await cur.execute(
                        """
                        INSERT INTO checkpoint_routes (id, plan_id, label, title, role, color)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (route.id, plan.id, route.label, route.title, route.role, route.color)
                    )
                    # Insert route points
                    for point in route.points:
                        if point.id:  # Only insert if point has valid data
                            await cur.execute(
                                """
                                INSERT INTO checkpoint_points (
                                    id, plan_id, kind, label, name, address, landmark, description, remarks, lat, lng, route_id
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (point.id, plan.id, point.kind, point.label, point.name, point.address,
                                 point.landmark, point.description, point.remarks, point.lat, point.lng, route.id)
                            )

            # Insert schedule
            await cur.execute(
                """
                INSERT INTO patrol_schedules (
                    plan_id, operation_date, end_date, start_time, end_time, recurring, recurring_days, expected_duration
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    plan.id, _parse_date(plan.schedule.operationDate), _parse_date(plan.schedule.endDate),
                    plan.schedule.startTime, plan.schedule.endTime, plan.schedule.recurring,
                    plan.schedule.recurringDays, plan.schedule.expectedDuration
                )
            )

            # Insert operational notes
            await cur.execute(
                """
                INSERT INTO operational_notes (
                    plan_id, general, safety, equipment, coordination, special, other
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    plan.id, plan.notes.general, plan.notes.safety, plan.notes.equipment,
                    plan.notes.coordination, plan.notes.special, plan.notes.other
                )
            )

            # Fetch the complete plan
            row = await _fetch_plan(conn, plan.id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create checkpoint plan")
    return row


@router.put("/{plan_id}")
async def update_plan(plan_id: str, plan: CheckpointPlanUpdate):
    """Update an existing checkpoint plan."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Check if plan exists
            await cur.execute("SELECT id FROM checkpoint_plans WHERE id = %s", (plan_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Checkpoint plan not found")

            # Build update fields dynamically
            fields = []
            params = []

            if plan.name is not None:
                fields.append("name = %s")
                params.append(plan.name)
            if plan.purpose is not None:
                fields.append("purpose = %s")
                params.append(plan.purpose)
            if plan.objective is not None:
                fields.append("objective = %s")
                params.append(plan.objective)
            if plan.rationale is not None:
                fields.append("rationale = %s")
                params.append(plan.rationale)
            if plan.target_area is not None:
                fields.append("target_area = %s")
                params.append(plan.target_area)
            if plan.remarks is not None:
                fields.append("remarks = %s")
                params.append(plan.remarks)
            if plan.linked_incident_ids is not None:
                fields.append("linked_incident_ids = %s")
                params.append(plan.linked_incident_ids)
            if plan.status is not None:
                if plan.status not in VALID_STATUSES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status. Choose one of: {', '.join(sorted(VALID_STATUSES))}"
                    )
                fields.append("status = %s")
                params.append(plan.status)
            if plan.submitted_by is not None:
                fields.append("submitted_by = %s")
                params.append(plan.submitted_by)
            if plan.submitted_at is not None:
                fields.append("submitted_at = %s")
                params.append(plan.submitted_at)
            if plan.decided_by is not None:
                fields.append("decided_by = %s")
                params.append(plan.decided_by)
            if plan.decided_at is not None:
                fields.append("decided_at = %s")
                params.append(plan.decided_at)
            if plan.revision_comment is not None:
                fields.append("revision_comment = %s")
                params.append(plan.revision_comment)
            if plan.rejection_reason is not None:
                fields.append("rejection_reason = %s")
                params.append(plan.rejection_reason)
            if plan.approval_comments is not None:
                fields.append("approval_comments = %s")
                params.append(plan.approval_comments)
            if plan.coverage is not None:
                fields.extend([
                    "coverage_pct = %s",
                    "coverage_covered = %s",
                    "coverage_total = %s",
                    "coverage_window = %s"
                ])
                params.extend([plan.coverage.pct, plan.coverage.covered, plan.coverage.total, plan.coverage.window])

            if fields:
                fields.append("updated_at = now()")
                params.append(plan_id)
                await cur.execute(
                    f"UPDATE checkpoint_plans SET {', '.join(fields)} WHERE id = %s",
                    params
                )

            # Update points if provided
            if plan.points is not None:
                await cur.execute("DELETE FROM checkpoint_points WHERE plan_id = %s AND route_id IS NULL", (plan_id,))
                for point in plan.points:
                    await cur.execute(
                        """
                        INSERT INTO checkpoint_points (
                            id, plan_id, kind, label, name, address, landmark, description, remarks, lat, lng, route_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (point.id, plan_id, point.kind, point.label, point.name, point.address,
                         point.landmark, point.description, point.remarks, point.lat, point.lng, None)
                    )

            # Update routes if provided
            if plan.routes is not None:
                await cur.execute("DELETE FROM checkpoint_routes WHERE plan_id = %s", (plan_id,))
                for route in plan.routes:
                    await cur.execute(
                        """
                        INSERT INTO checkpoint_routes (id, plan_id, label, title, role, color)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (route.id, plan_id, route.label, route.title, route.role, route.color)
                    )
                    # Insert route points
                    for point in route.points:
                        await cur.execute(
                            """
                            INSERT INTO checkpoint_points (
                                id, plan_id, kind, label, name, address, landmark, description, remarks, lat, lng, route_id
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (point.id, plan_id, point.kind, point.label, point.name, point.address,
                             point.landmark, point.description, point.remarks, point.lat, point.lng, route.id)
                        )

            # Update schedule if provided
            if plan.schedule is not None:
                if plan.schedule.recurring not in VALID_RECURRING:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid recurring value. Choose one of: {', '.join(sorted(VALID_RECURRING))}"
                    )
                await cur.execute("DELETE FROM patrol_schedules WHERE plan_id = %s", (plan_id,))
                await cur.execute(
                    """
                    INSERT INTO patrol_schedules (
                        plan_id, operation_date, end_date, start_time, end_time, recurring, recurring_days, expected_duration
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        plan_id, _parse_date(plan.schedule.operationDate), _parse_date(plan.schedule.endDate),
                        plan.schedule.startTime, plan.schedule.endTime, plan.schedule.recurring,
                        plan.schedule.recurringDays, plan.schedule.expectedDuration
                    )
                )

            # Update notes if provided
            if plan.notes is not None:
                await cur.execute("DELETE FROM operational_notes WHERE plan_id = %s", (plan_id,))
                await cur.execute(
                    """
                    INSERT INTO operational_notes (
                        plan_id, general, safety, equipment, coordination, special, other
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        plan_id, plan.notes.general, plan.notes.safety, plan.notes.equipment,
                        plan.notes.coordination, plan.notes.special, plan.notes.other
                    )
                )

            # Fetch the complete plan
            row = await _fetch_plan(conn, plan_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to update checkpoint plan")
    return row


@router.delete("/{plan_id}")
async def delete_plan(plan_id: str):
    """Delete a checkpoint plan and all related data."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Check if plan exists
            await cur.execute("SELECT id FROM checkpoint_plans WHERE id = %s", (plan_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Checkpoint plan not found")

            # Delete will cascade due to foreign key constraints
            await cur.execute("DELETE FROM checkpoint_plans WHERE id = %s", (plan_id,))

    return {"message": f"Checkpoint plan {plan_id} deleted successfully"}


@router.get("/approved/list")
async def get_approved_plans():
    """Get all approved checkpoint plans for scheduling purposes."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                _PLANS_LIST_SELECT + " WHERE cp.status = 'approved'",
            )
            rows = await cur.fetchall()
    return [_row_to_plan(r) for r in rows]
