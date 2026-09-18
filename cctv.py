import cv2
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["cctv"])

RTSP_URL = "rtsp://cpss.culiat:Cutenimaki09@192.168.1.40:554/stream1"


def generate_frames():
    """Read frames from the RTSP camera and yield them as JPEG bytes."""
    # CAP_FFMPEG is required for reliable RTSP streaming under OpenCV
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    try:
        while cap.isOpened():
            success, frame = cap.read()
            if not success:
                break
            ret, buffer = cv2.imencode(".jpg", frame)
            if not ret:
                continue
            frame_bytes = buffer.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
    finally:
        cap.release()


@router.get("/video_feed")
def video_feed():
    """MJPEG video stream endpoint — visit /video_feed in a browser."""
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )