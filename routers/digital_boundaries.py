from typing import Optional, List
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from db import get_db

router = APIRouter(prefix="/api/digital-boundaries", tags=["digital-boundaries"])


class Node(BaseModel):
    x: float  # longitude
    y: float  # latitude


class DigitalBoundaryCreate(BaseModel):
    id: str
    name: str = ""  # Allow empty name initially
    badge: str  # "Primary" or "Sub-zone"
    classification: str  # "Standard", "Residential", "Market", "Evacuation / Emergency", "Hazard Zone"
    status: str = "Active"  # "Active" or "Inactive"
    parent_id: Optional[str] = None
    created: str
    edited: str
    nodes: List[Node] = []  # Allow empty nodes for new boundaries
    visible: bool = True


class DigitalBoundaryUpdate(BaseModel):
    name: str
    badge: str
    classification: str
    status: str
    parent_id: Optional[str] = None
    edited: str
    nodes: List[Node]
    visible: bool


class DigitalBoundaryOut(BaseModel):
    id: str
    name: str
    badge: str
    classification: str
    status: str
    parent_id: Optional[str] = None
    created: str
    edited: str
    nodes: List[Node]
    visible: bool
    created_at: str
    updated_at: str


def _iso(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _normalize_nodes(raw) -> list:
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for n in data:
        if isinstance(n, dict) and "x" in n and "y" in n:
            try:
                out.append({"x": float(n["x"]), "y": float(n["y"])})
            except (TypeError, ValueError):
                continue
        elif isinstance(n, (list, tuple)) and len(n) >= 2:
            try:
                out.append({"x": float(n[0]), "y": float(n[1])})
            except (TypeError, ValueError):
                continue
    return out


def _row_to_out(row: dict) -> DigitalBoundaryOut:
    return DigitalBoundaryOut(
        id=row["id"],
        name=row["name"],
        badge=row["badge"],
        classification=row["classification"],
        status=row["status"],
        parent_id=row.get("parent_id"),
        created=row["created"],
        edited=row["edited"],
        nodes=_normalize_nodes(row.get("nodes")),
        visible=bool(row["visible"]) if row.get("visible") is not None else True,
        created_at=_iso(row.get("created_at")),
        updated_at=_iso(row.get("updated_at")),
    )


def _nodes_jsonb(nodes: Optional[List[Node]]) -> Jsonb:
    """psycopg will not adapt a Python list to JSONB; wrap it explicitly."""
    payload = [{"x": n.x, "y": n.y} for n in (nodes or [])]
    return Jsonb(payload)


@router.get("", response_model=List[DigitalBoundaryOut])
async def get_all_boundaries():
    """Get all digital boundaries."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM digital_boundaries ORDER BY badge, name"
            )
            rows = await cur.fetchall()
            out = []
            for row in rows:
                try:
                    out.append(_row_to_out(row))
                except Exception as e:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Could not load boundary {row.get('id')}: {e}",
                    )
            return out


@router.get("/{boundary_id}", response_model=DigitalBoundaryOut)
async def get_boundary(boundary_id: str):
    """Get a specific digital boundary by ID."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM digital_boundaries WHERE id = %s",
                (boundary_id,)
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Boundary not found")
            return _row_to_out(row)


@router.post("", response_model=DigitalBoundaryOut, status_code=201)
async def create_boundary(boundary: DigitalBoundaryCreate):
    """Create a new digital boundary."""
    # Validate that only one Primary boundary exists
    if boundary.badge == "Primary":
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) as count FROM digital_boundaries WHERE badge = 'Primary'"
                )
                result = await cur.fetchone()
                if result and result["count"] > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Only one Primary boundary is allowed"
                    )

    # Validate that Sub-zone has a parent
    if boundary.badge == "Sub-zone" and not boundary.parent_id:
        raise HTTPException(
            status_code=400,
            detail="Sub-zone must have a valid parent boundary"
        )

    # Validate parent exists if provided
    if boundary.parent_id:
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM digital_boundaries WHERE id = %s",
                    (boundary.parent_id,)
                )
                if not await cur.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail="Parent boundary not found"
                    )

    async with get_db() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    """
                    INSERT INTO digital_boundaries 
                    (id, name, badge, classification, status, parent_id, created, edited, nodes, visible)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        boundary.id,
                        boundary.name or "Unnamed Boundary",  # Default name if empty
                        boundary.badge,
                        boundary.classification,
                        boundary.status,
                        boundary.parent_id,
                        boundary.created,
                        boundary.edited,
                        _nodes_jsonb(boundary.nodes),
                        boundary.visible,
                    )
                )
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                if "duplicate key" in str(e).lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Boundary ID already exists",
                    )
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not save boundary: {e}",
                )

    # Return the created boundary
    return await get_boundary(boundary.id)


@router.put("/{boundary_id}", response_model=DigitalBoundaryOut)
async def update_boundary(boundary_id: str, boundary: DigitalBoundaryUpdate):
    """Update an existing digital boundary."""
    # Check if boundary exists
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM digital_boundaries WHERE id = %s",
                (boundary_id,)
            )
            existing = await cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Boundary not found")

    # Validate Primary boundary uniqueness if changing to Primary
    if boundary.badge == "Primary" and existing["badge"] != "Primary":
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) as count FROM digital_boundaries WHERE badge = 'Primary' AND id != %s",
                    (boundary_id,)
                )
                result = await cur.fetchone()
                if result and result["count"] > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Only one Primary boundary is allowed"
                    )

    # Validate that Sub-zone has a parent
    if boundary.badge == "Sub-zone" and not boundary.parent_id:
        raise HTTPException(
            status_code=400,
            detail="Sub-zone must have a valid parent boundary"
        )

    # Validate parent exists if provided
    if boundary.parent_id:
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM digital_boundaries WHERE id = %s AND id != %s",
                    (boundary.parent_id, boundary_id)
                )
                if not await cur.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail="Parent boundary not found"
                    )

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE digital_boundaries
                SET name = %s, badge = %s, classification = %s, status = %s,
                    parent_id = %s, edited = %s, nodes = %s, visible = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    boundary.name,
                    boundary.badge,
                    boundary.classification,
                    boundary.status,
                    boundary.parent_id,
                    boundary.edited,
                    _nodes_jsonb(boundary.nodes),
                    boundary.visible,
                    boundary_id,
                )
            )
            await conn.commit()

    return await get_boundary(boundary_id)


@router.patch("/{boundary_id}/status")
async def update_boundary_status(boundary_id: str, status: str):
    """Update boundary status (Active/Inactive) for archiving/restoring."""
    if status not in ["Active", "Inactive"]:
        raise HTTPException(
            status_code=400,
            detail="Status must be 'Active' or 'Inactive'"
        )

    # Check if boundary exists and is not Primary
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM digital_boundaries WHERE id = %s",
                (boundary_id,)
            )
            existing = await cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Boundary not found")

            if existing["badge"] == "Primary":
                raise HTTPException(
                    status_code=400,
                    detail="Primary boundary cannot be archived"
                )

    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE digital_boundaries
                SET status = %s, visible = %s, updated_at = now()
                WHERE id = %s
                """,
                (status, status == "Active", boundary_id)
            )
            await conn.commit()

    return await get_boundary(boundary_id)


@router.delete("/{boundary_id}")
async def delete_boundary(boundary_id: str):
    """Delete a digital boundary."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM digital_boundaries WHERE id = %s",
                (boundary_id,)
            )
            existing = await cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Boundary not found")

    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Unlink any child boundaries that reference this boundary as parent
            await cur.execute(
                "UPDATE digital_boundaries SET parent_id = NULL WHERE parent_id = %s",
                (boundary_id,)
            )
            await cur.execute(
                "DELETE FROM digital_boundaries WHERE id = %s",
                (boundary_id,)
            )
            await conn.commit()

    return {"message": "Boundary deleted successfully"}
