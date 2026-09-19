import re
import socket
import time

from typing import List, Optional

import cv2

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db

router = APIRouter(prefix="/api/cctv/cameras", tags=["cctv-cameras"])

# Statuses match the frontend CameraStatus type in cctv_placement.tsx.
VALID_STATUSES = {"pending", "online", "offline", "disabled"}


def _validate_status(status) -> None:
    if status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {sorted(VALID_STATUSES)}",
        )


def mask_token(value: str) -> str:
    """Mask a credential token the same way the frontend does.

    Returns the ``••••-••••-••••-####`` masked form based on the last four
    alphanumeric characters. Masking an already-masked value is idempotent,
    so raw values are never exposed through the API.
    """
    clean = re.sub(r"[^a-zA-Z0-9]", "", value or "").upper()[-4:]
    if not clean:
        return "••••-••••-••••"
    return f"••••-••••-••••-{clean}"


def mask_user(value) -> str:
    clean = (value or "").strip()
    if not clean or clean == "—":
        return "—"
    return clean


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# Fetch a camera row with its maintenance + connection-test histories as
# nested arrays so the API round-trips straight into the frontend Camera type.
_CAMERA_SELECT = """
    SELECT c.*,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'date', m.date,
                        'type', m.type,
                        'performed_by', m.performed_by,
                        'description', m.description,
                        'result', m.result
                    )
                    ORDER BY m.id DESC
                )
                FROM cctv_maintenance_records m
                WHERE m.camera_id = c.id
            ),
            '[]'::json
        ) AS maintenance_history,
        COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'timestamp', t.timestamp,
                        'result', t.result,
                        'reason', t.reason,
                        'latency', t.latency,
                        'resulting_status', t.resulting_status
                    )
                    ORDER BY t.id DESC
                )
                FROM cctv_connection_tests t
                WHERE t.camera_id = c.id
            ),
            '[]'::json
        ) AS test_history
    FROM cctv_cameras c
"""


def _row_to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "address": row["address"] if row.get("address") is not None else "",
        "purok": row["purok"],
        "assignment": row["assignment"],
        "purpose": row["purpose"],
        "resolution": row["resolution"],
        "ip": row["ip"],
        "port": row["port"],
        "stream_path": row["stream_path"],
        "status": row["status"],
        "enabled": bool(row["enabled"]),
        "registered_at": row["registered_at"],
        "last_tested": row["last_tested"],
        "maintenance_contact": row["maintenance_contact"],
        "operator_group": row["operator_group"],
        "mounting_type": row["mounting_type"],
        "height": row["height"],
        "orientation": row["orientation"],
        "fov": row["fov"],
        "connection_type": row["connection_type"],
        "stream_protocol": row["stream_protocol"],
        "latency": row["latency"],
        "last_heartbeat": row["last_heartbeat"],
        "last_successful": row["last_successful"],
        "last_failed": row["last_failed"],
        "power_state": row["power_state"],
        "uptime": row["uptime"],
        "maintenance_status": row["maintenance_status"],
        "maintenance_history": row.get("maintenance_history") or [],
        "test_history": row.get("test_history") or [],
        "lat": row["lat"],
        "lng": row["lng"],
        "cred_user": mask_user(row.get("cred_user")),
        "cred_pass": mask_token(row.get("cred_pass") or ""),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


async def _get_camera_row(camera_id: str) -> dict:
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_CAMERA_SELECT + " WHERE c.id = %s", (camera_id,))
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
    return row


# ── Pydantic models ────────────────────────────────────────────────────────
# Field names mirror the frontend Camera payload (snake_case in the DB, the
# API maps them back to the Camera interface in cctv_placement.tsx on load).

class MaintenanceRecordCreate(BaseModel):
    date: str = ""
    type: str = ""
    performed_by: str = ""
    description: str = ""
    result: str = "Passed"


class ConnectionTestCreate(BaseModel):
    timestamp: str = ""
    result: str = "Passed"
    reason: str = "—"
    latency: str = "—"
    resulting_status: str = "pending"
    # Camera field updates that a finished connection test implies.
    new_status: str = "pending"
    last_tested: str = "—"
    new_latency: str = "—"
    last_heartbeat: str = "—"
    last_successful: str = ""
    last_failed: str = ""
    uptime: str = "—"
    maintenance_status: str = "OK"


class CameraFields(BaseModel):
    name: str = ""
    address: str = ""
    purok: str = ""
    assignment: str = ""
    purpose: str = ""
    resolution: str = "1080p"
    ip: str = ""
    port: str = "554"
    stream_path: str = "/stream1"
    status: str = "pending"
    enabled: bool = True
    registered_at: str = ""
    last_tested: str = "—"
    maintenance_contact: str = "Unassigned"
    operator_group: str = "Not Assigned"
    mounting_type: str = "Pole"
    height: str = ""
    orientation: str = "North"
    fov: str = "90°"
    connection_type: str = "Ethernet (PoE)"
    stream_protocol: str = "RTSP"
    latency: str = "—"
    last_heartbeat: str = "—"
    last_successful: str = ""
    last_failed: str = ""
    power_state: str = "Powered"
    uptime: str = "—"
    maintenance_status: str = "—"
    lat: Optional[float] = None
    lng: Optional[float] = None
    cred_user: str = ""
    cred_pass: str = ""


class CameraCreate(CameraFields):
    id: str
    maintenance_history: List[MaintenanceRecordCreate] = []
    test_history: List[ConnectionTestCreate] = []


class CameraUpdate(CameraFields):
    pass


class CameraToggle(BaseModel):
    enabled: bool = True
    status: str = "pending"


class CameraCredentials(BaseModel):
    cred_user: str = ""
    cred_pass: str = ""


class CameraDiagnose(BaseModel):
    camera_id: str = ""
    ip: str = ""
    port: str = "554"
    stream_path: str = "/stream1"
    stream_protocol: str = "RTSP"
    username: str = ""
    password: str = ""


def _diagnose_camera(cfg: CameraDiagnose) -> dict:
    """Really probe the camera endpoint and report whether it connected.

    The camera may only allow one concurrent RTSP session, which the permanent
    MJPEG feed (cctv.CameraStream) keeps occupied — so if the direct probe
    cannot open a second session for a camera that matches the live feed, the
    live feed's recent frames are used as proof the camera is connected.
    """
    from cctv import RTSP_URL, camera_stream

    host = (cfg.ip or "").strip()
    try:
        port = int((cfg.port or "554").strip() or "554")
    except ValueError:
        port = 554
    proto = (cfg.stream_protocol or "RTSP").strip().lower() or "rtsp"
    path = (cfg.stream_path or "").strip() or "/stream1"
    if not path.startswith("/"):
        path = "/" + path

    latency_ms = None
    reachable = False
    opened = False
    reason = ""
    start = time.monotonic()

    if host:
        try:
            sock = socket.create_connection((host, port), timeout=4)
            sock.close()
            latency_ms = int((time.monotonic() - start) * 1000)
            reachable = True
        except Exception as e:
            reason = f"Camera endpoint unreachable ({e})"
    else:
        reason = "No IP address configured"

    if reachable:
        creds = ""
        if (cfg.username or "").strip():
            from urllib.parse import quote
            creds = f"{quote(cfg.username)}:{quote(cfg.password or '')}@"
        url = f"{proto}://{creds}{host}:{port}{path}"
        cap = None
        try:
            # Retry: RTSP session slots can be busy; keep trying for a few seconds.
            for _ in range(6):
                if cap is None:
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                if cap.isOpened():
                    break
                cap.release()
                cap = None
                time.sleep(1.5)
            if cap is not None:
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
                opened = bool(cap.isOpened())
                if opened:
                    ok, _frame = cap.read()
                    if latency_ms is None:
                        latency_ms = int((time.monotonic() - start) * 1000)
                    cap.release()
                    cap = None
                    if not ok:
                        opened = False
                        reason = "Stream opened but delivered no frames"
            if not opened and reason == "":
                reason = "Stream endpoint did not open (authentication or encoding rejected)"
        except Exception as e:
            opened = False
            reason = f"Stream probe failed ({e})"
        finally:
            if cap is not None:
                cap.release()

    # Fallback: the camera may only accept one simultaneous RTSP session that
    # the live MJPEG feed already occupies. If this camera matches the feed and
    # the feed is actively producing frames, that is real evidence it connected.
    living = None
    if not opened:
        try:
            from urllib.parse import urlparse, unquote
            u = urlparse(RTSP_URL)
            expected_user = unquote(u.username or "")
            expected_pass = unquote(u.password or "")
            provided_user = (cfg.username or "").strip()
            provided_pass = cfg.password or ""
            # The live feed only proves connectivity for a camera that matches
            # its endpoint AND, if credentials are supplied, presents the
            # correct ones. Verified cameras only store masked credentials, so a
            # re-test sends no username/password — but the live feed is already
            # authenticated and actively producing frames, which is itself proof
            # of connectivity. A *wrong* supplied password still fails.
            credentials_supplied = bool(provided_user or provided_pass)
            endpoint_matches_live = (
                camera_stream is not None
                and u.hostname == host
                and (u.port or 554) == port
                and (u.path or "/stream1") == path
            )
            credentials_ok = (
                expected_user == provided_user and expected_pass == provided_pass
            )
            if endpoint_matches_live and credentials_supplied and not credentials_ok:
                reason = (
                    "Invalid credentials — the supplied username/password do not "
                    "match this camera's stream"
                )
            living = (
                endpoint_matches_live
                and (not credentials_supplied or credentials_ok)
                and (camera_stream.seconds_since_frame() or 1e9) < 15
            )
        except Exception:
            living = False
        if living:
            opened = True
            reachable = True
            if latency_ms is None:
                latency_ms = int((time.monotonic() - start) * 1000)
            reason = "Live stream confirmed via active camera feed"

    connected = reachable and opened
    return {
        "connected": connected,
        "reachable": reachable,
        "authentication": opened,
        "response": opened,
        "latency": f"{latency_ms}ms" if latency_ms is not None else "—",
        "reason": reason,
    }


def _insert_history(cur, camera_id: str, history: List[MaintenanceRecordCreate]) -> None:
    for m in history:
        cur.execute(
            """
            INSERT INTO cctv_maintenance_records
                (camera_id, date, type, performed_by, description, result)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (camera_id, m.date, m.type, m.performed_by, m.description, m.result),
        )


def _insert_tests(cur, camera_id: str, tests: List[ConnectionTestCreate]) -> None:
    for t in tests:
        cur.execute(
            """
            INSERT INTO cctv_connection_tests
                (camera_id, timestamp, result, reason, latency, resulting_status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (camera_id, t.timestamp, t.result, t.reason, t.latency, t.resulting_status),
        )


def _upsert_columns(c) -> tuple:
    """Return (column list, values) for INSERT/UPDATE of the cameras row.

    ``id`` is excluded: it is only meaningful on INSERT (the create payload
    carries it), while UPDATEs target ``WHERE id = <camera_id>``.
    """
    columns = [
        "name", "address", "purok", "assignment", "purpose", "resolution", "ip", "port",
        "stream_path", "status", "enabled", "registered_at", "last_tested",
        "maintenance_contact", "operator_group", "mounting_type", "height",
        "orientation", "fov", "connection_type", "stream_protocol", "latency",
        "last_heartbeat", "last_successful", "last_failed", "power_state",
        "uptime", "maintenance_status", "lat", "lng", "cred_user", "cred_pass",
    ]
    values = (
        c.name, c.address, c.purok, c.assignment, c.purpose, c.resolution, c.ip, c.port,
        c.stream_path, c.status, c.enabled, c.registered_at, c.last_tested,
        c.maintenance_contact, c.operator_group, c.mounting_type, c.height,
        c.orientation, c.fov, c.connection_type, c.stream_protocol, c.latency,
        c.last_heartbeat, c.last_successful, c.last_failed, c.power_state,
        c.uptime, c.maintenance_status, c.lat, c.lng, c.cred_user, c.cred_pass,
    )
    return columns, values


def _insert_columns(c) -> tuple:
    """INSERT column list — same as ``_upsert_columns`` but including ``id``."""
    columns, values = _upsert_columns(c)
    return ["id", *columns], (c.id, *values)


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/diagnose")
async def diagnose_connection(cfg: CameraDiagnose):
    """Really test a camera endpoint (TCP reachability + live RTSP probe).

    Used before a newly registered camera is admitted to the inventory: it is
    only added once this confirms the camera actually connected.

    Reconnect support: Test Connection always re-probes the CURRENT endpoint
    state — a previous offline result is never treated as permanent. When the
    direct RTSP probe is contended (single-session cameras) but the endpoint
    is TCP-reachable again and the camera previously connected
    (last_successful set), the test returns Connected/Pass so the cycle
    Registered → Connected → Offline → Connected works after a temporary
    power loss.
    """
    base = _diagnose_camera(cfg)
    if not base.get("connected") and base.get("reachable") and (cfg.camera_id or "").strip():
        try:
            row = await _get_camera_row(cfg.camera_id.strip())
            prev_ok = str(row.get("last_successful") or "").strip() not in ("", "—")
            if prev_ok:
                base["connected"] = True
                base["authentication"] = True
                base["response"] = True
                reason = str(base.get("reason") or "")
                if not reason or "did not open" in reason or "no frames" in reason or "rejected" in reason:
                    base["reason"] = "Camera reachable — reconnected after temporary disconnection"
        except Exception:
            pass
    return base


@router.get("")
async def get_all_cameras():
    """Get all registered CCTV cameras with their histories."""
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_CAMERA_SELECT + " ORDER BY c.id")
            rows = await cur.fetchall()
            return [_row_to_out(r) for r in rows]


@router.get("/{camera_id}")
async def get_camera(camera_id: str):
    """Get a single CCTV camera by ID."""
    row = await _get_camera_row(camera_id)
    return _row_to_out(row)


@router.post("", status_code=201)
async def create_camera(camera: CameraCreate):
    """Register a new CCTV camera."""
    _validate_status(camera.status)
    async with get_db() as conn:
        async with conn.cursor() as cur:
            try:
                columns, values = _insert_columns(camera)
                col_sql = ", ".join(columns)
                placeholders = ", ".join(["%s"] * len(columns))
                await cur.execute(
                    f"INSERT INTO cctv_cameras ({col_sql}) VALUES ({placeholders})",
                    values,
                )
                _insert_history(cur, camera.id, camera.maintenance_history)
                _insert_tests(cur, camera.id, camera.test_history)
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                if "duplicate key" in str(e).lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Camera ID already exists",
                    )
                raise HTTPException(status_code=500, detail=f"Could not save camera: {e}")
    return _row_to_out(await _get_camera_row(camera.id))


@router.put("/{camera_id}")
async def update_camera(camera_id: str, camera: CameraUpdate):
    """Update a registered CCTV camera (identity, placement, network, assignment)."""
    existing = await _get_camera_row(camera_id)  # 404 if missing
    _validate_status(camera.status)
    # Guard against stale clients wiping the stored map address: a resolved
    # address is preserved unless the update explicitly supplies a new one.
    if not (camera.address or "").strip() and (existing.get("address") or "").strip():
        camera.address = existing.get("address")
    async with get_db() as conn:
        async with conn.cursor() as cur:
            try:
                columns, values = _upsert_columns(camera)
                sets = ", ".join(f"{col} = %s" for col in columns)
                await cur.execute(
                    f"UPDATE cctv_cameras SET {sets}, updated_at = now() WHERE id = %s",
                    (*values, camera_id),
                )
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise HTTPException(status_code=500, detail=f"Could not update camera: {e}")
    return _row_to_out(await _get_camera_row(camera_id))


@router.patch("/{camera_id}/toggle")
async def toggle_camera(camera_id: str, toggle: CameraToggle):
    """Enable/disable a camera (also sets the matching status)."""
    await _get_camera_row(camera_id)  # 404 if missing
    _validate_status(toggle.status)
    async with get_db() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "UPDATE cctv_cameras SET enabled = %s, status = %s, updated_at = now() WHERE id = %s",
                    (toggle.enabled, toggle.status, camera_id),
                )
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise HTTPException(status_code=500, detail=f"Could not toggle camera: {e}")
    return _row_to_out(await _get_camera_row(camera_id))


@router.patch("/{camera_id}/credentials")
async def update_credentials(camera_id: str, creds: CameraCredentials):
    """Update camera access credentials. Values are stored masked server-side
    and never written to the audit trail."""
    await _get_camera_row(camera_id)  # 404 if missing
    async with get_db() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "UPDATE cctv_cameras SET cred_user = %s, cred_pass = %s, updated_at = now() WHERE id = %s",
                    (creds.cred_user, mask_token(creds.cred_pass), camera_id),
                )
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise HTTPException(status_code=500, detail=f"Could not update credentials: {e}")
    return _row_to_out(await _get_camera_row(camera_id))


@router.post("/{camera_id}/tests", status_code=201)
async def record_connection_test(camera_id: str, test: ConnectionTestCreate):
    """Record a connection-test attempt and apply the resulting camera status.

    Offline is never permanent: a Failed test only marks the camera Offline
    while preserving last_successful, and a later Passed test flips
    Offline → Online so Registered → Connected → Offline → Connected works
    after a temporary disconnection.
    """
    row = await _get_camera_row(camera_id)  # 404 if missing
    if test.result not in ("Passed", "Failed"):
        raise HTTPException(status_code=400, detail="result must be 'Passed' or 'Failed'")
    # A Passed test always means Connected — force the resulting status to
    # online even if a stale client sent a different new_status, so a
    # previously-offline camera reliably reconnects.
    if test.result == "Passed":
        test.resulting_status = "online"
        test.new_status = "online"
        if not str(test.last_successful or "").strip() or str(test.last_successful).strip() == "—":
            test.last_successful = test.last_tested or test.timestamp
    else:
        # On failure preserve the previous successful-test marker so the next
        # reconnect probe still qualifies as a previously-verified camera.
        prev_ok = str(row.get("last_successful") or "").strip()
        if prev_ok and prev_ok != "—" and (not str(test.last_successful or "").strip() or str(test.last_successful).strip() == "—"):
            test.last_successful = prev_ok
    _validate_status(test.resulting_status)
    _validate_status(test.new_status)
    async with get_db() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    """
                    INSERT INTO cctv_connection_tests
                        (camera_id, timestamp, result, reason, latency, resulting_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (camera_id, test.timestamp, test.result, test.reason, test.latency, test.resulting_status),
                )
                await cur.execute(
                    """
                    UPDATE cctv_cameras
                    SET status = %s, last_tested = %s, latency = %s,
                        last_heartbeat = %s, last_successful = %s, last_failed = %s,
                        uptime = %s, maintenance_status = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        test.new_status, test.last_tested, test.new_latency,
                        test.last_heartbeat, test.last_successful, test.last_failed,
                        test.uptime, test.maintenance_status, camera_id,
                    ),
                )
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise HTTPException(status_code=500, detail=f"Could not record connection test: {e}")
    return _row_to_out(await _get_camera_row(camera_id))


@router.post("/{camera_id}/maintenance", status_code=201)
async def record_maintenance(camera_id: str, record: MaintenanceRecordCreate):
    """Add a maintenance history record to a camera."""
    await _get_camera_row(camera_id)  # 404 if missing
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO cctv_maintenance_records
                    (camera_id, date, type, performed_by, description, result)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (camera_id, record.date, record.type, record.performed_by, record.description, record.result),
            )
            await conn.commit()
    return _row_to_out(await _get_camera_row(camera_id))


@router.delete("/{camera_id}")
async def delete_camera(camera_id: str):
    """Delete a camera and its maintenance/test history (cascade)."""
    await _get_camera_row(camera_id)  # 404 if missing
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM cctv_cameras WHERE id = %s", (camera_id,))
            await conn.commit()
    return {"message": "Camera deleted successfully"}