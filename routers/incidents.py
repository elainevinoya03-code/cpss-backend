from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from db import get_db

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

# Valid statuses matching the frontend
VALID_STATUSES = {"new", "acknowledged", "in_progress", "resolved", "closed_false_alarm"}
VALID_SOURCES = {"resident", "tanod", "desk_officer", "cctv", "iot", "iot_cctv", "sos"}
VALID_PRIORITIES = {"Low", "Medium", "High"}
VALID_VERIFICATION_STATUSES = {"new", "under_review", "verified", "unverified"}

# Complex query to fetch full incident with all related data.
# incidents.report_id is the REAL relationship to the source resident report
# (reports.id). The joined `report` object lets callers read the original
# resident-submitted record straight from the incident without re-deriving it
# from tracking_id.
_INCIDENT_SELECT = """
    SELECT 
        i.*,
        CASE
            WHEN i.report_id IS NOT NULL THEN json_build_object(
                'id', r.id,
                'tracking_id', r.tracking_id,
                'user_email', r.user_email,
                'category', r.category,
                'subtype', r.subtype,
                'is_emergency', r.is_emergency,
                'priority', r.priority,
                'latitude', r.latitude,
                'longitude', r.longitude,
                'place', r.place,
                'landmark', r.landmark,
                'narrative', r.narrative,
                'action_taken', r.action_taken,
                'report_date_time', r.report_date_time,
                'incident_date_time', r.incident_date_time,
                'status', r.status,
                'requested_action', r.requested_action,
                'action_other', r.action_other,
                'anonymous', r.anonymous,
                'respondent_name', r.respondent_name,
                'respondent_address', r.respondent_address,
                'respondent_contact', r.respondent_contact,
                'respondent_relation', r.respondent_relation,
                'callback_phone', r.callback_phone,
                'people_affected', r.people_affected,
                'additional_description', r.additional_description,
                'specific_info', r.specific_info,
                'emergency_answers', r.emergency_answers,
                'witnesses', r.witnesses,
                'created_at', r.created_at,
                'updated_at', r.updated_at
            )
            ELSE NULL
        END AS report,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'id', d.id,
                        'incident', d.incident,
                        'team', d.team,
                        'status', d.status,
                        'purok', d.purok,
                        'eta', d.eta,
                        'photos', d.photos,
                        'assigneeType', d.assignee_type,
                        'dispatchedAt', d.dispatched_at,
                        'onSceneAt', d.on_scene_at
                    )
                )
                FROM dispatches d
                WHERE d.incident = i.id
            ),
            '[]'::json
        ) AS dispatches
    FROM incidents i
    LEFT JOIN reports r ON r.id = i.report_id
"""

# Pydantic models for request/response
class IncidentBase(BaseModel):
    id: str
    # Real FK to the source resident report (public.reports.id). Nullable —
    # incidents may be created without a resident report.
    report_id: Optional[int] = None
    category: str
    severity: str
    purok: str
    description: str
    source: str
    reporter: str
    time: str
    status: str = "new"
    photos: int = 0
    lat: Optional[float] = None
    lng: Optional[float] = None
    priority: str = "Medium"
    notes: List[str] = []
    rating: Optional[int] = None
    anonymous: bool = False
    tracking_token: Optional[str] = None
    acknowledged_at: Optional[str] = None
    sla_breached: bool = False
    duplicate_resolved: bool = False
    related_to: List[str] = []
    escalated_to_captain: bool = False
    escalated_reason: Optional[str] = None
    escalated_at: Optional[str] = None
    reporter_safe: bool = False
    reporter_safe_at: Optional[str] = None
    validation_label: Optional[str] = None
    related_alert_id: Optional[str] = None
    closed_reason: Optional[str] = None
    verification_status: str = "new"
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    unverified_reason: Optional[str] = None
    category_history: List[dict] = []
    assigned_team: Optional[str] = None
    dispatch_id: Optional[str] = None
    closure_history: List[dict] = []
    resolved_at: Optional[str] = None
    iot_data: Optional[dict] = None
    feedback: Optional[str] = None
    created_at: Optional[str] = None


class IncidentCreate(IncidentBase):
    pass


class IncidentUpdate(BaseModel):
    category: Optional[str] = None
    severity: Optional[str] = None
    purok: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[List[str]] = None
    rating: Optional[int] = None
    acknowledged_at: Optional[str] = None
    escalated_to_captain: Optional[bool] = None
    escalated_reason: Optional[str] = None
    escalated_at: Optional[str] = None
    reporter_safe: Optional[bool] = None
    reporter_safe_at: Optional[str] = None
    closed_reason: Optional[str] = None
    verification_status: Optional[str] = None
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    unverified_reason: Optional[str] = None
    assigned_team: Optional[str] = None
    resolved_at: Optional[str] = None
    feedback: Optional[str] = None


class DispatchBase(BaseModel):
    id: str
    incident: str
    team: str
    status: str = "pending"
    purok: str = ""
    eta: Optional[str] = None
    photos: int = 0
    assignee_type: str = "tanod"
    dispatched_at: Optional[str] = None
    on_scene_at: Optional[str] = None


class DispatchCreate(DispatchBase):
    pass


class DispatchUpdate(BaseModel):
    status: Optional[str] = None
    eta: Optional[str] = None
    on_scene_at: Optional[str] = None


class BlotterBase(BaseModel):
    id: str
    original_incident_id: str
    category: str
    title: str
    description: str
    severity: str
    purok: str
    source: str
    incident_date_time: str
    resolved_date_time: Optional[str] = None
    filed_date_time: str
    converted_date_time: str
    reporter: str
    anonymous: bool = False
    assigned_officer: str
    recorded_by: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_label: str = ""
    resolution_summary: str
    final_disposition: str = "Resolved"
    disposition_note: Optional[str] = None
    closure_reason: Optional[str] = None
    citizen_rating: int = 0
    citizen_feedback: Optional[str] = None
    photo_count: int = 0
    field_notes: List[str] = []
    evidence_references: List[str] = []
    record_status: str = "active"
    amendments: List[dict] = []


class BlotterCreate(BlotterBase):
    pass


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_incident(row: dict) -> dict:
    out = dict(row)
    for key in ("time", "acknowledged_at", "escalated_at", "reporter_safe_at", 
                "verified_at", "resolved_at", "created_at", "updated_at"):
        out[key] = _iso(out.get(key))
    return out


def _row_to_dispatch(row: dict) -> dict:
    out = dict(row)
    for key in ("dispatched_at", "on_scene_at", "created_at", "updated_at"):
        out[key] = _iso(out.get(key))
    return out


def _row_to_blotter(row: dict) -> dict:
    out = dict(row)
    for key in ("incident_date_time", "resolved_date_time", "filed_date_time", 
                "converted_date_time", "created_at", "updated_at"):
        out[key] = _iso(out.get(key))
    return out


async def _fetch_incident(conn, incident_id: str) -> Optional[dict]:
    async with conn.cursor() as cur:
        await cur.execute(_INCIDENT_SELECT + " WHERE i.id = %s", (incident_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_incident(row)


@router.get("")
async def list_incidents():
    """Return all incidents with full details, newest first."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_INCIDENT_SELECT + " ORDER BY i.time DESC, i.id DESC")
            rows = await cur.fetchall()
    return [_row_to_incident(r) for r in rows]


@router.get("/blotters")
async def list_blotters():
    """Return all blotter records, newest first."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM blotters ORDER BY created_at DESC, id DESC"
            )
            rows = await cur.fetchall()
    return [_row_to_blotter(r) for r in rows]


@router.get("/{incident_id}")
async def get_incident(incident_id: str):
    """Get a specific incident by ID with all related data."""
    async with get_db() as conn:
        row = await _fetch_incident(conn, incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return row


@router.post("")
async def create_incident(incident: IncidentCreate):
    """Create a new incident."""
    # Validate inputs
    if incident.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Choose one of: {', '.join(sorted(VALID_STATUSES))}"
        )
    if incident.source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source. Choose one of: {', '.join(sorted(VALID_SOURCES))}"
        )
    if incident.priority not in VALID_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid priority. Choose one of: {', '.join(sorted(VALID_PRIORITIES))}"
        )
    if incident.verification_status not in VALID_VERIFICATION_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid verification status. Choose one of: {', '.join(sorted(VALID_VERIFICATION_STATUSES))}"
        )

    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Validate the linked resident report (if any) and prevent duplicate
            # incidents for the same report: reports.id -> incidents.report_id.
            if incident.report_id is not None:
                await cur.execute(
                    "SELECT id FROM reports WHERE id = %s", (incident.report_id,)
                )
                if await cur.fetchone() is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Referenced report {incident.report_id} does not exist",
                    )
                await cur.execute(
                    "SELECT id FROM incidents WHERE report_id = %s", (incident.report_id,)
                )
                existing = await cur.fetchone()
                if existing is not None and existing["id"] != incident.id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"An incident already exists for report {incident.report_id}: {existing['id']}",
                    )

            await cur.execute(
                """
                INSERT INTO incidents (
                    id, report_id, category, severity, purok, description, source, reporter, time, status,
                    photos, lat, lng, priority, notes, rating, anonymous, tracking_token,
                    acknowledged_at, sla_breached, duplicate_resolved, related_to,
                    escalated_to_captain, escalated_reason, escalated_at, reporter_safe,
                    reporter_safe_at, validation_label, related_alert_id, closed_reason,
                    verification_status, verified_by, verified_at, unverified_reason,
                    category_history, assigned_team, dispatch_id, closure_history,
                    resolved_at, iot_data, feedback, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    report_id = EXCLUDED.report_id,
                    category = EXCLUDED.category,
                    severity = EXCLUDED.severity,
                    purok = EXCLUDED.purok,
                    description = EXCLUDED.description,
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    notes = EXCLUDED.notes,
                    rating = EXCLUDED.rating,
                    acknowledged_at = EXCLUDED.acknowledged_at,
                    escalated_to_captain = EXCLUDED.escalated_to_captain,
                    escalated_reason = EXCLUDED.escalated_reason,
                    escalated_at = EXCLUDED.escalated_at,
                    reporter_safe = EXCLUDED.reporter_safe,
                    reporter_safe_at = EXCLUDED.reporter_safe_at,
                    closed_reason = EXCLUDED.closed_reason,
                    verification_status = EXCLUDED.verification_status,
                    verified_by = EXCLUDED.verified_by,
                    verified_at = EXCLUDED.verified_at,
                    unverified_reason = EXCLUDED.unverified_reason,
                    assigned_team = EXCLUDED.assigned_team,
                    resolved_at = EXCLUDED.resolved_at,
                    feedback = EXCLUDED.feedback,
                    updated_at = now()
                """,
                (
                    incident.id, incident.report_id, incident.category, incident.severity,
                    incident.purok, incident.description, incident.source, incident.reporter,
                    incident.time, incident.status, incident.photos, incident.lat, incident.lng,
                    incident.priority, incident.notes, incident.rating, incident.anonymous,
                    incident.tracking_token, incident.acknowledged_at, incident.sla_breached,
                    incident.duplicate_resolved, incident.related_to, incident.escalated_to_captain,
                    incident.escalated_reason, incident.escalated_at, incident.reporter_safe,
                    incident.reporter_safe_at, incident.validation_label, incident.related_alert_id,
                    incident.closed_reason, incident.verification_status, incident.verified_by,
                    incident.verified_at, incident.unverified_reason, incident.category_history,
                    incident.assigned_team, incident.dispatch_id, incident.closure_history,
                    incident.resolved_at, incident.iot_data, incident.feedback, incident.created_at or datetime.now().isoformat()
                )
            )

            row = await _fetch_incident(conn, incident.id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create incident")
    return row


# ── Report → Incident conversion ────────────────────────────────────────────
# Resident reports keep their full user-submitted data in public.reports (the
# source of truth). This endpoint creates the *persisted* operational incident
# for a report and links them with the real FK (incidents.report_id). It is
# idempotent: if an incident already exists for the report (report_id set), the
# existing record is returned and no duplicate is created.

_REPORT_TO_INCIDENT_STATUS = {
    "pending": "new",
    "under_review": "acknowledged",
    "assigned": "in_progress",
    "resolved": "resolved",
    "closed": "closed_false_alarm",
}

_REPORT_TO_VERIFICATION = {
    "pending": "new",
    "under_review": "under_review",
    "resolved": "verified",
    "closed": "unverified",
    "assigned": "verified",
}


def _desk_priority_from_report(report: dict) -> str:
    priority = (report.get("priority") or "Normal").strip().lower()
    if priority in ("high", "critical") or report.get("is_emergency"):
        return "High"
    if priority == "low":
        return "Low"
    return "Medium"


def _severity_from_report(report: dict) -> str:
    if report.get("is_emergency"):
        return "critical"
    priority = (report.get("priority") or "Normal").strip().lower()
    if priority in ("high", "critical"):
        return "critical"
    if priority == "low":
        return "low"
    return "warning"


def _purok_from_place(report: dict) -> str:
    import re

    haystack = f"{report.get('place') or ''} {report.get('landmark') or ''}".lower()
    match = re.search(r"purok\s*\d", haystack)
    if match:
        digits = re.search(r"\d+", match.group(0))
        if digits:
            return f"Purok {digits.group(0)}"
    return ""


def _incident_from_report_dict(report: dict, photos_count: int) -> dict:
    status = _REPORT_TO_INCIDENT_STATUS.get(report.get("status") or "", "new")
    verification = _REPORT_TO_VERIFICATION.get(report.get("status") or "", "new")
    time_value = (
        report.get("incident_date_time")
        or report.get("report_date_time")
        or report.get("created_at")
        or datetime.now().isoformat()
    )
    return {
        "id": report["tracking_id"],
        "report_id": report["id"],
        "category": report.get("category") or report.get("subtype") or "Other",
        "severity": _severity_from_report(report),
        "purok": _purok_from_place(report),
        "description": report.get("narrative")
        or report.get("subtype")
        or "No description provided.",
        "source": "resident",
        "reporter": "Anonymous"
        if report.get("anonymous")
        else report.get("user_email") or "Resident",
        "time": time_value,
        "status": status,
        "photos": photos_count,
        "lat": report.get("latitude") or 0,
        "lng": report.get("longitude") or 0,
        "priority": _desk_priority_from_report(report),
        "notes": [],
        "rating": None,
        "anonymous": bool(report.get("anonymous")),
        "tracking_token": report.get("tracking_id"),
        "acknowledged_at": None,
        "sla_breached": False,
        "duplicate_resolved": False,
        "related_to": [],
        "escalated_to_captain": False,
        "escalated_reason": None,
        "escalated_at": None,
        "reporter_safe": False,
        "reporter_safe_at": None,
        "validation_label": None,
        "related_alert_id": None,
        "closed_reason": None,
        "verification_status": verification,
        "verified_by": None,
        "verified_at": None,
        "unverified_reason": None,
        "category_history": [],
        "assigned_team": None,
        "dispatch_id": None,
        "closure_history": [],
        "resolved_at": None,
        "iot_data": None,
        "feedback": None,
        "created_at": datetime.now().isoformat(),
    }


@router.post("/from-report/{report_id}")
async def create_incident_from_report(report_id: int):
    """Create (or return the existing) persisted incident for a resident report.

    The resident-submitted record stays in public.reports (source of truth);
    the returned incident is the operational record linked by
    incidents.report_id -> reports.id. Idempotent — a report is never given a
    second incident (one report → one incident).
    """
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM reports WHERE id = %s", (report_id,)
            )
            report = await cur.fetchone()
            if report is None:
                raise HTTPException(status_code=404, detail="Report not found")

            # Duplicate prevention: if the report already has an incident, return it.
            await cur.execute(
                "SELECT id FROM incidents WHERE report_id = %s", (report_id,)
            )
            existing = await cur.fetchone()
            if existing is not None:
                row = await _fetch_incident(conn, existing["id"])
                if row is None:
                    raise HTTPException(
                        status_code=404, detail="Existing incident not found"
                    )
                return row

            await cur.execute(
                "SELECT COUNT(*)::int FROM report_photos WHERE report_id = %s",
                (report_id,),
            )
            photos_row = await cur.fetchone()
            photos_count = photos_row["count"] if photos_row else 0

            incident = _incident_from_report_dict(report, photos_count)

            await cur.execute(
                """
                INSERT INTO incidents (
                    id, report_id, category, severity, purok, description, source, reporter, time, status,
                    photos, lat, lng, priority, notes, rating, anonymous, tracking_token,
                    acknowledged_at, sla_breached, duplicate_resolved, related_to,
                    escalated_to_captain, escalated_reason, escalated_at, reporter_safe,
                    reporter_safe_at, validation_label, related_alert_id, closed_reason,
                    verification_status, verified_by, verified_at, unverified_reason,
                    category_history, assigned_team, dispatch_id, closure_history,
                    resolved_at, iot_data, feedback, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    incident["id"], incident["report_id"], incident["category"],
                    incident["severity"], incident["purok"], incident["description"],
                    incident["source"], incident["reporter"], incident["time"],
                    incident["status"], incident["photos"], incident["lat"], incident["lng"],
                    incident["priority"], incident["notes"], incident["rating"],
                    incident["anonymous"], incident["tracking_token"], incident["acknowledged_at"],
                    incident["sla_breached"], incident["duplicate_resolved"], incident["related_to"],
                    incident["escalated_to_captain"], incident["escalated_reason"],
                    incident["escalated_at"], incident["reporter_safe"], incident["reporter_safe_at"],
                    incident["validation_label"], incident["related_alert_id"],
                    incident["closed_reason"], incident["verification_status"], incident["verified_by"],
                    incident["verified_at"], incident["unverified_reason"], incident["category_history"],
                    incident["assigned_team"], incident["dispatch_id"], incident["closure_history"],
                    incident["resolved_at"], incident["iot_data"], incident["feedback"],
                    incident["created_at"],
                ),
            )

            row = await _fetch_incident(conn, incident["id"])

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create incident")
    return row


@router.put("/{incident_id}")
async def update_incident(incident_id: str, incident: IncidentUpdate):
    """Update an existing incident."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Check if incident exists
            await cur.execute("SELECT id FROM incidents WHERE id = %s", (incident_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Incident not found")

            # Build dynamic update query
            fields = []
            params = []
            
            updateable_fields = {
                'category': incident.category,
                'severity': incident.severity,
                'purok': incident.purok,
                'description': incident.description,
                'status': incident.status,
                'priority': incident.priority,
                'notes': incident.notes,
                'rating': incident.rating,
                'acknowledged_at': incident.acknowledged_at,
                'escalated_to_captain': incident.escalated_to_captain,
                'escalated_reason': incident.escalated_reason,
                'escalated_at': incident.escalated_at,
                'reporter_safe': incident.reporter_safe,
                'reporter_safe_at': incident.reporter_safe_at,
                'closed_reason': incident.closed_reason,
                'verification_status': incident.verification_status,
                'verified_by': incident.verified_by,
                'verified_at': incident.verified_at,
                'unverified_reason': incident.unverified_reason,
                'assigned_team': incident.assigned_team,
                'resolved_at': incident.resolved_at,
                'feedback': incident.feedback,
            }
            
            for field, value in updateable_fields.items():
                if value is not None:
                    fields.append(f"{field} = %s")
                    params.append(value)
            
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            
            params.append(incident_id)
            fields.append("updated_at = now()")
            
            await cur.execute(
                f"UPDATE incidents SET {', '.join(fields)} WHERE id = %s",
                params
            )

            row = await _fetch_incident(conn, incident_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to update incident")
    return row


@router.delete("/{incident_id}")
async def delete_incident(incident_id: str):
    """Delete an incident."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM incidents WHERE id = %s", (incident_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Incident not found")
    return {"message": f"Incident {incident_id} deleted successfully"}


# Dispatch endpoints
@router.get("/{incident_id}/dispatches")
async def list_dispatches(incident_id: str):
    """Get all dispatches for a specific incident."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM dispatches WHERE incident = %s ORDER BY created_at DESC",
                (incident_id,)
            )
            rows = await cur.fetchall()
    return [_row_to_dispatch(r) for r in rows]


@router.post("/{incident_id}/dispatches")
async def create_dispatch(incident_id: str, dispatch: DispatchCreate):
    """Create a new dispatch for an incident."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Check if incident exists
            await cur.execute("SELECT id FROM incidents WHERE id = %s", (incident_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Incident not found")

            await cur.execute(
                """
                INSERT INTO dispatches (
                    id, incident, team, status, purok, eta, photos, assignee_type,
                    dispatched_at, on_scene_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    team = EXCLUDED.team,
                    status = EXCLUDED.status,
                    purok = EXCLUDED.purok,
                    eta = EXCLUDED.eta,
                    photos = EXCLUDED.photos,
                    assignee_type = EXCLUDED.assignee_type,
                    dispatched_at = EXCLUDED.dispatched_at,
                    on_scene_at = EXCLUDED.on_scene_at,
                    updated_at = now()
                """,
                (dispatch.id, incident_id, dispatch.team, dispatch.status, dispatch.purok,
                 dispatch.eta, dispatch.photos, dispatch.assignee_type, dispatch.dispatched_at,
                 dispatch.on_scene_at)
            )

            await cur.execute("SELECT * FROM dispatches WHERE id = %s", (dispatch.id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create dispatch")
    return _row_to_dispatch(row)


@router.put("/dispatches/{dispatch_id}")
async def update_dispatch(dispatch_id: str, dispatch: DispatchUpdate):
    """Update an existing dispatch."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Check if dispatch exists
            await cur.execute("SELECT id FROM dispatches WHERE id = %s", (dispatch_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Dispatch not found")

            # Build dynamic update query
            fields = []
            params = []
            
            updateable_fields = {
                'status': dispatch.status,
                'eta': dispatch.eta,
                'on_scene_at': dispatch.on_scene_at,
            }
            
            for field, value in updateable_fields.items():
                if value is not None:
                    fields.append(f"{field} = %s")
                    params.append(value)
            
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            
            params.append(dispatch_id)
            fields.append("updated_at = now()")
            
            await cur.execute(
                f"UPDATE dispatches SET {', '.join(fields)} WHERE id = %s",
                params
            )

            await cur.execute("SELECT * FROM dispatches WHERE id = %s", (dispatch_id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to update dispatch")
    return _row_to_dispatch(row)


@router.delete("/dispatches/{dispatch_id}")
async def delete_dispatch(dispatch_id: str):
    """Delete a dispatch."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM dispatches WHERE id = %s", (dispatch_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Dispatch not found")
    return {"message": f"Dispatch {dispatch_id} deleted successfully"}


# Blotter endpoints
@router.get("/blotters/{blotter_id}")
async def get_blotter(blotter_id: str):
    """Get a specific blotter by ID."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM blotters WHERE id = %s", (blotter_id,))
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Blotter not found")
    return _row_to_blotter(row)


@router.post("/blotters")
async def create_blotter(blotter: BlotterCreate):
    """Create a new blotter record."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Check if incident exists
            await cur.execute("SELECT id FROM incidents WHERE id = %s", (blotter.original_incident_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Original incident not found")

            await cur.execute(
                """
                INSERT INTO blotters (
                    id, original_incident_id, category, title, description, severity, purok,
                    source, incident_date_time, resolved_date_time, filed_date_time,
                    converted_date_time, reporter, anonymous, assigned_officer, recorded_by,
                    lat, lng, location_label, resolution_summary, final_disposition,
                    disposition_note, closure_reason, citizen_rating, citizen_feedback,
                    photo_count, field_notes, evidence_references, record_status, amendments
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    category = EXCLUDED.category,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    severity = EXCLUDED.severity,
                    purok = EXCLUDED.purok,
                    source = EXCLUDED.source,
                    incident_date_time = EXCLUDED.incident_date_time,
                    resolved_date_time = EXCLUDED.resolved_date_time,
                    filed_date_time = EXCLUDED.filed_date_time,
                    converted_date_time = EXCLUDED.converted_date_time,
                    reporter = EXCLUDED.reporter,
                    anonymous = EXCLUDED.anonymous,
                    assigned_officer = EXCLUDED.assigned_officer,
                    recorded_by = EXCLUDED.recorded_by,
                    lat = EXCLUDED.lat,
                    lng = EXCLUDED.lng,
                    location_label = EXCLUDED.location_label,
                    resolution_summary = EXCLUDED.resolution_summary,
                    final_disposition = EXCLUDED.final_disposition,
                    disposition_note = EXCLUDED.disposition_note,
                    closure_reason = EXCLUDED.closure_reason,
                    citizen_rating = EXCLUDED.citizen_rating,
                    citizen_feedback = EXCLUDED.citizen_feedback,
                    photo_count = EXCLUDED.photo_count,
                    field_notes = EXCLUDED.field_notes,
                    evidence_references = EXCLUDED.evidence_references,
                    record_status = EXCLUDED.record_status,
                    amendments = EXCLUDED.amendments,
                    updated_at = now()
                """,
                (
                    blotter.id, blotter.original_incident_id, blotter.category, blotter.title,
                    blotter.description, blotter.severity, blotter.purok, blotter.source,
                    blotter.incident_date_time, blotter.resolved_date_time, blotter.filed_date_time,
                    blotter.converted_date_time, blotter.reporter, blotter.anonymous,
                    blotter.assigned_officer, blotter.recorded_by, blotter.lat, blotter.lng,
                    blotter.location_label, blotter.resolution_summary, blotter.final_disposition,
                    blotter.disposition_note, blotter.closure_reason, blotter.citizen_rating,
                    blotter.citizen_feedback, blotter.photo_count, blotter.field_notes,
                    blotter.evidence_references, blotter.record_status, blotter.amendments
                )
            )

            await cur.execute("SELECT * FROM blotters WHERE id = %s", (blotter.id,))
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create blotter")
    return _row_to_blotter(row)


@router.delete("/blotters/{blotter_id}")
async def delete_blotter(blotter_id: str):
    """Delete a blotter record."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM blotters WHERE id = %s", (blotter_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Blotter not found")
    return {"message": f"Blotter {blotter_id} deleted successfully"}