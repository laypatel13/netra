"""
In-memory multi-frame evidence buffer for tracked vehicles.

Each camera thread maintains its own set of TrackBuffers - one per active
track ID.  Observations are collected as the vehicle moves through the frame,
and the best K frames are selected when the track expires or reaches a
processing threshold.

Memory management: each buffer is capped at MAX_TRACK_FRAMES observations.
Old observations are dropped (FIFO) when the cap is reached.  Buffers are
discarded entirely once a track is processed or expires - no permanent storage
of every captured frame.  Only the selected evidence frames are persisted.
"""
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from enum import Enum

from quality import QualityMetrics


class ConsensusState(str, Enum):
    CONSENSUS = "consensus"
    WEAK_CONSENSUS = "weak_consensus"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


@dataclass
class ColorConsensus:
    dominant_color: str
    confidence: float
    usable_observations: int
    unknown_observations: int
    dominance_ratio: float
    temporal_span: float
    consensus_state: ConsensusState
    unknown_reason: Optional[str] = None

# Buffer sizing
MAX_TRACK_FRAMES = 10       # max observations kept per track
MAX_EVIDENCE_FRAMES = 2     # best frames selected for processing
TEMPORAL_DIVERSITY_MIN = 3  # min frame-index gap between selected evidence frames

# Track expiry
TRACK_EXPIRY_SECONDS = 5.0  # expire track after this many seconds without observation


@dataclass
class Observation:
    """One frame's observation of a tracked vehicle."""
    frame_index: int
    pts_ms: float
    bbox: tuple[int, int, int, int]     # (x1, y1, x2, y2)
    confidence: float
    crop: np.ndarray                    # raw BGR crop - kept in memory only
    quality: QualityMetrics
    vehicle_type: str
    vehicle_color: Optional[str] = None
    color_unknown_reason: Optional[str] = None
    loop_count: int = 0


@dataclass
class TrackBuffer:
    """
    Collects observations for a single tracked vehicle on a single camera.

    Thread-safe only within its own camera thread - no cross-thread sharing
    needed since each camera thread has its own buffer set.
    """
    camera_id: str
    track_id: int
    observations: deque = field(default_factory=lambda: deque(maxlen=MAX_TRACK_FRAMES))
    # Wall-clock time the track was first/last seen. Sandbox feeds are served
    # on a common live timeline, so this is the clock that is comparable
    # across cameras (per-stream PTS is not); last_seen_at becomes the
    # observation's observed_at for cross-camera route linking.
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    # Monotonic time is deliberately separate from evidence timestamps.  It is
    # safe for local resource housekeeping even if the system clock changes.
    created_monotonic: float = field(default_factory=time.monotonic)
    last_seen_monotonic: float = field(default_factory=time.monotonic)
    loop_count: int = 0

    def add(self, obs: Observation):
        """Add an observation, updating last-seen time and loop count."""
        self.observations.append(obs)
        self.last_seen_at = time.time()
        self.last_seen_monotonic = time.monotonic()
        self.loop_count = max(self.loop_count, obs.loop_count)

    @property
    def frame_count(self) -> int:
        return len(self.observations)

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_seen_monotonic) > TRACK_EXPIRY_SECONDS

    @property
    def has_enough_frames(self) -> bool:
        """Has collected enough frames for evidence selection."""
        return self.frame_count >= MAX_EVIDENCE_FRAMES

    @property
    def dominant_type(self) -> Optional[str]:
        """Most common vehicle type across observations."""
        if not self.observations:
            return None
        from collections import Counter
        types = [o.vehicle_type for o in self.observations if o.vehicle_type]
        if not types:
            return None
        return Counter(types).most_common(1)[0][0]

    @property
    def dominant_color(self) -> ColorConsensus:
        """Most common vehicle color across observations, enforcing a 60% consensus."""
        if not self.observations:
            return ColorConsensus("unknown", 0.0, 0, 0, 0.0, 0.0, ConsensusState.UNKNOWN)
            
        from collections import Counter
        colors = [o.vehicle_color for o in self.observations]
        usable_colors = [c for c in colors if c and c != "unknown"]
        
        unknown_observations = len(colors) - len(usable_colors)
        usable_observations = len(usable_colors)
        
        first_pts = self.observations[0].pts_ms
        last_pts = self.observations[-1].pts_ms
        temporal_span = max(0.0, (last_pts - first_pts) / 1000.0)
        
        if not usable_colors:
            reasons = [o.color_unknown_reason for o in self.observations if o.color_unknown_reason]
            dominant_reason = None
            if reasons:
                dominant_reason = Counter(reasons).most_common(1)[0][0]
            return ColorConsensus("unknown", 0.0, usable_observations, unknown_observations, 0.0, temporal_span, ConsensusState.UNKNOWN, dominant_reason)
            
        counter = Counter(usable_colors)
        most_common, count = counter.most_common(1)[0]
        
        dominance_ratio = count / usable_observations
        
        # Calculate average confidence for the dominant color (we don't store individual confidences in buffer currently,
        # but we could. For now, confidence = dominance_ratio)
        confidence = dominance_ratio
        
        if dominance_ratio >= 0.6:
            state = ConsensusState.CONSENSUS if count > 1 else ConsensusState.WEAK_CONSENSUS
            return ColorConsensus(most_common, confidence, usable_observations, unknown_observations, dominance_ratio, temporal_span, state)
        else:
            return ColorConsensus("unknown", 0.0, usable_observations, unknown_observations, dominance_ratio, temporal_span, ConsensusState.CONFLICTING)

    @property
    def mean_confidence(self) -> float:
        """Average detection confidence across observations."""
        if not self.observations:
            return 0.0
        return sum(o.confidence for o in self.observations) / len(self.observations)


def select_best_frames(
    buffer: TrackBuffer,
    k: int = MAX_EVIDENCE_FRAMES,
    temporal_gap: int = TEMPORAL_DIVERSITY_MIN,
) -> list[Observation]:
    """
    Select the best K frames from a track buffer.

    Strategy:
    1. Sort all observations by quality (descending)
    2. Greedily select frames while enforcing temporal diversity -
       reject a frame if it's within `temporal_gap` frame indices
       of an already-selected frame

    This avoids selecting almost-identical consecutive frames (e.g.
    frames 201, 202, 203) when temporally diverse frames (e.g.
    201, 208, 216) have similar quality - the diverse set gives the
    OCR consensus more independent observations.
    """
    if not buffer.observations:
        return []

    # Sort by overall quality, best first
    ranked = sorted(buffer.observations, key=lambda o: o.quality.overall, reverse=True)

    selected: list[Observation] = []
    selected_indices: set[int] = set()

    for obs in ranked:
        if len(selected) >= k:
            break

        # Check temporal diversity
        too_close = any(
            abs(obs.frame_index - idx) < temporal_gap
            for idx in selected_indices
        )
        if too_close and len(selected) > 0:
            continue

        selected.append(obs)
        selected_indices.add(obs.frame_index)

    # If we couldn't fill K frames with diversity, relax the constraint
    if len(selected) < k:
        for obs in ranked:
            if len(selected) >= k:
                break
            if obs not in selected:
                selected.append(obs)

    # Return in chronological order for OCR consensus
    selected.sort(key=lambda o: o.frame_index)
    return selected


class BufferManager:
    """
    Manages track buffers for a single camera thread.

    Tracks are created on first observation and expire after inactivity.
    """

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self._buffers: dict[int, TrackBuffer] = {}

    def get_or_create(self, track_id: int) -> TrackBuffer:
        """Get existing buffer or create a new one for this track."""
        if track_id not in self._buffers:
            self._buffers[track_id] = TrackBuffer(
                camera_id=self.camera_id,
                track_id=track_id,
            )
        return self._buffers[track_id]

    def remove(self, track_id: int):
        """Remove a track buffer (after processing or expiry)."""
        self._buffers.pop(track_id, None)

    def get_expired(self) -> list[TrackBuffer]:
        """Return all expired track buffers."""
        return [b for b in self._buffers.values() if b.is_expired]

    def get_ready(self) -> list[TrackBuffer]:
        """Return buffers that have enough frames for processing."""
        return [b for b in self._buffers.values() if b.has_enough_frames]

    def cleanup_expired(self) -> list[TrackBuffer]:
        """Remove and return all expired buffers."""
        expired = self.get_expired()
        for b in expired:
            self.remove(b.track_id)
        return expired

    @property
    def active_count(self) -> int:
        return len(self._buffers)
