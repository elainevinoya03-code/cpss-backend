from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from db import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Mirrors the status lifecycle used by ReportService / report.dart:
#   pending -> under_review -> assigned -> resolved (| closed)
VALID_STATUSES = {"pending", "under_review", "assigned", "resolved", "closed"}
VALID_PRIORITIES = {"Normal", "Low", "Medium", "High", "Critical"}

STATUS_LABELS = {
    "pending": "New",
    "under_review": "Under Review",
    "assigned": "Assigned",
    "resolved": "Resolved",
    "closed": "Closed",
}

_REPORT_SELECT = """
    SELECT r.*,
           COALESCE(
             (SELECT json_agg(p) FROM (
                SELECT id, file_name, storage_path, media_type, created_at
                  FROM report_photos WHERE report_id = r.id ORDER BY id
              ) p),
             '[]'::json
           ) AS photos,
           COALESCE(
             (SELECT json_agg(v) FROM (
                SELECT id, file_name, storage_path, media_type, created_at
                  FROM report_videos WHERE report_id = r.id ORDER BY id
              ) v),
             '[]'::json
           ) AS videos,
           COALESCE(
             (SELECT json_agg(s) FROM (
                SELECT id, title, date, description, created_at
                  FROM report_status_updates WHERE report_id = r.id ORDER BY id
              ) s),
             '[]'::json
           ) AS status_updates
      FROM reports r
"""


class ReportUpdate(BaseModel):
    status: Optional[str] = Field(default=None)
    priority: Optional[str] = Field(default=None)
    resolution_note: Optional[str] = Field(default=None)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_report(row: dict) -> dict:
    out = dict(row)
    for key in ("created_at", "updated_at"):
        out[key] = _iso(row.get(key))
    return out


async def _fetch_report(conn, report_id: int) -> Optional[dict]:
    async with conn.cursor() as cur:
        await cur.execute(_REPORT_SELECT + " WHERE r.id = %s", (report_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_report(row)


def _formatted_now() -> str:
    now = datetime.now()
    h = now.hour % 12 or 12
    ampm = "AM" if now.hour < 12 else "PM"
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[now.month]} {now.day} · {h}:{now.minute:02d} {ampm}"


@router.get("")
async def list_reports():
    """Return every resident-submitted report, newest first, with all fields
    captured by report.dart plus attached photos/videos and the status
    timeline. Serves the Desk Officer Incident Triage module."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_REPORT_SELECT + " ORDER BY r.created_at DESC, r.id DESC")
            rows = await cur.fetchall()
    return [_row_to_report(r) for r in rows]


@router.get("/{report_id}")
async def get_report(report_id: int):
    async with get_db() as conn:
        row = await _fetch_report(conn, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return row


@router.patch("/{report_id}")
async def update_report(report_id: int, req: ReportUpdate):
    """Advance a report through the Desk Officer processing lifecycle
    (status) and/or reassign its priority. Every status change is appended
    to the report_status_updates timeline."""
    if req.status is None and req.priority is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    fields, params = [], []
    status_change = None
    if req.status is not None:
        new_status = req.status.strip().lower()
        if new_status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Choose one of: {', '.join(sorted(VALID_STATUSES))}",
            )
        fields.append("status = %s")
        params.append(new_status)
        status_change = new_status

    if req.priority is not None:
        new_priority = req.priority.strip()
        if new_priority not in VALID_PRIORITIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid priority. Choose one of: {', '.join(sorted(VALID_PRIORITIES))}",
            )
        fields.append("priority = %s")
        params.append(new_priority)

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT status, tracking_id FROM reports WHERE id = %s", (report_id,))
            existing = await cur.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Report not found")

            params.append(report_id)
            fields.append("updated_at = now()")
            await cur.execute(
                f"UPDATE reports SET {', '.join(fields)} WHERE id = %s",
                params,
            )

            if status_change is not None and status_change != existing["status"]:
                label = STATUS_LABELS.get(status_change, status_change.replace("_", " ").title())
                description = (req.resolution_note or "").strip()
                if not description:
                    description = f"Report status changed to {label}."
                await cur.execute(
                    "INSERT INTO report_status_updates (report_id, title, date, description) "
                    "VALUES (%s, %s, %s, %s)",
                    (report_id, label, _formatted_now(), description),
                )

            row = await _fetch_report(conn, report_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return row