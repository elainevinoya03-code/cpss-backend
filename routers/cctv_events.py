from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from db import get_db
from routers.incidents import _INCIDENT_SELECT, _row_to_incident

router = APIRouter(prefix="/api/cctv/events", tags=["cctv-events"])

# Mirrors the frontend IncidentAction type in surveillance_matrix.tsx.
VALID_ACTIONS = {"create_new", "link_existing"}

# Status/priority applied to incidents created from a CCTV tag. The Desk
# Officer Triage queue treats In Progress as being actively handled and
# Emergency as the highest operational priority (see dashboard SLA targets).
CCTV_INCIDENT_STATUS = "in_progress"
CCTV_INCIDENT_PRIORITY = "Emergency"
CCTV_INCIDENT_VERIFICATION = "verified"


class CctvEventCreate(BaseModel):
    id: str
    camera_id: str = ""
    camera_name: str = ""
    camera_location: str = ""
    camera_purok: str = ""
    category: str
    notes: Optional[str] = None
    timestamp: str
    operator: str = "CO-01"
    incident_action: str = "create_new"
    incident_id: Optional[str] = None


class CctvEventOut(BaseModel):
    id: str
    camera_id: Optional[str] = ""
    camera_name: Optional[str] = ""
    camera_location: Optional[str] = ""
    camera_purok: Optional[str] = ""
    category: Optional[str] = ""
    notes: Optional[str] = None
    timestamp: Optional[str] = None
    operator: Optional[str] = "CO-01"
    incident_action: Optional[str] = "create_new"
    incident_id: Optional[str] = None
    incident_status: Optional[str] = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    incident: Optional[dict] = None


# Self-heal DDL: if the production database was provisioned before migration
# 020_create_cctv_events_table.sql was applied, SELECT/INSERT would raise
# UndefinedTable -> FastAPI 500 -> browser reports it as a CORS failure
# (no ACAO header on the error path / Render proxy error). Creating the
# table IF NOT EXISTS keeps the endpoint at worst returning [].
_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.cctv_events (
    id               TEXT          PRIMARY KEY,
    camera_id        TEXT,
    camera_name      TEXT          NOT NULL DEFAULT '',
    camera_location  TEXT          NOT NULL DEFAULT '',
    camera_purok     TEXT          NOT NULL DEFAULT '',
    category         TEXT          NOT NULL DEFAULT '',
    notes            TEXT,
    timestamp        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    operator         TEXT          NOT NULL DEFAULT 'CO-01',
    incident_action  TEXT          NOT NULL DEFAULT 'create_new'
                                  CHECK (incident_action IN ('create_new', 'link_existing')),
    incident_id      TEXT,
    incident_status  TEXT          NOT NULL DEFAULT 'Pending Desk Officer Triage',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);
"""


async def _ensure_table(conn) -> None:
    async with conn.cursor() as cur:
        await cur.execute(_ENSURE_TABLE_SQL)


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _row_to_event(row: dict) -> dict:
    out = dict(row)
    for key in ("timestamp", "created_at", "updated_at"):
        out[key] = _iso(out.get(key))
    # Coerce NULLs to the tolerant defaults so response-model validation
    # never 500s on legacy rows (e.g. camera_id NULL via FK SET NULL).
    for key in (
        "camera_id", "camera_name", "camera_location", "camera_purok",
        "category", "operator", "incident_action", "incident_status",
    ):
        if out.get(key) is None:
            out[key] = "" if key != "incident_action" else "create_new"
    out.pop("incident", None)
    return out


async def _next_incident_id(conn) -> str:
    """Compute the next sequential INC-xxxx id from the persisted incidents."""
    default = 2072
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT COALESCE(MAX(
                regexp_replace(id, '^INC-', '')::int
            ), %s) AS max_seq
            FROM incidents
            WHERE id ~ '^INC-[0-9]+$'
            """,
            (default,),
        )
        row = await cur.fetchone()
        max_seq = row["max_seq"] if row else default
    return f"INC-{int(max_seq) + 1}"


async def _fetch_incident(conn, incident_id: str) -> Optional[dict]:
    async with conn.cursor() as cur:
        await cur.execute(_INCIDENT_SELECT + " WHERE i.id = %s", (incident_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_incident(row)


def _incident_from_event(event: dict, incident_id: str) -> dict:
    """Map a CCTV tag straight into the existing incidents table shape.

    The incidents table stays the single source of truth for operational
    incidents (no duplicate incident table). Fields mirror the report→incident
    mapping in routers/incidents.py, with CCTV-specific values:
      * source   = cctv
      * status   = in_progress (auto-started)
      * priority = Emergency (auto-set, the CCTV operator cannot override)
    """
    description = f"[CCTV: {event['camera_name']} ({event['camera_location']})] {event['category']}"
    if event.get("notes"):
        description += f" — {event['notes']}"

    return {
        "id": incident_id,
        "report_id": None,
        "category": event["category"],
        "severity": "critical",
        "purok": event.get("camera_purok") or "",
        "description": description,
        "source": "cctv",
        "reporter": event.get("operator") or "CO-01",
        "time": event["timestamp"],
        "status": CCTV_INCIDENT_STATUS,
        "photos": 0,
        "lat": None,
        "lng": None,
        "priority": CCTV_INCIDENT_PRIORITY,
        "notes": [f"[CCTV Tagged Event] {event['category']}"],
        "rating": None,
        "anonymous": False,
        "tracking_token": None,
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
        "verification_status": CCTV_INCIDENT_VERIFICATION,
        "verified_by": event.get("operator") or "CO-01",
        "verified_at": event["timestamp"],
        "unverified_reason": None,
        "category_history": [],
        "assigned_team": None,
        "dispatch_id": None,
        "closure_history": [],
        "resolved_at": None,
        "iot_data": {
            "sensorType": "cctv",
            "cameraId": event.get("camera_id") or "",
            "cameraName": event.get("camera_name") or "",
            "category": event["category"],
            "taggedAt": event["timestamp"],
        },
        "feedback": None,
        "created_at": datetime.now().isoformat(),
    }


async def _insert_incident(conn, incident: dict) -> None:
    async with conn.cursor() as cur:
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
                incident["id"], incident["report_id"], incident["category"],
                incident["severity"], incident["purok"], incident["description"],
                incident["source"], incident["reporter"], incident["time"],
                incident["status"], incident["photos"], incident["lat"], incident["lng"],
                incident["priority"], Jsonb(incident["notes"]), incident["rating"],
                incident["anonymous"], incident["tracking_token"], incident["acknowledged_at"],
                incident["sla_breached"], incident["duplicate_resolved"],
                Jsonb(incident["related_to"]),
                incident["escalated_to_captain"], incident["escalated_reason"],
                incident["escalated_at"], incident["reporter_safe"], incident["reporter_safe_at"],
                incident["validation_label"], incident["related_alert_id"],
                incident["closed_reason"], incident["verification_status"], incident["verified_by"],
                incident["verified_at"], incident["unverified_reason"],
                Jsonb(incident["category_history"]),
                incident["assigned_team"], incident["dispatch_id"],
                Jsonb(incident["closure_history"]),
                incident["resolved_at"],
                Jsonb(incident["iot_data"]) if incident["iot_data"] is not None else None,
                incident["feedback"],
                incident["created_at"],
            ),
        )


@router.get("", response_model=List[CctvEventOut])
@router.get("/", response_model=List[CctvEventOut], include_in_schema=False)
async def list_cctv_events():
    """Return all persisted CCTV tag events, newest first."""
    async with get_db() as conn:
        await _ensure_table(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM cctv_events ORDER BY timestamp DESC, id DESC"
            )
            rows = await cur.fetchall()
    return [_row_to_event(r) for r in rows]


@router.post("", response_model=CctvEventOut)
@router.post("/", response_model=CctvEventOut, include_in_schema=False)
async def create_cctv_event(event: CctvEventCreate):
    """Create a persisted CCTV tag event from the Surveillance Matrix.

    * ``create_new``   — a new operational incident is created on the existing
      ``incidents`` table (source=cctv, status=in_progress, priority=Emergency)
      and the event logs its id.
    * ``link_existing`` — the event references an already-open incident id.
    """
    if event.incident_action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"incident_action must be one of {sorted(VALID_ACTIONS)}",
        )

    incident_id = event.incident_id
    incident_status = "Linked to existing"
    created_incident = None

    async with get_db() as conn:
        await _ensure_table(conn)
        if event.incident_action == "create_new":
            incident_id = await _next_incident_id(conn)
            incident_status = "In Progress"
            incident = _incident_from_event(event.dict(), incident_id)
            try:
                await _insert_incident(conn, incident)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=400, detail="Failed to create incident")
            created_incident = await _fetch_incident(conn, incident_id)
            if created_incident is None:
                raise HTTPException(status_code=404, detail="Failed to create incident")
        else:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM incidents WHERE id = %s", (incident_id,)
                )
                if await cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Linked incident {incident_id} does not exist",
                    )

        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO cctv_events (
                    id, camera_id, camera_name, camera_location, camera_purok,
                    category, notes, timestamp, operator, incident_action,
                    incident_id, incident_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    camera_id = EXCLUDED.camera_id,
                    camera_name = EXCLUDED.camera_name,
                    camera_location = EXCLUDED.camera_location,
                    camera_purok = EXCLUDED.camera_purok,
                    category = EXCLUDED.category,
                    notes = EXCLUDED.notes,
                    timestamp = EXCLUDED.timestamp,
                    operator = EXCLUDED.operator,
                    incident_action = EXCLUDED.incident_action,
                    incident_id = EXCLUDED.incident_id,
                    incident_status = EXCLUDED.incident_status,
                    updated_at = now()
                """,
                (
                    event.id, event.camera_id, event.camera_name, event.camera_location,
                    event.camera_purok, event.category, event.notes, event.timestamp,
                    event.operator, event.incident_action, incident_id, incident_status,
                ),
            )

            await cur.execute(
                "SELECT * FROM cctv_events WHERE id = %s", (event.id,)
            )
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create CCTV event")

    out = dict(_row_to_event(row))
    out["incident"] = created_incident
    return out