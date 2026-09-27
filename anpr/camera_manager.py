"""
Camera connection manager for NETRA pipeline.

Manages the lifecycle of camera workers: enumeration, start/stop,
health tracking, reconnect with bounded backoff, and clean shutdown.

One broken camera must NOT stop other cameras.
A camera is CONNECTED only when the worker is actually receiving frames.

Connection states:
    DISCONNECTED  — not started or cleanly stopped
    CONNECTING    — worker is attempting to open the stream
    CONNECTED     — actively receiving frames
    DEGRADED      — connected but experiencing issues (frame timeouts)
    RECONNECTING  — lost connection, retrying with backoff
    STOPPED       — explicitly stopped by user/manager
    ERROR         — unrecoverable error, not retrying
"""
import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from camera_info import CameraInfo

log = logging.getLogger("netra.camera_manager")


class ConnectionState(str, Enum):
    """Explicit connection states for camera workers."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    ERROR = "error"


# Mapping from ConnectionState to API-friendly status strings
STATE_TO_API_STATUS = {
    ConnectionState.DISCONNECTED: "OFFLINE",
    ConnectionState.CONNECTING: "CONNECTING",
    ConnectionState.CONNECTED: "ONLINE",
    ConnectionState.DEGRADED: "DEGRADED",
    ConnectionState.RECONNECTING: "RECONNECTING",
    ConnectionState.STOPPED: "OFFLINE",
    ConnectionState.ERROR: "OFFLINE",
}


@dataclass
class CameraHealthInfo:
    """
    Runtime health information for a single camera.

    Written by one camera worker thread only. Read by the manager
    and health API. Fields use simple types for thread-safe reads
    (CPython GIL makes int/float/str reads atomic).
    """
    camera_id: str
    connection_state: ConnectionState = ConnectionState.DISCONNECTED

    # Frame counters
    frames_received: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0

    # Timestamps (time.time() floats)
    last_frame_received: float = 0.0
    last_frame_processed: float = 0.0

    # Connection reliability
    reconnect_count: int = 0
    current_error: Optional[str] = None

    # Worker status
    worker_alive: bool = False

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "camera_id": self.camera_id,
            "connection_state": self.connection_state.value,
            "api_status": STATE_TO_API_STATUS.get(self.connection_state, "OFFLINE"),
            "frames_received": self.frames_received,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "last_frame_received": self.last_frame_received,
            "last_frame_processed": self.last_frame_processed,
            "reconnect_count": self.reconnect_count,
            "current_error": self.current_error,
            "worker_alive": self.worker_alive,
        }

    def to_heartbeat_dict(self) -> dict:
        """Format for the pipeline heartbeat payload (backward compatible)."""
        return {
            "camera_id": self.camera_id,
            "status": STATE_TO_API_STATUS.get(self.connection_state, "OFFLINE").lower(),
            "connection_state": self.connection_state.value,
            "frames_read": self.frames_received,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "vehicles_detected": 0,  # filled by the worker
            "tracks_active": 0,      # filled by the worker
            "matches": 0,            # filled by the worker
            "reconnect_count": self.reconnect_count,
            "last_frame_time": self.last_frame_received,
            "current_error": self.current_error,
        }


@dataclass
class BackoffConfig:
    """Bounded exponential backoff configuration."""
    initial_ms: float = 2000
    max_ms: float = 30000
    multiplier: float = 2.0

    @classmethod
    def from_dict(cls, d: dict) -> "BackoffConfig":
        return cls(
            initial_ms=d.get("initial_ms", 2000),
            max_ms=d.get("max_ms", 30000),
            multiplier=d.get("multiplier", 2.0),
        )

    def to_dict(self) -> dict:
        return {
            "initial_ms": self.initial_ms,
            "max_ms": self.max_ms,
            "multiplier": self.multiplier,
        }


class CameraManager:
    """
    Manages the lifecycle of camera workers.

    Responsibilities:
    - Enumerate enabled cameras
    - Start/stop individual or all camera workers
    - Track health for all cameras
    - Prevent duplicate workers
    - Clean shutdown on Ctrl+C / SIGTERM

    Thread-safety: the manager itself runs on the main thread.
    Worker threads update their own CameraHealthInfo instances.
    """

    def __init__(self, backoff_cfg: Optional[BackoffConfig] = None):
        self._cameras: dict[str, CameraInfo] = {}
        self._health: dict[str, CameraHealthInfo] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._global_stop = threading.Event()
        self._lock = threading.Lock()
        self.backoff_cfg = backoff_cfg or BackoffConfig()
        self._worker_factory: Optional[Callable] = None
        self._shutdown_registered = False

    def register_cameras(self, cameras: list[CameraInfo]):
        """Register cameras that this manager is responsible for."""
        with self._lock:
            for cam in cameras:
                self._cameras[cam.camera_id] = cam
                if cam.camera_id not in self._health:
                    self._health[cam.camera_id] = CameraHealthInfo(
                        camera_id=cam.camera_id
                    )

    def set_worker_factory(self, factory: Callable):
        """
        Set the factory function that creates worker threads.

        The factory receives (CameraInfo, CameraHealthInfo, threading.Event, BackoffConfig)
        and returns a threading.Thread (not started).
        """
        self._worker_factory = factory

    def enumerate_enabled(self) -> list[CameraInfo]:
        """Return all enabled cameras."""
        return [c for c in self._cameras.values() if c.enabled]

    def start_camera(self, camera_id: str) -> bool:
        """Start a single camera worker. Returns False if already running or not found."""
        with self._lock:
            if camera_id not in self._cameras:
                log.error("[%s] Camera not registered", camera_id)
                return False

            if camera_id in self._workers and self._workers[camera_id].is_alive():
                log.warning("[%s] Worker already running — skipping duplicate", camera_id)
                return False

            if self._worker_factory is None:
                log.error("No worker factory set — call set_worker_factory() first")
                return False

            cam = self._cameras[camera_id]
            health = self._health[camera_id]
            stop_event = threading.Event()
            self._stop_events[camera_id] = stop_event

            # Reset health for new start
            health.connection_state = ConnectionState.CONNECTING
            health.current_error = None
            health.worker_alive = True

            worker = self._worker_factory(cam, health, stop_event, self.backoff_cfg)
            worker.daemon = True
            self._workers[camera_id] = worker
            worker.start()

            log.info("[%s] Worker started", camera_id)
            return True

    def stop_camera(self, camera_id: str) -> bool:
        """Stop a single camera worker. Returns False if not found/not running."""
        with self._lock:
            stop_event = self._stop_events.get(camera_id)
            if stop_event:
                stop_event.set()

            health = self._health.get(camera_id)
            if health:
                health.connection_state = ConnectionState.STOPPED
                health.worker_alive = False

            log.info("[%s] Stop signal sent", camera_id)
            return True

    def start_all(self, stagger_seconds: float = 0.25):
        """Start all enabled cameras with staggered connection timing."""
        self._register_signal_handlers()
        enabled = self.enumerate_enabled()
        log.info("Starting %d camera workers", len(enabled))

        for i, cam in enumerate(enabled):
            self.start_camera(cam.camera_id)
            if i < len(enabled) - 1 and stagger_seconds > 0:
                time.sleep(stagger_seconds)

        log.info("All %d camera workers started", len(enabled))

    def stop_all(self):
        """Stop all camera workers and wait for them to finish."""
        log.info("Stopping all camera workers...")
        self._global_stop.set()

        # Signal all workers to stop
        for stop_event in self._stop_events.values():
            stop_event.set()

        # Wait for workers to finish (with timeout)
        for camera_id, worker in self._workers.items():
            if worker.is_alive():
                worker.join(timeout=5.0)
                if worker.is_alive():
                    log.warning("[%s] Worker did not stop within timeout", camera_id)

        # Update health
        for health in self._health.values():
            health.connection_state = ConnectionState.STOPPED
            health.worker_alive = False

        log.info("All camera workers stopped")

    def wait(self):
        """Block until all workers finish or stop is called."""
        try:
            while not self._global_stop.is_set():
                all_done = True
                for worker in self._workers.values():
                    if worker.is_alive():
                        all_done = False
                        break
                if all_done:
                    break
                self._global_stop.wait(timeout=1.0)
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — stopping all workers")
            self.stop_all()

    def get_health(self, camera_id: str) -> Optional[CameraHealthInfo]:
        """Get health info for a specific camera."""
        health = self._health.get(camera_id)
        if health and camera_id in self._workers:
            # Check if the worker thread is actually alive
            worker = self._workers[camera_id]
            if not worker.is_alive() and health.connection_state not in (
                ConnectionState.STOPPED, ConnectionState.ERROR
            ):
                # Worker died unexpectedly
                health.connection_state = ConnectionState.ERROR
                health.worker_alive = False
                health.current_error = "Worker thread died unexpectedly"
        return health

    def get_all_health(self) -> dict[str, CameraHealthInfo]:
        """Get health info for all cameras. Checks worker liveness."""
        for camera_id in self._health:
            self.get_health(camera_id)  # triggers liveness check
        return dict(self._health)

    def is_any_alive(self) -> bool:
        """Check if any worker is still running."""
        return any(w.is_alive() for w in self._workers.values())

    def _register_signal_handlers(self):
        """Register clean shutdown on SIGINT/SIGTERM (main thread only)."""
        if self._shutdown_registered:
            return
        try:
            original_sigint = signal.getsignal(signal.SIGINT)

            def _shutdown_handler(signum, frame):
                log.info("Received signal %s — initiating clean shutdown", signum)
                self.stop_all()
                # Call original handler (if any) to allow normal exit
                if callable(original_sigint) and original_sigint not in (
                    signal.SIG_DFL, signal.SIG_IGN
                ):
                    original_sigint(signum, frame)
                else:
                    raise KeyboardInterrupt

            signal.signal(signal.SIGINT, _shutdown_handler)
            self._shutdown_registered = True
        except ValueError:
            # Not on main thread — can't register signal handlers
            pass
