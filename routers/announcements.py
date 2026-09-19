import random
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db

router = APIRouter(prefix="/api/announcements", tags=["announcements"])

VALID_STATUSES = {"draft", "published", "archived"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}


class AnnouncementCreate(BaseModel):
    title: str
    message: str
    category: str = "General"
    severity: str = "low"
    targetPurok: str = "All Puroks"
    createdBy: str = ""
    publish: bool = False


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    category: Optional[str] = None
    severity: Optional[str] = None
    targetPurok: Optional[str] = None


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_announcement(row: dict) -> dict:
    return {
        "id": row["id"],
        "announcementId": row["tracking_id"],
        "title": row["title"],
        "message": row["message"],
        "category": row["category"],
        "severity": row["severity"],
        "targetPurok": row["target_purok"],
        "status": row["status"],
        "createdBy": row["created_by"],
        "publishedBy": row["published_by"],
        "publishedAt": _iso(row["published_at"]),
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


async def _fetch_announcement(conn, announcement_id: int) -> Optional[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT * FROM announcements WHERE id = %s", (announcement_id,)
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_announcement(row)


def _generate_tracking_id() -> str:
    # ANNC-XXXXX (5 random digits) e.g. ANNC-04217
    digits = "".join(str(random.randint(0, 9)) for _ in range(5))
    return f"ANNC-{digits}"


@router.get("")
async def list_announcements(status: Optional[str] = None):
    """Return all announcements (admin view), newest first."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            if status and status in VALID_STATUSES:
                await cur.execute(
                    "SELECT * FROM announcements WHERE status = %s ORDER BY created_at DESC, id DESC",
                    (status,),
                )
            else:
                await cur.execute(
                    "SELECT * FROM announcements ORDER BY created_at DESC, id DESC"
                )
            rows = await cur.fetchall()
    return [_row_to_announcement(r) for r in rows]


@router.get("/published")
async def list_published_announcements(target_purok: Optional[str] = None):
    """Return published announcements sorted newest first.

    Used by the notification display flow. When target_purok is provided
    (e.g. "Purok 3"), only community-wide announcements or ones targeting
    that purok are returned.
    """
    purok = (target_purok or "").strip()
    async with get_db() as conn:
        async with conn.cursor() as cur:
            if purok and purok.lower() not in ("", "all puroks", "all"):
                await cur.execute(
                    """
                    SELECT * FROM announcements
                    WHERE status = 'published'
                      AND (target_purok = 'All Puroks' OR target_purok = %s)
                    ORDER BY published_at DESC, id DESC
                    """,
                    (purok,),
                )
            else:
                await cur.execute(
                    """
                    SELECT * FROM announcements
                    WHERE status = 'published'
                    ORDER BY published_at DESC, id DESC
                    """
                )
            rows = await cur.fetchall()
    return [_row_to_announcement(r) for r in rows]


@router.post("", status_code=201)
async def create_announcement(announcement: AnnouncementCreate):
    """Create an announcement. Draft by default; set publish=true to publish immediately."""
    title = announcement.title.strip()
    message = announcement.message.strip()
    if not title or not message:
        raise HTTPException(status_code=400, detail="Title and message are required")
    if announcement.severity not in VALID_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid severity. Choose one of: {', '.join(sorted(VALID_SEVERITIES))}",
        )

    tracking_id = _generate_tracking_id()
    publish = bool(announcement.publish)
    now_str = datetime.now(timezone.utc).isoformat()

    async with get_db() as conn:
        # Keep the tracking ID collision-free.
        async with conn.cursor() as cur:
            while True:
                await cur.execute(
                    "SELECT 1 FROM announcements WHERE tracking_id = %s",
                    (tracking_id,),
                )
                if not await cur.fetchone():
                    break
                tracking_id = _generate_tracking_id()

            await cur.execute(
                """
                INSERT INTO announcements (
                    tracking_id, title, message, category, severity, target_purok,
                    status, created_by, published_by, published_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    tracking_id,
                    title,
                    message,
                    (announcement.category or "General").strip(),
                    announcement.severity,
                    (announcement.targetPurok or "All Puroks").strip(),
                    "published" if publish else "draft",
                    announcement.createdBy.strip(),
                    announcement.createdBy.strip() if publish else "",
                    now_str if publish else None,
                ),
            )
            row_id = (await cur.fetchone())["id"]

        row = await _fetch_announcement(conn, row_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to create announcement")
    return row


@router.put("/{announcement_id}")
async def update_announcement(announcement_id: int, announcement: AnnouncementUpdate):
    """Update an announcement (drafts only). Publishing/archiving have dedicated endpoints."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM announcements WHERE id = %s", (announcement_id,))
            existing = await cur.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Announcement not found")
            if existing["status"] != "draft":
                raise HTTPException(status_code=400, detail="Only draft announcements can be edited")

            fields = []
            params = []
            updateable = {
                "title": announcement.title,
                "message": announcement.message,
                "category": announcement.category,
                "severity": announcement.severity,
                "target_purok": announcement.targetPurok,
            }
            for field, value in updateable.items():
                if value is not None:
                    if field == "severity" and value not in VALID_SEVERITIES:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid severity. Choose one of: {', '.join(sorted(VALID_SEVERITIES))}",
                        )
                    if field in ("title", "message") and not str(value).strip():
                        raise HTTPException(
                            status_code=400, detail="Title and message cannot be empty"
                        )
                    fields.append(f"{field} = %s")
                    params.append(str(value).strip())

            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")

            params.append(announcement_id)
            fields.append("updated_at = now()")
            await cur.execute(
                f"UPDATE announcements SET {', '.join(fields)} WHERE id = %s",
                params,
            )

        row = await _fetch_announcement(conn, announcement_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to update announcement")
    return row


@router.post("/{announcement_id}/publish")
async def publish_announcement(announcement_id: int, created_by: Optional[str] = ""):
    """Publish an announcement so users receive it as a notification."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM announcements WHERE id = %s", (announcement_id,))
            existing = await cur.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Announcement not found")
            if existing["status"] == "published":
                return _row_to_announcement(existing)

            await cur.execute(
                """
                UPDATE announcements
                SET status = 'published', published_by = %s, published_at = %s, updated_at = now()
                WHERE id = %s
                """,
                ((created_by or "").strip(), now_str, announcement_id),
            )

        row = await _fetch_announcement(conn, announcement_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Failed to publish announcement")
    return row


@router.delete("/{announcement_id}")
async def delete_announcement(announcement_id: int):
    """Delete an announcement (drafts, or archived records)."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM announcements WHERE id = %s", (announcement_id,))
            existing = await cur.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Announcement not found")
            if existing["status"] == "published":
                raise HTTPException(
                    status_code=400,
                    detail="Published announcements cannot be deleted. Archive them instead.",
                )
            await cur.execute("DELETE FROM announcements WHERE id = %s", (announcement_id,))
    return {"message": f"Announcement {announcement_id} deleted successfully"}