import asyncio
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import cv2
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from db import get_db

router = APIRouter(tags=["cctv"])

RTSP_URL = "rtsp://cpss.culiat:Cutenimaki09@192.168.1.40:554/stream1"

class CameraStream:
    def __init__(self):
        self.frame = None
        self.last_frame_at = None
        self.condition = threading.Condition()
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        while True:
            cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
            if cap.isOpened():
                while True:
                    success, frame = cap.read()
                    if not success:
                        break
                    
                    ret, buffer = cv2.imencode(".jpg", frame)
                    if ret:
                        with self.condition:
                            self.frame = buffer.tobytes()
                            self.last_frame_at = time.monotonic()
                            self.condition.notify_all()
            
            cap.release()
            time.sleep(5)  # Wait before reconnecting

    def get_frame(self):
        with self.condition:
            self.condition.wait()
            return self.frame

    def seconds_since_frame(self):
        """None if no frame yet, else seconds since the latest live frame."""
        with self.condition:
            if self.last_frame_at is None:
                return None
            return time.monotonic() - self.last_frame_at

camera_stream = CameraStream()

# ── Automatic reconnect / liveness monitor ──────────────────────────────────
# The MJPEG feed holds the only RTSP session most cheap cameras allow, so a
# second connection probe can fail even while the camera is streaming. Instead
# of relying on manual probes, this background thread watches the feed's frame
# liveness and reconciles the database status of the matching camera rows:
#   * frames fresh (< RECONNECT_WINDOW) and camera not online  → mark online
#   * frames stale (> OFFLINE_AFTER) and camera was online      → mark offline
# It only touches cameras that connected successfully before (last_successful
# set), so brand-new pending registrations still require the explicit
# verification test and are never auto-admitted.
MONITOR_POLL_INTERVAL = 10
RECONNECT_WINDOW_SECONDS = 15
OFFLINE_AFTER_SECONDS = 45


class CctvStatusMonitor:
    def __init__(self):
        self.thread = threading.Thread(
            target=self._loop, name="cctv-status-monitor", daemon=True
        )
        self.thread.start()

    def _loop(self):
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        while True:
            try:
                loop.run_until_complete(self._tick())
            except Exception:
                pass
            time.sleep(MONITOR_POLL_INTERVAL)

    @staticmethod
    async def _tick() -> None:
        parsed = urlparse(RTSP_URL)
        host = parsed.hostname or ""
        port = str(parsed.port or 554)
        path = parsed.path or "/stream1"
        since = camera_stream.seconds_since_frame()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        async with get_db() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, status FROM cctv_cameras
                    WHERE enabled = true
                      AND status <> 'disabled'
                      AND ip = %s AND port = %s AND stream_path = %s
                      AND last_successful IS NOT NULL
                      AND last_successful <> ''
                      AND last_successful <> '—'
                    """,
                    (host, port, path),
                )
                rows = await cur.fetchall()
                for r in rows:
                    if since is not None and since < RECONNECT_WINDOW_SECONDS:
                        if r["status"] != "online":
                            await cur.execute(
                                """
                                UPDATE cctv_cameras
                                SET status = 'online', last_tested = %s,
                                    last_heartbeat = %s, last_successful = %s,
                                    latency = %s, maintenance_status = 'OK',
                                    updated_at = now()
                                WHERE id = %s
                                """,
                                (
                                    stamp,
                                    stamp,
                                    stamp,
                                    f"{int(since * 1000)}ms",
                                    r["id"],
                                ),
                            )
                        else:
                            await cur.execute(
                                """
                                UPDATE cctv_cameras
                                SET last_heartbeat = %s, updated_at = now()
                                WHERE id = %s
                                """,
                                (stamp, r["id"]),
                            )
                    elif (
                        since is not None
                        and since > OFFLINE_AFTER_SECONDS
                        and r["status"] == "online"
                    ):
                        await cur.execute(
                            """
                            UPDATE cctv_cameras
                            SET status = 'offline', latency = '—',
                                last_failed = %s, updated_at = now()
                            WHERE id = %s
                            """,
                            (stamp, r["id"]),
                        )


cctv_status_monitor = CctvStatusMonitor()


def generate_frames():
    """Yield latest frames as MJPEG by waiting for the background thread."""
    while True:
        frame_bytes = camera_stream.get_frame()
        if frame_bytes:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )


@router.get("/video_feed")
def video_feed():
    """MJPEG video stream endpoint — visit /video_feed in a browser."""
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/api/cctv/live")
def live_feed_info():
    """Identify the camera that the backend ``/video_feed`` endpoint serves.

    Lets read-only clients (e.g. the CCTV operator Surveillance Matrix) match
    the single live MJPEG stream to its registered camera without hardcoding
    the camera id or address in the frontend.
    """
    parsed = urlparse(RTSP_URL)
    return {
        "rtsp_url": RTSP_URL,
        "ip": parsed.hostname or "",
        "port": str(parsed.port or 554),
        "stream_path": parsed.path or "",
    }