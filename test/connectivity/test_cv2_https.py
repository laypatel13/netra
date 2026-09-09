"""Quick smoke-test: can OpenCV open an authenticated RTSP stream from cctv.corp8.cloud?"""
import cv2
import os
from urllib.parse import quote as url_quote

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# Fill these in before running
EMAIL = os.getenv("CCTV_EMAIL", "you@example.com")
PASSWORD = os.getenv("CCTV_PASSWORD", "XXXX-XXXX-XXXX")
DIRECT_IP = "103.250.160.189"
CAM_ID = "cam04"

encoded_email = url_quote(EMAIL, safe="")
url = f"rtsp://{encoded_email}:{PASSWORD}@{DIRECT_IP}:8554/stream/{CAM_ID}"
print(f"Connecting to: rtsp://***@{DIRECT_IP}:8554/stream/{CAM_ID}")

cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
print("Opened:", cap.isOpened())
if cap.isOpened():
    ret, frame = cap.read()
    print("Read:", ret, "Shape:", frame.shape if ret else "N/A")
    cap.release()
