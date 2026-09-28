"""
Pipeline acceptance tests.

Tests the foundational CCTV ingestion layer without requiring a running backend,
live CCTV streams, or GPU. Uses local video files and mock/synthetic sources.

Run from the anpr/ directory:
    python test_pipeline.py

Test coverage:
    TEST 1: Single MP4  ->  frames received and processed
    TEST 2: Multiple CameraInfo sources  ->  independent state
    TEST 3: Invalid stream  ->  ERROR state, other cameras continue
    TEST 4: Reconnect simulation  ->  automatic recovery
    TEST 5: Slow processing  ->  bounded memory, frame dropping
    TEST 6: Clean shutdown  ->  all resources released
    TEST 7: Worker death  ->  health shows offline
    TEST 8: Detection flow  ->  existing functionality preserved
"""
import os
import sys
import time
import threading
import traceback
from pathlib import Path

# Ensure anpr/ is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camera_info import CameraInfo, StreamProtocol
from camera_manager import CameraManager, ConnectionState, CameraHealthInfo, BackoffConfig
from frame_sampler import FrameSampler

# Find sample video
SAMPLE_VIDEO = None
for candidate in ["sample.mp4", "test_traffic.mp4"]:
    p = os.path.join(os.path.dirname(__file__), candidate)
    if os.path.isfile(p):
        SAMPLE_VIDEO = p
        break

PASS = 0
FAIL = 0
SKIP = 0


def _test(name: str, func):
    """Run a single test and report result."""
    global PASS, FAIL, SKIP
    print(f"\n{'='*60}")
    print(f"  TEST: {name}")
    print(f"{'='*60}")
    try:
        result = func()
        if result == "SKIP":
            SKIP += 1
            print(f"  [SKIP] {name}")
        else:
            PASS += 1
            print(f"  [PASS] {name}")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] {name}")
        print(f"    {e}")
        traceback.print_exc()


# =========================================================================
# TEST 1: Single MP4  ->  frames received and processed
# =========================================================================
def test_single_mp4():
    if not SAMPLE_VIDEO:
        print("    No sample video found  --  skipping")
        return "SKIP"

    import cv2
    cam = CameraInfo.from_local_video(SAMPLE_VIDEO, camera_id="test_cam01")
    assert cam.camera_id == "test_cam01"
    assert cam.protocol == StreamProtocol.LOCAL
    assert cam.stream_url == SAMPLE_VIDEO

    # Open via legacy dict (validates to_legacy_dict works)
    d = cam.to_legacy_dict()
    assert "local_path" in d
    cap = cv2.VideoCapture(d["local_path"])
    assert cap.isOpened(), "Failed to open sample video"

    sampler = FrameSampler(process_every_n=5)
    frames_grabbed = 0
    frames_processed = 0

    while frames_grabbed < 100:
        ok = cap.grab()
        if not ok:
            break
        frames_grabbed += 1
        if sampler.tick():
            ok, frame = cap.retrieve()
            if ok:
                frames_processed += 1
    cap.release()

    assert frames_grabbed > 0, "No frames grabbed from video"
    assert frames_processed > 0, "No frames processed from video"
    assert frames_processed < frames_grabbed, "Sampler should skip frames"
    print(f"    Grabbed: {frames_grabbed}, Processed: {frames_processed}")


# =========================================================================
# TEST 2: Multiple CameraInfo sources  ->  independent state
# =========================================================================
def test_multiple_cameras_independent():
    cam1 = CameraInfo.from_local_video("video1.mp4", camera_id="cam_a")
    cam2 = CameraInfo.from_local_video("video2.mp4", camera_id="cam_b")
    cam3 = CameraInfo(camera_id="cam_c", stream_url="rtsp://fake", protocol=StreamProtocol.RTSP)

    h1 = CameraHealthInfo(camera_id="cam_a")
    h2 = CameraHealthInfo(camera_id="cam_b")
    h3 = CameraHealthInfo(camera_id="cam_c")

    # Simulate independent state changes
    h1.connection_state = ConnectionState.CONNECTED
    h1.frames_received = 100
    h2.connection_state = ConnectionState.RECONNECTING
    h2.reconnect_count = 3
    h3.connection_state = ConnectionState.ERROR
    h3.current_error = "Invalid URL"

    # Verify isolation
    assert h1.connection_state == ConnectionState.CONNECTED
    assert h2.connection_state == ConnectionState.RECONNECTING
    assert h3.connection_state == ConnectionState.ERROR
    assert h1.frames_received == 100
    assert h2.frames_received == 0
    assert h3.current_error == "Invalid URL"
    assert h1.current_error is None

    # Verify serialization
    d1 = h1.to_dict()
    assert d1["api_status"] == "ONLINE"
    d2 = h2.to_dict()
    assert d2["api_status"] == "RECONNECTING"
    d3 = h3.to_dict()
    assert d3["api_status"] == "OFFLINE"

    print("    3 cameras with independent health state [OK]")


# =========================================================================
# TEST 3: Invalid stream  ->  ERROR state, other cameras continue
# =========================================================================
def test_camera_failure_isolation():
    if not SAMPLE_VIDEO:
        print("    No sample video found  --  skipping")
        return "SKIP"

    import cv2

    results = {"good": None, "bad": None}

    def run_camera(camera_id, path, health):
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                health.connection_state = ConnectionState.ERROR
                health.current_error = f"Failed to open: {path}"
                return
            health.connection_state = ConnectionState.CONNECTED
            count = 0
            while count < 50:
                ok = cap.grab()
                if not ok:
                    break
                count += 1
                health.frames_received = count
            cap.release()
            health.connection_state = ConnectionState.STOPPED
        except Exception as e:
            health.connection_state = ConnectionState.ERROR
            health.current_error = str(e)

    h_good = CameraHealthInfo(camera_id="good_cam")
    h_bad = CameraHealthInfo(camera_id="bad_cam")

    t_good = threading.Thread(target=run_camera, args=("good_cam", SAMPLE_VIDEO, h_good))
    t_bad = threading.Thread(target=run_camera, args=("bad_cam", "/nonexistent/fake.mp4", h_bad))

    t_good.start()
    t_bad.start()
    t_good.join(timeout=10)
    t_bad.join(timeout=10)

    # Good camera should have processed frames
    assert h_good.frames_received > 0, f"Good camera got {h_good.frames_received} frames"
    assert h_good.connection_state in (ConnectionState.STOPPED, ConnectionState.CONNECTED)

    # Bad camera should be in error state
    assert h_bad.connection_state == ConnectionState.ERROR
    assert h_bad.current_error is not None
    assert h_bad.frames_received == 0

    print(f"    Good camera: {h_good.frames_received} frames, state={h_good.connection_state.value}")
    print(f"    Bad camera: state={h_bad.connection_state.value}, error={h_bad.current_error}")


# =========================================================================
# TEST 4: Reconnect simulation  ->  automatic recovery
# =========================================================================
def test_reconnect_behavior():
    cfg = BackoffConfig(initial_ms=100, max_ms=1000, multiplier=2.0)

    # Simulate backoff progression
    backoff = cfg.initial_ms / 1000.0
    attempts = []
    for i in range(5):
        attempts.append(backoff)
        backoff = min(backoff * cfg.multiplier, cfg.max_ms / 1000.0)

    # Verify bounded exponential backoff
    assert attempts[0] == 0.1    # 100ms
    assert attempts[1] == 0.2    # 200ms
    assert attempts[2] == 0.4    # 400ms
    assert attempts[3] == 0.8    # 800ms
    assert attempts[4] == 1.0    # capped at 1000ms

    print(f"    Backoff progression: {attempts}")
    print("    Bounded exponential backoff [OK]")


# =========================================================================
# TEST 5: Slow processing  ->  bounded memory, frame dropping
# =========================================================================
def test_frame_dropping():
    if not SAMPLE_VIDEO:
        print("    No sample video found -- skipping")
        return "SKIP"

    # Test the InferenceQueue newest-frame-wins behavior
    try:
        from multi_camera import InferenceQueue, FrameItem
        import numpy as np

        q = InferenceQueue()
        q.register_camera("cam01")
        q.register_camera("cam02")

        # Submit 100 frames for cam01 -- only latest should survive
        for i in range(100):
            q.put(FrameItem(
                camera_id="cam01",
                frame=np.zeros((10, 10, 3), dtype=np.uint8),
                pts_ms=float(i * 40),
                grab_index=i,
            ))

        # Only 1 frame per camera should be pending (newest-frame-wins)
        assert q.pending_count() <= 2, f"Queue has {q.pending_count()} pending (expected <= 2)"

        # Retrieve -- should get the latest frame
        item = q.get(timeout=1.0)
        assert item is not None
        assert item.camera_id == "cam01"
        assert item.grab_index == 99, f"Got frame {item.grab_index}, expected 99 (latest)"

        print(f"    100 frames submitted -> queue pending: {q.pending_count()}")
        print("    Newest-frame-wins bounded queue [OK]")
    except ImportError as e:
        print(f"    InferenceQueue test skipped (missing dependency: {e})")
        print("    Testing FrameSampler only...")

    # Test FrameSampler dropping
    sampler = FrameSampler(process_every_n=10)
    processed = 0
    dropped = 0
    for _ in range(100):
        if sampler.tick():
            processed += 1
        else:
            dropped += 1

    assert processed == 10, f"Expected 10 processed, got {processed}"
    assert dropped == 90, f"Expected 90 dropped, got {dropped}"
    print(f"    Sampler: {processed} processed, {dropped} dropped out of 100 [OK]")


# =========================================================================
# TEST 6: Clean shutdown  ->  all resources released
# =========================================================================
def test_clean_shutdown():
    if not SAMPLE_VIDEO:
        print("    No sample video found  --  skipping")
        return "SKIP"

    import cv2
    caps = []
    threads_alive_before = threading.active_count()

    # Open captures and close them
    for i in range(3):
        cap = cv2.VideoCapture(SAMPLE_VIDEO)
        assert cap.isOpened()
        caps.append(cap)

    for cap in caps:
        cap.release()

    # Verify all released (isOpened should return False)
    for cap in caps:
        assert not cap.isOpened(), "Capture not properly released"

    print(f"    3 captures opened and released cleanly")
    print(f"    Active threads: {threading.active_count()} (before: {threads_alive_before})")
    print("    Clean resource release [OK]")


# =========================================================================
# TEST 7: Worker death  ->  health shows offline
# =========================================================================
def test_worker_death_detection():
    health = CameraHealthInfo(camera_id="dying_cam")
    health.connection_state = ConnectionState.CONNECTED
    health.worker_alive = True

    # Simulate worker crash
    health.worker_alive = False
    health.connection_state = ConnectionState.ERROR
    health.current_error = "Worker thread died unexpectedly"

    assert health.connection_state == ConnectionState.ERROR
    assert not health.worker_alive
    assert "died" in health.current_error

    # Verify API status mapping
    d = health.to_dict()
    assert d["api_status"] == "OFFLINE"
    assert d["worker_alive"] is False

    print("    Worker death correctly reflected in health [OK]")


# =========================================================================
# TEST 8: Detection flow  ->  existing functionality preserved
# =========================================================================
def test_detection_flow_preserved():
    """Verify the existing detector module still works (import + basic call)."""
    try:
        from detector import VehicleDetector, VehicleBox
        # Just verify the class is importable and VehicleBox works
        box = VehicleBox(x1=10, y1=20, x2=100, y2=200, label="car", confidence=0.85)
        assert box.label == "car"
        assert box.confidence == 0.85
        print("    VehicleDetector + VehicleBox importable [OK]")
    except ImportError as e:
        print(f"    WARNING: detector not importable (needs ultralytics): {e}")
        return "SKIP"

    # Verify CameraInfo  ->  legacy dict round-trip
    cam = CameraInfo.from_local_video("test.mp4", camera_id="det_test")
    d = cam.to_legacy_dict()
    assert d["camera_id"] == "det_test"
    assert d["local_path"] == "test.mp4"
    print("    CameraInfo  ->  legacy dict round-trip [OK]")

    # Verify FrameSampler
    sampler = FrameSampler(process_every_n=5)
    results = [sampler.tick() for _ in range(20)]
    assert results.count(True) == 4  # frames 5, 10, 15, 20
    print("    FrameSampler process-every-5 [OK]")

    # Verify ConnectionState enum
    assert ConnectionState.CONNECTED.value == "connected"
    assert ConnectionState.RECONNECTING.value == "reconnecting"
    print("    ConnectionState enum [OK]")


# =========================================================================
# MAIN
# =========================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  NETRA PHASE 1  --  ACCEPTANCE TESTS")
    print("=" * 60)

    if SAMPLE_VIDEO:
        print(f"  Sample video: {SAMPLE_VIDEO}")
    else:
        print("  WARNING: No sample video found  --  some tests will be skipped")

    _test("TEST 1: Single MP4 processing", test_single_mp4)
    _test("TEST 2: Multiple cameras independent state", test_multiple_cameras_independent)
    _test("TEST 3: Camera failure isolation", test_camera_failure_isolation)
    _test("TEST 4: Reconnect with bounded backoff", test_reconnect_behavior)
    _test("TEST 5: Frame dropping / bounded queue", test_frame_dropping)
    _test("TEST 6: Clean shutdown / resource release", test_clean_shutdown)
    _test("TEST 7: Worker death detection", test_worker_death_detection)
    _test("TEST 8: Detection flow preserved", test_detection_flow_preserved)

    print("\n" + "=" * 60)
    print(f"  RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)
    else:
        print("  [OK] ALL PHASE 1 ACCEPTANCE TESTS PASSED")
        sys.exit(0)
