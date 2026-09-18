import cv2
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(tags=["cctv"])

RTSP_URL = "rtsp://cpss.culiat:Cutenimaki09@192.168.1.40:554/stream1"


import threading
import time

class CameraStream:
    def __init__(self):
        self.frame = None
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
                            self.condition.notify_all()
            
            cap.release()
            time.sleep(5)  # Wait before reconnecting

    def get_frame(self):
        with self.condition:
            self.condition.wait()
            return self.frame

camera_stream = CameraStream()

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